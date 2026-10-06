// Small inline SVG glyphs for MCP tools. Inline rather than an icon font or
// emoji: the project ships Arial only and no emoji, and these inherit
// currentColor so they work in both themes with no extra rules.
//
// A tool is matched on what it DOES, by name and parameters, so a
// player-authored tool gets a sensible glyph too. Nothing here reads the
// description: that text is untrusted and must never steer the UI.

type Glyph = 'read' | 'folder' | 'mail' | 'convert' | 'archive' | 'plug'

const RULES: [RegExp, Glyph][] = [
  [/(^|_)(list|ls|dir|browse|index)(_|$)/, 'folder'],
  [/(read|open|fetch|get|cat|load|view)/, 'read'],
  [/(mail|email|smtp|notify|message)/, 'mail'],
  [/(convert|render|transform|export|pdf|format)/, 'convert'],
  [/(archive|store|upload|backup|retain|save|put)/, 'archive'],
]

export function glyphFor(name: string, schema?: Record<string, string>): Glyph {
  const n = (name || '').toLowerCase()
  for (const [re, g] of RULES) if (re.test(n)) return g
  // fall back on the shape of the call: something taking a path reads a file
  if (schema && Object.keys(schema).some(k => /path|file|document/.test(k))) {
    return 'read'
  }
  return 'plug'
}

const PATHS: Record<Glyph, JSX.Element> = {
  // a document with a line of figures on it: reading a finance file
  read: (
    <>
      <path d="M5 2.5h6.5L15 6v11.5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1Z" />
      <path d="M11.5 2.5V6H15" />
      <path d="M6.5 10h7M6.5 13h4.5" />
    </>
  ),
  // stacked files in a folder: listing a directory
  folder: (
    <>
      <path d="M2.5 5.5a1 1 0 0 1 1-1h3.4l1.4 1.8H16a1 1 0 0 1 1 1v8.2a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1Z" />
      <path d="M5.5 9.5h9M5.5 12.5h6" />
    </>
  ),
  // envelope: outbound mail
  mail: (
    <>
      <rect x="2.5" y="4.5" width="15" height="11" rx="1" />
      <path d="m2.5 6 7.5 5 7.5-5" />
    </>
  ),
  // document with an arrow: one format becoming another
  convert: (
    <>
      <path d="M5 2.5h6.5L15 6v5" />
      <path d="M11.5 2.5V6H15" />
      <path d="M4 2.5v15" />
      <path d="M8 15.5h8m-3-3 3 3-3 3" />
    </>
  ),
  // a box with a lid: long-term storage
  archive: (
    <>
      <rect x="2.5" y="4" width="15" height="3.5" rx="0.8" />
      <path d="M4 7.5v8.5a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7.5" />
      <path d="M8 11h4" />
    </>
  ),
  // a plug: a third-party server you connected yourself
  plug: (
    <>
      <path d="M7 2.5v4m6-4v4" />
      <path d="M4.5 6.5h11v3a5.5 5.5 0 0 1-11 0Z" />
      <path d="M10 15v3" />
    </>
  ),
}

export default function ToolIcon(
  { name, schema, title }: {
    name: string; schema?: Record<string, string>; title?: string
  },
) {
  const g = glyphFor(name, schema)
  return (
    <svg className="toolicon" viewBox="0 0 20 20" width="18" height="18"
      fill="none" stroke="currentColor" strokeWidth="1.4"
      strokeLinecap="round" strokeLinejoin="round"
      role="img" aria-label={title || `${g} tool`}>
      {PATHS[g]}
    </svg>
  )
}
