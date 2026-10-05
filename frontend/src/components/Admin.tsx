import { FormEvent, useCallback, useEffect, useState } from 'react'
import { api, ApiError, EventInfo } from '../api'

export default function Admin() {
  const [events, setEvents] = useState<EventInfo[]>([])
  const [selected, setSelected] = useState<EventInfo | null>(null)
  const [notice, setNotice] = useState<{ kind: string; text: string } | null>(null)

  const load = useCallback(async () => {
    const d = await api.get('/api/events')
    setEvents(d.events)
    setSelected(sel => sel
      ? d.events.find((e: EventInfo) => e.id === sel.id) ?? null : sel)
  }, [])

  useEffect(() => { load() }, [load])

  const say = (kind: string, text: string) => {
    setNotice({ kind, text })
    window.setTimeout(() => setNotice(null), 6000)
  }

  const run = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn()
      await load()
      say('ok', okMsg)
    } catch (e) {
      say('err', e instanceof ApiError ? e.message : 'Action failed.')
    }
  }

  return (
    <div className="page">
      <h1>Administration</h1>
      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

      <section className="admin-section">
        <h2>Events</h2>
        <div className="cards">
          {events.map(ev => (
            <div className="card" key={ev.id}>
              <h3>{ev.name}</h3>
              <div className="dim">
                {new Date(ev.starts_at).toLocaleString()} to{' '}
                {new Date(ev.ends_at).toLocaleString()}
              </div>
              <div className="dim">
                {ev.paused ? 'paused · ' : ''}
                {ev.invite_only ? 'invite-only · ' : ''}
                {ev.registration_open ? 'registration open' : 'registration closed'}
                {ev.leaderboard_frozen ? ' · leaderboard frozen' : ''}
                {!ev.leaderboard_visible ? ' · leaderboard hidden' : ''}
              </div>
              <div className="row-wrap" style={{ marginTop: 10 }}>
                <button className="btn small"
                  onClick={() => setSelected(ev)}>Manage</button>
                <button className="btn small" onClick={() => run(
                  () => api.patch(`/api/admin/events/${ev.id}`,
                    { paused: !ev.paused }),
                  ev.paused ? 'Gameplay resumed.' : 'Gameplay paused.')}>
                  {ev.paused ? 'Resume' : 'Pause'}
                </button>
                <button className="btn small" onClick={() => run(
                  () => api.post(`/api/admin/events/${ev.id}/freeze`,
                    { frozen: !ev.leaderboard_frozen }),
                  ev.leaderboard_frozen ? 'Leaderboard unfrozen.'
                    : 'Leaderboard frozen (gameplay continues).')}>
                  {ev.leaderboard_frozen ? 'Unfreeze board' : 'Freeze board'}
                </button>
                <button className="btn small" onClick={() => run(
                  () => api.patch(`/api/admin/events/${ev.id}`,
                    { leaderboard_visible: !ev.leaderboard_visible }),
                  'Leaderboard visibility updated.')}>
                  {ev.leaderboard_visible ? 'Hide board' : 'Show board'}
                </button>
                <button className="btn small" onClick={() => run(
                  () => api.patch(`/api/admin/events/${ev.id}`,
                    { registration_open: !ev.registration_open }),
                  'Registration updated.')}>
                  {ev.registration_open ? 'Close reg' : 'Open reg'}
                </button>
              </div>
            </div>
          ))}
        </div>
        <CreateEvent onDone={() => { load(); say('ok', 'Event created.') }}
          onErr={m => say('err', m)} />
      </section>

      {selected && (
        <ManageEvent ev={selected} run={run} />
      )}
    </div>
  )
}

