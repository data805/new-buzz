import { useState, type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard, Radio, PhoneCall, BookOpen, Bot, Megaphone, Workflow, FlaskConical, Activity, ShieldCheck, Inbox, LogOut, Menu, X,
} from 'lucide-react'
import { useAuth, type Role } from '../lib/auth'
import { cx } from './ui'

const NAV: { to: string; label: string; icon: ReactNode; role: Role }[] = [
  { to: '/', label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" />, role: 'viewer' },
  { to: '/live', label: 'Live calls', icon: <Radio className="h-4 w-4" />, role: 'operator' },
  { to: '/calls', label: 'Call history', icon: <PhoneCall className="h-4 w-4" />, role: 'viewer' },
  { to: '/records', label: 'Follow-ups', icon: <Inbox className="h-4 w-4" />, role: 'viewer' },
  { to: '/knowledge', label: 'Knowledge', icon: <BookOpen className="h-4 w-4" />, role: 'viewer' },
  { to: '/agent', label: 'Agent settings', icon: <Bot className="h-4 w-4" />, role: 'viewer' },
  { to: '/outbound', label: 'Outbound', icon: <Megaphone className="h-4 w-4" />, role: 'viewer' },
  { to: '/workflows', label: 'Workflows', icon: <Workflow className="h-4 w-4" />, role: 'viewer' },
  { to: '/test', label: 'Test console', icon: <FlaskConical className="h-4 w-4" />, role: 'operator' },
  { to: '/system', label: 'System', icon: <Activity className="h-4 w-4" />, role: 'viewer' },
  { to: '/admin', label: 'Security & access', icon: <ShieldCheck className="h-4 w-4" />, role: 'admin' },
]

export default function Layout() {
  const { user, logout, can } = useAuth()
  const [open, setOpen] = useState(false)
  const nav = (
    <nav className="flex flex-col gap-0.5 p-3">
      {NAV.filter((n) => can(n.role)).map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.to === '/'}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            cx('flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium', isActive ? 'bg-brand-50 text-brand-700' : 'text-stone-600 hover:bg-stone-100 hover:text-stone-900')
          }
        >
          {n.icon}
          {n.label}
        </NavLink>
      ))}
    </nav>
  )
  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-stone-200 bg-white lg:flex">
        <Brand />
        <div className="flex-1 overflow-y-auto scroll-thin">{nav}</div>
        <UserBox name={user?.name || user?.email || ''} role={user?.role || ''} onLogout={logout} />
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 bg-stone-900/40 lg:hidden" onClick={() => setOpen(false)}>
          <aside className="flex h-full w-64 flex-col bg-white" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between pr-2">
              <Brand />
              <button onClick={() => setOpen(false)} className="rounded p-2 hover:bg-stone-100" aria-label="Close menu">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">{nav}</div>
            <UserBox name={user?.name || user?.email || ''} role={user?.role || ''} onLogout={logout} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-stone-200 bg-white px-4 py-2 lg:hidden">
          <button onClick={() => setOpen(true)} className="rounded p-2 hover:bg-stone-100" aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <span className="font-semibold">Voice Agent</span>
        </header>
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 scroll-thin">
          <div className="mx-auto max-w-7xl">
            {user?.auth_disabled && (
              <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                Demo mode: sign-in is disabled (AUTH_DISABLED=true). Anyone who can reach this server has admin access.
              </div>
            )}
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-5 py-4">
      <img src="/favicon.svg" alt="" className="h-7 w-7" />
      <div className="leading-tight">
        <div className="text-sm font-semibold">BongoHive AI Lab</div>
        <div className="text-xs text-stone-500">Voice Agent Console</div>
      </div>
    </div>
  )
}

function UserBox({ name, role, onLogout }: { name: string; role: string; onLogout: () => void }) {
  return (
    <div className="flex items-center justify-between border-t border-stone-200 px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium">{name}</div>
        <div className="text-xs capitalize text-stone-500">{role}</div>
      </div>
      <button onClick={onLogout} className="rounded p-2 text-stone-500 hover:bg-stone-100" title="Sign out" aria-label="Sign out">
        <LogOut className="h-4 w-4" />
      </button>
    </div>
  )
}
