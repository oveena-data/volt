import { FormEvent, useState } from 'react'
import { api, ApiError, setToken } from '../api'

export default function Auth({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      const data = mode === 'login'
        ? await api.post('/api/auth/login', { email, password })
        : await api.post('/api/auth/register',
            { email, password, display_name: name })
      setToken(data.token)
      onAuthed()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="authbox">
      <h1>VOLT ⚡</h1>
      <p className="sub">
        A capture-the-flag range for learning LLM prompt injection.
      </p>
      {err && <div className="notice err" role="alert">{err}</div>}
      <form onSubmit={submit}>
        {mode === 'register' && (
          <label className="field">
            <span>Display name (shown on the leaderboard)</span>
            <input className="input" value={name} required maxLength={40}
              onChange={e => setName(e.target.value)} />
          </label>
        )}
        <label className="field">
          <span>Email</span>
          <input className="input" type="email" value={email} required
            autoComplete="email" onChange={e => setEmail(e.target.value)} />
        </label>
        <label className="field">
          <span>Password{mode === 'register' ? ' (10+ characters)' : ''}</span>
          <input className="input" type="password" value={password} required
            minLength={mode === 'register' ? 10 : 1}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            onChange={e => setPassword(e.target.value)} />
        </label>
        <button className="btn primary" style={{ width: '100%' }} disabled={busy}>
          {busy ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>
      </form>
      <p className="alt">
        {mode === 'login' ? (
          <>New here?{' '}
            <a href="#" onClick={e => { e.preventDefault(); setMode('register') }}>
              Create an account</a></>
        ) : (
          <>Already registered?{' '}
            <a href="#" onClick={e => { e.preventDefault(); setMode('login') }}>
              Sign in</a></>
        )}
      </p>
    </div>
  )
}
