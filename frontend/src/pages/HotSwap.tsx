import React from 'react'
import Card from '../components/Card'
import StatusBadge from '../components/StatusBadge'

const MODULES = [
  { name: 'engine.portfolio',                  kind: 'Cython', path: 'engine/portfolio.pyx',                          desc: 'Portfolio buy/sell/summary tight loops' },
  { name: 'engine.risk_manager',               kind: 'Cython', path: 'engine/risk_manager.pyx',                       desc: 'can_trade() / record_trade() per cycle' },
  { name: 'engine.strategies.mean_reversion',  kind: 'Cython', path: 'engine/strategies/mean_reversion.pyx',          desc: 'C ring-buffer rolling mean' },
  { name: 'dex-oracle',                        kind: 'Rust',   path: 'dex-oracle/src/',                               desc: 'On-chain DEX price oracle sidecar' },
  { name: 'tx-engine',                         kind: 'Rust',   path: 'tx-engine/src/',                                desc: 'Transaction signing & submission sidecar' },
  { name: 'hinsdale',                          kind: 'Rust',   path: 'hinsdale/src/',                                 desc: 'EVM bytecode decompiler crate' },
]

export default function HotSwap() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>Hot-Swap Extensions</h1>
      <p style={{ color: 'var(--muted)', maxWidth: 640 }}>
        The <code style={{ color: 'var(--accent)' }}>HotSwapController</code> watches source files
        and recompiles Cython extensions (.pyx → .so) or Rust crates in-process
        without restarting the server.  File changes trigger debounced rebuilds
        within 500ms.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {MODULES.map(m => (
          <Card key={m.name} style={{ borderLeft: `3px solid ${m.kind === 'Rust' ? 'var(--yellow)' : 'var(--accent)'}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ fontWeight: 700, fontFamily: 'monospace', fontSize: 13 }}>{m.name}</span>
              <span className={`badge ${m.kind === 'Rust' ? 'badge-warn' : 'badge-info'}`}>{m.kind}</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>{m.desc}</div>
            <div style={{ fontSize: 11, color: 'var(--border)', fontFamily: 'monospace' }}>{m.path}</div>
          </Card>
        ))}
      </div>

      <Card title="How It Works">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13, color: 'var(--muted)' }}>
          <div>
            <strong style={{ color: 'var(--text)' }}>1. File watcher</strong>
            {' — '}Polls source files every 2s using stat() — works on Linux, macOS, Docker, and Termux.
          </div>
          <div>
            <strong style={{ color: 'var(--text)' }}>2. Debounce</strong>
            {' — '}Collects all changes within a 500ms window before triggering a rebuild to avoid partial-save artifacts.
          </div>
          <div>
            <strong style={{ color: 'var(--text)' }}>3. Cython rebuild</strong>
            {' — '}Runs <code style={{ color: 'var(--accent)' }}>python setup_cython.py build_ext --inplace</code> targeting
            only the changed .pyx file, then calls <code style={{ color: 'var(--accent)' }}>importlib.reload()</code>.
          </div>
          <div>
            <strong style={{ color: 'var(--text)' }}>4. Rust rebuild</strong>
            {' — '}Runs <code style={{ color: 'var(--accent)' }}>cargo build --release</code> in the crate directory.
            After a successful build, atomically replaces a symlink
            (<code style={{ color: 'var(--accent)' }}>sidecar.live</code>) pointing to the new binary.
          </div>
          <div>
            <strong style={{ color: 'var(--text)' }}>5. Zero downtime</strong>
            {' — '}In-flight requests complete against the old .so.  New calls see the fresh code
            immediately via Python's module cache.
          </div>
        </div>
      </Card>
    </div>
  )
}
