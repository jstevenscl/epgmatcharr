import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import EPGMatcher from '@/pages/EPGMatcher'
import Login from '@/pages/Login'
import Settings from '@/pages/Settings'
import api from '@/lib/api'

export const THEMES = ['dark', 'mid', 'light', 'mono'] as const
export type Theme = typeof THEMES[number]

function initTheme(): Theme {
  const saved = localStorage.getItem('epgmatcharr-theme') as Theme | null
  const t: Theme = (saved && (THEMES as readonly string[]).includes(saved)) ? saved as Theme : 'dark'
  document.documentElement.setAttribute('data-theme', t)
  return t
}

type AuthState = 'checking' | 'login' | 'ready'

export default function App() {
  const [showSettings, setShowSettings] = useState(false)
  const [authState, setAuthState]       = useState<AuthState>('checking')
  const [theme, setThemeState]          = useState<Theme>(initTheme)
  const queryClient = useQueryClient()

  function setTheme(t: Theme) {
    document.documentElement.setAttribute('data-theme', t)
    localStorage.setItem('epgmatcharr-theme', t)
    setThemeState(t)
  }

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn:  () => api.get('/settings/').then((r) => r.data),
    staleTime: 30_000,
    retry: false,
  })

  useEffect(() => {
    if (isLoading) return
    if (!settings?.has_credentials) {
      setAuthState('ready')
      return
    }
    const token = localStorage.getItem('epgmatcharr-session')
    if (!token) { setAuthState('login'); return }
    api.get('/auth/verify/')
      .then((r) => setAuthState(r.data.valid ? 'ready' : 'login'))
      .catch(() => setAuthState('login'))
  }, [isLoading, settings?.has_credentials, settings?.configured])

  function handleLogin() {
    setAuthState('ready')
  }

  function handleLogout() {
    api.post('/auth/logout/').finally(() => {
      localStorage.removeItem('epgmatcharr-session')
      setAuthState('login')
    })
  }

  function handleSettingsSaved() {
    queryClient.invalidateQueries({ queryKey: ['settings'] })
    queryClient.invalidateQueries({ queryKey: ['config'] })
    setShowSettings(false)
  }

  if (isLoading || authState === 'checking') {
    return (
      <div className="flex items-center justify-center min-h-screen text-muted-foreground gap-2">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-sm">Loading…</span>
      </div>
    )
  }

  if (!settings?.configured || showSettings) {
    return (
      <Settings
        firstRun={!settings?.configured}
        fromEnv={settings?.from_env}
        currentUrl={settings?.dispatcharr_url}
        hasCredentials={settings?.has_credentials ?? false}
        onSaved={handleSettingsSaved}
        onBack={settings?.configured ? () => setShowSettings(false) : undefined}
      />
    )
  }

  if (authState === 'login') {
    return <Login onLogin={handleLogin} />
  }

  return (
    <EPGMatcher
      onOpenSettings={() => setShowSettings(true)}
      onLogout={settings?.has_credentials ? handleLogout : undefined}
      theme={theme}
      onSetTheme={setTheme}
    />
  )
}
