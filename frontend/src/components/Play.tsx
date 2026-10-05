import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  api, ApiError, Challenge, EventInfo, Message, newMsgId, SessionState,
} from '../api'

interface PendingTurn {
  msgId: string
  text: string
  state: 'sending' | 'failed'
  error?: string
  retryable?: boolean
}

export default function Play() {
  const [events, setEvents] = useState<EventInfo[]>([])
  const [event, setEvent] = useState<EventInfo | null>(null)
  const [loadingEvents, setLoadingEvents] = useState(true)
  const [challenges, setChallenges] = useState<Challenge[]>([])
  const [session, setSession] = useState<SessionState | null>(null)
  const [pending, setPending] = useState<PendingTurn | null>(null)
  const [notice, setNotice] = useState('')
  const [extras, setExtras] = useState<any>(null)
  const [draft, setDraft] = useState('')
  const [flagGuess, setFlagGuess] = useState('')
  const [clockOffset, setClockOffset] = useState(0)
  const chatRef = useRef<HTMLDivElement>(null)

  const loadEvents = useCallback(async () => {
    try {
      const d = await api.get('/api/events')
      setEvents(d.events)
      // Prefer an event the player is already enrolled in; otherwise leave
      // selection empty so the enrol UI shows.
      setEvent(prev => {
        if (prev) return d.events.find((e: EventInfo) => e.id === prev.id) ?? prev
        return d.events.find((e: EventInfo) => e.enrolled) ?? null
      })
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Failed to load events.')
    } finally {
      setLoadingEvents(false)
    }
  }, [])

  const loadChallenges = useCallback(async (ev: EventInfo | null) => {
    if (!ev || !ev.enrolled) { setChallenges([]); return }
    try {
      const d = await api.get(`/api/events/${ev.id}`)
      setChallenges(d.challenges)
      if (d.event?.server_time) {
        setClockOffset(new Date(d.event.server_time).getTime() - Date.now())
      }
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Failed to load levels.')
    }
  }, [])

  useEffect(() => { loadEvents() }, [loadEvents])
  useEffect(() => {
    setSession(null)
    setExtras(null)
    setNotice('')
    loadChallenges(event)
  }, [event, loadChallenges])

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight })
  }, [session?.messages?.length, pending])

  const openLevel = async (c: Challenge) => {
    if (c.locked || !event) return
    setNotice('')
    setPending(null)
    setExtras(null)
    try {
      setSession(await api.post('/api/game/sessions', {
        challenge_id: c.challenge_id, mode: 'ranked', event_id: event.id,
      }))
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Could not open the level.')
    }
  }

  const refreshSession = async () => {
    if (!session) return
    try {
      setSession(await api.get(`/api/game/sessions/${session.game_session_id}`))
    } catch { /* keep current view */ }
  }

  const sendTurn = async (text: string, msgId: string) => {
    if (!session) return
    setPending({ msgId, text, state: 'sending' })
    setNotice('')
    try {
      const r = await api.post(
        `/api/game/sessions/${session.game_session_id}/message`,
        { client_msg_id: msgId, text })
      setPending(null)
      setExtras(r.extras || null)
      setSession(s => {
        if (!s) return s
        const msgs: Message[] = [...s.messages,
          { seq: s.messages.length + 1, role: 'user', text, at: '' },
          { seq: s.messages.length + 2,
            role: r.status === 'blocked' ? 'filter' : 'assistant',
            text: r.reply, at: '' }]
        return {
          ...s, messages: msgs, tokens: r.tokens, attempts: r.attempts,
          solved: r.solved || s.solved,
          solve: r.solve ? { ...r.solve, method: 'auto' } : s.solve,
        }
      })
      if (r.leaked) loadChallenges(event) // unlock the next level immediately
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 502) {
          setPending({ msgId, text, state: 'failed', retryable: true,
            error: 'Model backend unavailable. This did not count as an attempt.' })
        } else if (e.code === 'rate_limited' || e.code === 'queue_full') {
          setPending({ msgId, text, state: 'failed', retryable: true,
            error: e.message })
        } else if (e.code === 'in_progress' || e.code === 'busy') {
          setPending({ msgId, text, state: 'failed', retryable: true,
            error: 'A turn is already being processed. Retry in a moment.' })
        } else {
          setPending(null)
          setNotice(e.message)
          if (e.code === 'turn_limit') refreshSession()
        }
      } else {
        setPending({ msgId, text, state: 'failed', retryable: true,
          error: 'Network error. Retry. Duplicates are handled safely.' })
      }
    }
  }

  const submitDraft = (e: FormEvent) => {
    e.preventDefault()
    const text = draft.trim()
    if (!text || pending?.state === 'sending' || !session) return
    setDraft('')
    sendTurn(text, newMsgId())
  }

  const rotate = async (kind: 'reset' | 'new-chat') => {
    if (!session) return
    if (kind === 'reset' && !window.confirm(
      'Reset destroys this conversation and everything built up in it. ' +
      'Your solved levels and progress are kept. Continue?')) return
    try {
      setPending(null)
      setExtras(null)
      setSession(await api.post(
        `/api/game/sessions/${session.game_session_id}/${kind}`))
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Action failed.')
    }
  }

  const submitFlag = async (e: FormEvent) => {
    e.preventDefault()
    if (!session || !flagGuess.trim()) return
    try {
      const r = await api.post(
        `/api/game/sessions/${session.game_session_id}/submit`,
        { flag: flagGuess.trim() })
      if (r.correct) {
        setFlagGuess('')
        setNotice('')
        refreshSession()
        loadChallenges(event)
      } else {
        setNotice('That flag is not correct for your level.')
      }
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Submission failed.')
    }
  }

  const enroll = async (ev: EventInfo) => {
    let code: string | null = null
    if (ev.invite_only) {
      code = window.prompt('This event is invite-only. Enter your invite code:')
      if (!code) return
    }
    try {
      await api.post(`/api/events/${ev.id}/enroll`,
        code ? { invite_code: code } : {})
      await loadEvents()
      const d = await api.get('/api/events')
      const fresh = d.events.find((x: EventInfo) => x.id === ev.id)
      if (fresh) setEvent(fresh)
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Enrolment failed.')
    }
  }

  const nextPlayable = challenges.find(c => !c.solved && !c.locked && c.open_now)

  // ---- no event / not enrolled states ----
  if (loadingEvents) {
    return <div className="layout"><main className="playpane" /></div>
  }
  if (!event || !event.enrolled) {
    const joinable = events.filter(e => !e.enrolled)
    return (
      <div className="page">
        <h1>Join the game</h1>
        {notice && <div className="notice err">{notice}</div>}
        {events.length === 0 && (
          <div className="notice warn">
            No event is available yet. An organiser needs to create and open an
            event before play can begin.
          </div>
        )}
        {events.length > 0 && joinable.length === 0 && (
          <p className="dim">You are not enrolled in any open event.</p>
        )}
        <div className="cards">
          {joinable.map(ev => (
            <div className="card" key={ev.id}>
              <h3>{ev.name}</h3>
              <div className="dim">{ev.description || 'VOLT challenge event.'}</div>
              <div style={{ marginTop: 10 }}>
                {ev.registration_open ? (
                  <button className="btn primary" onClick={() => enroll(ev)}>
                    {ev.invite_only ? 'Join with invite code' : 'Join event'}
                  </button>
                ) : (
                  <span className="dim">Registration is closed.</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const current = session
    ? challenges.find(c => c.challenge_id === session.challenge_id)
    : null

  return (
    <div className="layout">
      <aside className="sidebar">
        {events.filter(e => e.enrolled).length > 1 && (
          <select className="input" style={{ marginBottom: 16 }}
            aria-label="Event" value={event.id}
            onChange={e => setEvent(events.find(x => x.id === e.target.value) || null)}>
            {events.filter(e => e.enrolled).map(ev =>
              <option key={ev.id} value={ev.id}>{ev.name}</option>)}
          </select>
        )}

        <div className="level-list" role="list">
          {challenges.map(c => {
            const isActive = session?.challenge_id === c.challenge_id
            const cls = 'level-item'
              + (isActive ? ' active' : '')
              + (c.locked ? ' locked' : '')
            return (
              <button key={c.challenge_id} role="listitem" className={cls}
                disabled={c.locked || !c.open_now}
                aria-disabled={c.locked}
                onClick={() => openLevel(c)}>
                <div className="num">
                  LEVEL {c.number}{c.locked ? ' · 🔒 locked' : ''}
                </div>
                <div className="name">{c.title}</div>
                <div className="pts">
                  {c.solved
                    ? <span className="done">✓ solved
                        {c.net_points != null ? ` · ${c.net_points} pts` : ''}</span>
                    : c.locked
                      ? <span className="dim">Solve the previous level to unlock</span>
                      : !c.open_now
                        ? 'unavailable'
                        : `${c.points} pts`}
                </div>
              </button>
            )
          })}
          {challenges.length === 0 && (
            <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
              No levels are available for this event yet.
            </p>
          )}
        </div>

        <EventTimer ev={event} offset={clockOffset} />
      </aside>

      <main className="playpane">
        {notice && <div className="notice err" role="alert">{notice}</div>}
        {!session ? (
          <div className="briefing">
            <h2>Talk to VOLT.</h2>
            <p>
              VOLT is an AI assistant guarding a secret flag on every level.
              Each level defends the flag differently. Pick a level on the left
              to begin. Level 1 is open; clearing a level unlocks the next.
            </p>
          </div>
        ) : (
          <>
            <div className="briefing">
              <h2>
                Level {current?.number}. {session.challenge.title}
                {session.solved && <span className="badge solved">solved</span>}
              </h2>
              <p className="subtitle-line">{session.challenge.subtitle}</p>
            </div>

            {session.solved && (
              <div className="solvebar">
                <span>
                  ⚡ Solved
                  {session.solve ? ` for ${session.solve.net_points} points` : ''}.
                  You can keep experimenting here.
                </span>
                {nextPlayable && nextPlayable.challenge_id !== session.challenge_id && (
                  <button className="btn primary small"
                    onClick={() => openLevel(nextPlayable)}>
                    Continue to Level {nextPlayable.number}
                  </button>
                )}
              </div>
            )}

            <div className="chat" ref={chatRef} aria-live="polite">
              {session.messages.map((m, i) => <MessageView key={i} m={m} />)}
              {pending?.state === 'sending' && (
                <>
                  <div className="msg user"><div className="bubble">{pending.text}</div></div>
                  <div className="pending" aria-label="Waiting for VOLT">
                    <span /><span /><span />
                  </div>
                </>
              )}
              {pending?.state === 'failed' && (
                <div className="turn-error" role="alert">
                  <span>{pending.error}</span>
                  {pending.retryable && (
                    <button className="btn small"
                      onClick={() => sendTurn(pending.text, pending.msgId)}>Retry</button>
                  )}
                  <button className="btn small"
                    onClick={() => setPending(null)}>Dismiss</button>
                </div>
              )}
            </div>

            {extras?.fragments?.length > 0 && (
              <div className="fragments">
                stored fragments:{' '}
                {extras.fragments.map((f: any) =>
                  <span key={f.name}><b>{f.name}</b>={f.value} </span>)}
              </div>
            )}

            <div className="composer">
              <form className="row" onSubmit={submitDraft}>
                <textarea className="input" value={draft}
                  placeholder="Message VOLT..." aria-label="Message"
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault(); submitDraft(e)
                    }
                  }} />
                <button className="btn primary"
                  disabled={pending?.state === 'sending' || !draft.trim()}>Send</button>
              </form>
              <div className="meta">
                <span title="Estimated tokens in the current conversation context">
                  {session.tokens.toLocaleString()} context tokens
                </span>
                <span>{session.attempts} attempts</span>
                {session.challenge.starter && (
                  <button className="btn small"
                    onClick={() => setDraft(session.challenge.starter || '')}>
                    Insert sample material
                  </button>
                )}
                <button className="btn small" onClick={() => rotate('new-chat')}>
                  New chat
                </button>
                <button className="btn small danger" onClick={() => rotate('reset')}>
                  Reset level
                </button>
                <form className="flagform" onSubmit={submitFlag}>
                  <input value={flagGuess} aria-label="Submit a flag"
                    placeholder="VOLT{...} submit a flag"
                    onChange={e => setFlagGuess(e.target.value)} />
                  <button className="btn small">Submit</button>
                </form>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}

function MessageView({ m }: { m: Message }) {
  const who = m.role === 'user' ? 'you' : m.role === 'filter' ? 'filter' : 'VOLT'
  // Model output is rendered strictly as text (React escapes it); it is never
  // interpreted as HTML or executed.
  return (
    <div className={`msg ${m.role}`}>
      <div className="who">{who}</div>
      <div className="bubble">{m.text}</div>
    </div>
  )
}

function EventTimer({ ev, offset }: { ev: EventInfo; offset: number }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const serverNow = now + offset
  const start = new Date(ev.starts_at).getTime()
  const end = new Date(ev.ends_at).getTime()
  let label: string
  if (serverNow < start) label = `starts in ${fmt(start - serverNow)}`
  else if (serverNow < end) label = `ends in ${fmt(end - serverNow)}`
  else label = 'event ended'
  return (
    <div className="aside-box">
      <b>{ev.name}</b>
      <div className="timer">{label}</div>
      {ev.paused && <div className="notice warn" style={{ marginTop: 8 }}>
        Gameplay is paused by the organisers.</div>}
    </div>
  )
}

function fmt(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m ${sec}s`
  return `${m}m ${sec}s`
}
