import { StrictMode, useState, type FormEvent, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './index.css'
import { AuthProvider, useAuth, type Role } from './lib/auth'
import { post } from './lib/api'
import Layout from './components/Layout'
import { Button, ErrorBox, Field, Input, Toaster } from './components/ui'
import Overview from './pages/Overview'
import Live from './pages/Live'
import Calls from './pages/Calls'
import Records from './pages/Records'
import Knowledge from './pages/Knowledge'
import AgentSettings from './pages/AgentSettings'
import Outbound from './pages/Outbound'
import Workflows from './pages/Workflows'
import TestConsole from './pages/TestConsole'
import SystemPage from './pages/System'
import Admin from './pages/Admin'

function Login() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-stone-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" className="h-8 w-8" alt="" />
          <div>
            <div className="font-semibold">BongoHive AI Lab</div>
            <div className="text-xs text-stone-500">Voice Agent Console</div>
          </div>
        </div>
        <ErrorBox error={error} />
        <Field label="Email">
          <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
        <Field label="Password">
          <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>
        <Button variant="primary" className="w-full" loading={busy}>
          Sign in
        </Button>
      </form>
    </div>
  )
}

function ChangePassword() {
  const { refresh } = useAuth()
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [error, setError] = useState<string | null>(null)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    try {
      await post('/api/auth/change-password', { current_password: cur, new_password: next })
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    }
  }
  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-stone-200 bg-white p-6 shadow-sm">
        <h1 className="font-semibold">Choose a new password</h1>
        <p className="text-sm text-stone-500">Your account uses a temporary password. Set a new one (10+ characters, mixed case and a digit).</p>
        <ErrorBox error={error} />
        <Field label="Current password">
          <Input type="password" value={cur} onChange={(e) => setCur(e.target.value)} required autoComplete="current-password" />
        </Field>
        <Field label="New password">
          <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} autoComplete="new-password" />
        </Field>
        <Button variant="primary" className="w-full">Save password</Button>
      </form>
    </div>
  )
}

function Guard({ role, children }: { role: Role; children: ReactNode }) {
  const { can } = useAuth()
  return can(role) ? <>{children}</> : <Navigate to="/" replace />
}

function App() {
  const { user, loading } = useAuth()
  if (loading) return <div className="p-10 text-center text-sm text-stone-500">Loading…</div>
  if (!user) return <Login />
  if (user.must_change_password) return <ChangePassword />
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="live" element={<Guard role="operator"><Live /></Guard>} />
        <Route path="calls" element={<Calls />} />
        <Route path="calls/:id" element={<Calls />} />
        <Route path="records" element={<Records />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="agent" element={<AgentSettings />} />
        <Route path="outbound" element={<Outbound />} />
        <Route path="workflows" element={<Workflows />} />
        <Route path="test" element={<Guard role="operator"><TestConsole /></Guard>} />
        <Route path="system" element={<SystemPage />} />
        <Route path="admin" element={<Guard role="admin"><Admin /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
        <Toaster />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
