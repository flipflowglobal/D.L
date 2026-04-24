/**
 * api.ts — Typed API client for the AUREON backend.
 *
 * All calls go through /api (proxied to localhost:8000 in dev,
 * same-origin in production).  Every function returns typed data
 * or throws on non-2xx.
 */

const BASE = '/api'
const WDOG = '/watchdog'

async function _get<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`GET ${url} → ${r.status}`)
  return r.json() as Promise<T>
}

async function _post<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`POST ${url} → ${r.status}`)
  return r.json() as Promise<T>
}

async function _del(url: string): Promise<void> {
  const r = await fetch(url, { method: 'DELETE' })
  if (!r.ok) throw new Error(`DELETE ${url} → ${r.status}`)
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string
  watchdog?: Record<string, unknown>
  loop?: Record<string, unknown>
}

export interface SystemStatus {
  running: boolean
  cycle_count: number
  loop_started_at: number | null
  watchdog_available: boolean
}

export interface Agent {
  id: string
  name: string
  strategy: string
  chain: string
  token: string
  running: boolean
  dry_run: boolean
  capital: number
  trades_made: number
  total_pnl: number
  win_rate: number
  cycle_count: number
  last_cycle_at: number | null
}

export interface CreateAgentPayload {
  name?: string
  strategy?: string
  chain?: string
  token?: string
  initial_capital?: number
  trade_size_eth?: number
  min_profit_usd?: number
  scan_interval?: number
  dry_run?: boolean
  private_key?: string
  rpc_url?: string
}

export interface Performance {
  agent_id: string
  capital: number
  total_pnl: number
  win_rate: number
  trades_made: number
  sharpe_ratio: number
  max_drawdown: number
}

export interface WatchdogHealth {
  status: string
  total_agents: number
  critical_events: number
  heals_performed: number
  severity_counts: Record<string, number>
  agents: WatchdogAgentStatus[]
}

export interface WatchdogAgentStatus {
  agent_id: string
  source: string
  running: boolean
  failures: number
  checks: number
  last_ok_ago_s: number
  last_event_type: string | null
  last_severity: string | null
}

export interface WatchdogEvent {
  event_type: string
  severity: string
  agent_id: string
  source: string
  message: string
  wall_time: number
}

export interface SwarmMetrics {
  total_agents: number
  running_agents: number
  total_pnl: number
  consensus_score: number
}

// ── System endpoints ───────────────────────────────────────────────────────

export const api = {
  health:       () => _get<HealthResponse>(`${BASE}/health`),
  status:       () => _get<SystemStatus>(`${BASE}/status`),
  strategies:   () => _get<string[]>(`${BASE}/strategies`),
  chains:       () => _get<string[]>(`${BASE}/chains`),
  tokens:       () => _get<string[]>(`${BASE}/tokens`),

  // Loop control
  loopStart:    () => _post<{ status: string }>(`${BASE}/aureon/start`),
  loopStop:     () => _post<{ status: string }>(`${BASE}/aureon/stop`),

  // Agents
  listAgents:   () => _get<Agent[]>(`${BASE}/agents`),
  getAgent:     (id: string) => _get<Agent>(`${BASE}/agents/${id}`),
  createAgent:  (p: CreateAgentPayload) => _post<Agent>(`${BASE}/agents`, p),
  startAgent:   (id: string) => _post<Agent>(`${BASE}/agents/${id}/start`),
  stopAgent:    (id: string) => _post<Agent>(`${BASE}/agents/${id}/stop`),
  deleteAgent:  (id: string) => _del(`${BASE}/agents/${id}`),
  performance:  (id: string) => _get<Performance>(`${BASE}/agents/${id}/performance`),

  // Swarm
  swarmMetrics: () => _get<SwarmMetrics>(`${BASE}/swarm/metrics`),
  swarmConsensus: () => _get<Record<string, unknown>>(`${BASE}/swarm/consensus`),
  swarmStart:   () => _post<{ status: string }>(`${BASE}/swarm/start`),
  swarmStop:    () => _post<{ status: string }>(`${BASE}/swarm/stop`),

  // Wallet
  generateWallet: () => _post<{ address: string; private_key: string }>(`${BASE}/wallet/generate`),

  // Memory
  getMemory:    (agentId: string) => _get<Record<string, string>>(`${BASE}/memory/${agentId}`),
  deleteMemory: (agentId: string) => _del(`${BASE}/memory/${agentId}`),

  // Watchdog
  watchdogHealth:  () => _get<WatchdogHealth>(`${WDOG}/health`),
  watchdogAgents:  () => _get<WatchdogAgentStatus[]>(`${WDOG}/agents`),
  watchdogEvents:  (n = 50, severity?: string) => {
    const q = severity ? `?n=${n}&severity=${severity}` : `?n=${n}`
    return _get<WatchdogEvent[]>(`${WDOG}/events${q}`)
  },
  watchdogMetrics: () => fetch(`${WDOG}/metrics`).then(r => r.text()),
  watchdogHeal:    (agentId: string) => _post<{ success: boolean }>(`${WDOG}/heal/${agentId}`),
}
