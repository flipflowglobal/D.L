import React, { useState } from 'react'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import Card from '../components/Card'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'

export default function Trades() {
  const { data: agents } = useApi(api.listAgents, 5000)
  const [selected, setSelected] = useState<string | null>(null)
  const { data: perf } = useApi(
    () => selected ? api.performance(selected) : Promise.resolve(null),
    5000,
  )

  const allTrades = agents?.reduce((s, a) => s + a.trades_made, 0) ?? 0
  const totalPnl  = agents?.reduce((s, a) => s + a.total_pnl,  0) ?? 0

  // Build a synthetic capital-over-time from agents
  const chartData = agents?.map((a, i) => ({
    name: a.name.slice(0, 8), capital: a.capital, pnl: a.total_pnl,
  })) ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>Trade Performance</h1>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        <Card>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>Total Trades</div>
          <div style={{ fontSize: 28, fontWeight: 700, marginTop: 4 }}>{allTrades}</div>
        </Card>
        <Card>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>Combined P&L</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)', marginTop: 4 }}>
            ${totalPnl.toFixed(2)}
          </div>
        </Card>
        <Card>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>Avg Win Rate</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent)', marginTop: 4 }}>
            {agents?.length
              ? `${(agents.reduce((s, a) => s + a.win_rate, 0) / agents.length * 100).toFixed(1)}%`
              : '—'}
          </div>
        </Card>
      </div>

      {chartData.length > 0 && (
        <Card title="Capital by Agent">
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--accent)" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="name" stroke="var(--muted)" tick={{ fontSize: 11 }} />
              <YAxis stroke="var(--muted)" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6 }} />
              <Area type="monotone" dataKey="capital" stroke="var(--accent)" fill="url(#grad)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Per-agent detail */}
      <Card title="Agent Performance">
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          {agents?.map(a => (
            <button key={a.id} onClick={() => setSelected(a.id)}
              style={{
                padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                border: `1px solid ${selected === a.id ? 'var(--accent)' : 'var(--border)'}`,
                background: selected === a.id ? 'rgba(34,211,238,0.1)' : 'var(--bg)',
                color: selected === a.id ? 'var(--accent)' : 'var(--muted)',
              }}
            >{a.name}</button>
          ))}
        </div>
        {perf ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
            {[
              { label: 'Capital',      value: `$${perf.capital.toFixed(2)}` },
              { label: 'Total P&L',    value: `$${perf.total_pnl.toFixed(2)}`, color: perf.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' },
              { label: 'Win Rate',     value: `${(perf.win_rate * 100).toFixed(1)}%` },
              { label: 'Trades',       value: perf.trades_made },
              { label: 'Sharpe',       value: perf.sharpe_ratio.toFixed(3) },
              { label: 'Max Drawdown', value: `${(perf.max_drawdown * 100).toFixed(1)}%`, color: 'var(--red)' },
            ].map(({ label, value, color }) => (
              <div key={label}>
                <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>{label}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: color ?? 'var(--text)', marginTop: 4 }}>{value}</div>
              </div>
            ))}
          </div>
        ) : (
          <p style={{ color: 'var(--muted)' }}>Select an agent above to see detailed performance.</p>
        )}
      </Card>
    </div>
  )
}
