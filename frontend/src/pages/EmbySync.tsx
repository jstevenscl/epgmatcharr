import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Loader2, Radio, RefreshCw, Search, Trash2, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import api from '@/lib/api'

interface EmbySettings {
  configured:  boolean
  emby_url:    string
  has_api_key: boolean
  zip_codes:   string[]
  country:     string
}

interface CoverageItem { name: string; station_id: string; current_station_id?: string | null; channel_number?: string; group?: string }
interface SelectedLineup { listings_id: string; name: string; zip_code: string; channels_covered: number }
interface StationCandidate { provider_id: string; provider_name: string; station_id: string; name: string }

interface PreviewReport {
  total_channels:      number
  would_map:            CoverageItem[]
  left_unchanged:       CoverageItem[]
  no_gn_id_count:       number
  excluded_count:       number
  no_emby_match:        CoverageItem[]
  no_lineup_coverage:   CoverageItem[]
  selected_lineups:     SelectedLineup[]
  candidates_tried:     number
  zip_codes_used:       string[]
  auto_derived_zip_count: number
  respect_existing:     boolean
}

interface PushResult {
  mapped_count:      number
  failed:            { name: string; station_id: string; error?: string }[]
  skipped_count:      number
  left_unchanged_count: number
  excluded_count:     number
  selected_lineups:   SelectedLineup[]
  zip_codes_used:     string[]
}

interface ChannelGroup { id: number; name: string }

function mappingChangeLabel(item: CoverageItem): string {
  if (!item.current_station_id) return `new → ${item.station_id}`
  if (item.current_station_id === item.station_id) return `${item.station_id} (unchanged)`
  return `${item.current_station_id} → ${item.station_id}`
}

