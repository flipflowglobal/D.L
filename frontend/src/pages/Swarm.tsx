import React from 'react'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import Card, { Stat } from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'

export default function Swarm() {
  const { data: metrics, refresh } = useApi(api.swarmMetrics, 4000)
  const { data: consensus }        = useApi(api.swarmConsensus, 6000)

  const start = async () => { try { await api.swarmStart(); refresh() } catch (e) { alert(String(e)) } }
  const stop  = async () => { try { await api.swarmStop();  refresh() } catch (e) { alert(String(e)) } }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ fontSize: 20, fontWeight: 700 }}>Swarm Consensus</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button size="sm" onClick={start}>Start Swarm</Button>
          <Button size="sm" variant="danger" onClick={stop}>Stop Swarm</Button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        <Card><Stat label="Total Agents"   value={metrics?.total_agents   ?? '…'} /></Card>
        <Card><Stat label="Running"        value={metrics?.running_agents  ?? '…'} color="var(--green)" /></Card>
        <Card><Stat label="Total P&L"      value={`$${(metrics?.total_pnl ?? 0).toFixed(2)}`}
          color={(metrics?.total_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'} /></Card>
        <Card><Stat label="Consensus"      value={`${((metrics?.consensus_score ?? 0) * 100).toFixed(0)}%`}
          color="var(--accent)" /></Card>
      </div>

      {consensus && (
        <Card title="Consensus Details">
          <pre style={{
            background: 'var(--bg)', padding: 16, borderRadius: 6, fontSize: 11,
            color: 'var(--muted)', overflow: 'auto', maxHeight: 400,
          }}>
            {JSON.stringify(consensus, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  )
}
