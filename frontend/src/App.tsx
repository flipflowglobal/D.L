import React from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import Dashboard  from './pages/Dashboard'
import Agents     from './pages/Agents'
import Swarm      from './pages/Swarm'
import Watchdog   from './pages/Watchdog'
import Trades     from './pages/Trades'
import HotSwap    from './pages/HotSwap'
import FlashLoans from './pages/FlashLoans'
import Memory     from './pages/Memory'

export default function App() {
  return (
    <BrowserRouter>
      <div style={{ display: 'flex', minHeight: '100vh' }}>
        <Sidebar />
        <main style={{ flex: 1, padding: 28, overflowY: 'auto', maxHeight: '100vh' }}>
          <Routes>
            <Route path="/"           element={<Dashboard />} />
            <Route path="/agents"     element={<Agents />} />
            <Route path="/swarm"      element={<Swarm />} />
            <Route path="/watchdog"   element={<Watchdog />} />
            <Route path="/trades"     element={<Trades />} />
            <Route path="/hotswap"    element={<HotSwap />} />
            <Route path="/flashloans" element={<FlashLoans />} />
            <Route path="/memory"     element={<Memory />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
