import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, ChevronLeft, ChevronRight, Loader2, Play, RefreshCw, Tv2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import api from '@/lib/api'

// ── Constants ────────────────────────────────────────────────────────────────

const CHAN_W    = 176   // px — sticky left channel column
const PX_PER_M = 5     // px per minute → 300px/hr
const ROW_H    = 52    // px per channel row
const HDR_H    = 32    // px — time header height
const SLOT_MIN = 30    // minutes between time labels

const OFFSETS = [-120, -60, -30, 0, 30, 60, 120] // minutes

function fmtOffset(m: number) {
  if (m === 0) return '0'
  const sign = m > 0 ? '+' : '−'
  const abs  = Math.abs(m)
  return abs % 60 === 0 ? `${sign}${abs / 60}h` : `${sign}${abs}m`
}

// ── Types ────────────────────────────────────────────────────────────────────

interface GuideChannel {
  channel_id:     number
  channel_name:   string
  channel_number: number | null
  tvg_id:         string
  has_epg:        boolean
  has_stream:     boolean
}

interface Program {
  title:       string
  start:       string
  stop:        string
  description: string
}

interface GuideData {
  window_start: string
  window_end:   string
  channels:     GuideChannel[]
  programs:     Record<string, Program[]>
}

// ── Program block tooltip ─────────────────────────────────────────────────────

function ProgramTooltip({
  program,
  blockLeft,
  blockWidth,
  totalWidth,
  offsetMin,
  onClose,
}: {
  program:    Program
  blockLeft:  number
  blockWidth: number
  totalWidth: number
  offsetMin:  number
  onClose:    () => void
}) {
  const start = new Date(new Date(program.start).getTime() + offsetMin * 60000)
  const stop  = new Date(new Date(program.stop).getTime()  + offsetMin * 60000)
  const fmt   = (d: Date) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const flipLeft = blockLeft + blockWidth > totalWidth - 240

  return (
    <div
      className={`absolute top-full mt-1 z-50 w-56 rounded-lg border border-border bg-card shadow-2xl p-3 space-y-1.5 ${flipLeft ? 'right-0' : 'left-0'}`}
      onClick={e => e.stopPropagation()}
    >
      <p className="text-xs font-semibold leading-snug text-foreground">{program.title}</p>
      <p className="text-[10px] text-muted-foreground">{fmt(start)} – {fmt(stop)}</p>
      {program.description && (
        <p className="text-[10px] text-foreground/70 leading-relaxed line-clamp-4">{program.description}</p>
      )}
      <button className="text-[10px] text-muted-foreground hover:text-foreground mt-0.5" onClick={onClose}>
        Close
      </button>
    </div>
  )
}

// ── Program block ─────────────────────────────────────────────────────────────

