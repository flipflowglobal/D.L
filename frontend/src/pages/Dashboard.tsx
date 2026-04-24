import React from 'react'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import Card, { Stat } from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'

export default function Dashboard() {
  const { data: health, loading: hl } = useApi(api.health, 5000)
  const { data: status, loading: sl } = useApi(api.status, 3000)
  const { data: agents }              = useApi(api.listAgents, 5000)
  const { data: wdog }                = useApi(api.watchdogHealth, 5000)

  const totalPnl = agents?.reduce((s, a) => s + a.total_pnl, 0) ?? 0
  const running  = agents?.filter(a => a.running).length ?? 0

  const pnlHistory = agents?.slice(0, 6).map((a, i) => ({
    name: a.name.slice(0, 8),
    pnl: parseFloat(a.total_pnl.toFixed(2)),
  })) ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>Dashboard</h1>

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        <Card>
          <Stat label="System"
            value={hl ? '…' : (health?.status ?? '?')}
            color={health?.status === 'ok' ? 'var(--green)' : 'var(--red)'} />
        </Card>
        <Card>
          <Stat label="Running Agents" value={sl ? '…' : running} color="var(--accent)" />
        </Card>
        <Card>
          <Stat label="Total P&L"
            value={`$${totalPnl.toFixed(2)}`}
            color={totalPnl >= 0 ? 'var(--green)' : 'var(--red)'} />
        </Card>
        <Card>
          <Stat label="Watchdog"
            value={wdog?.status ?? '…'}
            color={wdog?.status === 'OK' ? 'var(--green)' : 'var(--yellow)'} />
        </Card>
      </div>

      {/* Loop + watchdog row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <Card title="Agent Loop" subtitle="Main autonomous trading loop">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <StatusBadge status={status?.running ? 'running' : 'stopped'} />
              <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                {status?.cycle_count ?? 0} cycles
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button size="sm" onClick={() => api.loopStart()}>Start Loop</Button>
              <Button size="sm" variant="danger" onClick={() => api.loopStop()}>Stop Loop</Button>
            </div>
          </div>
        </Card>

        <Card title="Watchdog Legion" subtitle="Self-healing agent health monitor">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <Stat label="Agents" value={wdog?.total_agents ?? '…'} />
              <Stat label="Critical" value={wdog?.critical_events ?? '…'} color="var(--red)" />
              <Stat label="Heals" value={wdog?.heals_performed ?? '…'} color="var(--green)" />
            </div>
            {wdog?.severity_counts && (
              <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                {Object.entries(wdog.severity_counts).map(([k, v]) => (
                  <StatusBadge key={k} status={k} />
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* P&L Chart */}
      {pnlHistory.length > 0 && (
        <Card title="Agent P&L Snapshot">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={pnlHistory}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="name" stroke="var(--muted)" tick={{ fontSize: 11 }} />
              <YAxis stroke="var(--muted)" tick={{ fontSize: 11 }} />
              <Tooltip
                contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6 }}
                labelStyle={{ color: 'var(--text)' }}
              />
              <Line type="monotone" dataKey="pnl" stroke="var(--accent)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Recent agents */}
      {agents && agents.length > 0 && (
        <Card title="Active Agents">
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', textAlign: 'left' }}>
                {['Name','Strategy','Chain','Status','P&L','Win Rate'].map(h => (
                  <th key={h} style={{ padding: '6px 8px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agents.map(a => (
                <tr key={a.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 8px', fontWeight: 600 }}>{a.name}</td>
                  <td style={{ padding: '8px 8px', color: 'var(--muted)' }}>{a.strategy}</td>
                  <td style={{ padding: '8px 8px', color: 'var(--muted)' }}>{a.chain}</td>
                  <td style={{ padding: '8px 8px' }}><StatusBadge status={a.running ? 'running' : 'stopped'} /></td>
                  <td style={{ padding: '8px 8px', color: a.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    ${a.total_pnl.toFixed(2)}
                  </td>
                  <td style={{ padding: '8px 8px' }}>{(a.win_rate * 100).toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}
