import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  api, ApiError, Attachment, Challenge, EventInfo, Hint, McpTool, McpView,
  Message, newMsgId, SessionState, WorkflowTrace,
} from '../api'
import Postmortem from './Postmortem'
import ToolIcon from './ToolIcon'
import AgentWorkflow from './AgentWorkflow'
import voltMascot from '../assets/volt.webp'

interface PendingTurn {
  msgId: string
  text: string
  state: 'sending' | 'failed'
  error?: string
  retryable?: boolean
  attachment?: Attachment | null
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
  const [attachment, setAttachment] = useState<Attachment | null>(null)
  const [flagGuess, setFlagGuess] = useState('')
  const [pmOpen, setPmOpen] = useState(false)
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
    setPmOpen(false)
    setPending(null)
    setExtras(null)
    setAttachment(null)
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

  const sendTurn = async (text: string, msgId: string, att?: Attachment | null) => {
    if (!session) return
    setPending({ msgId, text, state: 'sending', attachment: att ?? null })
    setNotice('')
    try {
      const r = await api.post(
        `/api/game/sessions/${session.game_session_id}/message`,
        att ? { client_msg_id: msgId, text, attachment: att }
            : { client_msg_id: msgId, text })
      setPending(null)
      setExtras(r.extras || null)
      setSession(s => {
        if (!s) return s
        const msgs: Message[] = [...s.messages,
          { seq: s.messages.length + 1, role: 'user', text, at: '' },
          { seq: s.messages.length + 2,
            role: r.status === 'blocked' ? 'filter' : 'assistant',
            text: r.reply, at: '' }]
        // A flag in VOLT's reply does NOT solve the level: the player must
        // read it and submit it via "Submit a flag". The turn response
        // carries no leak/solve signal, so nothing here flips solved.
        return {
          ...s, messages: msgs, tokens: r.tokens, attempts: r.attempts,
          solved: r.solved || s.solved,
        }
      })
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 502) {
          setPending({ msgId, text, state: 'failed', retryable: true,
            attachment: att ?? null,
            error: 'Model backend unavailable. This did not count as an attempt.' })
        } else if (e.code === 'rate_limited' || e.code === 'queue_full') {
          setPending({ msgId, text, state: 'failed', retryable: true,
            attachment: att ?? null, error: e.message })
        } else if (e.code === 'in_progress' || e.code === 'busy') {
          setPending({ msgId, text, state: 'failed', retryable: true,
            attachment: att ?? null,
            error: 'A turn is already being processed. Retry in a moment.' })
        } else {
          setPending(null)
          setNotice(e.message)
          if (e.code === 'turn_limit') refreshSession()
        }
      } else {
        setPending({ msgId, text, state: 'failed', retryable: true,
          attachment: att ?? null,
          error: 'Network error. Retry. Duplicates are handled safely.' })
      }
    }
  }

  const submitDraft = (e: FormEvent) => {
    e.preventDefault()
    const text = draft.trim()
    if (!text || pending?.state === 'sending' || !session) return
    const att = attachment
    setDraft('')
    setAttachment(null)
    sendTurn(text, newMsgId(), att)
  }

  const rotate = async (kind: 'reset' | 'new-chat') => {
    if (!session) return
    if (kind === 'reset' && !window.confirm(
      'Reset destroys this conversation and everything built up in it, ' +
      'including any stored memory for this level. Your solved levels and ' +
      'progress are kept. Continue?')) return
    try {
      setPending(null)
      setExtras(null)
      setAttachment(null)
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
        // Submitting the correct flag is the ONLY thing that solves a level;
        // the solved banner (with efficiency bonus) is driven off this.
        if (r.solve) {
          setSession(s => s ? { ...s, solved: true, solve: r.solve } : s)
        }
        refreshSession()
        loadChallenges(event) // unlock the next level now the solve is recorded
      } else {
        setNotice('That flag is not correct for your level.')
      }
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Submission failed.')
    }
  }

  const unlockHint = async (index: number) => {
    if (!session) return
    try {
      await api.post(`/api/game/sessions/${session.game_session_id}/hints`,
        { hint_index: index })
      refreshSession()
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : 'Could not unlock the hint.')
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
                  Level {c.number}{c.locked ? ' (locked)' : ''}
                </div>
                <div className="name">{c.title}</div>
                <div className="pts">
                  {c.solved
                    ? <span className="done"
                        title={c.postmortem?.points
                          ? `${c.net_points} from the solve `
                            + `plus ${c.postmortem.points} from the postmortem`
                          : undefined}>Solved
                        {c.net_points != null
                          ? ` for ${c.net_points + (c.postmortem?.points || 0)} pts`
                          : ''}</span>
                    : c.locked
                      ? <span className="dim">Solve the previous level to unlock</span>
                      : !c.open_now
                        ? 'unavailable'
                        : `${c.points} pts + efficiency bonus`}
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
          <div className="briefing empty">
            <img className="mascot" src={voltMascot} alt="VOLT, the mascot" />
            <h2>Talk to VOLT.</h2>
            <p>
              VOLT is an AI assistant guarding a secret flag on every level.
              Each level defends the flag differently. Pick a level on the left
              to begin. Level 1 is open; clearing a level unlocks the next.
            </p>
            <p>
              Scoring rewards efficiency: fewer attempts and fewer model
              tokens mean a bigger bonus, and harder levels are worth more.
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
              {session.challenge.overview && (
                <p className="overview">{session.challenge.overview}</p>
              )}
            </div>

            {session.solved && (
              <div className="solvebar">
                <span>
                  Solved
                  {session.solve ? ` for ${session.solve.net_points} points` : ''}
                  {session.solve?.bonus
                    ? ` (includes a ${session.solve.bonus} point efficiency bonus)`
                    : ''}.
                  {session.postmortem?.points
                    ? ` Postmortem: +${session.postmortem.points}.`
                    : ''}
                  {' '}You can keep experimenting here.
                </span>
                {session.postmortem?.available && (
                  <button className={'btn small'
                      + (session.postmortem.answered ? '' : ' primary')}
                    aria-expanded={pmOpen}
                    onClick={() => setPmOpen(o => !o)}>
                    Level Postmortem
                    {session.postmortem.answered
                      ? ''
                      : ` +${session.postmortem.award} pts`}
                  </button>
                )}
                {nextPlayable && nextPlayable.challenge_id !== session.challenge_id && (
                  // an unanswered postmortem is the primary action; once it
                  // is answered, moving on to the next level is
                  <button className={'btn small'
                      + (session.postmortem?.available
                         && !session.postmortem.answered ? '' : ' primary')}
                    onClick={() => openLevel(nextPlayable)}>
                    Continue to Level {nextPlayable.number}
                  </button>
                )}
              </div>
            )}

            {/* The postmortem takes the whole pane while it is open: it is a
                short, focused debrief, not another panel competing with the
                transcript. Closing it returns to the chat unchanged. */}
            {pmOpen && session.solved ? (
              <Postmortem gsid={session.game_session_id}
                onClose={() => setPmOpen(false)}
                onAnswered={() => {
                  // the award changes the player's score, so re-read both
                  // the session and the level list from the server
                  refreshSession()
                  loadChallenges(event)
                }} />
            ) : (
            <>
            <div className="panestack" ref={chatRef}>
            <HintPanel session={session} onUnlock={unlockHint} />

            {session.mcp && (
              <ServersPanel mcp={session.mcp}
                gsid={session.game_session_id}
                onChange={refreshSession} />
            )}

            <div className="chat" aria-live="polite">
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
                      onClick={() => sendTurn(pending.text, pending.msgId,
                                              pending.attachment)}>Retry</button>
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

            {extras && <PipelineTrace extras={extras} />}
            </div>

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
                {session.mcp && (
                  <AttachButton attachment={attachment}
                    limits={session.mcp.limits}
                    onPick={setAttachment} onError={setNotice} />
                )}
                {session.challenge.new_chat !== false && (
                  <button className="btn small" onClick={() => rotate('new-chat')}>
                    New chat
                  </button>
                )}
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
          </>
        )}
      </main>
    </div>
  )
}

