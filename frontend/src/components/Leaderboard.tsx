import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, EventInfo } from '../api'

export default function Leaderboard() {
  const [events, setEvents] = useState<EventInfo[]>([])
  const [eventId, setEventId] = useState<string>('')
  const [entries, setEntries] = useState<any[]>([])
  const [feed, setFeed] = useState<any[]>([])
  const [active, setActive] = useState<number | null>(null)
  const [frozenAt, setFrozenAt] = useState<string | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/api/events').then(d => {
      setEvents(d.events)
      const enrolled = d.events.find((e: EventInfo) => e.enrolled)
      if (enrolled) setEventId(enrolled.id)
      else if (d.events[0]) setEventId(d.events[0].id)
    }).catch(() => setErr('Could not load events.'))
  }, [])

  const load = useCallback(async () => {
    if (!eventId) return
    setErr('')
    try {
      const [lb, fd, ac] = await Promise.all([
        api.get(`/api/events/${eventId}/leaderboard`),
        api.get(`/api/events/${eventId}/feed`),
        api.get(`/api/events/${eventId}/activity`),
      ])
      setEntries(lb.entries)
      setFrozenAt(lb.frozen_at)
      setFeed(fd.solves)
      setActive(ac.active_players)
    } catch (e) {
      setEntries([])
      setFeed([])
      setErr(e instanceof ApiError ? e.message : 'Could not load leaderboard.')
    }
  }, [eventId])

  useEffect(() => {
    load()
    const t = setInterval(load, 10000) // efficient polling; realtime later
    return () => clearInterval(t)
  }, [load])

  return (
    <div className="page">
      <h1>Leaderboard</h1>
      {events.length > 1 && (
        <p>
          <select className="input" style={{ width: 'auto' }} value={eventId}
            aria-label="Event" onChange={e => setEventId(e.target.value)}>
            {events.map(ev =>
              <option key={ev.id} value={ev.id}>{ev.name}</option>)}
          </select>
        </p>
      )}
      {err && <div className="notice warn">{err}</div>}
      {frozenAt && (
        <div className="notice warn">
          Leaderboard frozen at {new Date(frozenAt).toLocaleString()}. Play
          continues; standings will be revealed at the end.
        </div>
      )}
      {active !== null && (
        <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          {active} player{active === 1 ? '' : 's'} active in the last 5 minutes
        </p>
      )}
      <p className="dim" style={{ fontSize: 13 }}>
        Score = level points + efficiency bonus + postmortem awards. Fewer
        attempts and fewer model tokens at solve time earn a bigger bonus;
        harder levels are worth more. Each solved level's postmortem pays a
        flat bonus for the right defensive call, once. Ties go to the player
        who spent fewer tokens.
      </p>
      <table className="plain">
        <thead>
          <tr><th>#</th><th>Player</th><th>Score</th><th>Levels solved</th>
            <th>Postmortems</th><th>Attempts</th><th>Tokens spent</th>
            <th>Last solve</th></tr>
        </thead>
        <tbody>
          {entries.map(e => (
            <tr key={e.rank} className={e.me ? 'me' : ''}>
              <td>{e.rank}</td>
              <td>{e.display_name}{e.me ? ' (you)' : ''}</td>
              <td>{e.total}</td>
              <td>{e.solved}</td>
              <td title="Correct postmortem calls, and the points they paid">
                {e.postmortems_done || 0}
                {e.postmortem ? ` (+${e.postmortem})` : ''}</td>
              <td>{e.attempts}</td>
              <td>{Number(e.tokens || 0).toLocaleString()}</td>
              <td>{e.last_solve
                ? new Date(e.last_solve).toLocaleTimeString()
                : 'Not yet'}</td>
            </tr>
          ))}
          {entries.length === 0 && !err && (
            <tr><td colSpan={8} style={{ color: 'var(--text-dim)' }}>
              No players enrolled yet.</td></tr>
          )}
        </tbody>
      </table>

      <h1 style={{ marginTop: 36 }}>Recent solves</h1>
      <table className="plain">
        <tbody>
          {feed.map((s, i) => (
            <tr key={i}>
              <td>{s.display_name}</td>
              <td>solved level {s.number}</td>
              <td>{new Date(s.solved_at).toLocaleTimeString()}</td>
            </tr>
          ))}
          {feed.length === 0 && (
            <tr><td style={{ color: 'var(--text-dim)' }}>Quiet so far.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