function ProgramBlock({
  program,
  windowStartMs,
  windowEndMs,
  offsetMin,
  totalWidth,
}: {
  program:       Program
  windowStartMs: number
  windowEndMs:   number
  offsetMin:     number
  totalWidth:    number
}) {
  const [tip, setTip] = useState(false)

  const rawStart = new Date(program.start).getTime()
  const rawStop  = new Date(program.stop).getTime()
  const dispStart = rawStart + offsetMin * 60000
  const dispStop  = rawStop  + offsetMin * 60000

  const clampedStart = Math.max(dispStart, windowStartMs)
  const clampedStop  = Math.min(dispStop,  windowEndMs)
  if (clampedStart >= clampedStop) return null

  const left  = ((clampedStart - windowStartMs) / 60000) * PX_PER_M
  const width = Math.max(2, ((clampedStop - clampedStart) / 60000) * PX_PER_M - 2)

  const isNow = rawStart <= Date.now() && Date.now() < rawStop

  return (
    <div
      className="absolute top-1 bottom-1 rounded cursor-pointer select-none overflow-hidden group"
      style={{ left, width }}
      onClick={() => setTip(t => !t)}
    >
      <div className={`h-full px-1.5 flex items-center rounded border text-xs transition-colors ${
        isNow
          ? 'bg-primary/20 border-primary/50 group-hover:bg-primary/30'
          : 'bg-accent/60 border-border group-hover:bg-accent'
      }`}>
        <span className="truncate font-medium text-[11px]">{program.title}</span>
      </div>
      {tip && (
        <ProgramTooltip
          program={program}
          blockLeft={left}
          blockWidth={width}
          totalWidth={totalWidth}
          offsetMin={offsetMin}
          onClose={() => setTip(false)}
        />
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function EPGGuide({
  onPlay,
  guideWindowHours,
}: {
  onPlay:            (channelId: number, channelName: string) => void
  guideWindowHours?: number
}) {
  const hours     = guideWindowHours ?? 2
  const [offsetMin, setOffsetMin] = useState(0)
  const [nameFilter, setNameFilter] = useState('')

  const { data, isLoading, isError, refetch, isFetching } = useQuery<GuideData>({
    queryKey: ['epg-guide', hours],
    queryFn:  () => api.get(`/guide/?hours=${hours}`).then(r => r.data),
    staleTime: 120_000,
    refetchInterval: false,
  })

  const scrollRef = useRef<HTMLDivElement>(null)
  const nowLineRef = useRef<HTMLDivElement>(null)

  const windowStartMs = data ? new Date(data.window_start).getTime() : Date.now()
  const windowEndMs   = data ? new Date(data.window_end).getTime()   : Date.now() + hours * 3600000
  const totalWidth    = hours * 60 * PX_PER_M

  // Scroll to place "now" ~15% from left on load
  useEffect(() => {
    if (!data || !scrollRef.current) return
    const nowOffsetPx = ((Date.now() - windowStartMs) / 60000) * PX_PER_M
    const viewW = scrollRef.current.clientWidth - CHAN_W
    scrollRef.current.scrollLeft = Math.max(0, nowOffsetPx - viewW * 0.15)
  }, [data, windowStartMs])

  // Build 30-min time slots for the header
  const timeSlots: { label: string; left: number }[] = []
  if (data) {
    let t = new Date(windowStartMs)
    // Round up to next 30-min boundary
    const mins = t.getMinutes()
    const nextSlot = mins === 0 ? 0 : SLOT_MIN - (mins % SLOT_MIN)
    t = new Date(t.getTime() + nextSlot * 60000)
    while (t.getTime() < windowEndMs) {
      const left = ((t.getTime() - windowStartMs) / 60000) * PX_PER_M
      timeSlots.push({
        label: t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        left,
      })
      t = new Date(t.getTime() + SLOT_MIN * 60000)
    }
  }

  const nowLeft = ((Date.now() - windowStartMs) / 60000) * PX_PER_M

  const channels = (data?.channels ?? []).filter(ch =>
    !nameFilter || ch.channel_name.toLowerCase().includes(nameFilter.toLowerCase())
  )

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-3 h-full">

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Name filter */}
        <input
          className="h-8 px-2.5 text-xs rounded border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring w-40"
          placeholder="Filter channels…"
          value={nameFilter}
          onChange={e => setNameFilter(e.target.value)}
        />

        {/* Time offset */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-muted-foreground">EPG offset:</span>
          <div className="flex items-center border border-border rounded overflow-hidden">
            {OFFSETS.map(m => (
              <button
                key={m}
                onClick={() => setOffsetMin(m)}
                className={`px-2 py-1 text-[11px] transition-colors ${
                  offsetMin === m
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                }`}
              >
                {fmtOffset(m)}
              </button>
            ))}
          </div>
          {offsetMin !== 0 && (
            <span className="text-[10px] text-yellow-400 ml-1">
              Times shifted {fmtOffset(offsetMin)}
            </span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{channels.length} channels</span>
          <Button size="sm" variant="outline" className="h-8 text-xs gap-1.5" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
            Reload
          </Button>
        </div>
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20 text-muted-foreground gap-2">
          <Loader2 size={16} className="animate-spin" /> Building guide…
        </div>
      ) : isError ? (
        <div className="flex items-center justify-center py-20 text-muted-foreground gap-2 text-sm">
          <AlertCircle size={14} className="text-destructive" /> Failed to load guide data
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="overflow-auto rounded-lg border border-border bg-card flex-1"
          style={{ minHeight: 200 }}
        >
          {/* Inner container: total width = CHAN_W + totalWidth */}
          <div style={{ minWidth: CHAN_W + totalWidth }}>

            {/* Header row */}
            <div className="flex sticky top-0 z-20 bg-card border-b border-border" style={{ height: HDR_H }}>
              {/* Top-left corner */}
              <div className="shrink-0 border-r border-border bg-card z-30" style={{ width: CHAN_W, height: HDR_H }} />
              {/* Time labels */}
              <div className="relative flex-1 overflow-hidden" style={{ height: HDR_H }}>
                {timeSlots.map((slot, i) => (
                  <span
                    key={i}
                    className="absolute top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground px-1 whitespace-nowrap"
                    style={{ left: slot.left }}
                  >
                    {slot.label}
                  </span>
                ))}
                {/* Now marker in header */}
                {nowLeft >= 0 && nowLeft <= totalWidth && (
                  <div
                    className="absolute top-0 bottom-0 w-px bg-red-500/70"
                    style={{ left: nowLeft }}
                  />
                )}
              </div>
            </div>

            {/* Channel rows */}
            {channels.length === 0 ? (
              <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                No channels found
              </div>
            ) : channels.map(ch => {
              const programs = data?.programs[ch.tvg_id] ?? []
              return (
                <div key={ch.channel_id} className="flex border-b border-border last:border-0" style={{ height: ROW_H }}>

                  {/* Channel cell (sticky left) */}
                  <div
                    className="sticky left-0 z-10 shrink-0 bg-card border-r border-border flex items-center gap-2 px-2"
                    style={{ width: CHAN_W, height: ROW_H }}
                  >
                    <div className="flex flex-col min-w-0 flex-1">
                      {ch.channel_number != null && (
                        <span className="text-[9px] text-muted-foreground leading-none mb-0.5">
                          {ch.channel_number}
                        </span>
                      )}
                      <span className="text-xs font-medium truncate leading-tight">{ch.channel_name}</span>
                      {!ch.has_epg && (
                        <span className="text-[9px] text-muted-foreground/60 leading-none mt-0.5">No EPG</span>
                      )}
                    </div>
                    {ch.has_stream && (
                      <button
                        className="shrink-0 p-1 rounded hover:bg-primary/20 text-muted-foreground hover:text-primary transition-colors"
                        title={`Play ${ch.channel_name}`}
                        onClick={() => onPlay(ch.channel_id, ch.channel_name)}
                      >
                        <Play size={12} fill="currentColor" />
                      </button>
                    )}
                  </div>

                  {/* Program row */}
                  <div className="relative" style={{ width: totalWidth, height: ROW_H }}>
                    {programs.map((p, i) => (
                      <ProgramBlock
                        key={i}
                        program={p}
                        windowStartMs={windowStartMs}
                        windowEndMs={windowEndMs}
                        offsetMin={offsetMin}
                        totalWidth={totalWidth}
                      />
                    ))}
                    {programs.length === 0 && ch.has_epg && (
                      <div className="absolute inset-0 flex items-center px-2">
                        <span className="text-[10px] text-muted-foreground/50 italic">No data in cache — refresh EPG</span>
                      </div>
                    )}
                    {/* Current time line */}
                    {nowLeft >= 0 && nowLeft <= totalWidth && (
                      <div
                        ref={nowLineRef}
                        className="absolute top-0 bottom-0 w-px bg-red-500 z-10 pointer-events-none"
                        style={{ left: nowLeft }}
                      />
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-primary/20 border border-primary/40" />
          Now playing
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-accent/60 border border-border" />
          Upcoming
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-px h-3 bg-red-500" />
          Current time
        </span>
        <span className="flex items-center gap-1.5">
          <Play size={10} className="text-muted-foreground" /> Play stream
        </span>
        <span className="ml-auto flex items-center gap-1 opacity-60">
          <Tv2 size={10} /> Click any program block for details
        </span>
      </div>
    </div>
  )
}
