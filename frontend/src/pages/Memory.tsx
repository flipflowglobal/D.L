import React, { useState } from 'react'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import Card from '../components/Card'
import Button from '../components/Button'

export default function Memory() {
  const { data: agents }                       = useApi(api.listAgents, 10000)
  const [agentId, setAgentId]                  = useState<string>('')
  const [memory, setMemory]                    = useState<Record<string, string> | null>(null)
  const [loading, setLoading]                  = useState(false)
  const [clearing, setClearing]                = useState(false)

  const load = async () => {
    if (!agentId) return
    setLoading(true)
    try { setMemory(await api.getMemory(agentId)) }
    catch (e) { alert(String(e)) }
    finally { setLoading(false) }
  }

  const clear = async () => {
    if (!agentId || !confirm('Clear all memory for this agent?')) return
    setClearing(true)
    try { await api.deleteMemory(agentId); setMemory({}) }
    catch (e) { alert(String(e)) }
    finally { setClearing(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>Agent Memory</h1>

      <Card title="Select Agent">
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>Agent ID</div>
            <select
              value={agentId}
              onChange={e => setAgentId(e.target.value)}
              style={{ width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 13 }}
            >
              <option value="">— choose agent —</option>
              {agents?.map(a => <option key={a.id} value={a.id}>{a.name} ({a.id.slice(0, 8)})</option>)}
            </select>
          </div>
          <Button onClick={load} loading={loading}>Load</Button>
          <Button variant="danger" onClick={clear} loading={clearing}>Clear</Button>
        </div>
      </Card>

      {memory && (
        <Card title={`Memory — ${agentId.slice(0, 12)}…`} subtitle={`${Object.keys(memory).length} keys`}>
          {Object.keys(memory).length === 0 ? (
            <p style={{ color: 'var(--muted)' }}>No memory entries.</p>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, fontFamily: 'monospace' }}>
              <thead>
                <tr style={{ color: 'var(--muted)', textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px', width: '40%' }}>Key</th>
                  <th style={{ padding: '6px 8px' }}>Value</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(memory).map(([k, v]) => (
                  <tr key={k} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 8px', color: 'var(--accent)' }}>{k}</td>
                    <td style={{ padding: '6px 8px', color: 'var(--muted)', wordBreak: 'break-all' }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  )
}
