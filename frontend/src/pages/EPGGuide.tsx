import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Clock, Loader2, Search, Trash2, XCircle } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import api from '@/lib/api'

interface GuideChannel {
  channel_id:       number
  channel_name:     string
  channel_number:   number | null
  channel_group_id: number | null
  has_epg:          boolean
  epg_data_id:      number | null
  tvg_id:           string | null
  stream_count:     number | null
}

interface AssignedEpgSource { id: number; name: string; epg_data_ids: number[] }
interface ChannelGroup       { id: number; name: string }

function NowPlayingCell({ epgDataId, tvgId }: { epgDataId: number; tvgId?: string | null }) {
  const [load, setLoad] = useState(false)
  const { data, isFetching } = useQuery({
    queryKey: ['guide-now-playing', epgDataId],
    queryFn:  () => api.post('/now-playing/', { epg_data_id: epgDataId, tvg_id: tvgId ?? null, source_ids: [] }).then((r) => r.data),
    enabled:  load,
    staleTime: 120_000,
    retry: false,
  })
  if (!load) {
    return (
      <button className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1" onClick={() => setLoad(true)}>
        <Clock size={10} /> Now playing
      </button>
    )
  }
  if (isFetching) return <Loader2 size={10} className="animate-spin text-muted-foreground" />
  if (!data)      return <span className="text-xs text-muted-foreground italic">No data</span>
  return (
    <span className="text-xs text-foreground/70 flex items-center gap-1" title={data.description || undefined}>
      <Clock size={10} className="shrink-0" />
      {data.upcoming && <span className="opacity-75">Up next:</span>}
      <span className="truncate">{data.title}</span>
      {data.start && (
        <span className="shrink-0 opacity-60">
          {new Date(data.start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      )}
    </span>
  )
}

export default function EPGGuide({ onChannelDeleted }: { onChannelDeleted?: () => void }) {
  const [nameSearch,    setNameSearch]    = useState('')
  const [filterStatus,  setFilterStatus]  = useState<'all' | 'assigned' | 'unassigned'>('all')
  const [deletingId,    setDeletingId]    = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)

  const { data: channelData, isLoading, refetch } = useQuery({
    queryKey: ['guide-channels'],
    queryFn:  () => api.get('/channels/', { params: {} }).then((r) => r.data),
    staleTime: 30_000,
  })

  const { data: assignedSources } = useQuery<AssignedEpgSource[]>({
    queryKey: ['assigned-epg-sources'],
    queryFn:  () => api.get('/assigned-epg-sources/').then((r) => r.data),
    staleTime: 300_000,
  })

  const { data: groups } = useQuery<ChannelGroup[]>({
    queryKey: ['channel-groups'],
    queryFn:  () => api.get('/groups/').then((r) => r.data),
    staleTime: 60_000,
  })

  const epgSourceName = (epgDataId: number | null): string | null => {
    if (!epgDataId || !assignedSources) return null
    return assignedSources.find((s) => s.epg_data_ids.includes(epgDataId))?.name ?? null
  }

  const groupMap = Object.fromEntries((groups ?? []).map((g) => [g.id, g.name]))

  const channels: GuideChannel[] = channelData?.results ?? []

  const displayed = channels.filter((c) => {
    if (nameSearch && !c.channel_name.toLowerCase().includes(nameSearch.toLowerCase())) return false
    if (filterStatus === 'assigned'   && !c.has_epg) return false
    if (filterStatus === 'unassigned' &&  c.has_epg) return false
    return true
  })

  async function handleDelete(channelId: number) {
    setDeletingId(channelId)
    try {
      await api.delete(`/channels/${channelId}/`)
      refetch()
      onChannelDeleted?.()
    } finally {
      setDeletingId(null)
      setConfirmDelete(null)
    }
  }

  const noEpgCount = channels.filter((c) => !c.has_epg).length

  return (
    <div className="space-y-3">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-8 pl-6 text-xs w-48"
            placeholder="Filter by name…"
            value={nameSearch}
            onChange={(e) => setNameSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-1">
          {(['all', 'assigned', 'unassigned'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className={`h-8 px-3 text-xs rounded border transition-colors ${
                filterStatus === s
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-background border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              {s === 'all' ? 'All' : s === 'assigned' ? 'Has EPG' : 'No EPG'}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted-foreground ml-auto">
          {displayed.length} channels
          {noEpgCount > 0 && <span className="text-yellow-400 ml-1">· {noEpgCount} without EPG</span>}
        </span>
        <Button size="sm" variant="outline" className="h-8 text-xs gap-1.5" onClick={() => refetch()}>
          Reload
        </Button>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-muted-foreground gap-2 text-sm">
          <Loader2 size={16} className="animate-spin" /> Loading guide…
        </div>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden">
          {/* Header */}
          <div className="grid grid-cols-[56px_minmax(0,1.4fr)_130px_110px_minmax(0,1.4fr)_minmax(0,1.4fr)_40px] gap-0 border-b border-border bg-accent/30 text-xs text-muted-foreground font-medium">
            <div className="px-2 py-2">#</div>
            <div className="px-2 py-2">Channel</div>
            <div className="px-2 py-2">Group</div>
            <div className="px-2 py-2">EPG Status</div>
            <div className="px-2 py-2">EPG Source</div>
            <div className="px-2 py-2">Now Playing</div>
            <div className="px-2 py-2" />
          </div>

          <div className="overflow-y-auto" style={{ maxHeight: 'calc(100vh - 320px)', minHeight: '240px' }}>
            {displayed.length === 0 ? (
              <div className="text-center py-10 text-sm text-muted-foreground">No channels found</div>
            ) : displayed.map((ch) => (
              <div
                key={ch.channel_id}
                className="grid grid-cols-[56px_minmax(0,1.4fr)_130px_110px_minmax(0,1.4fr)_minmax(0,1.4fr)_40px] gap-0 border-b border-border last:border-0 hover:bg-accent/20 transition-colors text-sm"
              >
                <div className="px-2 py-2.5 text-xs text-muted-foreground">{ch.channel_number ?? '—'}</div>

                <div className="px-2 py-2.5 min-w-0">
                  <p className="font-medium truncate">{ch.channel_name}</p>
                  {ch.tvg_id && <p className="text-xs text-muted-foreground truncate">{ch.tvg_id}</p>}
                </div>

                <div className="px-2 py-2.5 text-xs text-muted-foreground truncate">
                  {ch.channel_group_id ? (groupMap[ch.channel_group_id] ?? '—') : '—'}
                </div>

                <div className="px-2 py-2.5">
                  {ch.has_epg ? (
                    <span className="flex items-center gap-1 text-xs text-green-400">
                      <CheckCircle2 size={11} /> Assigned
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <XCircle size={11} /> No EPG
                    </span>
                  )}
                </div>

                <div className="px-2 py-2.5 text-xs text-muted-foreground truncate">
                  {ch.epg_data_id ? (epgSourceName(ch.epg_data_id) ?? '—') : '—'}
                </div>

                <div className="px-2 py-2.5 min-w-0">
                  {ch.has_epg && ch.epg_data_id ? (
                    <NowPlayingCell epgDataId={ch.epg_data_id} tvgId={ch.tvg_id} />
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </div>

                <div className="px-1 py-2.5 flex items-center justify-center">
                  {confirmDelete === ch.channel_id ? (
                    <div className="flex items-center gap-1">
                      <button
                        className="text-[10px] text-destructive hover:text-red-400 font-medium"
                        disabled={deletingId === ch.channel_id}
                        onClick={() => handleDelete(ch.channel_id)}
                      >
                        {deletingId === ch.channel_id ? <Loader2 size={10} className="animate-spin" /> : 'Yes'}
                      </button>
                      <button
                        className="text-[10px] text-muted-foreground hover:text-foreground"
                        onClick={() => setConfirmDelete(null)}
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-destructive"
                      title="Delete channel from Dispatcharr"
                      onClick={() => setConfirmDelete(ch.channel_id)}
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
