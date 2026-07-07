import { createPortal } from 'react-dom'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, ChevronDown, Loader2, RefreshCw, Search, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import api from '@/lib/api'

// ─── Types ────────────────────────────────────────────────────────────────────

type GNStatus     = 'has_gn' | 'can_fill' | 'no_match' | 'no_tvg_id'
type FilterStatus = 'all' | GNStatus

interface GNChannel {
  channel_id:           number
  name:                 string
  tvg_id:               string | null
  channel_group_id:     number | null
  channel_logo:         string | null
  tvc_guide_stationid:  string | null
  gn_call_sign:         string | null
  gn_name:              string | null
  gn_icon_url:          string | null
  status:               GNStatus
  would_fill:           string | null
}

interface ChannelGroup { id: number; name: string }

interface GNStation {
  station_id: string
  call_sign:  string
  name:       string
  icon_url:   string | null
}

interface GNReport {
  channels: GNChannel[]
  summary:  Record<GNStatus, number>
}

// ─── Logo — white bg so dark logos are always visible ─────────────────────────

function Logo({ src, size = 40 }: { src: string | null | undefined; size?: number }) {
  return (
    <div
      style={{ width: size, height: size, minWidth: size, background: '#fff' }}
      className="rounded flex items-center justify-center flex-shrink-0 overflow-hidden border border-border/20"
    >
      {src && (
        <img
          src={src}
          alt=""
          style={{ width: '88%', height: '88%', objectFit: 'contain' }}
          onError={e => (e.currentTarget.style.display = 'none')}
        />
      )}
    </div>
  )
}

// ─── Config ───────────────────────────────────────────────────────────────────

const STATUS_CFG: Record<GNStatus, { label: string; cls: string }> = {
  has_gn:    { label: 'Has GN',    cls: 'text-green-500' },
  can_fill:  { label: 'Can Fill',  cls: 'text-blue-400'  },
  no_match:  { label: 'No Match',  cls: 'text-muted-foreground' },
  no_tvg_id: { label: 'No TVG-ID', cls: 'text-muted-foreground' },
}

const STATUS_ORDER: GNStatus[] = ['has_gn', 'can_fill', 'no_match', 'no_tvg_id']

// ─── Debounce ─────────────────────────────────────────────────────────────────

function useDebounce<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}

// ─── Solid dropdown style — explicit hsl() to avoid theme transparency ────────
const DROPDOWN_STYLE: React.CSSProperties = {
  background: 'hsl(var(--card))',
  border:     '1px solid hsl(var(--border))',
  boxShadow:  '0 8px 24px rgba(0,0,0,0.35)',
}

// ─── GroupFilter — multi-select with solid bg ─────────────────────────────────

