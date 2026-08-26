import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Hls from 'hls.js'
import mpegts from 'mpegts.js'
import {
  AlertCircle, CheckCircle2, Loader2, Play, RefreshCw, ScrollText, X, XCircle,
} from 'lucide-react'
import api from '@/lib/api'

// ─── EPG warm-cache status pill (app header) ───────────────────────────────────

export function EpgWarmIndicator({ onRefresh }: { onRefresh?: () => void }) {
  const [open,       setOpen]       = useState(false)
  const [refreshing, setRefreshing] = useState(false)
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

// ─── Application log viewer modal ───────────────────────────────────────────────

export function LogViewer({ onClose }: { onClose: () => void }) {
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

// ─── HLS/mpegts video preview modal ─────────────────────────────────────────────

export function VideoPlayer({ url, title, nowPlaying, onClose }: {
  url: string; title: string; nowPlaying?: { title: string; start: string; stop: string }; onClose: () => void
}) {
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
            { enableWorker: false,
              liveBufferLatencyChasing: false,
              autoCleanupSourceBuffer: true,
              stashInitialSize: 1024 * 512,
            },
          )
          mpegtsRef.current = player
          player.attachMediaElement(video)
          player.on(mpegts.Events.ERROR, (type: unknown, detail: unknown) => {
            const d = detail as Record<string, unknown>
            const msg = d?.msg ?? d?.message ?? JSON.stringify(d)
            setError(`${String(type)}: ${String(msg)}`)
            setStatus('error')
          })
          player.load()
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
