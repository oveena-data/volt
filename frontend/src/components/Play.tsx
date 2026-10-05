import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  api, ApiError, Challenge, EventInfo, Message, newMsgId, SessionState,
} from '../api'

type Scope = { kind: 'practice' } | { kind: 'event'; event: EventInfo }

interface PendingTurn {
  msgId: string
  text: string
  state: 'sending' | 'failed'
  error?: string
  retryable?: boolean
}

export default function Play() {
  const [events, setEvents] = useState<EventInfo[]>([])
  const [scope, setScope] = useState<Scope>({ kind: 'practice' })
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
    } catch { /* non-fatal */ }
  }, [])

  const loadChallenges = useCallback(async (sc: Scope) => {
    try {
      if (sc.kind === 'practice') {
        const d = await api.get('/api/practice/challenges')
        setChallenges(d.challenges)
      } else {
        const d = await api.get(`/api/events/${sc.event.id}`)
        setChallenges(d.challenges)
        if (d.event.server_time) {
          setClockOffset(new Date(d.event.server_time).getTime() - Date.now())
        }
      }
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Failed to load challenges.')
    }
  }, [])

  useEffect(() => { loadEvents() }, [loadEvents])
  useEffect(() => {
    setSession(null)
    setExtras(null)
    loadChallenges(scope)
  }, [scope, loadChallenges])

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight })
  }, [session?.messages?.length, pending])

  const openLevel = async (c: Challenge) => {
    setNotice('')
    setPending(null)
    setExtras(null)
    try {
      const body: any = {
        challenge_id: c.challenge_id,
        mode: scope.kind === 'practice' ? 'practice' : 'ranked',
      }
      if (scope.kind === 'event') body.event_id = scope.event.id
      setSession(await api.post('/api/game/sessions', body))
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
      if (r.leaked) loadChallenges(scope)
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
            error: 'A turn is already being processed — retry in a moment.' })
        } else {
          setPending(null)
          setNotice(e.message)
          if (e.code === 'turn_limit') refreshSession()
        }
      } else {
        setPending({ msgId, text, state: 'failed', retryable: true,
          error: 'Network error. Retry — duplicates are handled safely.' })
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
      'Reset destroys the conversation and all accumulated level state ' +
      '(solves and scores are kept). Continue?')) return
    try {
      setPending(null)
      setExtras(null)
      setSession(await api.post(
        `/api/game/sessions/${session.game_session_id}/${kind}`))
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Action failed.')
    }
  }

  const unlockHint = async (idx: number) => {
    if (!session) return
    const cost = session.mode === 'ranked'
      ? session.challenge.hint_costs[idx] : 0
    if (cost > 0 && !window.confirm(
      `Unlocking hint ${idx + 1} deducts ${cost} points from this ` +
      'challenge when you solve it. Continue?')) return
    try {
      await api.post(`/api/game/sessions/${session.game_session_id}/hints`,
        { hint_index: idx })
      refreshSession()
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Could not unlock hint.')
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
        loadChallenges(scope)
      } else {
        setNotice('That flag is not correct for your challenge.')
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
      if (fresh) setScope({ kind: 'event', event: fresh })
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Enrolment failed.')
    }
  }

  const current = session
    ? challenges.find(c => c.challenge_id === session.challenge_id)
    : null

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="mode-row" role="tablist" aria-label="Mode">
          <button role="tab" aria-selected={scope.kind === 'practice'}
            className={scope.kind === 'practice' ? 'active' : ''}
            onClick={() => setScope({ kind: 'practice' })}>Practice</button>
          {events.filter(e => e.enrolled).map(ev => (
            <button key={ev.id} role="tab"
              aria-selected={scope.kind === 'event' && scope.event.id === ev.id}
              className={scope.kind === 'event' && scope.event.id === ev.id
                ? 'active' : ''}
              onClick={() => setScope({ kind: 'event', event: ev })}>
              {ev.name}
            </button>
          ))}
        </div>

        <div className="level-list" role="list">
          {challenges.map(c => (
            <button key={c.challenge_id} role="listitem"
              className={'level-item' +
                (session?.challenge_id === c.challenge_id ? ' active' : '')}
              disabled={!c.available}
              onClick={() => openLevel(c)}>
              <div className="num">LEVEL {c.number}</div>
              <div className="name">{c.title}</div>
              <div className="pts">
                {c.solved
                  ? <span className="done">✓ solved
                      {c.net_points != null ? ` · ${c.net_points} pts` : ''}</span>
                  : `${c.points} pts${c.available ? '' : ' · unavailable'}`}
              </div>
            </button>
          ))}
          {challenges.length === 0 && (
            <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
              {scope.kind === 'event'
                ? 'No challenges visible for this event yet.'
                : 'No published challenges.'}
            </p>
          )}
        </div>

        {scope.kind === 'event' && (
          <EventTimer ev={scope.event} offset={clockOffset} />
        )}

        {events.filter(e => !e.enrolled).length > 0 && (
          <div className="aside-box">
            <b>Events open to you</b>
            {events.filter(e => !e.enrolled).map(ev => (
              <div key={ev.id} style={{ marginTop: 8 }}>
                {ev.name}{' '}
                <button className="btn small" onClick={() => enroll(ev)}>
                  {ev.invite_only ? 'Join (invite)' : 'Join'}
                </button>
              </div>
            ))}
          </div>
        )}
      </aside>

      <main className="playpane">
        {notice && <div className="notice err" role="alert">{notice}</div>}
        {!session ? (
          <div className="briefing">
            <h2>Welcome to the grid.</h2>
            <p>
              Each level is a small fictional app guarding a secret flag behind a
              different defence. Your job: get the model to give the flag up.
              Pick a level on the left to start.
            </p>
            <p className="lesson">
              Practice mode is a sandbox; event mode is ranked and scored.
            </p>
          </div>
        ) : (
          <>
            <div className="briefing">
              <h2>
                {session.challenge.title}
                <span className={'badge ' + session.mode}>{session.mode}</span>
                {session.solved && <span className="badge solved">solved</span>}
              </h2>
              <div className="tech">
                {session.challenge.codename} · {session.challenge.technique} ·{' '}
                {session.challenge.points} pts
              </div>
              <p>{session.challenge.briefing}</p>
              <div className="hints">
                {session.challenge.hint_costs.map((cost, idx) => {
                  const unlocked = session.unlocked_hints
                    .find(h => h.hint_index === idx)
                  const prevUnlocked = idx === 0 ||
                    session.unlocked_hints.some(h => h.hint_index === idx - 1)
                  return unlocked ? (
                    <details key={idx} open>
                      <summary>Hint {idx + 1}
                        {unlocked.cost > 0 ? ` (−${unlocked.cost} pts)` : ''}
                      </summary>
                      <p className="hint-text">{unlocked.text}</p>
                    </details>
                  ) : (
                    <div key={idx} style={{ marginTop: 6 }}>
                      <button className="btn small" disabled={!prevUnlocked}
                        onClick={() => unlockHint(idx)}>
                        Unlock hint {idx + 1}
                        {session.mode === 'ranked' && cost > 0
                          ? ` (−${cost} pts)` : ' (free)'}
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>

            {session.solved && session.solve && (
              <div className="solvebar">
                ⚡ Breached — solved {session.mode === 'ranked'
                  ? `for ${session.solve.net_points} points`
                  : 'in practice'}
                . You can keep experimenting; your solve is recorded.
              </div>
            )}

            <div className="chat" ref={chatRef} aria-live="polite">
              {session.messages.map((m, i) => (
                <MessageView key={i} m={m} />
              ))}
              {pending?.state === 'sending' && (
                <>
                  <div className="msg user">
                    <div className="bubble">{pending.text}</div>
                  </div>
                  <div className="pending" aria-label="Waiting for the model">
                    <span /><span /><span />
                  </div>
                </>
              )}
              {pending?.state === 'failed' && (
                <div className="turn-error" role="alert">
                  <span>{pending.error}</span>
                  {pending.retryable && (
                    <button className="btn small"
                      onClick={() => sendTurn(pending.text, pending.msgId)}>
                      Retry
                    </button>
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
                  placeholder={`Message ${session.challenge.codename}…`}
                  aria-label="Message"
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      submitDraft(e)
                    }
                  }} />
                <button className="btn primary"
                  disabled={pending?.state === 'sending' || !draft.trim()}>
                  Send
                </button>
              </form>
              <div className="meta">
                <span>{session.tokens.toLocaleString()} tokens</span>
                <span>{session.attempts} attempts</span>
                {session.challenge.starter && (
                  <button className="btn small"
                    onClick={() => setDraft(session.challenge.starter || '')}>
                    Insert sample payload
                  </button>
                )}
                <button className="btn small" onClick={() => rotate('new-chat')}>
                  New chat
                </button>
                <button className="btn small danger"
                  onClick={() => rotate('reset')}>
                  Reset level
                </button>
                <form className="flagform" onSubmit={submitFlag}>
                  <input value={flagGuess} aria-label="Submit a flag"
                    placeholder="VOLT{…} submit a flag"
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
  const who = m.role === 'user' ? 'you'
    : m.role === 'filter' ? 'filter' : 'model'
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