function CreateEvent({ onDone, onErr }:
  { onDone: () => void; onErr: (m: string) => void }) {
  const [form, setForm] = useState({
    slug: '', name: '', starts_at: '', ends_at: '',
    invite_only: false, registration_open: true,
  })
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    try {
      const body = {
        ...form,
        starts_at: new Date(form.starts_at).toISOString(),
        ends_at: new Date(form.ends_at).toISOString(),
      }
      const r = await api.post('/api/admin/events', body)
      // sensible default: offer all ten levels at their escalating defaults
      const defaults: [string, number][] = [
        ['l1', 100], ['l2', 200], ['l3', 350], ['l4', 550], ['l5', 800],
        ['l6', 1100], ['l7', 1500], ['l8', 2000], ['l9', 2600], ['l10', 3500]]
      for (const [cid, pts] of defaults) {
        await api.post(`/api/admin/events/${r.id}/challenges`,
          { challenge_id: cid, points: pts })
      }
      setForm({ slug: '', name: '', starts_at: '', ends_at: '',
        invite_only: false, registration_open: true })
      onDone()
    } catch (e) {
      onErr(e instanceof ApiError ? e.message : 'Create failed.')
    }
  }
  return (
    <form onSubmit={submit} style={{ marginTop: 22, maxWidth: 480 }}>
      <h3>New event</h3>
      <label className="field"><span>Slug (url-safe)</span>
        <input className="input" required pattern="[a-z0-9][a-z0-9\-]+"
          value={form.slug}
          onChange={e => setForm({ ...form, slug: e.target.value })} /></label>
      <label className="field"><span>Name</span>
        <input className="input" required value={form.name}
          onChange={e => setForm({ ...form, name: e.target.value })} /></label>
      <label className="field"><span>Starts</span>
        <input className="input" type="datetime-local" required
          value={form.starts_at}
          onChange={e => setForm({ ...form, starts_at: e.target.value })} /></label>
      <label className="field"><span>Ends</span>
        <input className="input" type="datetime-local" required
          value={form.ends_at}
          onChange={e => setForm({ ...form, ends_at: e.target.value })} /></label>
      <label style={{ display: 'block', marginBottom: 12 }}>
        <input type="checkbox" checked={form.invite_only}
          onChange={e => setForm({ ...form, invite_only: e.target.checked })} />
        {' '}Invite-only
      </label>
      <button className="btn primary">
        Create event (levels 1-10 at default points)
      </button>
    </form>
  )
}

