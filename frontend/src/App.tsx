import { useCallback, useEffect, useState } from 'react'
import { api, hasToken, setToken } from './api'
import Admin from './components/Admin'
import Auth from './components/Auth'
import Leaderboard from './components/Leaderboard'
import Play from './components/Play'

interface Me {
  id: string
  email: string
  display_name: string
  role: string
}

type View = 'play' | 'leaderboard' | 'admin'
type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('volt_theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* storage unavailable */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark' : 'light'
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [checked, setChecked] = useState(false)
  const [view, setView] = useState<View>('play')
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('volt_theme', theme) } catch { /* ok */ }
  }, [theme])

  const refresh = useCallback(async () => {
    if (!hasToken()) { setMe(null); setChecked(true); return }
    try {
      setMe(await api.get('/api/auth/me'))
    } catch {
      setToken(null)
      setMe(null)
    }
    setChecked(true)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const themeButton = (
    <button className="btn small" aria-label="Switch colour theme"
      onClick={() => setTheme(t => (t === 'light' ? 'dark' : 'light'))}>
      {theme === 'light' ? 'Dark mode' : 'Light mode'}
    </button>
  )

  if (!checked) return null
  if (!me) {
    return (
      <>
        <div style={{ display: 'flex', justifyContent: 'flex-end', padding: 12 }}>
          {themeButton}
        </div>
        <Auth onAuthed={refresh} />
      </>
    )
  }

  const logout = async () => {
    try { await api.post('/api/auth/logout') } catch { /* token may be stale */ }
    setToken(null)
    setMe(null)
  }

  return (
    <>
      <header className="topbar">
        <div className="logo">VOLT</div>
        <nav aria-label="Main">
          <button className={view === 'play' ? 'active' : ''}
            onClick={() => setView('play')}>Play</button>
          <button className={view === 'leaderboard' ? 'active' : ''}
            onClick={() => setView('leaderboard')}>Leaderboard</button>
          {me.role === 'admin' && (
            <button className={view === 'admin' ? 'active' : ''}
              onClick={() => setView('admin')}>Admin</button>
          )}
        </nav>
        <div className="spacer" />
        {themeButton}
        <span className="who">{me.display_name}</span>
        <button className="btn small" onClick={logout}>Sign out</button>
      </header>
      {view === 'play' && <Play />}
      {view === 'leaderboard' && <Leaderboard />}
      {view === 'admin' && me.role === 'admin' && <Admin />}
    </>
  )
}
