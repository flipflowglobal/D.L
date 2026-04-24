import React from 'react'

interface CardProps {
  title?: string
  subtitle?: string
  children: React.ReactNode
  style?: React.CSSProperties
  action?: React.ReactNode
}

export default function Card({ title, subtitle, children, style, action }: CardProps) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 'var(--radius)', padding: 20, ...style,
    }}>
      {(title || action) && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
          <div>
            {title && <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{title}</h3>}
            {subtitle && <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </div>
  )
}

export function Stat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: color ?? 'var(--text)', marginTop: 2 }}>{value}</div>
    </div>
  )
}