function ManageEvent({ ev, run }: {
  ev: EventInfo
  run: (fn: () => Promise<unknown>, ok: string) => Promise<void>
}) {
  const [detail, setDetail] = useState<any>(null)
  const [stats, setStats] = useState<any>(null)
  const [enrollments, setEnrollments] = useState<any[]>([])
  const [audit, setAudit] = useState<any[]>([])
  const [invites, setInvites] = useState<string[]>([])

  const load = useCallback(async () => {
    const [d, s, en, au] = await Promise.all([
      api.get(`/api/events/${ev.id}`),
      api.get(`/api/admin/events/${ev.id}/stats`),
      api.get(`/api/admin/events/${ev.id}/enrollments`),
      api.get('/api/admin/audit'),
    ])
    setDetail(d)
    setStats(s)
    setEnrollments(en.enrollments)
    setAudit(au.entries.slice(0, 20))
  }, [ev.id])

  useEffect(() => { load() }, [load])

  const setPoints = async (cid: string, points: number, enabled: boolean) => {
    await run(() => api.post(`/api/admin/events/${ev.id}/challenges`,
      { challenge_id: cid, points, enabled }), `Updated ${cid}.`)
    load()
  }

  const makeInvites = async () => {
    const r = await api.post(`/api/admin/events/${ev.id}/invites`,
      { count: 5, max_uses: 1 })
    setInvites(r.codes)
  }

  const exportCsv = async () => {
    const text = await api.getText(`/api/admin/events/${ev.id}/export`)
    const blob = new Blob([text], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `volt-${ev.slug}-results.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <section className="admin-section">
      <h2>Manage: {ev.name}</h2>

      <h3>Challenges</h3>
      <table className="plain">
        <thead><tr><th>Level</th><th>Points</th><th>Enabled</th><th /></tr></thead>
        <tbody>
          {(detail?.challenges || []).map((c: any) => (
            <ChallengeRow key={c.challenge_id} c={c} onSave={setPoints} />
          ))}
        </tbody>
      </table>

      <h3 style={{ marginTop: 24 }}>Per-level stats</h3>
      <table className="plain">
        <thead><tr>
          <th>Level</th><th>Starts</th><th>Solves</th><th>Attempts</th>
          <th>Hints</th><th>Errors</th><th>p50 / p95 latency</th><th>Tokens</th>
          <th>Median solve time</th>
        </tr></thead>
        <tbody>
          {(stats?.levels || []).map((l: any) => (
            <tr key={l.challenge_id}>
              <td>{l.challenge_id}</td>
              <td>{l.starts}</td>
              <td>{l.solves}</td>
              <td>{l.attempts}</td>
              <td>{l.hints_unlocked} (-{l.hint_cost_total})</td>
              <td>{l.error_turns}</td>
              <td>
                {l.p50_latency_ms != null
                  ? `${Math.round(l.p50_latency_ms)} / ${Math.round(l.p95_latency_ms)} ms`
                  : 'n/a'}
              </td>
              <td>{l.tokens.toLocaleString()}</td>
              <td>{l.median_time_to_solve_s != null
                ? `${Math.round(l.median_time_to_solve_s / 60)}m` : 'n/a'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {stats && (
        <p className="dim" style={{ fontSize: 13, color: 'var(--text-dim)' }}>
          Inference queue depth right now: {stats.inference_queue_depth}
        </p>
      )}

      <div className="row-wrap" style={{ margin: '16px 0' }}>
        <button className="btn" onClick={exportCsv}>Export results (CSV)</button>
        <button className="btn" onClick={makeInvites}>
          Generate 5 invite codes
        </button>
        <button className="btn" onClick={load}>Refresh</button>
      </div>
      {invites.length > 0 && (
        <p>New codes:{' '}
          {invites.map(c => <code className="flag" key={c}>{c} </code>)}</p>
      )}

      <h3>Enrolled players ({enrollments.length})</h3>
      <table className="plain">
        <tbody>
          {enrollments.map(u => (
            <tr key={u.user_id}>
              <td>{u.display_name}</td>
              <td>{u.email}</td>
              <td>{new Date(u.enrolled_at).toLocaleString()}</td>
              <td>
                <button className="btn small danger" onClick={() => run(
                  () => api.del(
                    `/api/admin/events/${ev.id}/enrollments/${u.user_id}`),
                  'Enrolment removed.').then(load)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ marginTop: 24 }}>Recent admin audit</h3>
      <table className="plain">
        <tbody>
          {audit.map(a => (
            <tr key={a.id}>
              <td>{new Date(a.at).toLocaleTimeString()}</td>
              <td>{a.actor}</td>
              <td>{a.action}</td>
              <td style={{ fontSize: 12 }}>
                {JSON.stringify(a.details)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function ChallengeRow({ c, onSave }: {
  c: any
  onSave: (cid: string, points: number, enabled: boolean) => void
}) {
  const [points, setPoints] = useState<number>(c.points)
  const [enabled, setEnabled] = useState<boolean>(c.available || c.points > 0)
  useEffect(() => { setPoints(c.points) }, [c.points])
  return (
    <tr>
      <td>{c.number}. {c.title} <span style={{ color: 'var(--text-faint)' }}>
        v{c.version}</span></td>
      <td>
        <input className="input" type="number" min={1} value={points}
          style={{ width: 90 }} aria-label={`Points for level ${c.number}`}
          onChange={e => setPoints(parseInt(e.target.value || '0', 10))} />
      </td>
      <td>
        <input type="checkbox" checked={enabled}
          aria-label={`Enable level ${c.number}`}
          onChange={e => setEnabled(e.target.checked)} />
      </td>
      <td>
        <button className="btn small"
          onClick={() => onSave(c.challenge_id, points, enabled)}>Save</button>
      </td>
    </tr>
  )
}
