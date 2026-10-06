// Thin typed API client. The bearer token lives in localStorage purely as a
// session convenience; all authoritative game state comes from the backend.

const BASE: string = (import.meta as any).env?.VITE_API_BASE || ''

export class ApiError extends Error {
  code: string
  status: number
  retryAfterS?: number
  constructor(message: string, code: string, status: number, retryAfterS?: number) {
    super(message)
    this.code = code
    this.status = status
    this.retryAfterS = retryAfterS
  }
}

let token: string | null = localStorage.getItem('volt_token')

export function setToken(t: string | null) {
  token = t
  if (t) localStorage.setItem('volt_token', t)
  else localStorage.removeItem('volt_token')
}

export function hasToken(): boolean {
  return !!token
}

async function req(method: string, path: string, body?: unknown): Promise<any> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`
  let resp: Response
  try {
    resp = await fetch(BASE + path, {
      method, headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('Network error. Check your connection.', 'network', 0)
  }
  const text = await resp.text()
  let data: any = {}
  try { data = text ? JSON.parse(text) : {} } catch { data = { raw: text } }
  if (!resp.ok) {
    const err = data?.error || {}
    throw new ApiError(err.message || `Request failed (${resp.status})`,
      err.code || 'error', resp.status, err.retry_after_s)
  }
  return data
}

export const api = {
  get: (p: string) => req('GET', p),
  post: (p: string, b?: unknown) => req('POST', p, b ?? {}),
  put: (p: string, b: unknown) => req('PUT', p, b),
  patch: (p: string, b: unknown) => req('PATCH', p, b),
  del: (p: string) => req('DELETE', p),
  getText: async (p: string): Promise<string> => {
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const r = await fetch(BASE + p, { headers })
    return r.text()
  },
}

export function newMsgId(): string {
  return (crypto as any).randomUUID
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(36).slice(2) + Date.now().toString(36)
}

// ---- shared types ----

export interface Challenge {
  challenge_id: string
  number: number
  title: string
  subtitle: string
  overview?: string
  points: number
  version: number
  available: boolean
  open_now: boolean
  locked: boolean
  starter: string | null
  solved: boolean
  solved_at: string | null
  net_points?: number | null
}

export interface EventInfo {
  id: string
  slug: string
  name: string
  description: string
  registration_open: boolean
  invite_only: boolean
  starts_at: string
  ends_at: string
  paused: boolean
  leaderboard_visible: boolean
  leaderboard_frozen: boolean
  enrolled?: boolean
  server_time?: string
}

export interface Message {
  seq: number
  role: 'user' | 'assistant' | 'filter'
  text: string
  at: string
}

export interface Hint {
  hint_index: number
  cost: number
  text: string
}

// ---- MCP tool servers (Level 9) ----
// `summary` is what a person approving a server is shown; `model_sees` is the
// full description the model is handed. The gap between them is the level.
export interface McpTool {
  name: string
  summary: string
  model_sees: string
  schema: Record<string, string>
  trusted: boolean
}

export interface McpServer {
  server: string
  trusted: boolean
  tools: McpTool[]
}

export interface McpView {
  connected: McpServer[]
  installed: McpServer | null
  limits: {
    max_tools: number
    max_description_chars: number
    max_manifest_chars: number
    max_steps: number
  }
  template: unknown
}

export interface Attachment {
  name: string
  text: string
}

export interface SessionState {
  game_session_id: string
  challenge_id: string
  mode: 'practice' | 'ranked'
  event_id: string | null
  generation: number
  challenge: {
    title: string; subtitle: string; overview?: string; starter: string | null
    points: number; version: number; hint_costs?: number[]
  }
  hints?: Hint[]
  // present only on levels that expose a tool catalogue
  mcp?: McpView
  messages: Message[]
  tokens: number
  attempts: number
  solved: boolean
  solve: {
    solved_at: string
    net_points: number
    method: string
    points?: number
    bonus?: number
    attempts?: number
    tokens_spent?: number
  } | null
}
