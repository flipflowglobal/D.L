import React from 'react'

type Status = 'ok' | 'warn' | 'crit' | 'info' | 'running' | 'stopped' | string

const MAP: Record<string, string> = {
  ok: 'badge-ok', OK: 'badge-ok', healthy: 'badge-ok', running: 'badge-ok',
  warn: 'badge-warn', WARNING: 'badge-warn', degraded: 'badge-warn', DEGRADED: 'badge-warn',
  crit: 'badge-crit', CRITICAL: 'badge-crit', critical: 'badge-crit', stopped: 'badge-crit',
  info: 'badge-info', INFO: 'badge-info',
}

export default function StatusBadge({ status }: { status: Status }) {
  const cls = MAP[status] ?? 'badge-info'
  return <span className={`badge ${cls}`}>{status}</span>
}
