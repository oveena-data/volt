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
  const [welcome, setWelcome] = useState(false)

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
        <Auth onAuthed={justRegistered => {
          if (justRegistered) setWelcome(true)
          refresh()
        }} />
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
      {welcome && (
        <WelcomeSplash name={me.display_name} onDone={() => setWelcome(false)} />
      )}
    </>
  )
}

// One-off celebration shown right after a new account is created.
const CONFETTI_COLORS = ['#2563eb', '#60a5fa', '#facc15', '#a3e635', '#f0f8fb']

function WelcomeSplash({ name, onDone }: { name: string; onDone: () => void }) {
  const [pieces] = useState(() => Array.from({ length: 60 }, (_, i) => ({
    left: Math.random() * 100,
    delay: Math.random() * 1.2,
    duration: 2.6 + Math.random() * 2,
    color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
    tilt: Math.random() * 360,
    size: 6 + Math.random() * 6,
  })))

  useEffect(() => {
    const t = setTimeout(onDone, 5200)
    return () => clearTimeout(t)
  }, [onDone])

  return (
    <div className="welcome-overlay" role="dialog" aria-label="Welcome"
      onClick={onDone}>
      <div className="confetti" aria-hidden="true">
        {pieces.map((p, i) => (
          <span key={i} style={{
            left: `${p.left}%`,
            background: p.color,
            width: p.size,
            height: p.size * 0.45,
            animationDelay: `${p.delay}s`,
            animationDuration: `${p.duration}s`,
            transform: `rotate(${p.tilt}deg)`,
          }} />
        ))}
      </div>
      <div className="welcome-card" onClick={e => e.stopPropagation()}>
        <h2>Welcome, {name}!</h2>
        <p>You just plugged yourself into the AI Prompt Injection CTF
          Challenge. VOLT has been told not to talk to strangers, and you
          look delightfully suspicious. Go make it awkward.</p>
        <button className="btn primary" onClick={onDone}>Let the sparks fly</button>
      </div>
    </div>
  )
}
