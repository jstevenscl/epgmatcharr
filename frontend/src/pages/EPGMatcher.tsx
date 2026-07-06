import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Hls from 'hls.js'
import mpegts from 'mpegts.js'
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Clock,
  ExternalLink,
  Loader2,
  LogOut,
  Moon,
  Palette,
  Pencil,
  Play,
  RefreshCw,
  ScrollText,
  Search,
  Settings,
  Sun,
  Trash2,
  Tv2,
  X,
  XCircle,
} from 'lucide-react'
import type { Theme } from '@/App'
import { THEMES } from '@/App'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import api from '@/lib/api'
import EPGGuide from '@/pages/EPGGuide'
import GNMatcher from '@/pages/GNMatcher'

type Tab = 'matcher' | 'guide' | 'gn'

// ─── Types ────────────────────────────────────────────────────────────────────

interface EpgSource   { id: number; name: string; url?: string }
interface ChannelGroup { id: number; name: string }

interface ChannelRow {
  channel_id:          number
  channel_name:        string
  channel_number:      number | null
  channel_group_id:    number | null
  channel_uuid:        string | null
  has_epg:             boolean
  epg_data_id:         number | null
  tvg_id:              string | null
  tvc_guide_stationid: string | null
  stream_count:        number | null
}

interface EpgCandidate {
  epg_data_id:   number
  name:          string
  tvg_id:        string | null
  icon_url:      string | null
  score:         number
  tier:          string
  epg_source_id: number | null
}

