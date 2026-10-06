import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, PostmortemDebrief, PostmortemOption } from '../api'

// Three beats, in order, and nothing on screen that is not one of them:
//   1. what broke, with the player's own winning message as the evidence
//   2. one decision, worth real points, with every option explained after
//   3. the fix, revealed by answering, as a before/after
//
// The debrief is served whole by the backend with no model call, so this
// component makes exactly two requests: load, and answer once.

const VERDICT_LABEL: Record<string, string> = {
  best: 'Best answer',
  partial: 'Helps, but partial',
  weak: 'Weak',
}

export default function Postmortem(
  { gsid, onAnswered, onClose }:
  { gsid: string; onAnswered: () => void; onClose: () => void },
) {
  const [pm, setPm] = useState<PostmortemDebrief | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      setPm(await api.get(`/api/game/sessions/${gsid}/postmortem`))
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not load the postmortem.')
    }
  }, [gsid])

  useEffect(() => { load() }, [load])

  const choose = async (key: string) => {
    if (!pm || pm.answered || busy) return
    setBusy(true)
    setErr('')
    try {
      const next: PostmortemDebrief = await api.post(
        `/api/game/sessions/${gsid}/postmortem`, { choice: key })
      setPm(next)
      onAnswered()          // scores change, so refresh session + level list
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not record that answer.')
    } finally {
      setBusy(false)
    }
  }

  if (err && !pm) {
    return (
      <div className="pm">
        <div className="pm-head">
          <b>Level Postmortem</b>
          <button className="btn small" onClick={onClose}>Close</button>
        </div>
        <div className="notice err" role="alert">{err}</div>
      </div>
    )
  }
  if (!pm) return null

  const answered = pm.answered
  const picked = (o: PostmortemOption) => answered && pm.choice === o.key

  return (
    <div className="pm">
      <div className="pm-head">
        <b>Level Postmortem</b>
        <span className="dim">Level {pm.number}. {pm.title}</span>
        <span className="spacer" />
        <button className="btn small" onClick={onClose}>Close</button>
      </div>

      {/* 1. what happened */}
      <section className="pm-step">
        <div className="pm-stepno">1</div>
        <div className="pm-stepbody">
          <h4>What happened</h4>
          <p className="pm-headline">{pm.headline}</p>
          <p>{pm.breach}</p>
          <div className="pm-chips">
            {pm.standards.map(st => (
              <span className="pm-chip" key={st.id} title={st.name}>
                <b>{st.id}</b> {st.name}{st.note ? ` (${st.note})` : ''}
              </span>
            ))}
          </div>
          {pm.evidence && (
            <div className="pm-evidence">
              <div className="pm-evidence-title">
                {pm.evidence.winning
                  ? 'Your winning message'
                  : 'Your most recent message'}
                {pm.evidence.truncated
                  ? ` (first 400 of ${pm.evidence.length.toLocaleString()} characters)`
                  : ''}
              </div>
              <pre className="pm-quote">{pm.evidence.text}
                {pm.evidence.truncated ? '...' : ''}</pre>
              {pm.evidence.signals.length > 0 && (
                <div className="pm-signals">
                  what you used:
                  {pm.evidence.signals.map(sig => (
                    <span className="pm-signal" key={sig}>{sig}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* 2. one decision */}
      <section className="pm-step">
        <div className="pm-stepno">2</div>
        <div className="pm-stepbody">
          <h4>
            One decision
            {!answered && (
              <span className="pm-award">+{pm.award} pts</span>
            )}
          </h4>
          <p>{pm.question.stem}</p>
          {err && <div className="notice err" role="alert">{err}</div>}
          <div className="pm-options">
            {pm.question.options.map(o => (
              <div key={o.key}
                className={'pm-option'
                  + (answered ? ` done v-${o.verdict}` : '')
                  + (picked(o) ? ' picked' : '')}>
                <button className="pm-option-btn" disabled={answered || busy}
                  onClick={() => choose(o.key)}>
                  <span className="pm-key">{o.key.toUpperCase()}</span>
                  <span className="pm-label">{o.label}</span>
                </button>
                {answered && o.verdict && (
                  <div className="pm-feedback">
                    <span className={`pm-verdict v-${o.verdict}`}>
                      {VERDICT_LABEL[o.verdict]}
                    </span>
                    {picked(o) && <span className="pm-yours">your answer</span>}
                    <p>{o.note}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
          {answered && (
            <div className={`pm-result ${pm.correct ? 'ok' : 'miss'}`}>
              {pm.correct
                ? `Correct. +${pm.points} points, already in your score.`
                : `Not this one. The best answer was `
                  + `${(pm.answer_key || '').toUpperCase()}; no points this time,`
                  + ' but the reasoning above is the part that matters.'}
            </div>
          )}
        </div>
      </section>

      {/* 3. the fix */}
      {answered && pm.fix && (
        <section className="pm-step">
          <div className="pm-stepno">3</div>
          <div className="pm-stepbody">
            <h4>The fix</h4>
            <p className="pm-where"><b>Where it belongs:</b> {pm.fix.where}</p>
            <div className="pm-ba">
              <div className="pm-ba-col before">
                <div className="pm-ba-title">{pm.fix.before_label}</div>
                <pre>{pm.fix.before}</pre>
              </div>
              <div className="pm-ba-col after">
                <div className="pm-ba-title">{pm.fix.after_label}</div>
                <pre>{pm.fix.after}</pre>
              </div>
            </div>
            <p className="pm-why">{pm.fix.why}</p>
          </div>
        </section>
      )}
    </div>
  )
}