function ManualMapPicker({ onPick, onCancel }: {
  onPick: (candidate: StationCandidate) => void
  onCancel: () => void
}) {
  const [query, setQuery] = useState('')
  const searchQuery = useQuery<StationCandidate[]>({
    queryKey: ['emby-station-search', query],
    queryFn:  () => api.get('/emby/stations/', { params: { query } }).then((r) => r.data),
    enabled:  query.trim().length >= 2,
  })

  return (
    <div className="rounded-md border border-border bg-muted/20 p-2 space-y-1.5">
      <div className="flex items-center gap-1.5">
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search station name…"
          className="flex-1 h-7 px-2 rounded border border-border bg-background text-xs outline-none focus:ring-1 focus:ring-primary"
        />
        <button onClick={onCancel} className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/50" title="Cancel">
          <X size={12} />
        </button>
      </div>
      {query.trim().length >= 2 && (
        <div className="max-h-40 overflow-y-auto space-y-0.5">
          {searchQuery.isFetching && <p className="text-xs text-muted-foreground px-1">Searching…</p>}
          {!searchQuery.isFetching && (searchQuery.data?.length ?? 0) === 0 && (
            <p className="text-xs text-muted-foreground px-1">No matching stations found in Emby's configured lineups.</p>
          )}
          {searchQuery.data?.map((c) => (
            <button
              key={`${c.provider_id}-${c.station_id}`}
              onClick={() => onPick(c)}
              className="w-full flex items-center justify-between text-xs px-2 py-1 rounded hover:bg-muted/60 text-left"
            >
              <span className="truncate">{c.name}</span>
              <span className="font-mono text-muted-foreground shrink-0 ml-2">{c.provider_name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function ExcludedGroupsPanel() {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  const [touched, setTouched] = useState(false)
  const [saved, setSaved] = useState(false)
  const queryClient = useQueryClient()

  const { data: allGroups } = useQuery<ChannelGroup[]>({
    queryKey: ['channel-groups'],
    queryFn:  () => api.get('/groups/').then((r) => r.data),
  })

  const { data: excludedData } = useQuery<{ group_ids: number[] }>({
    queryKey: ['emby-excluded-groups'],
    queryFn:  () => api.get('/emby/excluded-groups/').then((r) => r.data),
  })

  useEffect(() => {
    if (touched || !excludedData) return
    setSelected(excludedData.group_ids)
  }, [excludedData, touched])

  const saveMutation = useMutation({
    mutationFn: () => api.post('/emby/excluded-groups/', { group_ids: selected }),
    onSuccess: () => {
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
      queryClient.invalidateQueries({ queryKey: ['emby-excluded-groups'] })
    },
  })

  const toggle = (id: number) => {
    setTouched(true)
    setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])
  }

  return (
    <div className="rounded-md border border-border">
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-xs"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="text-muted-foreground">
          Excluded channel groups {selected.length > 0 && <span className="font-mono">({selected.length})</span>}
        </span>
        {open ? <ChevronUp size={12} className="text-muted-foreground" /> : <ChevronDown size={12} className="text-muted-foreground" />}
      </button>
      {open && (
        <div className="border-t border-border p-2.5 space-y-2">
          <p className="text-[10px] text-muted-foreground">
            Channels in these groups are never pushed to Emby, even if they have a GN station ID — for groups
            like SiriusXM where a channel name can coincidentally match a real TV channel's name.
          </p>
          {!allGroups ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 size={12} className="animate-spin" /> Loading channel groups…
            </div>
          ) : (
            <div className="max-h-40 overflow-y-auto space-y-0.5 rounded border border-border p-1.5">
              {allGroups.map((g) => (
                <label key={g.id} className="flex items-center gap-2 text-xs cursor-pointer px-1 py-0.5 rounded hover:bg-muted/40">
                  <input type="checkbox" checked={selected.includes(g.id)} onChange={() => toggle(g.id)} className="h-3.5 w-3.5" />
                  {g.name}
                </label>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Button size="sm" className="h-7 text-xs" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
              {saveMutation.isPending ? <><Loader2 size={12} className="animate-spin" /> Saving…</> : 'Save'}
            </Button>
            {saved && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 size={12} /> Saved</span>}
            {saveMutation.isError && (
              <span className="text-xs text-destructive flex items-center gap-1">
                <AlertCircle size={12} /> Save failed — check the connection and try again.
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function CollapsibleList({ title, items, tone, render, onManualMap, onClear, mapPickerFor, onOpenPicker, onClosePicker }: {
  title: string
  items: CoverageItem[]
  tone: 'green' | 'yellow' | 'red'
  render?: (item: CoverageItem) => string
  onManualMap?: (item: CoverageItem, candidate: StationCandidate) => void
  onClear?: (item: CoverageItem) => void
  mapPickerFor?: string | null
  onOpenPicker?: (channelNumber: string) => void
  onClosePicker?: () => void
}) {
  const [open, setOpen] = useState(false)
  const toneCls = {
    green:  'border-green-500/30 bg-green-500/5',
    yellow: 'border-yellow-500/30 bg-yellow-500/5',
    red:    'border-red-500/30 bg-red-500/5',
  }[tone]
  const countCls = {
    green:  'text-green-400',
    yellow: 'text-yellow-400',
    red:    'text-red-400',
  }[tone]

  if (items.length === 0) return null

  const hasActions = Boolean(onManualMap || onClear)

  return (
    <div className={`rounded-lg border ${toneCls}`}>
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm"
        onClick={() => setOpen(o => !o)}
      >
        <span className="font-medium">{title}</span>
        <span className="flex items-center gap-2">
          <span className={`tabular-nums font-mono text-xs ${countCls}`}>{items.length.toLocaleString()}</span>
          {open ? <ChevronUp size={14} className="text-muted-foreground" /> : <ChevronDown size={14} className="text-muted-foreground" />}
        </span>
      </button>
      {open && (
        <div className="max-h-64 overflow-y-auto border-t border-border/50 px-4 py-2 space-y-1">
          {items.slice(0, 500).map((item, i) => (
            <div key={i} className="py-0.5">
              <div className="flex items-center justify-between text-xs gap-2">
                <span className="truncate flex-1">
                  {item.name}
                  {item.group && <span className="text-muted-foreground"> · {item.group}</span>}
                </span>
                <span className="font-mono text-muted-foreground shrink-0">{render ? render(item) : item.station_id}</span>
                {hasActions && item.channel_number && (
                  <span className="flex items-center gap-0.5 shrink-0">
                    {onManualMap && (
                      <button
                        onClick={() => onOpenPicker?.(item.channel_number!)}
                        title="Manually map this channel"
                        className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/50"
                      >
                        <Search size={11} />
                      </button>
                    )}
                    {onClear && (
                      <button
                        onClick={() => onClear(item)}
                        title="Clear this channel's mapping"
                        className="p-1 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                      >
                        <Trash2 size={11} />
                      </button>
                    )}
                  </span>
                )}
              </div>
              {onManualMap && mapPickerFor === item.channel_number && (
                <div className="mt-1">
                  <ManualMapPicker
                    onCancel={() => onClosePicker?.()}
                    onPick={(candidate) => onManualMap(item, candidate)}
                  />
                </div>
              )}
            </div>
          ))}
          {items.length > 500 && (
            <p className="text-xs text-muted-foreground pt-1">…and {(items.length - 500).toLocaleString()} more</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function EmbySync() {
  const [pushResult, setPushResult] = useState<PushResult | null>(null)
  const [respectExisting, setRespectExisting] = useState(false)
  const [mapPickerFor, setMapPickerFor] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { data: settings, isLoading: settingsLoading } = useQuery<EmbySettings>({
    queryKey: ['emby-settings'],
    queryFn:  () => api.get('/emby/settings/').then((r) => r.data),
    staleTime: 30_000,
  })

  const previewQuery = useQuery<PreviewReport>({
    queryKey: ['emby-preview'],
    queryFn:  () => api.get('/emby/preview/', { params: { respect_existing: respectExisting } }).then((r) => r.data),
    enabled:  false,
    retry:    false,
  })

  const pushMutation = useMutation({
    mutationFn: () => api.post('/emby/push/', { respect_existing: respectExisting }).then((r) => r.data as PushResult),
    onSuccess:  (data) => setPushResult(data),
  })

  const [manualActionError, setManualActionError] = useState<string | null>(null)

  const manualMapMutation = useMutation({
    mutationFn: (vars: { channel_number: string; provider_id: string; station_id: string }) =>
      api.post('/emby/map-channel/', vars),
    onSuccess: () => {
      setManualActionError(null)
      setMapPickerFor(null)
      queryClient.invalidateQueries({ queryKey: ['emby-preview'] })
      previewQuery.refetch()
    },
    onError: (err) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setManualActionError(msg || 'Manual mapping failed — check the Emby connection.')
    },
  })

  const clearMutation = useMutation({
    mutationFn: (channel_number: string) => api.post('/emby/clear-channel/', { channel_number }),
    onSuccess: () => {
      setManualActionError(null)
      queryClient.invalidateQueries({ queryKey: ['emby-preview'] })
      previewQuery.refetch()
    },
    onError: (err) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setManualActionError(msg || 'Clearing the mapping failed — check the Emby connection.')
    },
  })

  const handleManualMap = (item: CoverageItem, candidate: StationCandidate) => {
    if (!item.channel_number) return
    manualMapMutation.mutate({ channel_number: item.channel_number, provider_id: candidate.provider_id, station_id: candidate.station_id })
  }
  const handleClear = (item: CoverageItem) => {
    if (!item.channel_number) return
    clearMutation.mutate(item.channel_number)
  }

  if (settingsLoading) {
    return (
      <div className="flex items-center justify-center py-24 text-muted-foreground gap-2">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-sm">Loading…</span>
      </div>
    )
  }

  if (!settings?.configured) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3 p-4">
        <Radio size={28} className="opacity-30" />
        <p className="text-sm text-center max-w-sm">
          Connect Emby first — add your Emby server URL, API key, and at least one ZIP code
          in <span className="text-foreground font-medium">Settings</span>.
        </p>
      </div>
    )
  }

  const report = previewQuery.data

  return (
    <div className="space-y-3 p-4">
      <Card>
        <CardContent className="space-y-3 pt-4 pb-4">
          <div>
            <p className="text-sm font-medium flex items-center gap-1.5">
              <Radio size={14} className="text-primary" /> Emby Guide Sync
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Auto-detects the TV markets your channels cover from their call signs, discovers Gracenote
              lineups for those markets{settings.zip_codes.length > 0 && <> (plus <span className="font-mono">{settings.zip_codes.join(', ')}</span> from Settings)</>},
              picks the minimal set that covers your matched channels, and maps each channel to its known GN station ID on Emby.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              onClick={() => { setPushResult(null); previewQuery.refetch() }}
              disabled={previewQuery.isFetching}
              className="h-8 text-xs gap-1.5"
            >
              {previewQuery.isFetching
                ? <><Loader2 size={12} className="animate-spin" /> Previewing…</>
                : <><RefreshCw size={12} /> {report ? 'Re-run Preview' : 'Preview Coverage'}</>
              }
            </Button>
            {previewQuery.isError && (
              <span className="text-xs text-destructive flex items-center gap-1">
                <AlertCircle size={12} /> Preview failed — check the Emby connection in Settings.
              </span>
            )}
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer w-fit">
            <input
              type="checkbox"
              checked={respectExisting}
              onChange={(e) => setRespectExisting(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            Don't touch channels that already have a different mapping in Emby
          </label>
          <ExcludedGroupsPanel />
          <p className="text-[10px] text-muted-foreground">
            Preview is fully reversible — it doesn't change anything on your Emby server. Nothing is pushed until you click Push below.
          </p>
        </CardContent>
      </Card>

      {previewQuery.isFetching && !report && (
        <div className="flex items-center justify-center py-16 text-muted-foreground gap-2">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Discovering lineups and checking coverage… this can take up to a minute.</span>
        </div>
      )}

      {report && (
        <>
          {/* Summary */}
          <div className={`grid gap-2 ${report.left_unchanged.length > 0 ? 'grid-cols-5' : 'grid-cols-4'}`}>
            <div className="rounded-lg border border-green-500/30 bg-green-500/5 px-3 py-2.5">
              <div className="text-lg font-semibold text-green-400 tabular-nums">{report.would_map.length.toLocaleString()}</div>
              <div className="text-[10px] text-muted-foreground">Would be mapped</div>
            </div>
            {report.left_unchanged.length > 0 && (
              <div className="rounded-lg border border-sky-500/30 bg-sky-500/5 px-3 py-2.5">
                <div className="text-lg font-semibold text-sky-400 tabular-nums">{report.left_unchanged.length.toLocaleString()}</div>
                <div className="text-[10px] text-muted-foreground">Left unchanged</div>
              </div>
            )}
            <div className="rounded-lg border border-border bg-muted/20 px-3 py-2.5">
              <div className="text-lg font-semibold tabular-nums">{report.no_gn_id_count.toLocaleString()}</div>
              <div className="text-[10px] text-muted-foreground">No GN ID yet</div>
            </div>
            <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-3 py-2.5">
              <div className="text-lg font-semibold text-yellow-400 tabular-nums">{report.no_lineup_coverage.length.toLocaleString()}</div>
              <div className="text-[10px] text-muted-foreground">No lineup covers it</div>
            </div>
            <div className="rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2.5">
              <div className="text-lg font-semibold text-red-400 tabular-nums">{report.no_emby_match.length.toLocaleString()}</div>
              <div className="text-[10px] text-muted-foreground">Not found in Emby</div>
            </div>
          </div>

          {/* Selected lineups */}
          <Card>
            <CardContent className="pt-4 pb-4 space-y-2">
              {report.zip_codes_used && (
                <p className="text-xs font-medium">
                  {report.zip_codes_used.length} market{report.zip_codes_used.length !== 1 ? 's' : ''} detected
                  <span className="text-muted-foreground font-normal"> ({report.auto_derived_zip_count ?? 0} auto, {report.zip_codes_used.length - (report.auto_derived_zip_count ?? 0)} from Settings) — </span>
                  <span className="font-mono text-muted-foreground">{report.zip_codes_used.join(', ')}</span>
                </p>
              )}
              <p className="text-xs font-medium">
                {report.selected_lineups.length} lineup{report.selected_lineups.length !== 1 ? 's' : ''} needed
                <span className="text-muted-foreground font-normal"> (out of {report.candidates_tried} candidates checked)</span>
              </p>
              <div className="space-y-1">
                {report.selected_lineups.map(lu => (
                  <div key={lu.listings_id} className="flex items-center justify-between text-xs">
                    <span className="truncate">{lu.name} <span className="text-muted-foreground font-mono">({lu.zip_code})</span></span>
                    <span className="font-mono text-muted-foreground shrink-0 ml-2">{lu.channels_covered} channels</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Detail lists */}
          <div className="space-y-2">
            <CollapsibleList
              title="Would be mapped" items={report.would_map} tone="green" render={mappingChangeLabel}
              onManualMap={handleManualMap} onClear={handleClear}
              mapPickerFor={mapPickerFor} onOpenPicker={setMapPickerFor} onClosePicker={() => setMapPickerFor(null)}
            />
            <CollapsibleList
              title="Left unchanged — already has a different mapping in Emby"
              items={report.left_unchanged}
              tone="yellow"
              render={mappingChangeLabel}
              onManualMap={handleManualMap} onClear={handleClear}
              mapPickerFor={mapPickerFor} onOpenPicker={setMapPickerFor} onClosePicker={() => setMapPickerFor(null)}
            />
            <CollapsibleList
              title="No lineup covers it — try more ZIP codes" items={report.no_lineup_coverage} tone="yellow"
              onManualMap={handleManualMap} onClear={handleClear}
              mapPickerFor={mapPickerFor} onOpenPicker={setMapPickerFor} onClosePicker={() => setMapPickerFor(null)}
            />
            <CollapsibleList
              title="Not found in Emby's channel scan"
              items={report.no_emby_match}
              tone="red"
              render={() => 'check tuner'}
            />
          </div>
          <p className="text-[10px] text-muted-foreground px-1">
            Manual overrides (search icon) and clears (trash icon) apply immediately to Emby — they don't wait for Push. The lists above refresh automatically afterward.
          </p>
          {manualActionError && (
            <p className="text-xs text-destructive flex items-center gap-1 px-1">
              <AlertCircle size={12} className="shrink-0" /> {manualActionError}
            </p>
          )}

          {report.no_gn_id_count > 0 && (
            <p className="text-xs text-muted-foreground px-1">
              {report.no_gn_id_count.toLocaleString()} channel{report.no_gn_id_count !== 1 ? 's' : ''} skipped — no GN station ID
              matched yet. Run <span className="text-foreground font-medium">GN Matcher</span> first to include them here.
            </p>
          )}
          {report.excluded_count > 0 && (
            <p className="text-xs text-muted-foreground px-1">
              {report.excluded_count.toLocaleString()} channel{report.excluded_count !== 1 ? 's' : ''} skipped — in an excluded channel group.
            </p>
          )}

          {/* Push */}
          <Card>
            <CardContent className="pt-4 pb-4 space-y-3">
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  className="h-8 text-xs gap-1.5"
                  disabled={report.would_map.length === 0 || pushMutation.isPending}
                  onClick={() => pushMutation.mutate()}
                >
                  {pushMutation.isPending
                    ? <><Loader2 size={12} className="animate-spin" /> Pushing…</>
                    : <><Upload size={12} /> Push {report.would_map.length.toLocaleString()} Mapping{report.would_map.length !== 1 ? 's' : ''} to Emby</>
                  }
                </Button>
                {pushMutation.isError && (
                  <span className="text-xs text-destructive flex items-center gap-1">
                    <AlertCircle size={12} /> Push failed — check the Emby connection.
                  </span>
                )}
              </div>

              {pushResult && (
                <div className="flex items-center gap-2 text-sm rounded-md px-3 py-2 border border-green-500/20 bg-green-500/10 text-green-400">
                  <CheckCircle2 size={14} className="shrink-0" />
                  Mapped {pushResult.mapped_count.toLocaleString()} channels
                  {pushResult.left_unchanged_count > 0 && `, left ${pushResult.left_unchanged_count.toLocaleString()} unchanged`}
                  {pushResult.skipped_count > 0 && `, skipped ${pushResult.skipped_count.toLocaleString()}`}
                  {pushResult.failed.length > 0 && `, ${pushResult.failed.length} failed`}
                </div>
              )}
              {pushResult && pushResult.failed.length > 0 && (
                <CollapsibleList
                  title="Failed"
                  items={pushResult.failed.map(f => ({ name: f.name, station_id: f.error ?? 'error' }))}
                  tone="red"
                />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
