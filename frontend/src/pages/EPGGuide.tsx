import { useCallback, useEffect, useMemo, useRef, useState, memo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, ChevronLeft, ChevronRight, Loader2, Play, RefreshCw, Tv2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import api from '@/lib/api'

// ── Constants ────────────────────────────────────────────────────────────────

const CHAN_W    = 184   // px — sticky left channel column
const PX_PER_M = 5     // px per minute → 300px/hr
const ROW_H    = 76    // px per channel row (taller for stacked logo)
const HDR_H    = 32    // px — time header height
const SLOT_MIN = 30    // minutes between time labels

const OFFSETS = [-120, -60, -30, 0, 30, 60, 120] // minutes

function fmtOffset(m: number) {
  if (m === 0) return '0'
  const sign = m > 0 ? '+' : '−'
  const abs  = Math.abs(m)
  return abs % 60 === 0 ? `${sign}${abs / 60}h` : `${sign}${abs}m`
}

const fmtTime = (d: Date) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

// ── Types ────────────────────────────────────────────────────────────────────

interface Profile {
  id:   number
  name: string
}

interface GuideChannel {
  channel_id:       number
  channel_name:     string
  channel_number:   number | null
  channel_group:    string
  channel_group_id: number | null
  tvg_id:           string
  logo_url:         string
  has_epg:          boolean
  has_stream:       boolean
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

interface SelectedProgram {
  program:     Program
  channelName: string
  offsetMin:   number
  clientX:     number
  clientY:     number
}

// ── Program block ─────────────────────────────────────────────────────────────

const ProgramBlock = memo(function ProgramBlock({
  program,
  windowStartMs,
  windowEndMs,
  offsetMin,
  onSelect,
}: {
  program:       Program
  windowStartMs: number
  windowEndMs:   number
  offsetMin:     number
  onSelect:      (e: React.MouseEvent) => void
}) {
  const rawStart  = new Date(program.start).getTime()
  const rawStop   = new Date(program.stop).getTime()
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
      onClick={e => { e.stopPropagation(); onSelect(e) }}
    >
      <div className={`h-full px-1.5 py-1 flex flex-col justify-center rounded border transition-colors ${
        isNow
          ? 'bg-primary/20 border-primary/50 group-hover:bg-primary/30'
          : 'bg-accent/60 border-border group-hover:bg-accent'
      }`}>
        <span className="truncate font-medium text-[11px] leading-tight">{program.title}</span>
        <span className="truncate text-[9px] opacity-60 leading-tight mt-0.5">
          {fmtTime(new Date(dispStart))} – {fmtTime(new Date(dispStop))}
        </span>
        {program.description && width > 120 && (
          <span className="truncate text-[9px] opacity-50 leading-tight mt-0.5">{program.description}</span>
        )}
      </div>
    </div>
  )
})

// ── Program detail overlay ────────────────────────────────────────────────────

function ProgramDetail({ selected, onClose }: { selected: SelectedProgram; onClose: () => void }) {
  const start    = new Date(new Date(selected.program.start).getTime() + selected.offsetMin * 60000)
  const stop     = new Date(new Date(selected.program.stop).getTime()  + selected.offsetMin * 60000)
  const W        = 280
  const rawLeft  = selected.clientX - W / 2
  const rawTop   = selected.clientY + 14
  const left     = Math.max(8, Math.min(rawLeft, window.innerWidth  - W - 8))
  const flipUp   = rawTop + 200 > window.innerHeight - 8
  const top      = flipUp ? selected.clientY - 14 : rawTop
  const transform = flipUp ? 'translateY(-100%)' : undefined

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        className="fixed z-50 rounded-xl border border-border bg-card shadow-2xl p-4 space-y-2"
        style={{ left, top, width: W, transform }}
      >
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-semibold leading-snug text-foreground">{selected.program.title}</p>
          <button className="shrink-0 mt-0.5 text-muted-foreground hover:text-foreground transition-colors" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <p className="text-[11px] text-muted-foreground font-medium">{selected.channelName}</p>
        <p className="text-[10px] text-muted-foreground">{fmtTime(start)} – {fmtTime(stop)}</p>
        {selected.program.description && (
          <p className="text-[11px] text-foreground/75 leading-relaxed">{selected.program.description}</p>
        )}
      </div>
    </>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function EPGGuide({
  onPlay,
  guideWindowHours,
}: {
  onPlay:            (channelId: number, channelName: string, nowPlaying?: { title: string; start: string; stop: string }) => void
  guideWindowHours?: number
}) {
  const hours = guideWindowHours ?? 2
  const [offsetMin,   setOffsetMin]   = useState(0)
  const [nameFilter,  setNameFilter]  = useState('')
  const [groupFilter, setGroupFilter] = useState('')
  const [profileId,   setProfileId]   = useState<number | null>(null)
  const [selected,    setSelected]    = useState<SelectedProgram | null>(null)

  const { data: profiles } = useQuery<Profile[]>({
    queryKey:  ['profiles'],
    queryFn:   () => api.get('/profiles/').then(r => r.data),
    staleTime: 60_000,
  })

  const { data, isLoading, isError, refetch, isFetching } = useQuery<GuideData>({
    queryKey: ['epg-guide', hours, profileId],
    queryFn:  () => api.get(`/guide/?hours=${hours}${profileId ? `&profile_id=${profileId}` : ''}`).then(r => r.data),
    staleTime: 30_000,
    refetchInterval: false,
  })

  const scrollRef = useRef<HTMLDivElement>(null)

  const windowStartMs = useMemo(() => data ? new Date(data.window_start).getTime() : Date.now(), [data])
  const windowEndMs   = useMemo(() => data ? new Date(data.window_end).getTime()   : Date.now() + hours * 3600000, [data, hours])
  const totalWidth    = hours * 60 * PX_PER_M

  useEffect(() => {
    if (!data || !scrollRef.current) return
    const nowOffsetPx = ((Date.now() - windowStartMs) / 60000) * PX_PER_M
    const viewW = scrollRef.current.clientWidth - CHAN_W
    scrollRef.current.scrollLeft = Math.max(0, nowOffsetPx - viewW * 0.15)
  }, [data, windowStartMs])

  const timeSlots = useMemo(() => {
    const slots: { label: string; left: number }[] = []
    if (!data) return slots
    let t = new Date(windowStartMs)
    const mins = t.getMinutes()
    const nextSlot = mins === 0 ? 0 : SLOT_MIN - (mins % SLOT_MIN)
    t = new Date(t.getTime() + nextSlot * 60000)
    while (t.getTime() < windowEndMs) {
      slots.push({ label: fmtTime(t), left: ((t.getTime() - windowStartMs) / 60000) * PX_PER_M })
      t = new Date(t.getTime() + SLOT_MIN * 60000)
    }
    return slots
  }, [data, windowStartMs, windowEndMs])

  const nowLeft     = ((Date.now() - windowStartMs) / 60000) * PX_PER_M
  const allChannels = useMemo(() => data?.channels ?? [], [data])
  const groups      = useMemo(() => Array.from(new Set(allChannels.map(ch => ch.channel_group).filter(Boolean))).sort(), [allChannels])
  const channels    = useMemo(() => allChannels.filter(ch =>
    (!nameFilter  || ch.channel_name.toLowerCase().includes(nameFilter.toLowerCase())) &&
    (!groupFilter || ch.channel_group === groupFilter)
  ), [allChannels, nameFilter, groupFilter])

  const handleSelect = useCallback((program: Program, channelName: string, e: React.MouseEvent) => {
    setSelected({ program, channelName, offsetMin, clientX: e.clientX, clientY: e.clientY })
  }, [offsetMin])

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-3 h-full">

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <input
          className="h-8 px-2.5 text-xs rounded border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring w-40"
          placeholder="Filter channels…"
          value={nameFilter}
          onChange={e => setNameFilter(e.target.value)}
        />

        <select
          className="h-8 px-2 text-xs rounded border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring max-w-[180px]"
          value={groupFilter}
          onChange={e => setGroupFilter(e.target.value)}
        >
          <option value="">All Groups</option>
          {groups.map(g => <option key={g} value={g}>{g}</option>)}
        </select>

        <select
          className="h-8 px-2 text-xs rounded border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring max-w-[180px]"
          value={profileId ?? ''}
          onChange={e => setProfileId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All Profiles</option>
          {(profiles ?? []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>

        <div className="flex items-center gap-1">
          <button
            className="h-7 px-1.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors flex items-center"
            onClick={() => { if (scrollRef.current) scrollRef.current.scrollLeft -= 2 * 60 * PX_PER_M }}
            title="Back 2 hours"
          >
            <ChevronLeft size={12} /><ChevronLeft size={12} />
          </button>
          <button
            className="h-7 px-1.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors flex items-center"
            onClick={() => { if (scrollRef.current) scrollRef.current.scrollLeft += 2 * 60 * PX_PER_M }}
            title="Forward 2 hours"
          >
            <ChevronRight size={12} /><ChevronRight size={12} />
          </button>
        </div>

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
          <span className="text-xs text-muted-foreground ml-2">
            Channels ({channels.length})
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline" className="h-8 text-xs gap-1.5" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
            Reload
          </Button>
        </div>
      </div>

      {/* Grid */}
      {isLoading || isFetching ? (
        <div className="flex items-center justify-center py-20 text-muted-foreground gap-2">
          <Loader2 size={16} className="animate-spin" /> Loading guide…
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
          <div style={{ minWidth: CHAN_W + totalWidth }}>

            {/* Header row */}
            <div className="flex sticky top-0 z-20 bg-card border-b border-border" style={{ height: HDR_H }}>
              <div className="shrink-0 border-r border-border bg-card z-30" style={{ width: CHAN_W, height: HDR_H }} />
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
                {nowLeft >= 0 && nowLeft <= totalWidth && (
                  <div className="absolute top-0 bottom-0 w-px bg-red-500/70" style={{ left: nowLeft }} />
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
                    className="sticky left-0 z-10 shrink-0 bg-card border-r border-border flex flex-col items-center justify-center gap-1 px-2 py-1"
                    style={{ width: CHAN_W, height: ROW_H }}
                  >
                    {/* Logo row */}
                    <div className="flex items-center justify-between w-full gap-1">
                      <div className="flex items-center justify-center w-10 h-8 rounded bg-white p-0.5 shrink-0">
                        {ch.logo_url ? (
                          <img
                            src={ch.logo_url}
                            alt=""
                            className="max-w-full max-h-full object-contain"
                            onError={e => { (e.currentTarget as HTMLImageElement).parentElement!.innerHTML = '' }}
                          />
                        ) : (
                          <Tv2 size={16} className="text-muted-foreground/40" />
                        )}
                      </div>
                      {ch.has_stream && (
                        <button
                          className="shrink-0 p-1 rounded hover:bg-primary/20 text-muted-foreground hover:text-primary transition-colors"
                          title={`Play ${ch.channel_name}`}
                          onClick={() => {
                            const now = Date.now()
                            const np = programs.find(p =>
                              new Date(p.start).getTime() <= now && now < new Date(p.stop).getTime()
                            )
                            onPlay(ch.channel_id, ch.channel_name, np ? { title: np.title, start: np.start, stop: np.stop } : undefined)
                          }}
                        >
                          <Play size={12} fill="currentColor" />
                        </button>
                      )}
                    </div>
                    {/* Name row */}
                    <div className="flex flex-col min-w-0 w-full">
                      {ch.channel_number != null && (
                        <span className="text-[9px] text-muted-foreground leading-none">{ch.channel_number}</span>
                      )}
                      <span className="text-[11px] font-medium truncate leading-tight">{ch.channel_name}</span>
                      {!ch.has_epg && (
                        <span className="text-[9px] text-muted-foreground/60 leading-none">No EPG</span>
                      )}
                    </div>
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
                        onSelect={(e) => handleSelect(p, ch.channel_name, e)}
                      />
                    ))}
                    {programs.length === 0 && ch.has_epg && (
                      <div className="absolute inset-0 flex items-center px-2">
                        <span className="text-[10px] text-muted-foreground/50 italic">No data in window</span>
                      </div>
                    )}
                    {nowLeft >= 0 && nowLeft <= totalWidth && (
                      <div
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
          <Tv2 size={10} /> Click any program for details
        </span>
      </div>

      {/* Program detail overlay */}
      {selected && (
        <ProgramDetail
          selected={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}
