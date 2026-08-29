import { useState, useRef } from 'react'
import { Search, Loader2, CheckCircle2, Circle, Copy, Check } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import api from '@/lib/api'

interface EpgGuruResult {
  tvg_id:               string
  name:                 string
  gn_id:                string | null
  country:              string | null
  market:               string
  tier:                 string
  already_configured:   boolean
  suggested_source_url: string | null
}

const TIER_LABEL: Record<string, string> = {
  '7dayiptv':      'IPTV',
  '7daygracenote': 'Gracenote',
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent shrink-0"
      title="Copy"
      onClick={() => {
        navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 1200)
      }}
    >
      {copied ? <Check size={12} className="text-primary" /> : <Copy size={12} />}
    </button>
  )
}

export default function EpgGuruSearch() {
  const [q, setQ]             = useState('')
  const [results, setResults] = useState<EpgGuruResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)
  const debounce = useRef<ReturnType<typeof setTimeout>>()

  function handleSearch(val: string) {
    setQ(val)
    if (debounce.current) clearTimeout(debounce.current)
    if (!val.trim()) { setResults(null); setError(null); return }
    setLoading(true)
    setError(null)
    debounce.current = setTimeout(async () => {
      try {
        const { data } = await api.get('/epg-guru-search/', { params: { q: val, limit: 40 } })
        setResults(data)
      } catch {
        setError('Search failed — the epg.guru cache may still be downloading on first use (can take a few minutes).')
      } finally {
        setLoading(false)
      }
    }, 350)
  }

  return (
    <div className="space-y-4 max-w-3xl">
      <Card>
        <CardContent className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Search epg.guru's full channel roster (FullGuide + USFast, both tiers) directly — independent of
            which EPG sources you actually have configured. Use this when a channel isn't matching well:
            find out whether the right tvg_id is sitting in a source you haven't added yet, or already in one
            you have, just under a different name than the matcher found.
          </p>
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-8"
              placeholder="Search by channel name or tvg_id…"
              value={q}
              onChange={(e) => handleSearch(e.target.value)}
              autoFocus
            />
            {loading && (
              <Loader2 size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 animate-spin text-muted-foreground" />
            )}
          </div>
        </CardContent>
      </Card>

      {error && <p className="text-xs text-destructive px-1">{error}</p>}

      {results && results.length === 0 && !loading && (
        <p className="text-sm text-muted-foreground px-1">
          No matches in epg.guru's FullGuide or USFast data — this channel likely doesn't exist there under
          any name close to "{q}".
        </p>
      )}

      {results && results.length > 0 && (
        <Card>
          <CardContent className="p-0 divide-y divide-border">
            {results.map((r, i) => (
              <div key={`${r.market}-${r.tier}-${r.tvg_id}-${i}`} className="flex items-center gap-3 px-4 py-2.5">
                <div className="shrink-0">
                  {r.already_configured
                    ? <CheckCircle2 size={15} className="text-primary" />
                    : <Circle size={15} className="text-muted-foreground" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium truncate">{r.name || '(no name)'}</p>
                    {r.country && (
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground shrink-0 uppercase"
                        title="Country this specific tvg_id is tagged for — the same GN id can appear under other countries too"
                      >
                        {r.country}
                      </span>
                    )}
                    <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground shrink-0">
                      {r.market} · {TIER_LABEL[r.tier] ?? r.tier}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 mt-0.5">
                    <p className="text-xs text-muted-foreground font-mono truncate">{r.tvg_id}</p>
                    <CopyButton text={r.tvg_id} />
                    {r.gn_id && <span className="text-xs text-muted-foreground shrink-0">· GN {r.gn_id}</span>}
                  </div>
                  {r.already_configured ? (
                    <p className="text-xs text-primary mt-0.5">
                      Already in a source you have configured — if this isn't matching, it's the matcher logic, not a missing source.
                    </p>
                  ) : r.suggested_source_url ? (
                    <div className="flex items-center gap-1 mt-0.5">
                      <p className="text-xs text-muted-foreground">
                        Not in your configured sources — add <span className="font-mono">{r.suggested_source_url}</span> as an EPG source to get this.
                      </p>
                      <CopyButton text={r.suggested_source_url} />
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
