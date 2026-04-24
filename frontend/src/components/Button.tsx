import React from 'react'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'danger' | 'ghost'
  size?: 'sm' | 'md'
  loading?: boolean
}

export default function Button({
  variant = 'primary', size = 'md', loading, children, disabled, style, ...rest
}: ButtonProps) {
  const colors: Record<string, { bg: string; color: string; border: string }> = {
    primary: { bg: 'rgba(34,211,238,0.15)', color: 'var(--accent)',  border: 'var(--accent)' },
    danger:  { bg: 'rgba(248,113,113,0.12)', color: 'var(--red)',    border: 'var(--red)' },
    ghost:   { bg: 'transparent',            color: 'var(--muted)',  border: 'var(--border)' },
  }
  const c = colors[variant]
  const pad = size === 'sm' ? '4px 10px' : '7px 16px'
  const fs  = size === 'sm' ? 12 : 13

  return (
    <button
      disabled={disabled || loading}
      style={{
        background: c.bg, color: c.color, border: `1px solid ${c.border}`,
        padding: pad, borderRadius: 'var(--radius)', fontSize: fs, fontWeight: 600,
        cursor: disabled || loading ? 'not-allowed' : 'pointer',
        opacity: disabled || loading ? 0.5 : 1,
        transition: 'all 0.15s',
        ...style,
      }}
      {...rest}
    >
      {loading ? '…' : children}
    </button>
  )
}