function GroupFilter({
  groups, selectedIds, onChange,
}: {
  groups:      ChannelGroup[]
  selectedIds: number[]
  onChange:    (ids: number[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const label =
    selectedIds.length === 0 ? 'All groups'
    : selectedIds.length === 1 ? (groups.find(g => g.id === selectedIds[0])?.name ?? '1 group')
    : `${selectedIds.length} groups`

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen(o => !o)}
        className={`h-8 px-3 rounded-md border text-xs flex items-center gap-1.5 transition-colors whitespace-nowrap ${
          open || selectedIds.length > 0
            ? 'border-primary bg-primary/10 text-foreground'
            : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/40'
        }`}
        style={{ background: open || selectedIds.length > 0 ? undefined : 'hsl(var(--card))' }}
      >
        {label}
        <ChevronDown size={11} className={`transition-transform duration-150 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div
          className="absolute top-full mt-1 left-0 z-50 min-w-[200px] max-h-64 overflow-y-auto rounded-md py-1"
          style={DROPDOWN_STYLE}
        >
          <button
            className="w-full text-left px-3 py-1.5 text-xs hover:bg-muted/50 transition-colors text-muted-foreground"
            onClick={() => { onChange([]); setOpen(false) }}
          >
            All groups
          </button>
          <div className="border-t border-border my-1" />
          {groups.map(g => (
            <label
              key={g.id}
              className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-muted/50 cursor-pointer transition-colors"
            >
              <input
                type="checkbox"
                className="accent-primary"
                checked={selectedIds.includes(g.id)}
                onChange={e => {
                  if (e.target.checked) onChange([...selectedIds, g.id])
                  else onChange(selectedIds.filter(id => id !== g.id))
                }}
              />
              <span className="truncate">{g.name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── GN Search Popover — portaled to body, fixed position, pre-seeded query ───

interface SearchPopoverProps {
  channelId:   number
  channelName: string
  anchorEl:    HTMLElement | null
  onAssign:    (channelId: number, stationId: string) => void
  onClose:     () => void
  isPending:   boolean
}

function GNSearchPopover({ channelId, channelName, anchorEl, onAssign, onClose, isPending }: SearchPopoverProps) {
  // Pre-populate with channel name so suggestions appear immediately
  const [query, setQuery]   = useState(channelName)
  const debouncedQ          = useDebounce(query, 250)
  const inputRef            = useRef<HTMLInputElement>(null)
  const containerRef        = useRef<HTMLDivElement>(null)
  const [pos, setPos]       = useState({ top: 0, right: 0 })

  // Position below the anchor button using fixed coords
  useEffect(() => {
    if (anchorEl) {
      const rect = anchorEl.getBoundingClientRect()
      setPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right })
    }
    inputRef.current?.focus()
  }, [anchorEl])

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current && !containerRef.current.contains(e.target as Node) &&
        anchorEl && !anchorEl.contains(e.target as Node)
      ) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose, anchorEl])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const { data: results, isFetching } = useQuery({
    queryKey: ['gn-lookup', debouncedQ],
    queryFn:  () => api.get(`/gn-station-db/lookup/?q=${encodeURIComponent(debouncedQ)}&limit=15`).then(r => r.data as GNStation[]),
    enabled:  debouncedQ.length >= 2,
    staleTime: 30_000,
  })

  const popover = (
    <div
      ref={containerRef}
      style={{
        position: 'fixed',
        top:      pos.top,
        right:    pos.right,
        zIndex:   9999,
        width:    '22rem',
        ...DROPDOWN_STYLE,
        borderRadius: '6px',
      }}
    >
      {/* Search input */}
      <div className="p-2 border-b border-border">
        <div className="relative">
          <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <input
            ref={inputRef}
            className="w-full pl-6 pr-6 py-1.5 text-xs rounded outline-none focus:ring-1 focus:ring-primary"
            style={{ background: 'hsl(var(--background))', border: '1px solid hsl(var(--border))', color: 'hsl(var(--foreground))' }}
            placeholder="Channel name or call sign…"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {query && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={() => setQuery('')}
            >
              <X size={10} />
            </button>
          )}
        </div>
      </div>

      {/* Results */}
      <div className="max-h-72 overflow-y-auto">
        {isFetching && (
          <div className="flex items-center justify-center py-4 text-muted-foreground gap-1.5">
            <Loader2 size={11} className="animate-spin" />
            <span className="text-xs">Searching…</span>
          </div>
        )}
        {!isFetching && debouncedQ.length >= 2 && (!results || results.length === 0) && (
          <p className="py-4 text-center text-xs text-muted-foreground">No stations found for "{debouncedQ}"</p>
        )}
        {debouncedQ.length < 2 && (
          <p className="py-3 text-center text-xs text-muted-foreground">Type to search GN stations</p>
        )}
        {results?.map(station => (
          <button
            key={station.station_id}
            className="w-full flex items-center gap-2.5 px-3 py-2 hover:bg-muted/40 transition-colors text-left"
            style={{ color: 'hsl(var(--foreground))' }}
            onClick={() => onAssign(channelId, station.station_id)}
            disabled={isPending}
          >
            <Logo src={station.icon_url} size={32} />
            <div className="flex-1 min-w-0">
              <div className="font-medium text-xs truncate">{station.call_sign}</div>
              <div className="text-xs truncate" style={{ color: 'hsl(var(--muted-foreground))' }}>{station.name}</div>
            </div>
            <span className="text-xs font-mono shrink-0" style={{ color: 'hsl(var(--muted-foreground))' }}>{station.station_id}</span>
          </button>
        ))}
      </div>
    </div>
  )

  return createPortal(popover, document.body)
}

// ─── Channel Row ──────────────────────────────────────────────────────────────

function ChannelRow({
  ch, isSearching, onSearch, onCloseSearch, onFill, onAssign, assignPending, fillPending,
}: {
  ch:            GNChannel
  isSearching:   boolean
  onSearch:      (id: number, anchorEl: HTMLElement) => void
  onCloseSearch: () => void
  onFill:        (id: number, sid: string) => void
  onAssign:      (id: number, sid: string) => void
  assignPending: boolean
  fillPending:   boolean
}) {
  const cfg        = STATUS_CFG[ch.status]
  const gnId       = ch.tvc_guide_stationid || ch.would_fill
  const gnName     = ch.gn_name || ch.gn_call_sign || gnId
  const searchBtnRef = useRef<HTMLButtonElement>(null)

  return (
    <tr className="border-b border-border/50 hover:bg-muted/20 transition-colors">
      {/* Channel logo */}
      <td className="px-2 py-1.5 w-12">
        <Logo src={ch.channel_logo} size={40} />
      </td>

      {/* Channel name */}
      <td className="px-3 py-1.5 font-medium max-w-[200px]">
        <span className="block truncate text-xs" title={ch.name}>{ch.name}</span>
      </td>

      {/* TVG-ID */}
      <td className="px-3 py-1.5 text-muted-foreground font-mono text-xs hidden md:table-cell max-w-[160px]">
        <span className="block truncate" title={ch.tvg_id ?? ''}>
          {ch.tvg_id ?? <span className="opacity-30">—</span>}
        </span>
      </td>

      {/* Arrow */}
      <td className="px-1 py-1.5 text-center text-muted-foreground/30 w-5 text-xs select-none">
        {(ch.status === 'has_gn' || ch.status === 'can_fill') ? '→' : ''}
      </td>

      {/* GN logo */}
      <td className="px-2 py-1.5 w-12">
        <Logo src={ch.gn_icon_url} size={40} />
      </td>

      {/* GN station name */}
      <td className="px-3 py-1.5 max-w-[160px]">
        <span className="block truncate text-xs" title={gnName ?? ''}>
          {gnName ?? <span className="text-muted-foreground/30 text-xs">—</span>}
        </span>
      </td>

      {/* GN ID */}
      <td className="px-3 py-1.5 font-mono text-xs text-muted-foreground hidden lg:table-cell w-20">
        {gnId ?? <span className="opacity-30">—</span>}
      </td>

      {/* Status */}
      <td className="px-3 py-1.5 w-20">
        <span className={`text-xs ${cfg.cls}`}>{cfg.label}</span>
      </td>

      {/* Action */}
      <td className="px-2 py-1.5 w-24">
        <div className="flex items-center gap-1">
          {ch.status === 'can_fill' && (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-xs text-blue-400 hover:text-blue-300 hover:bg-blue-400/10"
              onClick={() => onFill(ch.channel_id, ch.would_fill!)}
              disabled={fillPending || assignPending}
            >
              Fill
            </Button>
          )}
          <button
            ref={searchBtnRef}
            title={isSearching ? 'Close search' : 'Search / assign GN station'}
            className={`p-1 rounded transition-colors ${
              isSearching
                ? 'text-primary bg-primary/10'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
            }`}
            onClick={() => {
              if (isSearching) {
                onCloseSearch()
              } else if (searchBtnRef.current) {
                onSearch(ch.channel_id, searchBtnRef.current)
              }
            }}
          >
            {isSearching ? <X size={12} /> : <Search size={12} />}
          </button>
        </div>
      </td>

      {/* Portal — rendered outside the overflow container so it's never clipped */}
      {isSearching && (
        <GNSearchPopover
          channelId={ch.channel_id}
          channelName={ch.name}
          anchorEl={searchBtnRef.current}
          onAssign={onAssign}
          onClose={onCloseSearch}
          isPending={assignPending}
        />
      )}
    </tr>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function GNMatcher() {
  const queryClient                 = useQueryClient()
  const [filterStatus, setFilter]   = useState<FilterStatus>('all')
  const [nameSearch, setName]       = useState('')
  const [groupIds, setGroupIds]     = useState<number[]>([])
  const [assigningId, setAssigning] = useState<number | null>(null)

  const { data: report, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['gn-channel-report'],
    queryFn:  () => api.get('/gn-station-db/channel-report/').then(r => r.data as GNReport),
    staleTime: 120_000,
    retry: false,
  })

  const { data: groups } = useQuery<ChannelGroup[]>({
    queryKey: ['channel-groups'],
    queryFn:  () => api.get('/groups/').then(r => r.data),
    staleTime: 60_000,
  })

  const assignMutation = useMutation({
    mutationFn: (data: { channel_id: number; station_id: string }) =>
      api.post('/gn-station-db/assign/', data).then(r => r.data),
    onSuccess: () => {
      setAssigning(null)
      queryClient.invalidateQueries({ queryKey: ['gn-channel-report'] })
    },
  })

  const fillOneMutation = useMutation({
    mutationFn: (data: { channel_id: number; station_id: string }) =>
      api.post('/gn-station-db/assign/', data).then(r => r.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gn-channel-report'] }),
  })

  const fillAllMutation = useMutation({
    mutationFn: () => api.post('/gn-station-db/fill-ids/').then(r => r.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gn-channel-report'] }),
  })

  const handleSearch  = useCallback((channelId: number, _anchorEl: HTMLElement) => {
    setAssigning(channelId)
  }, [])

  const handleAssign  = useCallback((channelId: number, stationId: string) => {
    assignMutation.mutate({ channel_id: channelId, station_id: stationId })
  }, [assignMutation])

  const handleFillOne = useCallback((channelId: number, stationId: string) => {
    fillOneMutation.mutate({ channel_id: channelId, station_id: stationId })
  }, [fillOneMutation])

  const filtered = useMemo(() => {
    if (!report) return []
    return report.channels.filter(ch => {
      if (filterStatus !== 'all' && ch.status !== filterStatus) return false
      if (groupIds.length > 0 && !groupIds.includes(ch.channel_group_id ?? -1)) return false
      if (nameSearch && !ch.name.toLowerCase().includes(nameSearch.toLowerCase())) return false
      return true
    })
  }, [report, filterStatus, groupIds, nameSearch])

  // ── Render ─────────────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24 text-muted-foreground gap-2">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-sm">Building channel report…</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
        <AlertCircle size={24} />
        <p className="text-sm">GN Station DB is not available.</p>
        <p className="text-xs">Download it in Settings → GN Station DB.</p>
      </div>
    )
  }

  if (!report) return null

  const summary      = report.summary
  const canFillCount = summary.can_fill

  return (
    <div className="space-y-3 p-4">

      {/* Stats pills */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setFilter('all')}
          className={`px-3 py-1 rounded-full border text-xs transition-colors ${
            filterStatus === 'all'
              ? 'border-primary bg-primary/10 text-foreground'
              : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/40'
          }`}
        >
          All <span className="tabular-nums ml-1">{report.channels.length.toLocaleString()}</span>
        </button>

        {STATUS_ORDER.map(key => {
          const cfg = STATUS_CFG[key]
          return (
            <button
              key={key}
              onClick={() => setFilter(f => f === key ? 'all' : key)}
              className={`px-3 py-1 rounded-full border text-xs transition-colors ${
                filterStatus === key
                  ? 'border-primary bg-primary/10 text-foreground'
                  : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/40'
              }`}
            >
              <span className={cfg.cls}>{cfg.label}</span>
              <span className="tabular-nums ml-1">{summary[key].toLocaleString()}</span>
            </button>
          )
        })}

        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="ml-auto p-1 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw size={13} className={isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Search + group filter + fill all */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <Input
            className="pl-8 h-8 text-sm"
            placeholder="Filter channels by name…"
            value={nameSearch}
            onChange={e => setName(e.target.value)}
          />
          {nameSearch && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={() => setName('')}
            >
              <X size={12} />
            </button>
          )}
        </div>

        {groups && groups.length > 0 && (
          <GroupFilter groups={groups} selectedIds={groupIds} onChange={setGroupIds} />
        )}

        {canFillCount > 0 && (
          <Button
            size="sm"
            variant="outline"
            className="h-8 text-xs shrink-0"
            onClick={() => fillAllMutation.mutate()}
            disabled={fillAllMutation.isPending || fillOneMutation.isPending}
          >
            {fillAllMutation.isPending ? <Loader2 size={12} className="animate-spin mr-1" /> : null}
            Fill All ({canFillCount.toLocaleString()})
          </Button>
        )}
      </div>

      {/* Channel table */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-auto" style={{ maxHeight: 'calc(100vh - 260px)' }}>
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 z-10 bg-card border-b border-border">
                <tr>
                  <th className="px-2 py-2 w-12" />
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">Channel</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground hidden md:table-cell">TVG-ID</th>
                  <th className="w-5" />
                  <th className="px-2 py-2 w-12" />
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">GN Station</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground hidden lg:table-cell w-20">GN ID</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground w-20">Status</th>
                  <th className="px-2 py-2 w-24" />
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-12 text-center text-muted-foreground text-sm">
                      No channels match the current filter.
                    </td>
                  </tr>
                ) : (
                  filtered.map(ch => (
                    <ChannelRow
                      key={ch.channel_id}
                      ch={ch}
                      isSearching={assigningId === ch.channel_id}
                      onSearch={handleSearch}
                      onCloseSearch={() => setAssigning(null)}
                      onFill={handleFillOne}
                      onAssign={handleAssign}
                      assignPending={assignMutation.isPending}
                      fillPending={fillOneMutation.isPending || fillAllMutation.isPending}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {filtered.length > 0 && (
            <div className="px-4 py-2 text-xs text-muted-foreground border-t border-border">
              {filtered.length.toLocaleString()} of {report.channels.length.toLocaleString()} channels
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
