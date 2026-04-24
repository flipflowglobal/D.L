import React from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, Bot, Activity, Shield,
  BarChart2, Cpu, Zap, BookOpen,
} from 'lucide-react'

const NAV = [
  { to: '/',           icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/agents',     icon: Bot,             label: 'Agents' },
  { to: '/swarm',      icon: Activity,        label: 'Swarm' },
  { to: '/watchdog',   icon: Shield,          label: 'Watchdog' },
  { to: '/trades',     icon: BarChart2,       label: 'Trades' },
  { to: '/hotswap',    icon: Cpu,             label: 'Hot-Swap' },
  { to: '/flashloans', icon: Zap,             label: 'Flash Loans' },
  { to: '/memory',     icon: BookOpen,        label: 'Memory' },
]

export default function Sidebar() {
  return (
    <nav style={{
      width: 220, minHeight: '100vh', background: 'var(--surface)',
      borderRight: '1px solid var(--border)', display: 'flex',
      flexDirection: 'column', gap: 4, padding: '16px 8px',
    }}>
      {/* Logo */}
      <div style={{ padding: '8px 12px 20px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <svg width="24" height="24" viewBox="0 0 32 32">
          <circle cx="16" cy="16" r="14" fill="#0a0f1e"/>
          <polygon points="16,4 28,24 4,24" fill="none" stroke="#22d3ee" strokeWidth="2"/>
          <circle cx="16" cy="16" r="3" fill="#22d3ee"/>
        </svg>
        <span style={{ fontWeight: 700, fontSize: 16, color: 'var(--text)' }}>AUREON</span>
      </div>

      {NAV.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          style={({ isActive }) => ({
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '8px 12px', borderRadius: 'var(--radius)',
            color: isActive ? 'var(--accent)' : 'var(--muted)',
            background: isActive ? 'rgba(34,211,238,0.08)' : 'transparent',
            fontWeight: isActive ? 600 : 400,
            transition: 'all 0.15s',
          })}
        >
          <Icon size={16} />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