// A plain .txt picker, styled as one more small button in the composer meta
// row next to "New chat". Nothing about the attachment enters a prompt: the
// backend mounts it on the level's filesystem server, so VOLT can only reach
// it by calling a file-reading tool.
//
// The caps come from the server, so this rejects an oversized file before
// uploading it against exactly the numbers the API enforces.
function AttachButton({ attachment, limits, onPick, onError }: {
  attachment: Attachment | null
  limits: McpView['limits']
  onPick: (a: Attachment | null) => void
  onError: (m: string) => void
}) {
  const ref = useRef<HTMLInputElement>(null)

  const choose = async (file: File | undefined) => {
    if (!file) return
    if (!/\.txt$/i.test(file.name)) {
      onError('Attachments must be .txt files.')
      return
    }
    if (file.size > limits.max_attachment_bytes) {
      onError(`That file is ${Math.ceil(file.size / 1024)} KB; the limit is `
        + `${Math.floor(limits.max_attachment_bytes / 1024)} KB.`)
      return
    }
    const text = await file.text()
    if (text.length > limits.max_attachment_chars) {
      onError(`That file is ${text.length.toLocaleString()} characters; the `
        + `limit is ${limits.max_attachment_chars.toLocaleString()}.`)
      return
    }
    if (!text.trim()) {
      onError('That file is empty.')
      return
    }
    onError('')
    onPick({ name: file.name, text })
  }

  return (
    <>
      <input ref={ref} type="file" accept=".txt,text/plain"
        style={{ display: 'none' }} aria-hidden="true"
        onChange={e => {
          choose(e.target.files?.[0])
          e.target.value = ''   // let the same file be picked again
        }} />
      {attachment ? (
        <span className="chip" title={`${attachment.text.length} characters`}>
          {attachment.name}
          <button className="chip-x" aria-label="Remove attachment"
            onClick={() => onPick(null)}>&times;</button>
        </span>
      ) : (
        <button className="btn small" onClick={() => ref.current?.click()}
          title={`.txt only, up to `
            + `${Math.floor(limits.max_attachment_bytes / 1024)} KB, `
            + `${limits.max_attachments} per conversation`}>
          Attach .txt
        </button>
      )}
    </>
  )
}

