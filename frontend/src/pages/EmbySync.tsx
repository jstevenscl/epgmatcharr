import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Loader2, Radio, RefreshCw, Settings as SettingsIcon, Upload } from 'lucide-react'
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

interface CoverageItem { name: string; station_id: string }
interface SelectedLineup { listings_id: string; name: string; zip_code: string; channels_covered: number }

interface PreviewReport {
  total_channels:      number
  would_map:            CoverageItem[]
  no_gn_id_count:       number
  no_emby_match:        CoverageItem[]
  no_lineup_coverage:   CoverageItem[]
  selected_lineups:     SelectedLineup[]
  candidates_tried:     number
  zip_codes_used:       string[]
  auto_derived_zip_count: number
}

interface PushResult {
  mapped_count:      number
  failed:            { name: string; station_id: string; error?: string }[]
  skipped_count:      number
  selected_lineups:   SelectedLineup[]
  zip_codes_used:     string[]
}

function CollapsibleList({ title, items, tone, render }: {
  title: string
  items: CoverageItem[]
  tone: 'green' | 'yellow' | 'red'
  render?: (item: CoverageItem) => string
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
            <div key={i} className="flex items-center justify-between text-xs py-0.5">
              <span className="truncate">{item.name}</span>
              <span className="font-mono text-muted-foreground shrink-0 ml-2">{render ? render(item) : item.station_id}</span>
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

  const { data: settings, isLoading: settingsLoading } = useQuery<EmbySettings>({
    queryKey: ['emby-settings'],
    queryFn:  () => api.get('/emby/settings/').then((r) => r.data),
    staleTime: 30_000,
  })

  const previewQuery = useQuery<PreviewReport>({
    queryKey: ['emby-preview'],
    queryFn:  () => api.get('/emby/preview/').then((r) => r.data),
    enabled:  false,
    retry:    false,
  })

  const pushMutation = useMutation({
    mutationFn: () => api.post('/emby/push/').then((r) => r.data as PushResult),
    onSuccess:  (data) => setPushResult(data),
  })

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
          <div className="flex items-start justify-between gap-3">
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
            <SettingsIcon size={13} className="text-muted-foreground shrink-0 mt-0.5" />
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
          <div className="grid grid-cols-4 gap-2">
            <div className="rounded-lg border border-green-500/30 bg-green-500/5 px-3 py-2.5">
              <div className="text-lg font-semibold text-green-400 tabular-nums">{report.would_map.length.toLocaleString()}</div>
              <div className="text-[10px] text-muted-foreground">Would be mapped</div>
            </div>
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
            <CollapsibleList title="Would be mapped" items={report.would_map} tone="green" />
            <CollapsibleList title="No lineup covers it — try more ZIP codes" items={report.no_lineup_coverage} tone="yellow" />
            <CollapsibleList
              title="Not found in Emby's channel scan"
              items={report.no_emby_match}
              tone="red"
              render={() => 'check tuner'}
            />
          </div>

          {report.no_gn_id_count > 0 && (
            <p className="text-xs text-muted-foreground px-1">
              {report.no_gn_id_count.toLocaleString()} channel{report.no_gn_id_count !== 1 ? 's' : ''} skipped — no GN station ID
              matched yet. Run <span className="text-foreground font-medium">GN Matcher</span> first to include them here.
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
