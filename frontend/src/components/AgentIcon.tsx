// Inline SVG glyphs for the Level 10 threat-intel agents. Inline rather than
// an icon font or emoji: the project ships Arial only and no emoji, and these
// inherit currentColor so one glyph works in both themes.
//
// Each glyph says what the agent DOES, so the workflow reads as a pipeline
// even before you read the labels: a sweep that looks outward, a graph that
// joins things up, a shield that authorises, a sheet that goes out.

type Glyph = 'scout' | 'analyst' | 'commander' | 'publisher' | 'agent'

const PATHS: Record<Glyph, JSX.Element> = {
  // radar sweep over a horizon: collection from outside the perimeter
  scout: (
    <>
      <path d="M10 10.5a7.5 7.5 0 1 1-5.3-7.2" />
      <path d="M10 10.5 15.8 4.7" />
      <path d="M10 10.5a4 4 0 1 1-2.8-3.8" />
      <circle cx="10" cy="10.5" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  // linked nodes: correlating separate findings into one picture
  analyst: (
    <>
      <circle cx="4.5" cy="5" r="2" />
      <circle cx="4.5" cy="15" r="2" />
      <circle cx="15" cy="10" r="2.2" />
      <path d="M6.3 6.1 13.2 9M6.3 13.9 13.2 11" />
    </>
  ),
  // shield with a rank chevron: the agent that authorises and tasks
  commander: (
    <>
      <path d="M10 2.4 16.5 4.6v5.2c0 3.6-2.6 6.6-6.5 7.8-3.9-1.2-6.5-4.2-6.5-7.8V4.6Z" />
      <path d="m6.8 9.6 3.2 2.6 3.2-2.6" />
      <path d="m6.8 6.6 3.2 2.6 3.2-2.6" />
    </>
  ),
  // a sheet leaving a frame: the released, user-visible brief
  publisher: (
    <>
      <path d="M3.5 3.5h8.2a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1Z" />
      <path d="M5.3 7h5.1M5.3 10h5.1M5.3 13h3" />
      <path d="M15 6.6v6.8M17.5 8.2v3.6" />
    </>
  ),
  // fallback: a plain actor
  agent: (
    <>
      <circle cx="10" cy="6.5" r="3" />
      <path d="M4 17c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" />
    </>
  ),
}

export default function AgentIcon(
  { icon, title, size = 20 }: { icon: string; title?: string; size?: number },
) {
  const g: Glyph = (icon in PATHS ? icon : 'agent') as Glyph
  return (
    <svg className="agenticon" viewBox="0 0 20 20" width={size} height={size}
      fill="none" stroke="currentColor" strokeWidth="1.4"
      strokeLinecap="round" strokeLinejoin="round"
      role="img" aria-label={title || `${g} agent`}>
      {PATHS[g]}
    </svg>
  )
}

// The connector between two agent cards: a short rail with an arrowhead,
// drawn rather than typed (the UI ships no arrow characters).
export function FlowArrow({ label }: { label?: string }) {
  return (
    <div className="flowarrow" aria-hidden="true">
      <svg viewBox="0 0 34 12" width="34" height="12" fill="none"
        stroke="currentColor" strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round">
        <path d="M1 6h28" />
        <path d="m25.5 2.5 4 3.5-4 3.5" />
      </svg>
      {label && <span className="flowarrow-label">{label}</span>}
    </div>
  )
}
