import scoutArt from '../assets/scout.webp'
import analystArt from '../assets/analyst.webp'
import commanderArt from '../assets/commander.webp'
import publisherArt from '../assets/publisher.webp'

// The Level 10 threat-intel agents each carry their own illustrated badge,
// in the same art style as the VOLT mascot: a compass for the Scout's
// outward collection, a lens over linked nodes for the Analyst, a targeting
// scanner for the Commander, a dispatch printer for the Publisher. The
// generic fallback stays an inline SVG that inherits currentColor.

const ART: Record<string, string> = {
  scout: scoutArt,
  analyst: analystArt,
  commander: commanderArt,
  publisher: publisherArt,
}

export default function AgentIcon(
  { icon, title, size = 56 }: { icon: string; title?: string; size?: number },
) {
  const art = ART[icon]
  if (art) {
    return (
      <img className="agenticon" src={art} width={size} height={size}
        alt={title || `${icon} agent`} />
    )
  }
  return (
    <svg className="agenticon" viewBox="0 0 20 20" width={size} height={size}
      fill="none" stroke="currentColor" strokeWidth="1.4"
      strokeLinecap="round" strokeLinejoin="round"
      role="img" aria-label={title || 'agent'}>
      <circle cx="10" cy="6.5" r="3" />
      <path d="M4 17c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" />
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
