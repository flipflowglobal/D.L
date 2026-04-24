import React, { useState } from 'react'
import { api, WatchdogEvent } from '../api'
import { useApi } from '../hooks/useApi'
import Card from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import Button from '../components/Button'

const SEV_COLOR: Record<string, string> = {
  INFO: 'var(--accent)', WARNING: 'var(--yellow)', CRITICAL: 'var(--red)', HEALED: 'var(--green)',
}

export default function Watchdog() {
  const { data: health, refresh: refreshHealth }  = useApi(api.watchdogHealth, 5000)
  const { data: wdAgents, refresh: refreshAgents } = useApi(api.watchdogAgents, 5000)
  const [severity, setSeverity]  = useState<string>('')
  const { data: events }         = useApi(() => api.watchdogEvents(50, severity || undefined), 3000)
  const [healing, setHealing]    = useState<Record<string, boolean>>({})

  const heal = async (agentId: string) => {
    setHealing(h => ({ ...h, [agentId]: true }))
    try {
      await api.watchdogHeal(agentId)
      refreshHealth(); refreshAgents()
    } catch (e) { alert(String(e)) }
    finally { setHealing(h => ({ ...h, [agentId]: false })) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>Watchdog Legion</h1>

      {/* Summary KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        {[
          { label: 'Status',   value: health?.status ?? '…', color: health?.status === 'OK' ? 'var(--green)' : 'var(--yellow)' },
          { label: 'Agents',   value: health?.total_agents ?? '…' },
          { label: 'Critical', value: health?.critical_events ?? '…', color: 'var(--red)' },
          { label: 'Heals',    value: health?.heals_performed ?? '…', color: 'var(--green)' },
        ].map(({ label, value, color }) => (
          <Card key={label}>
            <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: color ?? 'var(--text)' }}>{value}</div>
          </Card>
        ))}
      </div>

      {/* Agent status table */}
      <Card title="Agent Status">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ color: 'var(--muted)', textAlign: 'left' }}>
              {['Agent ID','Source','Running','Failures','Checks','Last OK (s)','Last Event','Actions'].map(h => (
                <th key={h} style={{ padding: '6px 8px', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {wdAgents?.map(a => (
              <tr key={a.agent_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: 11 }}>{a.agent_id}</td>
                <td style={{ padding: '8px', color: 'var(--muted)' }}>{a.source}</td>
                <td style={{ padding: '8px' }}><StatusBadge status={a.running ? 'running' : 'stopped'} /></td>
                <td style={{ padding: '8px', color: a.failures > 0 ? 'var(--red)' : 'var(--text)' }}>{a.failures}</td>
                <td style={{ padding: '8px' }}>{a.checks}</td>
                <td style={{ padding: '8px', color: a.last_ok_ago_s > 60 ? 'var(--yellow)' : 'var(--text)' }}>
                  {a.last_ok_ago_s.toFixed(1)}
                </td>
                <td style={{ padding: '8px' }}>
                  {a.last_event_type
                    ? <span style={{ color: SEV_COLOR[a.last_severity ?? ''] ?? 'var(--muted)', fontSize: 11 }}>
                        {a.last_event_type}
                      </span>
                    : <span style={{ color: 'var(--muted)' }}>—</span>
                  }
                </td>
                <td style={{ padding: '8px' }}>
                  <Button size="sm" variant="ghost" loading={healing[a.agent_id]} onClick={() => heal(a.agent_id)}>
                    Heal
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* Event stream */}
      <Card
        title="Event Stream"
        action={
          <div style={{ display: 'flex', gap: 6 }}>
            {['', 'INFO', 'WARNING', 'CRITICAL', 'HEALED'].map(s => (
              <button
                key={s}
                onClick={() => setSeverity(s)}
                style={{
                  padding: '3px 8px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                  border: `1px solid ${severity === s ? 'var(--accent)' : 'var(--border)'}`,
                  background: severity === s ? 'rgba(34,211,238,0.1)' : 'transparent',
                  color: severity === s ? 'var(--accent)' : 'var(--muted)',
                  cursor: 'pointer',
                }}
              >
                {s || 'ALL'}
              </button>
            ))}
          </div>
        }
      >
        <div style={{ maxHeight: 360, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
          {events?.slice().reverse().map((e: WatchdogEvent, i: number) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'flex-start', gap: 10,
              padding: '6px 8px', borderRadius: 6,
              background: e.severity === 'CRITICAL' ? 'rgba(248,113,113,0.05)' : 'transparent',
            }}>
              <span style={{ color: SEV_COLOR[e.severity] ?? 'var(--muted)', fontSize: 11, minWidth: 60, fontWeight: 600 }}>
                {e.severity}
              </span>
              <span style={{ color: 'var(--muted)', fontSize: 11, minWidth: 120, fontFamily: 'monospace' }}>
                {e.agent_id}
              </span>
              <span style={{ fontSize: 12, flex: 1 }}>{e.message}</span>
              <span style={{ color: 'var(--muted)', fontSize: 11, whiteSpace: 'nowrap' }}>
                {new Date(e.wall_time * 1000).toLocaleTimeString()}
              </span>
            </div>
          ))}
          {events?.length === 0 && (
            <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 20 }}>No events</p>
          )}
        </div>
      </Card>
    </div>
  )
}
