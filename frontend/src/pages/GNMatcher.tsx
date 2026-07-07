import { createPortal } from 'react-dom'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, CheckSquare, ChevronDown, Loader2, RefreshCw, Search, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import api from '@/lib/api'

// ─── Types ────────────────────────────────────────────────────────────────────

type Confidence = 'high' | 'medium' | 'low' | 'none' | 'has_gn'
type ConfFilter = 'all' | Confidence

interface GNStation {
  station_id: string
  call_sign:  string
  name:       string
  icon_url:   string | null
  score:      number
  tier:       string
}

interface GNMatchChannel {
  channel_id:           number
  name:                 string
  tvg_id:               string | null
  channel_group_id:     number | null
  channel_logo:         string | null
  tvc_guide_stationid:  string | null
  candidates:           GNStation[]
  top_score:            number
  confidence:           Confidence
}

interface GNMatchReport {
  channels: GNMatchChannel[]
  summary:  Record<Confidence, number>
}

interface ChannelGroup { id: number; name: string }

// ─── Config ───────────────────────────────────────────────────────────────────

const CONF_CFG: Record<Confidence, { label: string; cls: string; pill: string }> = {
  high:   { label: 'High',   cls: 'text-green-500',           pill: 'border-green-500/40 bg-green-500/10 text-green-400' },
  medium: { label: 'Medium', cls: 'text-yellow-400',          pill: 'border-yellow-500/40 bg-yellow-500/10 text-yellow-400' },
  low:    { label: 'Low',    cls: 'text-orange-400',          pill: 'border-orange-500/40 bg-orange-500/10 text-orange-400' },
  none:   { label: 'None',   cls: 'text-muted-foreground',    pill: 'border-border bg-muted/30 text-muted-foreground' },
  has_gn: { label: 'Has GN', cls: 'text-blue-400',            pill: 'border-blue-500/40 bg-blue-500/10 text-blue-400' },
}

const CONF_ORDER: Confidence[] = ['high', 'medium', 'low', 'none', 'has_gn']

// ─── Helpers ──────────────────────────────────────────────────────────────────

function useDebounce<T>(value: T, ms: number): T {
  const [d, setD] = useState(value)
  useEffect(() => { const t = setTimeout(() => setD(value), ms); return () => clearTimeout(t) }, [value, ms])
  return d
}

const SOLID_BG: React.CSSProperties = {
  background: 'hsl(var(--card))',
  border:     '1px solid hsl(var(--border))',
  boxShadow:  '0 8px 24px rgba(0,0,0,0.35)',
}

function Logo({ src, size = 40 }: { src: string | null | undefined; size?: number }) {
  return (
    <div style={{ width: size, height: size, minWidth: size, background: '#fff' }}
      className="rounded flex items-center justify-center flex-shrink-0 overflow-hidden border border-border/20">
      {src && <img src={src} alt="" style={{ width: '88%', height: '88%', objectFit: 'contain' }}
        onError={e => (e.currentTarget.style.display = 'none')} />}
    </div>
  )
}

function ScoreBar({ score }: { score: number }) {
  const pct  = Math.round(score * 100)
  const color = score >= 0.90 ? '#22c55e' : score >= 0.65 ? '#eab308' : score >= 0.30 ? '#f97316' : '#6b7280'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 rounded-full bg-muted/50 overflow-hidden">
        <div style={{ width: `${pct}%`, background: color, height: '100%' }} className="rounded-full" />
      </div>
      <span className="text-xs tabular-nums" style={{ color }}>{pct}%</span>
    </div>
  )
}

// ─── Group Filter ─────────────────────────────────────────────────────────────

