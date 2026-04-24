import React, { useState } from 'react'
import Card from '../components/Card'
import Button from '../components/Button'

interface SimResult {
  profit: number
  path: string
  gas_est: number
}

export default function FlashLoans() {
  const [amount, setAmount]   = useState('10000')
  const [token,  setToken]    = useState('ETH')
  const [dex,    setDex]      = useState('uniswap_v3')
  const [result, setResult]   = useState<SimResult | null>(null)
  const [loading, setLoading] = useState(false)

  const simulate = async () => {
    setLoading(true)
    setResult(null)
    try {
      // Call backend flash loan simulation endpoint
      const r = await fetch('/api/agents', { method: 'GET' })
      if (!r.ok) throw new Error(`${r.status}`)
      // For now show a synthetic preview (real call would be POST /flashloan/simulate)
      const spread  = Math.random() * 0.003 + 0.0005
      const premium = 0.0009
      const net     = (spread - premium) * Number(amount) * (token === 'ETH' ? 3000 : 1)
      setResult({ profit: parseFloat(net.toFixed(4)), path: `${token} → USDC → ${token}`, gas_est: 320000 })
    } catch (e) { alert(String(e)) }
    finally { setLoading(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>Flash Loan Simulator</h1>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        <Card title="Configure">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {[
              { label: 'Borrow Amount', value: amount, set: setAmount, type: 'number' },
            ].map(({ label, value, set, type }) => (
              <label key={label} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{label}</span>
                <input type={type} value={value} onChange={e => set(e.target.value)}
                  style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13 }} />
              </label>
            ))}

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>Token</span>
              <select value={token} onChange={e => setToken(e.target.value)}
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13 }}>
                {['ETH','USDC','WBTC','DAI'].map(t => <option key={t}>{t}</option>)}
              </select>
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>DEX Route</span>
              <select value={dex} onChange={e => setDex(e.target.value)}
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13 }}>
                {['uniswap_v3','sushiswap','curve','balancer'].map(d => <option key={d}>{d}</option>)}
              </select>
            </label>

            <Button onClick={simulate} loading={loading}>Simulate</Button>
          </div>
        </Card>

        {result && (
          <Card title="Simulation Result">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>Estimated Profit</div>
                <div style={{ fontSize: 32, fontWeight: 700, color: result.profit > 0 ? 'var(--green)' : 'var(--red)', marginTop: 4 }}>
                  {result.profit > 0 ? '+' : ''}{result.profit} {token}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>Path</div>
                <div style={{ fontFamily: 'monospace', fontSize: 13, marginTop: 4, color: 'var(--accent)' }}>{result.path}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>Estimated Gas</div>
                <div style={{ fontSize: 16, marginTop: 4 }}>{result.gas_est.toLocaleString()} units</div>
              </div>
            </div>
          </Card>
        )}
      </div>

      <Card title="Protocol Info">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, fontSize: 13 }}>
          {[
            { label: 'Protocol', value: 'Aave V3' },
            { label: 'Flash Fee', value: '0.09%' },
            { label: 'Max Loan', value: 'Pool liquidity' },
            { label: 'Networks', value: 'Ethereum, Polygon, Arbitrum' },
            { label: 'Contract', value: 'NexusFlashReceiver.sol' },
            { label: 'Strategy', value: 'Thompson Sampling DEX router' },
          ].map(({ label, value }) => (
            <div key={label}>
              <div style={{ color: 'var(--muted)', fontSize: 11, textTransform: 'uppercase' }}>{label}</div>
              <div style={{ marginTop: 4 }}>{value}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
