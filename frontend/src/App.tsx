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

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [checked, setChecked] = useState(false)
  const [view, setView] = useState<View>('play')

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

  if (!checked) return null
  if (!me) return <Auth onAuthed={refresh} />

  const logout = async () => {
    try { await api.post('/api/auth/logout') } catch { /* token may be stale */ }
    setToken(null)
    setMe(null)
  }

  return (
    <div>
      <header className="topbar">
        <div className="logo">VOLT<span>⚡</span></div>
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
        <span className="who">{me.display_name}</span>
        <button className="btn small" onClick={logout}>Sign out</button>
      </header>
      {view === 'play' && <Play />}
      {view === 'leaderboard' && <Leaderboard />}
      {view === 'admin' && me.role === 'admin' && <Admin />}
    </div>
  )
}
