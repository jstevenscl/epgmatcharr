import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CalendarDays, ExternalLink, Flame, Hash, ListChecks, Loader2, LogOut, Moon,
  Palette, Radio, ScrollText, Search, Settings as SettingsIcon, Sun, Tv2,
} from 'lucide-react'
import { EpgWarmIndicator, LogViewer } from '@/components/app-shared'
import EPGGuide from '@/pages/EPGGuide'
import EPGMatcher from '@/pages/EPGMatcher'
import EmbySync from '@/pages/EmbySync'
import EpgGuruSearch from '@/pages/EpgGuruSearch'
import GNMatcher from '@/pages/GNMatcher'
import Login from '@/pages/Login'
import Settings from '@/pages/Settings'
import api from '@/lib/api'

export const THEMES = ['dark', 'mid', 'light', 'mono', 'warm'] as const
export type Theme = typeof THEMES[number]

export const THEME_META: Record<Theme, { label: string; icon: ReactNode }> = {
  dark:  { label: 'Dark',  icon: <Moon size={11} /> },
  mid:   { label: 'Mid',   icon: <Palette size={11} /> },
  light: { label: 'Light', icon: <Sun size={11} /> },
  mono:  { label: 'Mono',  icon: <span className="text-[10px] font-bold leading-none">M</span> },
  warm:  { label: 'Warm',  icon: <Flame size={11} /> },
}

function initTheme(): Theme {
  const saved = localStorage.getItem('epgmatcharr-theme') as Theme | null
  const t: Theme = (saved && (THEMES as readonly string[]).includes(saved)) ? saved as Theme : 'dark'
  document.documentElement.setAttribute('data-theme', t)
  return t
}

type Tab = 'matcher' | 'guide' | 'gn' | 'emby' | 'epgSearch'

interface NavItem { id: Tab; label: string; icon: ReactNode }
const NAV_ITEMS: NavItem[] = [
  { id: 'matcher',   label: 'Matcher',       icon: <ListChecks size={15} /> },
  { id: 'guide',     label: 'EPG Guide',     icon: <CalendarDays size={15} /> },
  { id: 'gn',        label: 'GN Matcher',    icon: <Hash size={15} /> },
  { id: 'epgSearch', label: 'EPG Guru Search', icon: <Search size={15} /> },
  { id: 'emby',      label: 'Emby Sync',     icon: <Radio size={15} /> },
]

function initTab(): Tab {
  const saved = localStorage.getItem('epgmatcharr-tab')
  return saved === 'matcher' || saved === 'guide' || saved === 'gn' || saved === 'emby' || saved === 'epgSearch'
    ? saved : 'matcher'
}

type AuthState = 'checking' | 'login' | 'ready'

export default function App() {
  const [showSettings, setShowSettings] = useState(false)
  const [showLogs, setShowLogs]         = useState(false)
  const [authState, setAuthState]       = useState<AuthState>('checking')
  const [theme, setThemeState]          = useState<Theme>(initTheme)
  const [activeTab, setActiveTabState]  = useState<Tab>(initTab)
  const queryClient = useQueryClient()

  function setActiveTab(t: Tab) {
    localStorage.setItem('epgmatcharr-tab', t)
    setActiveTabState(t)
  }

  function setTheme(t: Theme) {
    document.documentElement.setAttribute('data-theme', t)
    localStorage.setItem('epgmatcharr-theme', t)
    setThemeState(t)
  }

  const { data: settings, isLoading } = useQuery<{
    configured:         boolean
    dispatcharr_url:    string
    from_env:           boolean
    has_credentials:    boolean
    guide_window_hours: number
    enable_epg_guide:   boolean
  }>({
    queryKey: ['settings'],
    queryFn:  () => api.get('/settings/').then((r) => r.data),
    staleTime: 30_000,
    retry: false,
  })

  const { data: versionData } = useQuery<{ version: string }>({
    queryKey: ['version'],
    queryFn:  () => api.get('/version/').then((r) => r.data),
    staleTime: Infinity,
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

  const navItems = settings.enable_epg_guide === false ? NAV_ITEMS.filter((n) => n.id !== 'guide') : NAV_ITEMS
  const activeLabel = NAV_ITEMS.find((n) => n.id === activeTab)?.label ?? ''

  return (
    <div className="min-h-screen grid grid-cols-[232px_1fr]">
      <aside className="sticky top-0 h-screen flex flex-col border-r border-border bg-card px-2.5 py-4 overflow-y-auto">
        <div className="flex items-center gap-2 px-1.5 pb-4">
          <div className="w-7 h-7 rounded-md bg-primary/15 text-primary flex items-center justify-center shrink-0">
            <Tv2 size={16} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-bold tracking-tight leading-tight">EPGmatcharr</div>
            {versionData?.version && (
              <div className="text-[10px] text-muted-foreground font-mono truncate">v{versionData.version}</div>
            )}
          </div>
        </div>
        <nav className="flex-1 space-y-0.5">
          {navItems.map((item) => {
            const isActive = activeTab === item.id
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center gap-2.5 px-2 py-1.5 rounded-md text-[13px] font-medium transition-colors ${
                  isActive
                    ? 'bg-primary/10 text-foreground border border-primary/30'
                    : 'text-muted-foreground border border-transparent hover:text-foreground hover:bg-accent'
                }`}
              >
                <span className={isActive ? 'text-primary' : 'opacity-80'}>{item.icon}</span>
                {item.label}
              </button>
            )
          })}
        </nav>
      </aside>

      <div className="flex flex-col min-w-0">
        <header className="sticky top-0 z-10 flex items-center gap-3 px-5 py-2.5 border-b border-border bg-card">
          <span className="text-[15px] font-semibold">{activeLabel}</span>
          <EpgWarmIndicator />
          <div className="flex-1" />
          {settings.dispatcharr_url && (
            <a
              href={settings.dispatcharr_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
            >
              <ExternalLink size={11} /> Open Dispatcharr
            </a>
          )}
          <div className="flex items-center gap-0.5 rounded border border-border p-0.5">
            {(THEMES as readonly Theme[]).map((t) => {
              const meta = THEME_META[t]
              return (
                <button
                  key={t}
                  title={meta.label}
                  onClick={() => setTheme(t)}
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
            className="text-muted-foreground hover:text-foreground transition-colors p-1.5 rounded hover:bg-accent"
            title="Application logs"
            onClick={() => setShowLogs(true)}
          >
            <ScrollText size={15} />
          </button>
          <button
            className="text-muted-foreground hover:text-foreground transition-colors p-1.5 rounded hover:bg-accent"
            title="Connection settings"
            onClick={() => setShowSettings(true)}
          >
            <SettingsIcon size={15} />
          </button>
          {settings.has_credentials && (
            <button
              className="text-muted-foreground hover:text-foreground transition-colors p-1.5 rounded hover:bg-accent"
              title="Sign out"
              onClick={handleLogout}
            >
              <LogOut size={15} />
            </button>
          )}
        </header>
        <main className="flex-1 min-w-0 p-4">
          {activeTab === 'matcher'   && <EPGMatcher />}
          {activeTab === 'guide'     && <EPGGuide guideWindowHours={settings.guide_window_hours ?? 2} />}
          {activeTab === 'gn'        && <GNMatcher />}
          {activeTab === 'epgSearch' && <EpgGuruSearch />}
          {activeTab === 'emby'      && <EmbySync />}
        </main>
      </div>

      {showLogs && <LogViewer onClose={() => setShowLogs(false)} />}
    </div>
  )
}
