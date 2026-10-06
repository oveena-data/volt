import { Fragment, useEffect, useRef, useState } from 'react'
import { WorkflowRecord, WorkflowTrace } from '../api'
import AgentIcon, { FlowArrow } from './AgentIcon'

// Level 10's agentic workflow, as it actually ran this turn.
//
// The panel exists because four stochastic hand-offs are otherwise opaque:
// the player needs to see which agent ate their injection, and at which
// hand-off the classification changed. Every value here comes from the
// backend trace; the record bodies arrive already withheld where showing
// them would hand over the restricted value, so this view can never
// substitute for the Publisher's brief.
export default function AgentWorkflow({ wf }: { wf: WorkflowTrace }) {
  const [open, setOpen] = useState<string | null>(null)
  const detailRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open !== null) {
      detailRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [open])

  const shown = wf.agents.find(a => a.id === open) || null

  return (
    <div className="workflow">
      <div className="workflow-rail">
        <span className="wf-source" title="Your submitted source enters the platform unvetted">
          source
          <LabelChip label={wf.source_label} />
        </span>
        {wf.agents.map((a, i) => {
          // the class this agent hands on: what it actually produced, or
          // its own output baseline when it produces prose (the Publisher)
          const handOff = a.produced.length
            ? a.produced[a.produced.length - 1].label
            : a.status === 'ran' ? a.emits : null
          return (
            <Fragment key={a.id}>
              <FlowArrow />
              <button type="button"
                className={'wf-agent' + (a.status === 'declined' ? ' declined' : '')
                  + (open === a.id ? ' open' : '')}
                aria-expanded={open === a.id}
                onClick={() => setOpen(v => (v === a.id ? null : a.id))}>
                <AgentIcon icon={a.icon} title={`${a.name}, ${a.role}`} />
                <span className="wf-agent-name">{a.name}</span>
                <span className="wf-agent-role">{a.role}</span>
                {a.status === 'declined'
                  ? <span className="wf-declined">did not act</span>
                  : handOff && <LabelChip label={handOff} />}
              </button>
              {i === wf.agents.length - 1 && <FlowArrow />}
            </Fragment>
          )
        })}
        <span className="wf-source">
          brief
          <LabelChip label="PUBLIC" />
        </span>
      </div>

      <div className="wf-summary">
        {wf.laundered
          ? `Classification re-minted at the Analyst: ${wf.combined} findings were combined, so the recommendation was booked as first-party OFFICIAL instead of inheriting SENSITIVE.`
          : 'No classification was re-minted on this turn.'}
        {wf.dropped > 0 && ` ${wf.dropped} record${wf.dropped === 1 ? ' was' : 's were'} refused at a trust gate.`}
        {wf.redacted && ' The release policy redacted a recognised secret format from the brief.'}
      </div>

      {shown && (
        <div className="wf-detail" ref={detailRef}>
          <div className="wf-detail-head">
            <AgentIcon icon={shown.icon} size={22} />
            <div>
              <b>{shown.name}</b> <span className="dim">{shown.role}</span>
              <div className="dim small">{shown.summary}</div>
            </div>
          </div>

          <div className="wf-grants">
            <span className="wf-grant">
              clearance<i>{shown.clearance}</i>
            </span>
            <span className="wf-grant">
              acts on<i>{shown.accepts.join(', ')}</i>
            </span>
            {shown.tools.map(t => (
              <span key={t} className="wf-grant tool"><i>{t}</i></span>
            ))}
          </div>

          <RecordList title="Received" records={shown.received} empty="Nothing reached this agent." />
          {shown.refused.length > 0 && (
            <RecordList title="Refused at the gate" records={shown.refused} empty="" />
          )}
          <RecordList
            title={shown.id === 'publisher' ? 'Published' : 'Produced'}
            records={shown.produced}
            empty={shown.id === 'publisher'
              ? 'The brief is the reply above.'
              : 'This agent produced no records.'} />

          {shown.notes.map((n, i) => (
            <div key={i} className="wf-note">{n}</div>
          ))}
        </div>
      )}
    </div>
  )
}

function RecordList(
  { title, records, empty }: {
    title: string; records: WorkflowRecord[]; empty: string
  },
) {
  if (records.length === 0 && !empty) return null
  return (
    <div className="wf-records">
      <div className="wf-records-title">{title}</div>
      {records.length === 0
        ? <div className="dim small">{empty}</div>
        : records.map(r => (
          <div key={r.sid} className="wf-record">
            <div className="wf-record-head">
              <code>{r.sid}</code>
              <LabelChip label={r.label} />
              <span className="wf-field">{r.field}</span>
              <span className="dim small">from {r.origin}</span>
            </div>
            <div className={'wf-record-body' + (r.withheld ? ' withheld' : '')}>
              {r.withheld
                ? 'Withheld: this record carries the restricted validation value. The audit trail shows that it exists and how it is classified, not what it says.'
                : r.text}
            </div>
          </div>
        ))}
    </div>
  )
}

function LabelChip({ label }: { label: string }) {
  return <span className={'provchip p-' + label.toLowerCase()}>{label}</span>
}