interface ChannelMatch {
  channel_id: number
  confidence: 'high' | 'medium' | 'low' | 'none'
  candidates: EpgCandidate[]
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const THEME_META: Record<Theme, { label: string; icon: React.ReactNode }> = {
  dark:  { label: 'Dark',  icon: <Moon  size={11} /> },
  mid:   { label: 'Mid',   icon: <Palette size={11} /> },
  light: { label: 'Light', icon: <Sun   size={11} /> },
  mono:  { label: 'Mono',  icon: <span className="text-[10px] font-bold leading-none">M</span> },
}

const TIER_LABEL: Record<string, string> = {
  tvg_id_exact:  'tvg_id',
  gn_exact:      'GN exact',
  gn_id:         'GN fwd',
  gn_rev:        'GN rev',
  gn_db_bridge:  'GN bridge',
  callsign:      'Callsign',
  name_fuzzy:    'Fuzzy',
}

interface AssignedEpgSource { id: number; name: string; epg_data_ids: number[] }

// ─── HLS Video Player modal ───────────────────────────────────────────────────

function EpgWarmIndicator({ onRefresh }: { onRefresh?: () => void }) {
  const [open,       setOpen]       = React.useState(false)
  const [refreshing, setRefreshing] = React.useState(false)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['epg-warm-status'],
    queryFn:  () => api.get('/epg-warm-status/').then((r) => r.data),
    refetchInterval: (q) => {
      const d = q.state.data
      if (!d || d.idle || (!d.all_ready && d.warming > 0)) return 4000
      return false
    },
    staleTime: 0,
  })

  async function handleRefresh(e: React.MouseEvent) {
    e.stopPropagation()
    setRefreshing(true)
    try {
      await api.post('/epg/refresh/')
      queryClient.invalidateQueries({ queryKey: ['epg-warm-status'] })
      onRefresh?.()
    } finally {
      setRefreshing(false)
    }
  }

  const isActive = refreshing || (!data?.idle && (data?.warming ?? 0) > 0)

  if (isLoading || !data || data.idle) {
    return (
      <button
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors px-1.5 py-0.5 rounded hover:bg-accent"
        title="Refresh EPG sources"
        onClick={handleRefresh}
        disabled={isActive}
      >
        <RefreshCw size={11} className={isActive ? 'animate-spin' : ''} />
        {isActive ? 'Warming…' : 'Refresh EPG'}
      </button>
    )
  }

  const sources: { id: number; name: string; status: string }[] = data.sources ?? []

  const statusIcon = (s: string) => {
    if (s === 'ready')   return <CheckCircle2 size={11} className="text-green-400 shrink-0" />
    if (s === 'warming') return <Loader2      size={11} className="text-yellow-300 animate-spin shrink-0" />
    return <XCircle size={11} className="text-red-400 shrink-0" />
  }

  const pill = data.all_ready ? (
    <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 border border-green-500/25 cursor-pointer select-none">
      <CheckCircle2 size={11} /> EPG ready
    </span>
  ) : (
    <span className="flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full bg-yellow-400/15 text-yellow-300 border border-yellow-400/25 cursor-pointer select-none">
      <Loader2 size={11} className="animate-spin" />
      Warming EPG
      {data.total > 0 && (
        <span className="opacity-70 font-normal">
          {data.ready}/{data.total}
          {data.errors > 0 && ` · ${data.errors} failed`}
        </span>
      )}
    </span>
  )

  return (
    <div className="flex items-center gap-1.5">
      <div className="relative" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
        {pill}
        {open && sources.length > 0 && (
          <div className="absolute left-0 top-full mt-1.5 z-50 min-w-[220px] rounded-md border border-border bg-neutral-900 shadow-xl p-2 space-y-1">
            <p className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide px-1 pb-1 border-b border-border">
              EPG Sources
            </p>
            {sources.map((s) => (
              <div key={s.id} className="flex items-center gap-2 px-1 py-0.5 text-xs text-popover-foreground">
                {statusIcon(s.status)}
                <span className="truncate">{s.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <button
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent"
        title="Force re-warm all EPG sources"
        onClick={handleRefresh}
        disabled={isActive}
      >
        <RefreshCw size={11} className={isActive ? 'animate-spin' : ''} />
      </button>
    </div>
  )
}

function LogViewer({ onClose }: { onClose: () => void }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['logs'],
    queryFn:  () => api.get('/logs/?limit=200').then((r) => r.data),
    staleTime: 0,
    refetchInterval: 5000,
  })
  const entries: { time: string; level: string; name: string; message: string }[] = data?.entries ?? []
  const levelColor = (l: string) => {
    if (l === 'ERROR' || l === 'CRITICAL') return 'text-red-400'
    if (l === 'WARNING') return 'text-yellow-400'
    if (l === 'INFO')    return 'text-green-400'
    return 'text-muted-foreground'
  }

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-end justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-full max-w-4xl mx-4 mb-4 bg-card border border-border rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        style={{ maxHeight: '60vh' }}
      >
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <div className="flex items-center gap-2">
            <ScrollText size={13} className="text-primary" />
            <span className="text-sm font-medium">Application Logs</span>
            <span className="text-xs text-muted-foreground">({entries.length} entries)</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-0.5 rounded hover:bg-accent"
              onClick={() => refetch()}
            >
              Refresh
            </button>
            <button className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </div>
        <div className="overflow-y-auto font-mono text-xs p-3 space-y-0.5 bg-black/40" style={{ maxHeight: 'calc(60vh - 48px)' }}>
          {isLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground py-4 justify-center">
              <Loader2 size={12} className="animate-spin" /> Loading…
            </div>
          ) : entries.length === 0 ? (
            <p className="text-muted-foreground text-center py-4">No log entries yet</p>
          ) : [...entries].reverse().map((e, i) => (
            <div key={i} className="flex items-start gap-2 py-0.5">
              <span className="text-muted-foreground shrink-0 w-16">{e.time}</span>
              <span className={`shrink-0 w-14 font-semibold ${levelColor(e.level)}`}>{e.level}</span>
              <span className="text-muted-foreground shrink-0 max-w-[140px] truncate">{e.name}</span>
              <span className="text-foreground/80 break-all">{e.message}</span>
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body
  )
}


function VideoPlayer({ url, title, nowPlaying, onClose }: { url: string; title: string; nowPlaying?: { title: string; start: string; stop: string }; onClose: () => void }) {
  const videoRef  = useRef<HTMLVideoElement>(null)
  const hlsRef    = useRef<Hls | null>(null)
  const mpegtsRef = useRef<mpegts.Player | null>(null)
  const [status, setStatus] = useState<'checking' | 'playing' | 'error'>('checking')
  const [error,  setError]  = useState<string | null>(null)

  function destroyPlayers() {
    if (hlsRef.current)    { hlsRef.current.destroy();    hlsRef.current    = null }
    if (mpegtsRef.current) { mpegtsRef.current.destroy(); mpegtsRef.current = null }
    const v = videoRef.current
    if (v) { v.pause(); v.removeAttribute('src'); v.load() }
  }

  useEffect(() => {
    destroyPlayers()
    setStatus('checking')
    setError(null)

    const sessionToken = localStorage.getItem('epgmatcharr-session')
    const fetchHeaders: Record<string, string> = {}
    if (sessionToken) fetchHeaders['X-Session-Token'] = sessionToken

    fetch(url, { headers: fetchHeaders })
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => null)
          setError(body?.detail ?? `HTTP ${r.status}`)
          setStatus('error')
          return
        }

        const streamType = r.headers.get('X-Stream-Type')
        const ct   = r.headers.get('content-type') ?? ''
        const text = await r.text()
        const video = videoRef.current
        if (!video) return

        if (streamType === 'ts' || (!ct.includes('mpegurl') && !text.trim().startsWith('#EXTM3U'))) {
          // FFmpeg produces fMP4 — feed it to the browser via MSE using mpegts.js type:'mp4'
          const tsUrl = url.replace('/api/stream/', '/api/stream-ts/')
          if (!mpegts.isSupported()) {
            setError('Live stream playback is not supported in this browser.')
            setStatus('error')
            return
          }
          const player = mpegts.createPlayer(
            { type: 'mpegts', url: tsUrl, isLive: true },
            { enableWorker: false, liveBufferLatencyChasing: false,
              autoCleanupSourceBuffer: true },
          )
          mpegtsRef.current = player
          player.attachMediaElement(video)
          player.load()
          player.on(mpegts.Events.ERROR, (type: unknown, detail: unknown) => {
            setError(`${String(type)}: ${String(detail)}`)
            setStatus('error')
          })
          setStatus('playing')
          video.addEventListener('canplay', () => video.play().catch(() => {}), { once: true })
        } else {
          // HLS m3u8
          const blob    = new Blob([text], { type: 'application/vnd.apple.mpegurl' })
          const blobUrl = URL.createObjectURL(blob)
          setStatus('playing')
          if (Hls.isSupported()) {
            const hls = new Hls({ enableWorker: false })
            hlsRef.current = hls
            hls.loadSource(blobUrl)
            hls.attachMedia(video)
            hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); URL.revokeObjectURL(blobUrl) })
            hls.on(Hls.Events.ERROR, (_e, data) => {
              if (data.fatal) { setError(data.details); setStatus('error') }
            })
          } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = url
            video.play().catch(() => {})
          } else {
            setError('HLS playback is not supported in this browser.')
            setStatus('error')
          }
        }
      })
      .catch((e) => { setError(String(e)); setStatus('error') })

    return () => destroyPlayers()
  }, [url])

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/80"
      onClick={onClose}
    >
      <div
        className="relative bg-card border border-border rounded-xl overflow-hidden w-full max-w-3xl mx-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="flex items-center gap-2">
              <Play size={13} className="text-primary shrink-0" />
              <span className="text-sm font-medium truncate max-w-xs">{title}</span>
            </div>
            {nowPlaying && (
              <span className="text-[11px] text-muted-foreground ml-5 truncate">
                Now: {nowPlaying.title}
                {' · '}
                {new Date(nowPlaying.start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                {' – '}
                {new Date(nowPlaying.stop).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
          </div>
          <button
            className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent shrink-0 ml-2"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        {status === 'checking' && (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 size={14} className="animate-spin" /> Checking stream…
          </div>
        )}

        {status === 'error' && error && (
          <div className="px-6 py-10 space-y-3 text-center">
            <div className="flex items-center justify-center gap-2 text-sm text-destructive">
              <AlertCircle size={14} className="shrink-0" />
              <span>{error}</span>
            </div>
          </div>
        )}

        <video
          ref={videoRef}
          controls
          className={`w-full aspect-video bg-black ${status !== 'playing' ? 'hidden' : ''}`}
        />
      </div>
    </div>,
    document.body
  )
}

// ─── Candidate picker dropdown ────────────────────────────────────────────────

function CandidatePicker({
  candidates,
  selected,
  sourceIds,
  onSelect,
}: {
  candidates: EpgCandidate[]
  selected:   EpgCandidate | null
  sourceIds:  number[]
  onSelect:   (c: EpgCandidate) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [searchResults, setSearchResults] = useState<EpgCandidate[]>([])
  const [searching, setSearching] = useState(false)
  const [dropPos, setDropPos] = useState<{ top: number; left: number; width: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const t = useRef<ReturnType<typeof setTimeout> | null>(null)

  const displayed = search ? searchResults : candidates
  const current   = selected ?? candidates[0] ?? null

  function openDropdown() {
    if (triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect()
      setDropPos({ top: r.bottom + 4, left: r.left, width: 320 })
    }
    setOpen(true)
  }

  function closeDropdown() { setOpen(false); setSearch('') }

  function handleSearch(q: string) {
    setSearch(q)
    if (t.current) clearTimeout(t.current)
    if (!q.trim()) { setSearchResults([]); return }
    setSearching(true)
    t.current = setTimeout(async () => {
      try {
        const { data } = await api.get('/search/', { params: { source_ids: sourceIds.join(','), q, limit: 20 } })
        setSearchResults(data)
      } finally { setSearching(false) }
    }, 300)
  }

  if (!current) return <span className="text-xs text-muted-foreground">—</span>

  return (
    <div className="w-full min-w-0">
      <button
        ref={triggerRef}
        className="flex items-center gap-1 text-sm hover:opacity-80 w-full min-w-0 text-left"
        title={`${current.name}${current.tvg_id ? ` — ${current.tvg_id}` : ''}`}
        onClick={() => open ? closeDropdown() : openDropdown()}
      >
        {current.icon_url && (
          <img src={current.icon_url} className="w-4 h-4 rounded object-contain shrink-0" alt="" />
        )}
        <div className="flex flex-col min-w-0 flex-1">
          <span className="truncate text-sm leading-tight">{current.name}</span>
          {current.tvg_id && (
            <span className="truncate text-xs text-muted-foreground leading-tight">{current.tvg_id}</span>
          )}
        </div>
        <ChevronDown size={11} className="shrink-0 text-muted-foreground ml-1" />
      </button>

      {open && dropPos && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={closeDropdown} />
          <div
            className="fixed z-50 border border-border rounded-lg shadow-2xl"
            style={{ top: dropPos.top, left: dropPos.left, width: dropPos.width, backgroundColor: 'hsl(var(--card))' }}
          >
            <div className="p-2 border-b border-border">
              <div className="relative">
                <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  autoFocus
                  className="w-full bg-background text-sm pl-6 pr-2 py-1.5 rounded border border-border outline-none focus:border-primary"
                  placeholder="Search EPG entries…"
                  value={search}
                  onChange={(e) => handleSearch(e.target.value)}
                />
                {searching && <Loader2 size={11} className="absolute right-2 top-1/2 -translate-y-1/2 animate-spin text-muted-foreground" />}
              </div>
            </div>
            <div className="max-h-60 overflow-y-auto py-1">
              {displayed.length === 0 ? (
                <p className="text-xs text-muted-foreground px-3 py-2">
                  {search ? 'No results' : 'No candidates'}
                </p>
              ) : displayed.map((c) => (
                <button
                  key={c.epg_data_id}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-accent transition-colors ${current?.epg_data_id === c.epg_data_id ? 'bg-accent' : ''}`}
                  onClick={() => { onSelect(c); closeDropdown() }}
                >
                  {c.icon_url && <img src={c.icon_url} className="w-5 h-5 rounded object-contain shrink-0" alt="" />}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{c.name}</p>
                    {c.tvg_id && <p className="text-xs text-muted-foreground truncate">{c.tvg_id}</p>}
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-xs text-muted-foreground">{Math.round(c.score * 100)}%</p>
                    <p className="text-xs text-muted-foreground">{TIER_LABEL[c.tier] ?? c.tier}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        </>,
        document.body
      )}
    </div>
  )
}

// ─── Manual search for no-match rows ─────────────────────────────────────────

function ManualSearch({
  sourceIds,
  onSelect,
}: {
  sourceIds: number[]
  onSelect:  (c: EpgCandidate) => void
}) {
  const [q, setQ]           = useState('')
  const [results, setResults] = useState<EpgCandidate[]>([])
  const [loading, setLoading] = useState(false)
  const [dropPos, setDropPos] = useState<{ top: number; left: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const t = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleSearch(val: string) {
    setQ(val)
    if (t.current) clearTimeout(t.current)
    if (!val.trim()) { setResults([]); return }
    setLoading(true)
    if (inputRef.current) {
      const r = inputRef.current.getBoundingClientRect()
      setDropPos({ top: r.bottom + 4, left: r.left })
    }
    t.current = setTimeout(async () => {
      try {
        const { data } = await api.get('/search/', { params: { source_ids: sourceIds.join(','), q: val, limit: 20 } })
        setResults(data)
      } finally { setLoading(false) }
    }, 300)
  }

  return (
    <div className="relative w-56">
      <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
      <input
        ref={inputRef}
        className="w-full bg-background text-xs pl-6 pr-2 py-1 rounded border border-border outline-none focus:border-primary"
        placeholder="Search EPG…"
        value={q}
        onChange={(e) => handleSearch(e.target.value)}
      />
      {loading && <Loader2 size={10} className="absolute right-2 top-1/2 -translate-y-1/2 animate-spin text-muted-foreground" />}
      {results.length > 0 && dropPos && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={() => { setQ(''); setResults([]) }} />
          <div
            className="fixed z-50 border border-border rounded shadow-2xl max-h-48 overflow-y-auto"
            style={{ top: dropPos.top, left: dropPos.left, width: 288, backgroundColor: 'hsl(var(--card))' }}
          >
            {results.map((r) => (
              <button
                key={r.epg_data_id}
                className="w-full flex items-center gap-2 px-2 py-1.5 text-left hover:bg-accent transition-colors"
                onClick={() => { onSelect(r); setQ(''); setResults([]) }}
              >
                {r.icon_url && <img src={r.icon_url} className="w-4 h-4 rounded shrink-0" alt="" />}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate">{r.name}</p>
                  {r.tvg_id && <p className="text-xs text-muted-foreground truncate">{r.tvg_id}</p>}
                </div>
              </button>
            ))}
          </div>
        </>,
        document.body
      )}
    </div>
  )
}

// ─── Now Playing (lazy) ───────────────────────────────────────────────────────

function NowPlayingInline({ sourceIds, epgDataId, tvgId }: { sourceIds: number[]; epgDataId: number; tvgId?: string | null }) {
  const { data, isFetching } = useQuery({
    queryKey: ['now-playing', epgDataId, sourceIds],
    queryFn:  () =>
      api.post('/now-playing/', { epg_data_id: epgDataId, tvg_id: tvgId ?? null, source_ids: sourceIds })
        .then((r) => r.data),
    staleTime: 120_000,
    retry: false,
  })
  if (isFetching) return <Loader2 size={10} className="animate-spin text-muted-foreground" />
  if (!data) return <span className="text-xs text-muted-foreground italic">No program data</span>
  return (
    <span className="text-xs text-foreground/70 flex items-center gap-1 mt-0.5" title={data.description || undefined}>
      <Clock size={10} className="shrink-0" />
      {data.upcoming && <span className="opacity-75">Up next:</span>}
      <span className="truncate">{data.title}</span>
      {data.start && (
        <span className="shrink-0 opacity-75">
          {new Date(data.start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      )}
    </span>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function EPGMatcher({
  onOpenSettings,
  onLogout,
  theme,
  onSetTheme,
}: {
  onOpenSettings?: () => void
  onLogout?:       () => void
  theme: Theme
  onSetTheme: (t: Theme) => void
}) {
  const [tab, setTab]                         = useState<Tab>('matcher')
  const [showLogs, setShowLogs]               = useState(false)

  const [selectedSources, setSelectedSources] = useState<number[]>([])
  const [tvgIdFilter, setTvgIdFilter]         = useState('')

  const [filterGroupIds, setFilterGroupIds]   = useState<number[]>([])
  const [filterUnassigned, setFilterUnassigned] = useState(false)
  const [nameSearch, setNameSearch]           = useState('')

  const [groupDropOpen, setGroupDropOpen]     = useState(false)
  const [groupDropPos, setGroupDropPos]       = useState<{ top: number; left: number; width: number } | null>(null)
  const [groupSearch, setGroupSearch]         = useState('')
  const groupDropRef = useRef<HTMLButtonElement>(null)

  const [pendingNames, setPendingNames]       = useState<Record<number, string>>({})
  const [editingNameFor, setEditingNameFor]   = useState<number | null>(null)
  const [editValue, setEditValue]             = useState('')

  const [channels, setChannels]               = useState<ChannelRow[] | null>(null)
  const [loadingChannels, setLoadingChannels] = useState(false)

  const [matchResults, setMatchResults]       = useState<Record<number, ChannelMatch>>({})
  const [matching, setMatching]               = useState(false)
  const [matchError, setMatchError]           = useState<string | null>(null)
  const [matchRan, setMatchRan]               = useState(false)
  const [xmltvCacheReady, setXmltvCacheReady] = useState(false)

  const [checked, setChecked]                 = useState<Set<number>>(new Set())
  const [overrides, setOverrides]             = useState<Record<number, EpgCandidate>>({})
  const [nowPlayingFor, setNowPlayingFor]     = useState<Record<number, boolean>>({})

  const [committing, setCommitting]           = useState(false)
  const [commitMsg, setCommitMsg]             = useState<string | null>(null)
  const [commitError, setCommitError]         = useState<string | null>(null)

  const [previewUrl, setPreviewUrl]           = useState<string | null>(null)
  const [previewTitle, setPreviewTitle]       = useState('')
  const [previewNowPlaying, setPreviewNowPlaying] = useState<{ title: string; start: string; stop: string } | undefined>(undefined)

  const [deleteTarget, setDeleteTarget] = useState<{ id: number; name: string } | null>(null)
  const [deleting,      setDeleting]    = useState(false)

  const [filterEpgSourceId, setFilterEpgSourceId]   = useState<number | null>(null)
  const [epgSourceDropOpen, setEpgSourceDropOpen]   = useState(false)
  const [epgSourceDropPos,  setEpgSourceDropPos]    = useState<{ top: number; left: number; width: number } | null>(null)
  const [epgSourceSearch,   setEpgSourceSearch]     = useState('')
  const epgSourceDropRef = useRef<HTMLButtonElement>(null)

  // ── Data queries ───────────────────────────────────────────────────────────

  const { data: sources } = useQuery<EpgSource[]>({
    queryKey: ['epg-sources'],
    queryFn:  () => api.get('/sources/').then((r) => r.data),
    staleTime: 60_000,
  })

  const { data: groups } = useQuery<ChannelGroup[]>({
    queryKey: ['channel-groups'],
    queryFn:  () => api.get('/groups/').then((r) => r.data),
    staleTime: 60_000,
  })

  const { data: config } = useQuery<{ dispatcharr_url: string }>({
    queryKey: ['config'],
    queryFn:  () => api.get('/config/').then((r) => r.data),
    staleTime: Infinity,
  })

  const { data: versionData } = useQuery<{ version: string }>({
    queryKey: ['version'],
    queryFn:  () => api.get('/version/').then((r) => r.data),
    staleTime: Infinity,
  })

  const { data: settingsData } = useQuery<{ guide_window_hours: number; enable_epg_guide: boolean }>({
    queryKey: ['settings'],
    queryFn:  () => api.get('/settings/').then((r) => r.data),
    staleTime: 60_000,
  })

  const { data: assignedEpgSources } = useQuery<AssignedEpgSource[]>({
    queryKey: ['assigned-epg-sources'],
    queryFn:  () => api.get('/assigned-epg-sources/').then((r) => r.data),
    staleTime: 300_000,
  })

  const groupMap = Object.fromEntries((groups ?? []).map((g) => [g.id, g.name]))

  const epgDataToSourceName = useMemo(() => {
    const map: Record<number, string> = {}
    for (const src of (assignedEpgSources ?? [])) {
      for (const id of src.epg_data_ids) {
        map[id] = src.name
      }
    }
    return map
  }, [assignedEpgSources])

  const filterEpgDataIds = useMemo(() => {
    if (filterEpgSourceId === null) return null
    const src = (assignedEpgSources ?? []).find((s) => s.id === filterEpgSourceId)
    return src ? new Set(src.epg_data_ids) : null
  }, [filterEpgSourceId, assignedEpgSources])

  // ── Handlers ──────────────────────────────────────────────────────────────

  async function loadChannels() {
    setLoadingChannels(true)
    setMatchResults({})
    setMatchRan(false)
    setChecked(new Set())
    setOverrides({})
    setCommitMsg(null)
    setCommitError(null)
    try {
      const params: Record<string, unknown> = {}
      if (filterGroupIds.length > 0) params.group_ids = filterGroupIds.join(',')
      if (filterUnassigned) params.unassigned_only = true
      const { data } = await api.get('/channels/', { params })
      setChannels(data.results)
    } finally {
      setLoadingChannels(false)
    }
  }

  async function runMatch() {
    if (selectedSources.length === 0 || checked.size === 0) return
    setMatching(true)
    setMatchError(null)
    try {
      const { data } = await api.post('/match/', {
        source_ids:      selectedSources,
        channel_ids:     [...checked],
        unassigned_only: false,
        tvg_id_filter:   tvgIdFilter.trim() || null,
      })
      const byId: Record<number, ChannelMatch> = {}
      for (const r of data.results) byId[r.channel_id] = r
      setMatchResults(byId)
      setMatchRan(true)
      setXmltvCacheReady(false)
      setChecked((prev) => {
        const next = new Set(prev)
        for (const r of data.results) {
          if (r.confidence !== 'high') next.delete(r.channel_id)
        }
        return next
      })
      if (selectedSources.length > 0) {
        const poll = async () => {
          for (let i = 0; i < 30; i++) {
            await new Promise((r) => setTimeout(r, 2000))
            try {
              const { data: status } = await api.get('/cache-status/', {
                params: { source_ids: selectedSources.join(',') },
              })
              if (Object.values(status).every((v) => v === 'ready')) { setXmltvCacheReady(true); return }
            } catch { break }
          }
          setXmltvCacheReady(true)
        }
        poll()
      }
    } catch (e: unknown) {
      setMatchError(e instanceof Error ? e.message : 'Match failed')
    } finally {
      setMatching(false)
    }
  }

  const handleCheck = useCallback((id: number, val: boolean) => {
    setChecked((prev) => { const next = new Set(prev); val ? next.add(id) : next.delete(id); return next })
  }, [])

  function selectAll() {
    if (!displayedChannels) return
    setChecked(new Set(displayedChannels.map((c) => c.channel_id)))
  }

  function selectAllHigh() {
    setChecked((prev) => {
      const next = new Set(prev)
      for (const [id, r] of Object.entries(matchResults)) {
        if (r.confidence === 'high') next.add(Number(id))
      }
      return next
    })
  }

  function deselectAll() { setChecked(new Set()) }

  function startEdit(channelId: number, currentName: string) {
    setEditingNameFor(channelId)
    setEditValue(pendingNames[channelId] ?? currentName)
  }

  function commitEdit(channelId: number, originalName: string) {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== originalName) {
      setPendingNames((prev) => ({ ...prev, [channelId]: trimmed }))
    } else {
      setPendingNames((prev) => { const next = { ...prev }; delete next[channelId]; return next })
    }
    setEditingNameFor(null)
  }

  function cancelEdit() { setEditingNameFor(null) }

  function handleStreamPreview(ch: ChannelRow) {
    setPreviewUrl(`/api/stream/${ch.channel_id}`)
    setPreviewTitle(ch.channel_name)
    setPreviewNowPlaying(undefined)
  }

  async function handleDeleteChannel() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await api.delete(`/channels/${deleteTarget.id}/`)
      setChannels((prev) => prev ? prev.filter((c) => c.channel_id !== deleteTarget.id) : prev)
    } finally {
      setDeleting(false)
      setDeleteTarget(null)
    }
  }

  async function handleCommit() {
    if (checked.size === 0 && Object.keys(pendingNames).length === 0) return

    const associations = [...checked].flatMap((channelId) => {
      const result    = matchResults[channelId]
      if (!result) return []
      const candidate = overrides[channelId] ?? result.candidates[0]
      if (!candidate?.epg_data_id) return []
      return [{ channel_id: channelId, epg_data_id: candidate.epg_data_id }]
    })
    const nameChanges = Object.entries(pendingNames)
      .filter(([id, name]) => {
        const ch = channels?.find((c) => c.channel_id === Number(id))
        return ch && name !== ch.channel_name
      })
      .map(([id, name]) => ({ channel_id: Number(id), name }))

    if (associations.length === 0 && nameChanges.length === 0) return

    setCommitting(true)
    setCommitMsg(null)
    setCommitError(null)
    try {
      await api.post('/commit/', { associations, name_changes: nameChanges })

      const committed  = new Set(associations.map((a) => a.channel_id))
      const renamedIds = new Set(nameChanges.map((n) => n.channel_id))

      setCommitMsg(
        [
          associations.length > 0 ? `${associations.length} channel${associations.length !== 1 ? 's' : ''} assigned` : null,
          nameChanges.length > 0  ? `${nameChanges.length} renamed` : null,
        ].filter(Boolean).join(', ') + ' successfully.'
      )
      setChannels((prev) =>
        prev ? prev.map((c) => {
          let updated = c
          if (committed.has(c.channel_id))  updated = { ...updated, has_epg: true }
          if (renamedIds.has(c.channel_id)) {
            const newName = nameChanges.find((n) => n.channel_id === c.channel_id)?.name
            if (newName) updated = { ...updated, channel_name: newName }
          }
          return updated
        }) : prev
      )
      setMatchResults((prev) => {
        const next = { ...prev }
        committed.forEach((id) => delete next[id])
        return next
      })
      setChecked(new Set())
      setPendingNames((prev) => {
        const next = { ...prev }
        committed.forEach((id) => delete next[id])
        renamedIds.forEach((id) => delete next[id])
        return next
      })
    } catch (err: unknown) {
      setCommitError(`Commit failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setCommitting(false)
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────

  const groupDropLabel =
    filterGroupIds.length === 0 ? 'All groups'
    : filterGroupIds.length === 1 ? (groupMap[filterGroupIds[0]] ?? `Group ${filterGroupIds[0]}`)
    : `${filterGroupIds.length} groups`

  const epgSourceDropLabel =
    filterEpgSourceId === null
      ? 'Filter by EPG source'
      : ((assignedEpgSources ?? []).find((s) => s.id === filterEpgSourceId)?.name ?? `Source #${filterEpgSourceId}`)

  const displayedChannels = channels?.filter((c) => {
    if (nameSearch && !c.channel_name.toLowerCase().includes(nameSearch.toLowerCase())) return false
    if (filterEpgDataIds !== null && !filterEpgDataIds.has(c.epg_data_id ?? -1)) return false
    return true
  }) ?? null

  const checkedCount        = checked.size
  const matchedHighCount    = Object.values(matchResults).filter((r) => r.confidence === 'high').length
  const commitCount         = [...checked].filter((id) => {
    const r = matchResults[id]
    const c = overrides[id] ?? r?.candidates[0]
    return !!c?.epg_data_id
  }).length

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-4">
      {previewUrl && (
        <VideoPlayer url={previewUrl} title={previewTitle} nowPlaying={previewNowPlaying} onClose={() => { setPreviewUrl(null); setPreviewNowPlaying(undefined) }} />
      )}

      {showLogs && <LogViewer onClose={() => setShowLogs(false)} />}

      {/* Delete channel confirmation modal */}
      {deleteTarget && createPortal(
        <div className="fixed inset-0 z-[300] flex items-center justify-center bg-black/60 p-4" onClick={() => !deleting && setDeleteTarget(null)}>
          <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-sm p-6 space-y-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-start gap-3">
              <div className="p-2 rounded-lg bg-destructive/10 shrink-0">
                <Trash2 size={18} className="text-destructive" />
              </div>
              <div>
                <h2 className="text-base font-semibold">Delete Channel</h2>
                <p className="text-sm text-muted-foreground mt-1">
                  Permanently remove <span className="text-foreground font-medium">{deleteTarget.name}</span> from Dispatcharr? This cannot be undone.
                </p>
              </div>
            </div>
            <div className="flex items-center justify-end gap-3 pt-1">
              <Button variant="outline" size="sm" className="px-5" disabled={deleting} onClick={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button size="sm" className="px-5 bg-destructive text-destructive-foreground hover:bg-destructive/90" disabled={deleting} onClick={handleDeleteChannel}>
                {deleting ? <><Loader2 size={13} className="animate-spin mr-1.5" />Deleting…</> : 'Delete Channel'}
              </Button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Header */}
      <div className="flex items-center gap-2">
        <Tv2 size={20} className="text-primary" />
        <h1 className="text-xl font-semibold">EPGmatcharr</h1>
        {versionData?.version && (
          <span className="text-[11px] text-muted-foreground font-mono leading-none mt-0.5">
            v{versionData.version}
          </span>
        )}
        <EpgWarmIndicator />
        <div className="ml-auto flex items-center gap-3">
          {config?.dispatcharr_url && (
            <a
              href={config.dispatcharr_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
            >
              <ExternalLink size={11} /> Open Dispatcharr
            </a>
          )}

          {/* Theme switcher */}
          <div className="flex items-center gap-0.5 rounded border border-border p-0.5">
            {(THEMES as readonly Theme[]).map((t) => {
              const meta = THEME_META[t]
              return (
                <button
                  key={t}
                  title={meta.label}
                  onClick={() => onSetTheme(t)}
                  className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] transition-colors ${
                    theme === t
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                  }`}
                >
                  {meta.icon}
                  <span>{meta.label}</span>
                </button>
              )
            })}
          </div>

          <button
            className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent"
            title="Application logs"
            onClick={() => setShowLogs(true)}
          >
            <ScrollText size={15} />
          </button>

          {onOpenSettings && (
            <button
              className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent"
              title="Connection settings"
              onClick={onOpenSettings}
            >
              <Settings size={15} />
            </button>
          )}

          {onLogout && (
            <button
              className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent"
              title="Sign out"
              onClick={onLogout}
            >
              <LogOut size={15} />
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-0 border-b border-border">
        {([
          ['matcher', 'Matcher'],
          ...(settingsData?.enable_epg_guide !== false ? [['guide', 'EPG Guide']] : []),
          ['gn', 'GN Matcher'],
        ] as [Tab, string][]).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === id
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'guide' && (
        <EPGGuide
          guideWindowHours={settingsData?.guide_window_hours ?? 2}
          onPlay={(id, name, np) => { setPreviewUrl(`/api/stream/${id}`); setPreviewTitle(name); setPreviewNowPlaying(np) }}
        />
      )}

      {tab === 'gn' && <GNMatcher />}

      {tab === 'matcher' && <>

      {/* ── Setup card ── */}
      <Card>
        <CardContent className="pt-4 space-y-3">
          {/* EPG Sources */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground w-24 shrink-0">EPG Sources</span>
            {!sources ? (
              <Loader2 size={13} className="animate-spin text-muted-foreground" />
            ) : sources.map((s) => (
              <Button
                key={s.id}
                size="sm"
                variant={selectedSources.includes(s.id) ? 'default' : 'outline'}
                className="h-7 text-xs"
                onClick={() =>
                  setSelectedSources((prev) =>
                    prev.includes(s.id) ? prev.filter((x) => x !== s.id) : [...prev, s.id]
                  )
                }
              >
                {s.name}
              </Button>
            ))}
            {sources && sources.length === 0 && (
              <span className="text-xs text-muted-foreground">No EPG sources found in Dispatcharr</span>
            )}
          </div>

          {/* TVG-ID filter */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground w-24 shrink-0">TVG-ID filter</span>
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="h-7 pl-6 text-xs w-48"
                placeholder="e.g. .us or .uk (optional)"
                value={tvgIdFilter}
                onChange={(e) => setTvgIdFilter(e.target.value)}
              />
            </div>
            <span className="text-xs text-muted-foreground">Only match EPG entries whose tvg_id contains this string</span>
          </div>
        </CardContent>
      </Card>

      {/* ── Channel table card ── */}
      <Card>
        <CardContent className="pt-4 space-y-3">
          {/* Filter bar */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Group filter */}
            <button
              ref={groupDropRef}
              className="h-8 text-xs bg-background border border-border rounded px-2 pr-6 outline-none focus:border-primary flex items-center gap-1 min-w-[140px] relative"
              onClick={() => {
                if (groupDropOpen) { setGroupDropOpen(false); return }
                const rect = groupDropRef.current?.getBoundingClientRect()
                if (rect) setGroupDropPos({ top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 220) })
                setGroupDropOpen(true)
              }}
            >
              <span className="truncate flex-1 text-left">{groupDropLabel}</span>
              <ChevronDown size={11} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground shrink-0" />
            </button>

            {/* Unassigned toggle */}
            <label className="flex items-center gap-1.5 text-xs cursor-pointer select-none">
              <input
                type="checkbox"
                checked={filterUnassigned}
                onChange={(e) => setFilterUnassigned(e.target.checked)}
                className="cursor-pointer"
              />
              Unassigned only
            </label>

            {/* Filter by EPG source */}
            {assignedEpgSources && assignedEpgSources.length > 0 && (
              <button
                ref={epgSourceDropRef}
                className={`h-8 text-xs border rounded px-2 pr-6 outline-none flex items-center gap-1 min-w-[160px] max-w-[240px] relative transition-colors ${
                  filterEpgSourceId !== null
                    ? 'bg-primary/10 border-primary text-primary'
                    : 'bg-background border-border'
                }`}
                onClick={() => {
                  if (epgSourceDropOpen) { setEpgSourceDropOpen(false); return }
                  const rect = epgSourceDropRef.current?.getBoundingClientRect()
                  if (rect) setEpgSourceDropPos({ top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 280) })
                  setEpgSourceDropOpen(true)
                }}
              >
                <span className="truncate flex-1 text-left">{epgSourceDropLabel}</span>
                <ChevronDown size={11} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground shrink-0" />
              </button>
            )}

            {/* Name search */}
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="h-8 pl-6 text-xs w-48"
                placeholder="Filter by name…"
                value={nameSearch}
                onChange={(e) => setNameSearch(e.target.value)}
              />
            </div>

            {/* Load button */}
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-xs gap-1.5 ml-auto"
              disabled={loadingChannels}
              onClick={loadChannels}
            >
              {loadingChannels
                ? <><Loader2 size={12} className="animate-spin" /> Loading…</>
                : channels
                ? <><RefreshCw size={12} /> Reload</>
                : 'Load Channels'
              }
            </Button>
          </div>

          {/* Bulk actions */}
          {channels && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground border-b border-border pb-2">
              <span>{displayedChannels?.length ?? 0} channel{displayedChannels?.length !== 1 ? 's' : ''}</span>
              {channels.filter((c) => !c.has_epg).length > 0 && (
                <span>· {channels.filter((c) => !c.has_epg).length} without EPG</span>
              )}
              {Object.keys(pendingNames).length > 0 && (
                <span className="text-yellow-400">· {Object.keys(pendingNames).length} rename{Object.keys(pendingNames).length !== 1 ? 's' : ''} pending</span>
              )}
              <span className="ml-auto flex items-center gap-1.5">
                <button className="hover:text-foreground transition-colors" onClick={selectAll}>Select all</button>
                <span>·</span>
                <button className="hover:text-foreground transition-colors" onClick={() => {
                  if (!displayedChannels) return
                  setChecked(new Set(displayedChannels.filter((c) => !c.has_epg).map((c) => c.channel_id)))
                }}>Select unassigned</button>
                {matchRan && matchedHighCount > 0 && (
                  <>
                    <span>·</span>
                    <button className="hover:text-foreground transition-colors text-green-400" onClick={selectAllHigh}>
                      Select all high
                    </button>
                  </>
                )}
                <span>·</span>
                <button className="hover:text-foreground transition-colors" onClick={deselectAll}>Deselect all</button>
              </span>
            </div>
          )}

          {/* Run match / commit bar */}
          {channels && (
            <div className="flex items-center gap-3">
              {!matchRan ? (
                <>
                  <Button
                    size="sm"
                    disabled={matching || checkedCount === 0 || selectedSources.length === 0}
                    onClick={runMatch}
                    className="gap-2"
                  >
                    {matching
                      ? <><Loader2 size={13} className="animate-spin" /> Matching…</>
                      : `Run Match on ${checkedCount} selected`
                    }
                  </Button>
                  {Object.keys(pendingNames).length > 0 && (
                    <Button size="sm" disabled={committing} onClick={handleCommit} className="gap-2">
                      {committing
                        ? <><Loader2 size={13} className="animate-spin" /> Committing…</>
                        : <><CheckCircle2 size={13} /> Apply {Object.keys(pendingNames).length} rename{Object.keys(pendingNames).length !== 1 ? 's' : ''}</>
                      }
                    </Button>
                  )}
                  {checkedCount === 0 && Object.keys(pendingNames).length === 0 && (
                    <span className="text-xs text-muted-foreground">Select channels then Run Match</span>
                  )}
                  {checkedCount > 0 && selectedSources.length === 0 && (
                    <span className="text-xs text-muted-foreground">Select an EPG source above first</span>
                  )}
                </>
              ) : (
                <>
                  <Button
                    size="sm"
                    disabled={matching || checkedCount === 0 || selectedSources.length === 0}
                    variant="outline"
                    onClick={runMatch}
                    className="gap-2"
                  >
                    {matching
                      ? <><Loader2 size={13} className="animate-spin" /> Matching…</>
                      : `Re-run Match on ${checkedCount} selected`
                    }
                  </Button>
                  <Button
                    size="sm"
                    disabled={committing || (commitCount === 0 && Object.keys(pendingNames).length === 0)}
                    onClick={handleCommit}
                    className="gap-2"
                  >
                    {committing
                      ? <><Loader2 size={13} className="animate-spin" /> Committing…</>
                      : <><CheckCircle2 size={13} /> Commit {[
                          commitCount > 0                        ? `${commitCount} assignment${commitCount !== 1 ? 's' : ''}` : null,
                          Object.keys(pendingNames).length > 0  ? `${Object.keys(pendingNames).length} rename${Object.keys(pendingNames).length !== 1 ? 's' : ''}` : null,
                        ].filter(Boolean).join(' + ')}</>
                    }
                  </Button>
                </>
              )}
              {matchError  && <span className="text-xs text-destructive flex items-center gap-1"><AlertCircle size={12} /> {matchError}</span>}
              {commitMsg   && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 size={12} /> {commitMsg}</span>}
              {commitError && <span className="text-xs text-destructive flex items-center gap-1"><AlertCircle size={12} /> {commitError}</span>}
            </div>
          )}

          {/* ── Channel table ── */}
          {loadingChannels && (
            <div className="flex items-center justify-center py-10 text-muted-foreground gap-2 text-sm">
              <Loader2 size={16} className="animate-spin" /> Loading channels…
            </div>
          )}

          {!loadingChannels && channels && displayedChannels && (
            <div className="rounded-lg border border-border overflow-hidden">
              {/* Header */}
              <div className="grid grid-cols-[32px_56px_minmax(0,1.2fr)_130px_110px_minmax(0,1.8fr)_40px] gap-0 border-b border-border bg-accent/30 text-xs text-muted-foreground font-medium">
                <div className="px-2 py-2 flex items-center">
                  <input
                    type="checkbox"
                    className="cursor-pointer"
                    checked={displayedChannels.length > 0 && displayedChannels.every((c) => checked.has(c.channel_id))}
                    onChange={(e) => e.target.checked ? selectAll() : deselectAll()}
                  />
                </div>
                <div className="px-2 py-2">#</div>
                <div className="px-2 py-2">Channel Name</div>
                <div className="px-2 py-2">Group</div>
                <div className="px-2 py-2">EPG Status</div>
                <div className="px-2 py-2">Match</div>
                <div className="px-2 py-2" />
              </div>

              {/* Rows */}
              <div className="overflow-y-auto" style={{ maxHeight: 'calc(100vh - 420px)', minHeight: '240px' }}>
                {displayedChannels.length === 0 ? (
                  <div className="text-center py-8 text-sm text-muted-foreground">No channels found</div>
                ) : displayedChannels.map((ch) => {
                  const isChecked    = checked.has(ch.channel_id)
                  const match        = matchResults[ch.channel_id]
                  const override     = overrides[ch.channel_id] ?? null
                  const isEditing    = editingNameFor === ch.channel_id
                  const displayName  = pendingNames[ch.channel_id] ?? ch.channel_name
                  const hasNameChange = !!pendingNames[ch.channel_id]

                  return (
                    <div
                      key={ch.channel_id}
                      className={`grid grid-cols-[32px_56px_minmax(0,1.2fr)_130px_110px_minmax(0,1.8fr)_40px] gap-0 border-b border-border last:border-0 hover:bg-accent/20 transition-colors text-sm ${!isChecked ? 'opacity-60' : ''}`}
                    >
                      {/* Checkbox */}
                      <div className="px-2 py-2.5 flex items-start">
                        <input type="checkbox" className="cursor-pointer mt-0.5" checked={isChecked}
                          onChange={(e) => handleCheck(ch.channel_id, e.target.checked)} />
                      </div>

                      {/* Channel # */}
                      <div className="px-2 py-2.5 text-muted-foreground text-xs flex items-start">
                        {ch.channel_number ?? '—'}
                      </div>

                      {/* Name — inline edit */}
                      <div className="px-2 py-2.5 min-w-0 flex items-start group">
                        {isEditing ? (
                          <div className="min-w-0 w-full">
                            <input
                              autoFocus
                              className="w-full bg-background text-sm border border-border rounded px-1.5 py-0.5 outline-none focus:border-primary"
                              value={editValue}
                              onChange={(e) => setEditValue(e.target.value)}
                              onBlur={() => commitEdit(ch.channel_id, ch.channel_name)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') e.currentTarget.blur()
                                if (e.key === 'Escape') cancelEdit()
                              }}
                            />
                            <div className="flex items-center gap-1 mt-0.5">
                              {(() => {
                                const candidate = override ?? match?.candidates[0] ?? null
                                return candidate ? (
                                  <button className="text-[10px] text-blue-400 hover:text-blue-300 transition-colors"
                                    onMouseDown={(e) => { e.preventDefault(); setEditValue(candidate.name) }}>
                                    Use EPG
                                  </button>
                                ) : null
                              })()}
                              <button className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                                onMouseDown={(e) => { e.preventDefault(); setEditValue(ch.channel_name) }}>
                                Revert
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="min-w-0 flex items-start gap-1 w-full">
                            <div className="min-w-0 flex-1">
                              <p className={`font-medium truncate ${hasNameChange ? 'text-yellow-300' : ''}`}>{displayName}</p>
                              {hasNameChange && <p className="text-[10px] text-muted-foreground truncate italic">was: {ch.channel_name}</p>}
                              {ch.tvg_id && !hasNameChange && <p className="text-xs text-muted-foreground truncate">{ch.tvg_id}</p>}
                              {ch.tvc_guide_stationid && !hasNameChange && <p className="text-[10px] text-muted-foreground/70 truncate">GN: {ch.tvc_guide_stationid}</p>}
                            </div>
                            <button
                              className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 mt-0.5 p-0.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                              title="Rename channel"
                              onClick={() => startEdit(ch.channel_id, displayName)}
                            >
                              <Pencil size={10} />
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Group */}
                      <div className="px-2 py-2.5 text-xs text-muted-foreground flex items-start">
                        <span className="truncate">
                          {ch.channel_group_id ? (groupMap[ch.channel_group_id] ?? `Group ${ch.channel_group_id}`) : '—'}
                        </span>
                      </div>

                      {/* EPG status / confidence */}
                      <div className="px-2 py-2.5 flex items-start">
                        {match ? (
                          <span className={`badge-conf badge-${match.confidence}`}>
                            {match.confidence === 'high' ? 'High'
                              : match.confidence === 'medium' ? 'Review'
                              : match.confidence === 'low' ? 'Low'
                              : 'No Match'}
                          </span>
                        ) : ch.has_epg ? (
                          <div className="flex flex-col gap-0.5">
                            <span className="flex items-center gap-1 text-xs text-green-400 shrink-0">
                              <CheckCircle2 size={12} /> Assigned
                            </span>
                            {ch.epg_data_id && epgDataToSourceName[ch.epg_data_id] && (
                              <span
                                className="text-[10px] text-foreground/60 truncate max-w-[100px]"
                                title={epgDataToSourceName[ch.epg_data_id]}
                              >
                                {epgDataToSourceName[ch.epg_data_id]}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="flex items-center gap-1 text-xs text-muted-foreground"><XCircle size={12} /> No EPG</span>
                        )}
                      </div>

                      {/* Match candidate */}
                      <div className="px-2 py-2.5 min-w-0 flex items-start">
                        {match ? (
                          <div className="min-w-0 w-full flex flex-col gap-0.5">
                            {match.confidence === 'none' && !override ? (
                              <ManualSearch
                                sourceIds={selectedSources}
                                onSelect={(c) => {
                                  setOverrides((prev) => ({ ...prev, [ch.channel_id]: c }))
                                  handleCheck(ch.channel_id, true)
                                }}
                              />
                            ) : (
                              <CandidatePicker
                                candidates={match.candidates}
                                selected={override}
                                sourceIds={selectedSources}
                                onSelect={(c) => {
                                  setOverrides((prev) => ({ ...prev, [ch.channel_id]: c }))
                                  handleCheck(ch.channel_id, true)
                                }}
                              />
                            )}
                            {(() => {
                              const candidate = override ?? match.candidates[0] ?? null
                              if (!candidate) return null
                              if (nowPlayingFor[ch.channel_id]) {
                                return <NowPlayingInline sourceIds={selectedSources} epgDataId={candidate.epg_data_id} tvgId={candidate.tvg_id} />
                              }
                              return (
                                <button
                                  className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 w-fit"
                                  onClick={() => setNowPlayingFor((p) => ({ ...p, [ch.channel_id]: true }))}
                                  title={!xmltvCacheReady ? 'EPG cache still loading…' : undefined}
                                >
                                  {!xmltvCacheReady
                                    ? <><Loader2 size={10} className="animate-spin" /> EPG loading…</>
                                    : <><Clock size={10} /> Now playing</>
                                  }
                                </button>
                              )
                            })()}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </div>

                      {/* Stream preview */}
                      <div className="px-1 py-2.5 flex items-start justify-center gap-0.5">
                        <button
                          className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground flex flex-col items-center gap-0"
                          title={ch.stream_count ? 'Preview stream' : 'No streams configured'}
                          disabled={!ch.stream_count}
                          onClick={() => handleStreamPreview(ch)}
                        >
                          <Play size={13} />
                          {ch.stream_count != null && (
                            <span className={`text-[9px] leading-none font-medium ${ch.stream_count === 0 ? 'text-destructive' : 'text-muted-foreground'}`}>
                              {ch.stream_count}
                            </span>
                          )}
                        </button>
                        <button
                          className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-destructive"
                          title="Delete channel from Dispatcharr"
                          onClick={() => setDeleteTarget({ id: ch.channel_id, name: ch.channel_name })}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {!loadingChannels && !channels && (
            <div className="text-center py-10 text-sm text-muted-foreground">
              Set filters above and click <strong>Load Channels</strong>.
            </div>
          )}
        </CardContent>
      </Card>

      {/* EPG source filter portal */}
      {epgSourceDropOpen && epgSourceDropPos && createPortal(
        <>
          <div className="fixed inset-0 z-[90]" onClick={() => { setEpgSourceDropOpen(false); setEpgSourceSearch('') }} />
          <div
            className="fixed z-[91] border border-border rounded-lg shadow-2xl overflow-hidden"
            style={{ top: epgSourceDropPos.top, left: epgSourceDropPos.left, width: epgSourceDropPos.width, backgroundColor: 'hsl(var(--card))' }}
          >
            <div className="px-2 pt-2 pb-1">
              <input
                autoFocus
                type="text"
                placeholder="Search EPG sources…"
                value={epgSourceSearch}
                onChange={(e) => setEpgSourceSearch(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="w-full text-xs px-2 py-1.5 rounded border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <div className="max-h-56 overflow-y-auto py-1">
              {!epgSourceSearch && (
                <button
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent transition-colors ${filterEpgSourceId === null ? 'text-primary font-medium' : 'text-foreground'}`}
                  onClick={() => { setFilterEpgSourceId(null); setEpgSourceDropOpen(false); setEpgSourceSearch('') }}
                >
                  <span className="flex-1">All channels</span>
                  {filterEpgSourceId === null && <CheckCircle2 size={11} className="shrink-0" />}
                </button>
              )}
              {(assignedEpgSources ?? [])
                .filter((s) => !epgSourceSearch || s.name.toLowerCase().includes(epgSourceSearch.toLowerCase()))
                .map((s) => (
                  <button
                    key={s.id}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent transition-colors ${filterEpgSourceId === s.id ? 'text-primary' : 'text-foreground'}`}
                    onClick={() => { setFilterEpgSourceId(s.id); setEpgSourceDropOpen(false); setEpgSourceSearch('') }}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="truncate">{s.name}</p>
                      <p className="text-muted-foreground truncate">{s.epg_data_ids.length} channel{s.epg_data_ids.length !== 1 ? 's' : ''} assigned</p>
                    </div>
                    {filterEpgSourceId === s.id && <CheckCircle2 size={11} className="shrink-0" />}
                  </button>
                ))}
              {epgSourceSearch && (assignedEpgSources ?? []).filter((s) => s.name.toLowerCase().includes(epgSourceSearch.toLowerCase())).length === 0 && (
                <p className="px-3 py-2 text-xs text-muted-foreground">No results for "{epgSourceSearch}"</p>
              )}
            </div>
          </div>
        </>,
        document.body
      )}

      {/* Group multi-select portal */}
      {groupDropOpen && groupDropPos && createPortal(
        <>
          <div className="fixed inset-0 z-[90]" onClick={() => { setGroupDropOpen(false); setGroupSearch('') }} />
          <div
            className="fixed z-[91] border border-border rounded-lg shadow-2xl overflow-hidden"
            style={{ top: groupDropPos.top, left: groupDropPos.left, width: groupDropPos.width, backgroundColor: 'hsl(var(--card))' }}
          >
            <div className="px-2 pt-2 pb-1">
              <input
                autoFocus
                type="text"
                placeholder="Search groups…"
                value={groupSearch}
                onChange={(e) => setGroupSearch(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="w-full text-xs px-2 py-1.5 rounded border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <div className="max-h-56 overflow-y-auto py-1">
              {!groupSearch && (
                <button
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent transition-colors ${filterGroupIds.length === 0 ? 'text-primary font-medium' : 'text-foreground'}`}
                  onClick={() => { setFilterGroupIds([]); setGroupDropOpen(false); setGroupSearch('') }}
                >
                  <span className="flex-1">All groups</span>
                  {filterGroupIds.length === 0 && <CheckCircle2 size={11} className="shrink-0" />}
                </button>
              )}
              {(groups ?? [])
                .filter((g) => !groupSearch || g.name.toLowerCase().includes(groupSearch.toLowerCase()))
                .map((g) => {
                  const isSelected = filterGroupIds.includes(g.id)
                  return (
                    <button
                      key={g.id}
                      className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent transition-colors ${isSelected ? 'text-primary' : 'text-foreground'}`}
                      onClick={() => setFilterGroupIds((prev) => isSelected ? prev.filter((x) => x !== g.id) : [...prev, g.id])}
                    >
                      <input type="checkbox" checked={isSelected} readOnly className="cursor-pointer shrink-0" />
                      <span className="flex-1 truncate">{g.name}</span>
                    </button>
                  )
                })}
              {groupSearch && (groups ?? []).filter((g) => g.name.toLowerCase().includes(groupSearch.toLowerCase())).length === 0 && (
                <p className="px-3 py-2 text-xs text-muted-foreground">No groups matching "{groupSearch}"</p>
              )}
            </div>
          </div>
        </>,
        document.body
      )}

      </>}
    </div>
  )
}
