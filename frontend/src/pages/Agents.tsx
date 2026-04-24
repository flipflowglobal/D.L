import React, { useState } from 'react'
import { api, CreateAgentPayload } from '../api'
import { useApi } from '../hooks/useApi'
import Card from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'

const STRATEGIES = ['arb', 'ppo', 'mean_reversion', 'flash_loan', 'adaptive']
const CHAINS     = ['ethereum', 'arbitrum', 'polygon', 'bsc', 'base']
const TOKENS     = ['ETH', 'USDC', 'WBTC', 'ARB', 'MATIC']

const DEFAULT: CreateAgentPayload = {
  name: 'Agent-1', strategy: 'arb', chain: 'ethereum', token: 'ETH',
  initial_capital: 10000, trade_size_eth: 0.05, min_profit_usd: 2.0,
  scan_interval: 30, dry_run: true,
}

export default function Agents() {
  const { data: agents, loading, refresh } = useApi(api.listAgents, 5000)
  const [form, setForm]     = useState<CreateAgentPayload>(DEFAULT)
  const [creating, setCreating] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy]     = useState<Record<string, boolean>>({})

  const set = (k: keyof CreateAgentPayload, v: unknown) =>
    setForm(f => ({ ...f, [k]: v }))

  const create = async () => {
    setCreating(true)
    try { await api.createAgent(form); refresh() }
    catch (e) { alert(String(e)) }
    finally { setCreating(false); setShowForm(false) }
  }

  const toggle = async (id: string, running: boolean) => {
    setBusy(b => ({ ...b, [id]: true }))
    try {
      running ? await api.stopAgent(id) : await api.startAgent(id)
      refresh()
    } catch (e) { alert(String(e)) }
    finally { setBusy(b => ({ ...b, [id]: false })) }
  }

  const remove = async (id: string) => {
    if (!confirm('Delete this agent?')) return
    setBusy(b => ({ ...b, [id]: true }))
    try { await api.deleteAgent(id); refresh() }
    catch (e) { alert(String(e)) }
    finally { setBusy(b => ({ ...b, [id]: false })) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ fontSize: 20, fontWeight: 700 }}>Trading Agents</h1>
        <Button onClick={() => setShowForm(s => !s)}>+ New Agent</Button>
      </div>

      {showForm && (
        <Card title="Create Agent">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
            {([
              ['name', 'Name', 'text'],
              ['initial_capital', 'Capital ($)', 'number'],
              ['trade_size_eth', 'Trade size (ETH)', 'number'],
              ['min_profit_usd', 'Min profit ($)', 'number'],
              ['scan_interval', 'Interval (s)', 'number'],
            ] as [keyof CreateAgentPayload, string, string][]).map(([k, label, type]) => (
              <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{label}</span>
                <input
                  type={type}
                  value={String(form[k] ?? '')}
                  onChange={e => set(k, type === 'number' ? Number(e.target.value) : e.target.value)}
                  style={{
                    background: 'var(--bg)', border: '1px solid var(--border)',
                    borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13,
                  }}
                />
              </label>
            ))}

            {(['strategy', 'chain', 'token'] as const).map(k => (
              <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'capitalize' }}>{k}</span>
                <select
                  value={String(form[k])}
                  onChange={e => set(k, e.target.value)}
                  style={{
                    background: 'var(--bg)', border: '1px solid var(--border)',
                    borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13,
                  }}
                >
                  {(k === 'strategy' ? STRATEGIES : k === 'chain' ? CHAINS : TOKENS).map(v => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
            <input type="checkbox" checked={form.dry_run} onChange={e => set('dry_run', e.target.checked)} />
            <span style={{ fontSize: 13 }}>Dry run (no real transactions)</span>
          </label>

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <Button onClick={create} loading={creating}>Create</Button>
            <Button variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button>
          </div>
        </Card>
      )}

      {loading && <p style={{ color: 'var(--muted)' }}>Loading…</p>}

      {agents?.map(agent => (
        <Card key={agent.id} style={{ borderLeft: `3px solid ${agent.running ? 'var(--green)' : 'var(--border)'}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontWeight: 700 }}>{agent.name}</span>
                <StatusBadge status={agent.running ? 'running' : 'stopped'} />
                {agent.dry_run && <span className="badge badge-purple">DRY RUN</span>}
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                {agent.strategy} · {agent.chain} · {agent.token}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button
                size="sm"
                variant={agent.running ? 'danger' : 'primary'}
                loading={busy[agent.id]}
                onClick={() => toggle(agent.id, agent.running)}
              >
                {agent.running ? 'Stop' : 'Start'}
              </Button>
              <Button size="sm" variant="ghost" loading={busy[agent.id]} onClick={() => remove(agent.id)}>
                Delete
              </Button>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16, marginTop: 16 }}>
            {[
              { label: 'Capital',    value: `$${agent.capital.toFixed(0)}` },
              { label: 'P&L',        value: `$${agent.total_pnl.toFixed(2)}`, color: agent.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' },
              { label: 'Trades',     value: agent.trades_made },
              { label: 'Win Rate',   value: `${(agent.win_rate * 100).toFixed(1)}%` },
              { label: 'Cycles',     value: agent.cycle_count },
            ].map(({ label, value, color }) => (
              <div key={label}>
                <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>{label}</div>
                <div style={{ fontSize: 16, fontWeight: 700, color: color ?? 'var(--text)', marginTop: 2 }}>{value}</div>
              </div>
            ))}
          </div>
        </Card>
      ))}

      {!loading && agents?.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>
          No agents yet. Click "+ New Agent" to create one.
        </p>
      )}
    </div>
  )
}