// The MCP servers panel. Collapsed by default behind a small button, exactly
// like the hints panel, so levels without a tool catalogue are untouched.
function ServersPanel({ mcp, gsid, onChange }: {
  mcp: McpView; gsid: string; onChange: () => void
}) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const toolCount = mcp.connected.reduce((n, s) => n + s.tools.length, 0)
    + (mcp.installed?.tools.length || 0)

  const current = JSON.stringify(
    mcp.installed
      ? { server: mcp.installed.server,
          tools: mcp.installed.tools.map(t => ({
            name: t.name, description: t.model_sees, inputSchema: t.schema })) }
      : mcp.template,
    null, 2)

  const save = async (manifest: unknown) => {
    setBusy(true)
    setErr('')
    try {
      await api.put(`/api/game/sessions/${gsid}/tools`, { manifest })
      setDraft('')
      setEditing(false)
      onChange()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not update the server.')
    } finally {
      setBusy(false)
    }
  }

  const install = () => {
    try {
      save(JSON.parse(draft || current))
    } catch {
      setErr('That is not valid JSON.')
    }
  }

  return (
    <div className="hintpanel">
      <button className="btn small" aria-expanded={open}
        onClick={() => setOpen(o => !o)}>
        {open ? 'Hide MCP servers' : `MCP servers (${toolCount})`}
      </button>

      {open && (
        <div className="hints servers">
          {/* One grid across every server, so the row fills the pane instead
              of leaving a column of dead space per server. Each card carries
              its own server name. */}
          <div className="toolgrid">
            {mcp.connected.flatMap(srv =>
              srv.tools.map(t => (
                <ToolCard key={`${srv.server}.${t.name}`} tool={t}
                  server={srv.server} />
              )))}
            {!editing && mcp.installed?.tools.map(t => (
              <ToolCard key={`${mcp.installed!.server}.${t.name}`} tool={t}
                server={mcp.installed!.server} />
            ))}
          </div>

          <section className="srv">
            <h4>
              {mcp.installed ? `${mcp.installed.server} manifest` : 'your server'}
              <span className="tag yours">
                {mcp.installed ? 'installed by you' : 'not installed'}
              </span>
            </h4>

            {(editing || !mcp.installed) && (
              <>
                <textarea className="input mono" rows={9}
                  aria-label="MCP server manifest"
                  value={draft || current}
                  onChange={e => setDraft(e.target.value)} />
                <p className="limits">
                  up to {mcp.limits.max_tools} tools,{' '}
                  {mcp.limits.max_description_chars.toLocaleString()} characters
                  per description
                </p>
              </>
            )}

            {err && <div className="notice err">{err}</div>}

            <div className="srv-actions">
              {(editing || !mcp.installed) ? (
                <>
                  <button className="btn primary small" disabled={busy}
                    onClick={install}>
                    {mcp.installed ? 'Save changes' : 'Install server'}
                  </button>
                  {mcp.installed && (
                    <button className="btn small" disabled={busy}
                      onClick={() => { setEditing(false); setDraft(''); setErr('') }}>
                      Cancel
                    </button>
                  )}
                </>
              ) : (
                <button className="btn small" onClick={() => setEditing(true)}>
                  Edit manifest
                </button>
              )}
              {mcp.installed && !editing && (
                <button className="btn small danger" disabled={busy}
                  onClick={() => save(null)}>Uninstall</button>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

// One tool, as a consent screen shows it: an icon for what it does, its name
// and parameters, and the one-line summary. The full description the model is
// handed sits behind a disclosure, because the gap between those two is the
// whole point of the level.
function ToolCard({ tool, server }: { tool: McpTool; server: string }) {
  const [show, setShow] = useState(false)
  const params = Object.keys(tool.schema || {})
  return (
    <div className={'toolcard' + (tool.trusted ? '' : ' yours')}>
      <div className="toolcard-head">
        <ToolIcon name={tool.name} schema={tool.schema} />
        <div className="toolcard-id">
          <code>{tool.name}</code>
          <span className="srvname">
            {server}
            {!tool.trusted && <i> yours</i>}
          </span>
        </div>
      </div>
      {params.length > 0 && (
        <div className="params">
          {params.map(p => <span key={p} className="param">{p}</span>)}
        </div>
      )}
      <p className="toolcard-sum">{tool.summary || 'no description'}</p>
      <button className="linkbtn" aria-expanded={show}
        onClick={() => setShow(v => !v)}>
        {show ? 'Hide what VOLT is handed' : 'What VOLT is handed'}
      </button>
      {show && <pre className="toolsees">{tool.model_sees || '(empty)'}</pre>}
    </div>
  )
}

function HintPanel({ session, onUnlock }:
  { session: SessionState; onUnlock: (i: number) => void }) {
  const [open, setOpen] = useState(false)
  const costs = session.challenge.hint_costs || []
  if (costs.length === 0) return null
  const unlocked = new Map((session.hints || []).map(h => [h.hint_index, h]))
  const nextIndex = costs.findIndex((_, i) => !unlocked.has(i))
  const spent = (session.hints || []).reduce((a, h) => a + h.cost, 0)

  return (
    <div className="hintpanel">
      <button className="btn small" aria-expanded={open}
        onClick={() => setOpen(o => !o)}>
        {open ? 'Hide hints' : `Hints (${unlocked.size}/${costs.length})`}
        {spent > 0 ? ` -${spent} pts` : ''}
      </button>
      {open && (
        <div className="hints">
          <p className="dim" style={{ margin: '6px 0' }}>
            Paid hints are deducted from this level's score when you solve it.
            More hints unlocked means fewer points.
          </p>
          {costs.map((cost, i) => {
            const h: Hint | undefined = unlocked.get(i)
            if (h) {
              return (
                <div key={i} className="hint-row revealed">
                  <b>Hint {i + 1}</b>
                  {h.cost > 0 ? <span className="dim"> (-{h.cost} pts)</span> : null}
                  <div>{h.text}</div>
                </div>
              )
            }
            const isNext = i === nextIndex
            return (
              <div key={i} className="hint-row">
                <b>Hint {i + 1}</b>
                <span className="dim">
                  {cost > 0 ? ` locked - costs ${cost} pts` : ' locked - free'}
                </span>
                <button className="btn small" disabled={!isNext}
                  title={isNext ? '' : 'Unlock the previous hint first'}
                  onClick={() => onUnlock(i)}>
                  {cost > 0 ? `Unlock for ${cost} pts` : 'Reveal (free)'}
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function PipelineTrace({ extras }: { extras: any }) {
  const stages = extras.pipeline as { stage: string; verdict?: string; status?: string }[] | undefined
  const agents = extras.agents as any[] | undefined
  const workflow = extras.workflow as WorkflowTrace | undefined
  const mcp = extras.mcp as McpTrace | undefined
  const memoryLoaded = extras.memory_loaded as number | undefined

  if (!stages && !agents && !workflow && !mcp && memoryLoaded === undefined) {
    return null
  }

  return (
    <div className="trace">
      {stages && (
        <div className="trace-row">
          {stages.map((s, i) => (
            <span key={i} className="trace-stage">
              {s.stage}
              <b>{s.verdict || s.status}</b>
            </span>
          ))}
        </div>
      )}

      {workflow && <AgentWorkflow wf={workflow} />}

      {agents && !workflow && (
        <div className="trace-row">
          {agents.map((a, i) => (
            <span key={i} className="trace-stage">
              {a.agent}
              {a.provenance_out && <b>out: {a.provenance_out}</b>}
            </span>
          ))}
        </div>
      )}

      {memoryLoaded !== undefined && (
        <div className="trace-note">
          {memoryLoaded === 0
            ? 'No stored preferences were loaded for this session.'
            : `${memoryLoaded} stored preference${memoryLoaded === 1 ? '' : 's'} loaded as trusted context.`}
        </div>
      )}

      {mcp && <McpTrace_ mcp={mcp} />}
    </div>
  )
}

interface McpStep {
  tool: string
  server: string
  ok: boolean
  trusted: boolean
  arguments_preview: Record<string, string>
  result: string
}

interface McpTrace {
  steps: McpStep[]
  inbound: { server: string; tool: string; arguments: Record<string, unknown> }[]
  dlp_blocked: boolean
  installed_server: string | null
  files_visible: string[]
}

// The tool loop as it actually ran, in the same pill-and-note shape the other
// pipeline levels use. Arguments and results arrive already redacted by the
// backend: this is the organisation's audit view, and it is not where a
// secret can be read.
function McpTrace_({ mcp }: { mcp: McpTrace }) {
  const [openStep, setOpenStep] = useState<number | null>(null)
  const detailRef = useRef<HTMLDivElement>(null)
  // the trace sits at the bottom of the scrolling column, so an expanded step
  // would otherwise open below the fold
  useEffect(() => {
    if (openStep !== null) {
      detailRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [openStep])
  return (
    <>
      {mcp.steps.length > 0 && (
        <div className="trace-row">
          {mcp.steps.map((st, i) => (
            <button key={i} className={'trace-stage as-btn'
              + (st.trusted ? '' : ' untrusted')}
              aria-expanded={openStep === i}
              onClick={() => setOpenStep(v => (v === i ? null : i))}>
              <ToolIcon name={st.tool.split('.').pop() || st.tool} />
              {st.tool}
              <b>{st.ok ? 'ok' : 'error'}</b>
            </button>
          ))}
        </div>
      )}

      {openStep !== null && mcp.steps[openStep] && (
        <div className="stepdetail" ref={detailRef}>
          <div className="args">
            {Object.entries(mcp.steps[openStep].arguments_preview).map(
              ([k, v]) => (
                <span key={k} className="param">{k}<i>{v}</i></span>
              ))}
            {Object.keys(mcp.steps[openStep].arguments_preview).length === 0 && (
              <span className="dim">no arguments</span>
            )}
          </div>
          <div className="dim">{mcp.steps[openStep].result}</div>
          <div className="dim small">
            Egress monitoring records what each call was, not what it carried.
          </div>
        </div>
      )}

      {mcp.steps.length === 0 && (
        <div className="trace-note">VOLT answered without calling a tool.</div>
      )}

      {mcp.dlp_blocked && (
        <div className="trace-note">
          Outbound content policy withheld VOLT's chat reply on this turn.
        </div>
      )}

      {mcp.inbound.length > 0 && (
        <div className="trace-note">
          {mcp.inbound.length} call{mcp.inbound.length === 1 ? '' : 's'} left the
          organisation to <b>{mcp.installed_server}</b>. Your server logged the
          arguments in the reply above.
        </div>
      )}
    </>
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