function GroupFilter({ groups, selectedIds, onChange }: {
  groups: ChannelGroup[]; selectedIds: number[]; onChange: (ids: number[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  const label = selectedIds.length === 0 ? 'All groups'
    : selectedIds.length === 1 ? (groups.find(g => g.id === selectedIds[0])?.name ?? '1 group')
    : `${selectedIds.length} groups`

  return (
    <div ref={ref} className="relative shrink-0">
      <button onClick={() => setOpen(o => !o)}
        style={{ background: 'hsl(var(--card))' }}
        className={`h-8 px-3 rounded-md border text-xs flex items-center gap-1.5 whitespace-nowrap ${
          selectedIds.length > 0 ? 'border-primary text-foreground' : 'border-border text-muted-foreground hover:text-foreground'
        }`}>
        {label}<ChevronDown size={11} className={open ? 'rotate-180' : ''} />
      </button>
      {open && (
        <div className="absolute top-full mt-1 left-0 z-50 min-w-[200px] max-h-64 overflow-y-auto rounded-md py-1" style={SOLID_BG}>
          <button className="w-full text-left px-3 py-1.5 text-xs hover:bg-muted/50 text-muted-foreground"
            onClick={() => { onChange([]); setOpen(false) }}>All groups</button>
          <div className="border-t border-border my-1" />
          {groups.map(g => (
            <label key={g.id} className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-muted/50 cursor-pointer">
              <input type="checkbox" className="accent-primary" checked={selectedIds.includes(g.id)}
                onChange={e => e.target.checked ? onChange([...selectedIds, g.id]) : onChange(selectedIds.filter(i => i !== g.id))} />
              <span className="truncate">{g.name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Candidate Picker Popover ─────────────────────────────────────────────────
// Shows scored candidates from match result + a search box for manual lookup.
// Portaled to document.body so it can't be clipped by overflow:auto.

interface CandidatePopoverProps {
  channelId:       number
  candidates:      GNStation[]
  initialQuery:    string
  onQueryChange:   (q: string) => void
  anchorEl:        HTMLElement | null
  onSelect:        (station: GNStation) => void
  onClose:         () => void
}

function CandidatePopover({ channelId, candidates, initialQuery, onQueryChange, anchorEl, onSelect, onClose }: CandidatePopoverProps) {
  const [query, setQuery]   = useState(initialQuery)
  const debouncedQ          = useDebounce(query, 250)
  const inputRef            = useRef<HTMLInputElement>(null)
  const containerRef        = useRef<HTMLDivElement>(null)
  const [pos, setPos]       = useState<{ top?: number; bottom?: number; right: number }>({ right: 0 })

  useEffect(() => {
    if (!anchorEl) return
    const rect       = anchorEl.getBoundingClientRect()
    const POPOVER_H  = 440
    const spaceBelow = window.innerHeight - rect.bottom
    if (spaceBelow < POPOVER_H && rect.top > spaceBelow) {
      setPos({ bottom: window.innerHeight - rect.top + 4, right: window.innerWidth - rect.right })
    } else {
      setPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right })
    }
    setTimeout(() => inputRef.current?.focus(), 10)
  }, [anchorEl])

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node) &&
          anchorEl && !anchorEl.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [onClose, anchorEl])

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose])

  const handleQueryChange = (q: string) => { setQuery(q); onQueryChange(q) }

  const { data: searchResults, isFetching } = useQuery({
    queryKey: ['gn-lookup', debouncedQ],
    queryFn:  () => api.get(`/gn-station-db/lookup/?q=${encodeURIComponent(debouncedQ)}&limit=12`).then(r => r.data as GNStation[]),
    enabled:  debouncedQ.length >= 2,
    staleTime: 30_000,
  })

  // Show search results if query entered, else show match candidates
  const showSearch     = query.length >= 2
  const displayList    = showSearch ? (searchResults ?? []) : candidates

  const posStyle: React.CSSProperties = {
    position: 'fixed', right: pos.right, zIndex: 9999, width: '24rem', ...SOLID_BG, borderRadius: '6px',
  }
  if (pos.top !== undefined)    posStyle.top    = pos.top
  if (pos.bottom !== undefined) posStyle.bottom = pos.bottom

  return createPortal(
    <div ref={containerRef} style={posStyle}>
      {/* Search bar */}
      <div className="p-2 border-b border-border">
        <div className="relative">
          <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <input ref={inputRef}
            className="w-full pl-6 pr-6 py-1.5 text-xs rounded outline-none focus:ring-1 focus:ring-primary"
            style={{ background: 'hsl(var(--background))', border: '1px solid hsl(var(--border))', color: 'hsl(var(--foreground))' }}
            placeholder="Search stations or leave blank for suggestions…"
            value={query}
            onChange={e => handleQueryChange(e.target.value)}
          />
          {query && (
            <button className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onMouseDown={e => { e.preventDefault(); handleQueryChange('') }}>
              <X size={10} />
            </button>
          )}
        </div>
        {!showSearch && candidates.length > 0 && (
          <p className="text-xs text-muted-foreground mt-1.5 px-1">Suggested matches — click to select, or search above</p>
        )}
      </div>

      {/* Results */}
      <div className="max-h-80 overflow-y-auto">
        {showSearch && isFetching && (
          <div className="flex items-center justify-center py-4 gap-1.5 text-muted-foreground">
            <Loader2 size={11} className="animate-spin" /><span className="text-xs">Searching…</span>
          </div>
        )}
        {showSearch && !isFetching && displayList.length === 0 && (
          <p className="py-4 text-center text-xs text-muted-foreground">No stations found for "{debouncedQ}"</p>
        )}
        {!showSearch && candidates.length === 0 && (
          <p className="py-4 text-center text-xs text-muted-foreground">No suggestions — type to search manually</p>
        )}
        {displayList.map((station, idx) => (
          <button key={`${station.station_id}-${idx}`}
            className="w-full flex items-center gap-2.5 px-3 py-2 hover:bg-muted/40 transition-colors text-left"
            style={{ color: 'hsl(var(--foreground))' }}
            onMouseDown={e => { e.stopPropagation(); onSelect(station) }}>
            <Logo src={station.icon_url} size={32} />
            <div className="flex-1 min-w-0">
              <div className="font-medium text-xs truncate">{station.call_sign}</div>
              <div className="text-xs truncate" style={{ color: 'hsl(var(--muted-foreground))' }}>{station.name}</div>
            </div>
            <div className="flex flex-col items-end shrink-0 gap-0.5">
              <span className="text-xs font-mono" style={{ color: 'hsl(var(--muted-foreground))' }}>{station.station_id}</span>
              {!showSearch && station.score !== undefined && (
                <span className="text-xs" style={{ color: 'hsl(var(--muted-foreground))' }}>{Math.round(station.score * 100)}%</span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>,
    document.body
  )
}

// ─── Channel Row ──────────────────────────────────────────────────────────────

interface RowProps {
  ch:            GNMatchChannel
  selected:      GNStation | null          // user's current selection (override or top candidate)
  isOpen:        boolean
  searchQuery:   string
  onToggle:      (checked: boolean) => void
  checked:       boolean
  onOpenPicker:  (channelId: number, anchorEl: HTMLElement) => void
  onClosePicker: () => void
  onSelect:      (channelId: number, station: GNStation) => void
  onQueryChange: (q: string) => void
}

function ChannelRow({ ch, selected, isOpen, searchQuery, onToggle, checked, onOpenPicker, onClosePicker, onSelect, onQueryChange }: RowProps) {
  const cfg        = CONF_CFG[ch.confidence]
  const btnRef     = useRef<HTMLButtonElement>(null)
  const isHasGn    = ch.confidence === 'has_gn'
  const topStation = selected ?? ch.candidates[0] ?? null

  return (
    <tr className="border-b border-border/50 hover:bg-muted/20 transition-colors">
      {/* Checkbox */}
      <td className="px-2 py-1.5 w-8">
        {!isHasGn && topStation && (
          <button onClick={() => onToggle(!checked)} className="text-muted-foreground hover:text-foreground transition-colors">
            {checked ? <CheckSquare size={14} className="text-primary" /> : <Square size={14} />}
          </button>
        )}
      </td>

      {/* Channel logo */}
      <td className="px-1 py-1.5 w-12"><Logo src={ch.channel_logo} size={40} /></td>

      {/* Channel name */}
      <td className="px-3 py-1.5 max-w-[180px]">
        <span className="block truncate text-xs font-medium" title={ch.name}>{ch.name}</span>
        {ch.tvg_id && (
          <span className="block truncate text-xs font-mono text-muted-foreground/60" title={ch.tvg_id}>{ch.tvg_id}</span>
        )}
      </td>

      {/* Arrow */}
      <td className="px-1 w-4 text-center text-muted-foreground/30 text-xs select-none">
        {topStation ? '→' : ''}
      </td>

      {/* GN station logo */}
      <td className="px-1 py-1.5 w-12"><Logo src={topStation?.icon_url} size={40} /></td>

      {/* GN station info */}
      <td className="px-3 py-1.5 max-w-[180px]">
        {topStation ? (
          <>
            <span className="block truncate text-xs font-medium" title={topStation.call_sign}>{topStation.call_sign}</span>
            <span className="block truncate text-xs text-muted-foreground/70" title={topStation.name}>{topStation.name}</span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground/30">—</span>
        )}
      </td>

      {/* Score bar */}
      <td className="px-3 py-1.5 w-28">
        {topStation && !isHasGn ? <ScoreBar score={topStation.score} /> : null}
        {isHasGn && <span className="text-xs text-blue-400 font-mono">{ch.tvc_guide_stationid}</span>}
      </td>

      {/* Confidence badge */}
      <td className="px-3 py-1.5 w-20">
        <span className={`text-xs px-1.5 py-0.5 rounded border ${cfg.pill}`}>{cfg.label}</span>
      </td>

      {/* Picker button */}
      <td className="px-2 py-1.5 w-10">
        <button ref={btnRef}
          title={isOpen ? 'Close' : 'Pick GN station'}
          className={`p-1 rounded transition-colors ${isOpen ? 'text-primary bg-primary/10' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'}`}
          onClick={() => {
            if (isOpen) { onClosePicker() }
            else if (btnRef.current) { onOpenPicker(ch.channel_id, btnRef.current) }
          }}>
          {isOpen ? <X size={12} /> : <Search size={12} />}
        </button>
      </td>

      {isOpen && (
        <CandidatePopover
          channelId={ch.channel_id}
          candidates={ch.candidates}
          initialQuery={searchQuery}
          onQueryChange={onQueryChange}
          anchorEl={btnRef.current}
          onSelect={s => onSelect(ch.channel_id, s)}
          onClose={onClosePicker}
        />
      )}
    </tr>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function GNMatcher() {
  const queryClient = useQueryClient()

  // Setup
  const [groupIds, setGroupIds]       = useState<number[]>([])
  const [matchRan, setMatchRan]       = useState(false)

  // Filters (post-match)
  const [filterConf, setFilterConf]   = useState<ConfFilter>('all')
  const [nameSearch, setNameSearch]   = useState('')

  // Selection state
  const [checked, setChecked]         = useState<Set<number>>(new Set())
  const [overrides, setOverrides]     = useState<Record<number, GNStation>>({})

  // Picker (candidate popover)
  const [openId, setOpenId]           = useState<number | null>(null)
  const [pickerQuery, setPickerQuery] = useState('')

  const { data: groups } = useQuery<ChannelGroup[]>({
    queryKey: ['channel-groups'],
    queryFn:  () => api.get('/groups/').then(r => r.data),
    staleTime: 60_000,
  })

  const matchQuery = useQuery<GNMatchReport>({
    queryKey: ['gn-match', groupIds],
    queryFn:  () => {
      const params = groupIds.length > 0 ? `?group_ids=${groupIds.join(',')}` : ''
      return api.get(`/gn-station-db/match/${params}`).then(r => r.data)
    },
    enabled:   matchRan,
    staleTime: 120_000,
    retry:     false,
  })

  const commitMutation = useMutation({
    mutationFn: (assignments: { channel_id: number; station_id: string }[]) =>
      api.post('/gn-station-db/bulk-assign/', { assignments }).then(r => r.data),
    onSuccess: () => {
      setChecked(new Set())
      setOverrides({})
      queryClient.invalidateQueries({ queryKey: ['gn-match'] })
    },
  })

  const handleRunMatch = () => {
    setMatchRan(true)
    setChecked(new Set())
    setOverrides({})
    if (matchRan) queryClient.invalidateQueries({ queryKey: ['gn-match', groupIds] })
  }

  const handleOpenPicker = useCallback((channelId: number, _anchorEl: HTMLElement) => {
    if (channelId !== openId) {
      // Seed picker query with channel name
      const ch = matchQuery.data?.channels.find(c => c.channel_id === channelId)
      setPickerQuery(ch?.name ?? '')
    }
    setOpenId(channelId)
  }, [openId, matchQuery.data])

  const handleSelect = useCallback((channelId: number, station: GNStation) => {
    setOverrides(prev => ({ ...prev, [channelId]: station }))
    setChecked(prev => { const s = new Set(prev); s.add(channelId); return s })
    setOpenId(null)
  }, [])

  const handleToggle = useCallback((channelId: number, val: boolean) => {
    setChecked(prev => {
      const s = new Set(prev)
      if (val) s.add(channelId)
      else s.delete(channelId)
      return s
    })
  }, [])

  const filtered = useMemo(() => {
    if (!matchQuery.data) return []
    return matchQuery.data.channels.filter(ch => {
      if (filterConf !== 'all' && ch.confidence !== filterConf) return false
      if (nameSearch && !ch.name.toLowerCase().includes(nameSearch.toLowerCase())) return false
      return true
    })
  }, [matchQuery.data, filterConf, nameSearch])

  // Pre-check all high confidence unassigned channels after match loads
  useEffect(() => {
    if (!matchQuery.data || !matchRan) return
    const highIds = matchQuery.data.channels
      .filter(ch => ch.confidence === 'high' && !ch.tvc_guide_stationid && ch.candidates.length > 0)
      .map(ch => ch.channel_id)
    setChecked(new Set(highIds))
  }, [matchQuery.data, matchRan])

  const commitList = useMemo(() =>
    [...checked].map(id => {
      const station = overrides[id] ?? matchQuery.data?.channels.find(c => c.channel_id === id)?.candidates[0]
      return station ? { channel_id: id, station_id: station.station_id } : null
    }).filter(Boolean) as { channel_id: number; station_id: string }[],
  [checked, overrides, matchQuery.data])

  const summary = matchQuery.data?.summary

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-3 p-4">

      {/* Setup card */}
      <Card>
        <CardHeader className="pb-2 pt-4">
          <CardTitle className="text-sm font-medium">GN Station Matcher</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 pb-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground w-24 shrink-0">Channel group</span>
            {groups && groups.length > 0
              ? <GroupFilter groups={groups} selectedIds={groupIds} onChange={setGroupIds} />
              : <span className="text-xs text-muted-foreground">Loading…</span>}
            <span className="text-xs text-muted-foreground">
              {groupIds.length === 0 ? 'All channels will be scored' : `Only channels in selected group${groupIds.length > 1 ? 's' : ''}`}
            </span>
          </div>

          <div className="flex items-center gap-3 pt-1">
            <Button size="sm" onClick={handleRunMatch} disabled={matchQuery.isFetching}
              className="h-8 text-xs">
              {matchQuery.isFetching
                ? <><Loader2 size={12} className="animate-spin mr-1.5" />Scoring…</>
                : matchRan ? <><RefreshCw size={12} className="mr-1.5" />Re-run Match</> : 'Run Match'}
            </Button>
            {matchQuery.error && (
              <span className="text-xs text-destructive flex items-center gap-1">
                <AlertCircle size={12} /> GN Station DB not available — download it in Settings
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Results */}
      {matchRan && matchQuery.data && (
        <>
          {/* Summary pills */}
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => setFilterConf('all')}
              className={`px-3 py-1 rounded-full border text-xs transition-colors ${
                filterConf === 'all' ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground'
              }`}>
              All <span className="tabular-nums ml-1">{matchQuery.data.channels.length.toLocaleString()}</span>
            </button>
            {CONF_ORDER.map(key => {
              const cnt = summary?.[key] ?? 0
              if (cnt === 0) return null
              const cfg = CONF_CFG[key]
              return (
                <button key={key} onClick={() => setFilterConf(f => f === key ? 'all' : key)}
                  className={`px-3 py-1 rounded-full border text-xs transition-colors ${
                    filterConf === key ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground'
                  }`}>
                  <span className={cfg.cls}>{cfg.label}</span>
                  <span className="tabular-nums ml-1">{cnt.toLocaleString()}</span>
                </button>
              )
            })}
          </div>

          {/* Filter + action bar */}
          <div className="flex flex-wrap gap-2 items-center">
            <div className="relative flex-1 min-w-[160px]">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
              <Input className="pl-8 h-8 text-xs" placeholder="Filter by channel name…"
                value={nameSearch} onChange={e => setNameSearch(e.target.value)} />
              {nameSearch && (
                <button className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setNameSearch('')}><X size={12} /></button>
              )}
            </div>

            <button className="text-xs text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap"
              onClick={() => {
                const ids = filtered
                  .filter(ch => ch.confidence !== 'has_gn' && ch.candidates.length > 0)
                  .map(ch => ch.channel_id)
                setChecked(new Set(ids))
              }}>
              Select all visible
            </button>
            <button className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => setChecked(new Set())}>
              Clear
            </button>

            <Button size="sm" className="h-8 text-xs ml-auto shrink-0"
              disabled={commitList.length === 0 || commitMutation.isPending}
              onClick={() => commitMutation.mutate(commitList)}>
              {commitMutation.isPending
                ? <><Loader2 size={12} className="animate-spin mr-1.5" />Committing…</>
                : `Commit ${commitList.length} assignment${commitList.length !== 1 ? 's' : ''}`}
            </Button>
          </div>

          {/* Table */}
          <Card>
            <CardContent className="p-0">
              <div className="overflow-auto" style={{ maxHeight: 'calc(100vh - 320px)' }}>
                <table className="w-full text-xs border-collapse">
                  <thead className="sticky top-0 z-10 bg-card border-b border-border">
                    <tr>
                      <th className="px-2 py-2 w-8" />
                      <th className="w-12" />
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Channel</th>
                      <th className="w-4" />
                      <th className="w-12" />
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">GN Station</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground w-28">Score</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground w-20">Confidence</th>
                      <th className="w-10" />
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 ? (
                      <tr>
                        <td colSpan={9} className="py-12 text-center text-muted-foreground text-sm">
                          No channels match the current filter.
                        </td>
                      </tr>
                    ) : filtered.map(ch => (
                      <ChannelRow
                        key={ch.channel_id}
                        ch={ch}
                        selected={overrides[ch.channel_id] ?? null}
                        isOpen={openId === ch.channel_id}
                        searchQuery={openId === ch.channel_id ? pickerQuery : ''}
                        checked={checked.has(ch.channel_id)}
                        onToggle={v => handleToggle(ch.channel_id, v)}
                        onOpenPicker={handleOpenPicker}
                        onClosePicker={() => setOpenId(null)}
                        onSelect={handleSelect}
                        onQueryChange={setPickerQuery}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              {filtered.length > 0 && (
                <div className="px-4 py-2 text-xs text-muted-foreground border-t border-border">
                  {filtered.length.toLocaleString()} of {matchQuery.data.channels.length.toLocaleString()} channels
                  {checked.size > 0 && <span className="ml-3 text-primary">{checked.size} selected for commit</span>}
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}

      {matchRan && matchQuery.isFetching && !matchQuery.data && (
        <div className="flex items-center justify-center py-24 text-muted-foreground gap-2">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Scoring {groupIds.length > 0 ? 'selected channels' : 'all channels'} against GN Station DB…</span>
        </div>
      )}

      {!matchRan && (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
          <Search size={28} className="opacity-30" />
          <p className="text-sm">Select a channel group (optional) and click Run Match to score GN station candidates.</p>
        </div>
      )}
    </div>
  )
}
