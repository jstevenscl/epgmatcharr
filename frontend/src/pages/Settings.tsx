import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, ArrowLeft, CheckCircle2, Database, ExternalLink, KeyRound, Loader2, LogOut, RefreshCw, Settings as SettingsIcon, Tv2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import api from '@/lib/api'

interface Props {
  firstRun:       boolean
  fromEnv?:       boolean
  currentUrl?:    string
  hasCredentials: boolean
  onSaved:        () => void
  onBack?:        () => void
}

export default function Settings({ firstRun, fromEnv, currentUrl, hasCredentials, onSaved, onBack }: Props) {
  const queryClient = useQueryClient()
  const [url,   setUrl]   = useState(currentUrl ?? '')
  const [token, setToken] = useState('')
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)

  const [ttl,              setTtl]              = useState<string>('')
  const [windowBefore,     setWindowBefore]     = useState<string>('')
  const [windowAfter,      setWindowAfter]      = useState<string>('')
  const [guideWindowHours,  setGuideWindowHours]  = useState<string>('')
  const [backfillGnId,    setBackfillGnId]    = useState<boolean | null>(null)
  const [backfillTvgId,   setBackfillTvgId]   = useState<boolean | null>(null)
  const [enableEpgGuide,  setEnableEpgGuide]  = useState<boolean | null>(null)
  const [epgSaved,          setEpgSaved]          = useState(false)
  const [repullDone,        setRepullDone]        = useState(false)

  const [credUsername, setCredUsername]   = useState('')
  const [credPassword, setCredPassword]   = useState('')
  const [credConfirm,  setCredConfirm]    = useState('')
  const [credSaved,    setCredSaved]      = useState(false)
  const [credError,    setCredError]      = useState<string | null>(null)

  // Load current EPG settings
  useQuery({
    queryKey: ['settings'],
    queryFn:  () => api.get('/settings/').then((r) => r.data),
    staleTime: 30_000,
    retry: false,
    select: (data) => {
      if (ttl === '')               setTtl(String(data.epg_cache_ttl_hours     ?? 1))
      if (windowBefore === '')      setWindowBefore(String(data.epg_window_hours_before ?? 0.5))
      if (windowAfter === '')       setWindowAfter(String(data.epg_window_hours_after  ?? 3))
      if (guideWindowHours === '')  setGuideWindowHours(String(data.guide_window_hours ?? 2))
      if (backfillGnId === null)    setBackfillGnId(data.backfill_gn_id ?? false)
      if (backfillTvgId === null)   setBackfillTvgId(data.backfill_tvg_id ?? false)
      if (enableEpgGuide === null)  setEnableEpgGuide(data.enable_epg_guide ?? true)
      return data
    },
  })

  const testMutation = useMutation({
    mutationFn: () =>
      api.post('/settings/test/', { dispatcharr_url: url.trim(), dispatcharr_token: token.trim() })
        .then((r) => r.data),
    onSuccess: (data) => setTestResult(data),
    onError: () => setTestResult({ ok: false, message: 'Request failed — is EPGmatcharr running?' }),
  })

  const saveMutation = useMutation({
    mutationFn: () =>
      api.post('/settings/', { dispatcharr_url: url.trim(), dispatcharr_token: token.trim() })
        .then((r) => r.data),
    onSuccess: () => onSaved(),
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setTestResult({ ok: false, message: msg ?? 'Save failed' })
    },
  })

  const epgMutation = useMutation({
    mutationFn: () =>
      api.post('/settings/epg/', {
        epg_cache_ttl_hours:     parseFloat(ttl)              || 1,
        epg_window_hours_before: parseFloat(windowBefore)     || 0.5,
        epg_window_hours_after:  parseFloat(windowAfter)      || 3,
        guide_window_hours:      parseFloat(guideWindowHours) || 2,
        backfill_gn_id:      backfillGnId ?? false,
        backfill_tvg_id:     backfillTvgId ?? false,
        enable_epg_guide:    enableEpgGuide ?? true,
      }).then((r) => r.data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['settings'] }); setEpgSaved(true); setTimeout(() => setEpgSaved(false), 3000) },
  })

  const repullMutation = useMutation({
    mutationFn: () => api.post('/epg/repull/').then((r) => r.data),
    onSuccess: () => { setRepullDone(true); setTimeout(() => setRepullDone(false), 4000) },
  })

  const { data: gnDbStatus, refetch: refetchGnDb } = useQuery({
    queryKey:  ['gn-station-db-status'],
    queryFn:   () => api.get('/gn-station-db/status/').then((r) => r.data),
    refetchInterval: (query) => query.state.data?.updating ? 3000 : false,
  })

  const gnDbUpdateMutation = useMutation({
    mutationFn: () => api.post('/gn-station-db/update/').then((r) => r.data),
    onSuccess: () => { setTimeout(() => refetchGnDb(), 1000) },
  })


  const credMutation = useMutation({
    mutationFn: () =>
      api.post('/settings/credentials/', { username: credUsername.trim(), password: credPassword })
        .then((r) => r.data),
    onSuccess: () => {
      setCredSaved(true)
      setCredPassword('')
      setCredConfirm('')
      setTimeout(() => setCredSaved(false), 4000)
      onSaved()
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setCredError(msg ?? 'Failed to save credentials')
    },
  })

  function handleCredSave() {
    setCredError(null)
    if (!credUsername.trim()) { setCredError('Username is required.'); return }
    if (credPassword.length < 6) { setCredError('Password must be at least 6 characters.'); return }
    if (credPassword !== credConfirm) { setCredError('Passwords do not match.'); return }
    credMutation.mutate()
  }

  const disconnectMutation = useMutation({
    mutationFn: () => api.post('/settings/disconnect/').then((r) => r.data),
    onSuccess: () => onSaved(),
  })

  const canTest = url.trim().length > 0 && token.trim().length > 0
  const canSave = canTest && testResult?.ok === true

  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-background">
      <div className="w-full max-w-md space-y-6">

        {/* Header */}
        <div className="text-center space-y-2">
          <div className="flex items-center justify-center gap-2">
            <Tv2 size={28} className="text-primary" />
            <h1 className="text-2xl font-semibold">EPGmatcharr</h1>
          </div>
          {firstRun ? (
            <p className="text-sm text-muted-foreground">
              Connect to your Dispatcharr instance to get started.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground flex items-center justify-center gap-1.5">
              <SettingsIcon size={13} /> Connection settings
            </p>
          )}
        </div>

        {/* Env var notice */}
        {fromEnv && (
          <div className="flex items-start gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-400">
            <AlertCircle size={15} className="shrink-0 mt-0.5" />
            <span>
              Connection is configured via environment variables and cannot be changed here.
              {currentUrl && <><br /><span className="text-yellow-300/70 font-mono text-xs">{currentUrl}</span></>}
            </span>
          </div>
        )}

        {/* Connection form */}
        {!fromEnv && (
          <Card>
            <CardContent className="pt-6 space-y-4">

              <div className="space-y-1.5">
                <label className="text-sm font-medium">Dispatcharr URL</label>
                <Input
                  type="url"
                  placeholder="http://192.168.1.100:4888"
                  value={url}
                  onChange={(e) => { setUrl(e.target.value); setTestResult(null) }}
                  className="font-mono text-sm"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-sm font-medium">API Token</label>
                <Input
                  type="password"
                  placeholder="Paste your Dispatcharr API token"
                  value={token}
                  onChange={(e) => { setToken(e.target.value); setTestResult(null) }}
                  className="font-mono text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  Find this in Dispatcharr under{' '}
                  <span className="text-foreground font-medium">Settings → API Keys</span>.
                </p>
              </div>

              {testResult && (
                <div className={`flex items-center gap-2 text-sm rounded-md px-3 py-2 border ${
                  testResult.ok
                    ? 'text-green-400 bg-green-500/10 border-green-500/20'
                    : 'text-red-400 bg-red-500/10 border-red-500/20'
                }`}>
                  {testResult.ok
                    ? <CheckCircle2 size={14} className="shrink-0" />
                    : <AlertCircle size={14} className="shrink-0" />
                  }
                  {testResult.message}
                </div>
              )}

              <div className="flex items-center gap-2 pt-1">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!canTest || testMutation.isPending}
                  onClick={() => testMutation.mutate()}
                  className="gap-1.5"
                >
                  {testMutation.isPending
                    ? <><Loader2 size={13} className="animate-spin" /> Testing…</>
                    : 'Test Connection'
                  }
                </Button>

                <Button
                  size="sm"
                  disabled={!canSave || saveMutation.isPending}
                  onClick={() => saveMutation.mutate()}
                  className="gap-1.5 ml-auto"
                >
                  {saveMutation.isPending
                    ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
                    : firstRun ? 'Connect' : 'Save'
                  }
                </Button>
              </div>

              {!testResult?.ok && canTest && (
                <p className="text-xs text-muted-foreground text-center">
                  Test the connection first, then save.
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {/* EPG Cache settings */}
        <Card>
          <CardContent className="pt-6 space-y-4">
            <div>
              <h2 className="text-sm font-semibold">EPG Cache</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Controls how long XMLTV data is cached and how wide a window of programmes to keep.
              </p>
            </div>

            <div className="grid grid-cols-4 gap-3">
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Cache TTL (hrs)</label>
                <Input
                  type="number"
                  min="0.25"
                  max="24"
                  step="0.25"
                  value={ttl}
                  onChange={(e) => setTtl(e.target.value)}
                  className="text-sm"
                />
                <p className="text-[10px] text-muted-foreground">Default: 1</p>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Window before (hrs)</label>
                <Input
                  type="number"
                  min="0"
                  max="6"
                  step="0.25"
                  value={windowBefore}
                  onChange={(e) => setWindowBefore(e.target.value)}
                  className="text-sm"
                />
                <p className="text-[10px] text-muted-foreground">Default: 0.5</p>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Window after (hrs)</label>
                <Input
                  type="number"
                  min="0.5"
                  max="24"
                  step="0.5"
                  value={windowAfter}
                  onChange={(e) => setWindowAfter(e.target.value)}
                  className="text-sm"
                />
                <p className="text-[10px] text-muted-foreground">Default: 3</p>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Guide window (hrs)</label>
                <Input
                  type="number"
                  min="0.5"
                  max="12"
                  step="0.5"
                  value={guideWindowHours}
                  onChange={(e) => setGuideWindowHours(e.target.value)}
                  className="text-sm"
                />
                <p className="text-[10px] text-muted-foreground">Default: 2</p>
              </div>
            </div>

            <label className="flex items-start gap-2.5 cursor-pointer select-none group">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={backfillGnId ?? false}
                onChange={(e) => setBackfillGnId(e.target.checked)}
              />
              <span>
                <span className="text-xs font-medium">Backfill GN IDs on commit</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">
                  When committing EPG assignments, write the matched EPG entry's GN station ID
                  back to any channel that doesn't already have one set in Dispatcharr.
                </span>
              </span>
            </label>

            <label className="flex items-start gap-2.5 cursor-pointer select-none group">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={backfillTvgId ?? false}
                onChange={(e) => setBackfillTvgId(e.target.checked)}
              />
              <span>
                <span className="text-xs font-medium">Backfill tvg-id on commit</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">
                  Write the matched EPG entry's tvg-id back to any channel that has no tvg-id set.
                  Use this to convert call-sign channels to Gracenote station ID format.
                </span>
              </span>
            </label>

            <label className="flex items-start gap-2.5 cursor-pointer select-none group">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={enableEpgGuide ?? true}
                onChange={(e) => setEnableEpgGuide(e.target.checked)}
              />
              <span>
                <span className="text-xs font-medium">Enable EPG Guide</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">
                  Show the EPG Guide tab with live programme data. Disable for a lighter experience
                  if you only need channel matching.
                </span>
              </span>
            </label>

            <div className="flex items-center gap-2 flex-wrap">
              <Button
                size="sm"
                variant="outline"
                disabled={epgMutation.isPending}
                onClick={() => epgMutation.mutate()}
                className="gap-1.5"
              >
                {epgMutation.isPending
                  ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
                  : 'Save EPG Settings'
                }
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={repullMutation.isPending}
                onClick={() => repullMutation.mutate()}
                className="gap-1.5"
                title="Clear the XMLTV cache and force a fresh download from all sources"
              >
                {repullMutation.isPending
                  ? <><Loader2 size={13} className="animate-spin" /> Pulling…</>
                  : 'Repull EPG Sources'
                }
              </Button>
              {epgSaved && (
                <span className="text-xs text-green-400 flex items-center gap-1">
                  <CheckCircle2 size={12} /> Saved
                </span>
              )}
              {repullDone && (
                <span className="text-xs text-green-400 flex items-center gap-1">
                  <CheckCircle2 size={12} /> Repull started
                </span>
              )}
            </div>
          </CardContent>
        </Card>

        {/* GN Station DB */}
        <Card>
          <CardContent className="pt-6 space-y-4">
            <div>
              <h2 className="text-sm font-semibold flex items-center gap-1.5">
                <Database size={13} className="text-primary" />
                GN Station DB
              </h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Local database of GN station IDs. Updated weekly — download the latest build to get new stations.
                Use the <span className="text-foreground font-medium">GN Matcher</span> tab to fill and manage GN IDs per channel.
              </p>
            </div>

            {gnDbStatus?.available ? (
              <div className="text-xs text-muted-foreground space-y-0.5">
                <div><span className="text-foreground font-medium">{gnDbStatus.count?.toLocaleString()}</span> stations loaded</div>
                {gnDbStatus.version && <div>Version: <span className="font-mono">{gnDbStatus.version}</span></div>}
                {gnDbStatus.built_at && (
                  <div>Built: {new Date(gnDbStatus.built_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</div>
                )}
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-3 py-2.5 text-xs text-yellow-400">
                <AlertCircle size={13} className="shrink-0 mt-0.5" />
                <span>No GN Station DB found. Download the latest build to enable GN ID backfill.</span>
              </div>
            )}

            {gnDbStatus?.updating && (
              <div className="text-xs text-muted-foreground flex items-center gap-1.5">
                <Loader2 size={12} className="animate-spin" />
                {gnDbStatus.progress || 'Updating…'}
              </div>
            )}

            {gnDbStatus?.error && (
              <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2">
                <AlertCircle size={12} className="shrink-0" /> {gnDbStatus.error}
              </div>
            )}

            <Button
              size="sm"
              variant="outline"
              disabled={gnDbUpdateMutation.isPending || gnDbStatus?.updating}
              onClick={() => gnDbUpdateMutation.mutate()}
              className="gap-1.5"
            >
              {gnDbStatus?.updating
                ? <><Loader2 size={13} className="animate-spin" /> Updating…</>
                : <><RefreshCw size={13} /> {gnDbStatus?.available ? 'Update GN Station DB' : 'Download GN Station DB'}</>
              }
            </Button>
          </CardContent>
        </Card>

        {/* Login credentials */}
        <Card>
          <CardContent className="pt-6 space-y-4">
            <div>
              <h2 className="text-sm font-semibold flex items-center gap-1.5">
                <KeyRound size={13} className="text-primary" />
                {hasCredentials ? 'Change Login Credentials' : 'Set Up Login'}
              </h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                {hasCredentials
                  ? 'Update the username and password used to sign in.'
                  : 'Protect EPGmatcharr with a username and password. Required before you can use the app.'}
              </p>
            </div>

            {!hasCredentials && (
              <div className="flex items-start gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-3 py-2.5 text-xs text-yellow-400">
                <AlertCircle size={13} className="shrink-0 mt-0.5" />
                <span>No login credentials set. Set them below to enable authentication.</span>
              </div>
            )}

            <div className="space-y-1.5">
              <label className="text-sm font-medium">Username</label>
              <Input
                autoComplete="username"
                value={credUsername}
                onChange={(e) => { setCredUsername(e.target.value); setCredError(null) }}
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium">{hasCredentials ? 'New password' : 'Password'}</label>
              <Input
                type="password"
                autoComplete="new-password"
                value={credPassword}
                onChange={(e) => { setCredPassword(e.target.value); setCredError(null) }}
              />
              <p className="text-[10px] text-muted-foreground">Minimum 6 characters</p>
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium">Confirm password</label>
              <Input
                type="password"
                autoComplete="new-password"
                value={credConfirm}
                onChange={(e) => { setCredConfirm(e.target.value); setCredError(null) }}
                onKeyDown={(e) => e.key === 'Enter' && handleCredSave()}
              />
            </div>

            {credError && (
              <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2">
                <AlertCircle size={14} className="shrink-0" /> {credError}
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={credMutation.isPending || !credUsername.trim() || !credPassword || !credConfirm}
                onClick={handleCredSave}
                className="gap-1.5"
              >
                {credMutation.isPending
                  ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
                  : hasCredentials ? 'Update Credentials' : 'Set Credentials'
                }
              </Button>
              {credSaved && (
                <span className="text-xs text-green-400 flex items-center gap-1">
                  <CheckCircle2 size={12} /> Saved
                </span>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Back link */}
        {onBack && (
          <div className="text-center">
            <button
              className="text-sm text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1.5 mx-auto"
              onClick={onBack}
            >
              <ArrowLeft size={13} /> Back
            </button>
          </div>
        )}

        {/* Disconnect */}
        {!firstRun && !fromEnv && (
          <div className="text-center pt-2 border-t border-border">
            <button
              className="text-xs text-muted-foreground hover:text-destructive transition-colors flex items-center gap-1.5 mx-auto disabled:opacity-50"
              disabled={disconnectMutation.isPending}
              onClick={() => disconnectMutation.mutate()}
            >
              {disconnectMutation.isPending
                ? <><Loader2 size={11} className="animate-spin" /> Disconnecting…</>
                : <><LogOut size={11} /> Disconnect from Dispatcharr</>
              }
            </button>
            <p className="text-[10px] text-muted-foreground mt-1">
              Clears the saved URL and token. You will be taken back to setup.
            </p>
          </div>
        )}

        {/* Dispatcharr link */}
        {url && !fromEnv && (
          <div className="text-center">
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
            >
              <ExternalLink size={11} /> Open Dispatcharr
            </a>
          </div>
        )}
      </div>
    </div>
  )
}
