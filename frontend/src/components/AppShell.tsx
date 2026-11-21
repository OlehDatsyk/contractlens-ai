import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bell, BarChart3, CalendarClock, ChevronsUpDown, FileText, FileUp, GitCompare, LayoutDashboard, LogOut, Menu as MenuIcon,
  MessageSquareText, Plus, Settings, X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { Notification } from '@/lib/types'
import { cn, formatDateTime } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'
import { Logo } from './Logo'
import { Button } from './ui/button'
import { Dialog, Menu } from './ui/dialog'
import { Field, Input, Spinner } from './ui/primitives'

const NAV = [
  { to: '/app', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/app/contracts', label: 'Contracts', icon: FileText },
  { to: '/app/upload', label: 'Upload', icon: FileUp },
  { to: '/app/assistant', label: 'AI assistant', icon: MessageSquareText },
  { to: '/app/compare', label: 'Compare', icon: GitCompare },
  { to: '/app/deadlines', label: 'Deadlines', icon: CalendarClock },
  { to: '/app/reports', label: 'Reports', icon: BarChart3 },
  { to: '/app/settings', label: 'Settings', icon: Settings },
]

function WorkspaceSwitcher() {
  const { workspaces, current, select } = useWorkspace()
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const qc = useQueryClient()
  const create = useMutation({
    mutationFn: () => api<{ id: string }>('/api/workspaces', { method: 'POST', body: { name } }),
    onSuccess: async (ws) => {
      await qc.invalidateQueries({ queryKey: ['workspaces'] })
      select(ws.id)
      setCreating(false)
      setName('')
      toast.success('Workspace created')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <>
      <Menu
        align="start"
        trigger={
          <button className="flex w-full items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 py-2 text-left text-sm hover:bg-slate-50" aria-label="Switch workspace">
            <span className="flex size-6 shrink-0 items-center justify-center rounded bg-brand-100 text-xs font-semibold text-brand-800">
              {(current?.name ?? '?').slice(0, 1).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium text-slate-900">{current?.name ?? 'No workspace'}</span>
              <span className="block text-xs capitalize text-slate-500">{current?.role}</span>
            </span>
            <ChevronsUpDown className="size-4 text-slate-400" />
          </button>
        }
        items={[
          ...workspaces.map((w) => ({ label: `${w.name}${w.id === current?.id ? ' ✓' : ''}`, onSelect: () => select(w.id) })),
          'separator' as const,
          { label: 'New workspace', icon: <Plus />, onSelect: () => setCreating(true) },
        ]}
      />
      <Dialog
        open={creating}
        onOpenChange={setCreating}
        title="Create workspace"
        description="Workspaces keep contracts, reports and members fully separated."
        footer={<Button loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>Create</Button>}
      >
        <form onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate() }}>
          <Field label="Name" htmlFor="ws-name">
            <Input id="ws-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} autoFocus />
          </Field>
        </form>
      </Dialog>
    </>
  )
}

function Notifications() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const { data = [], isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api<Notification[]>('/api/notifications?limit=20'),
    refetchInterval: 60_000,
  })
  const unread = data.filter((n) => !n.read_at).length
  const markRead = useMutation({
    mutationFn: () => api('/api/notifications/read', { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  })
  return (
    <>
      <Button variant="ghost" size="icon" aria-label={`Notifications${unread ? ` (${unread} unread)` : ''}`} onClick={() => setOpen(true)} className="relative">
        <Bell />
        {unread > 0 && <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-red-600" aria-hidden />}
      </Button>
      <Dialog open={open} onOpenChange={setOpen} title="Notifications" wide
        footer={unread > 0 ? <Button variant="secondary" size="sm" onClick={() => markRead.mutate()}>Mark all as read</Button> : undefined}>
        {isLoading ? <Spinner /> : data.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">No notifications yet. You’ll be notified when analyses finish and deadlines approach.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {data.map((n) => (
              <li key={n.id}>
                <button
                  className="flex w-full gap-3 py-3 text-left hover:bg-slate-50"
                  onClick={() => { if (n.link) { navigate(n.link); setOpen(false) } }}
                >
                  <span className={cn('mt-1.5 size-2 shrink-0 rounded-full', n.read_at ? 'bg-transparent' : 'bg-brand-600')} aria-hidden />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-slate-900">{n.title}</span>
                    {n.body && <span className="block text-sm text-slate-500">{n.body}</span>}
                    <span className="block text-xs text-slate-400">{formatDateTime(n.created_at)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Dialog>
    </>
  )
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <Link to="/app" className="px-1" onClick={onNavigate}><Logo /></Link>
      <WorkspaceSwitcher />
      <nav aria-label="Main" className="flex-1 space-y-0.5">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn('flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors',
                isActive ? 'bg-brand-50 text-brand-800' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900')
            }
          >
            <Icon className="size-4" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <p className="px-1 text-[11px] leading-snug text-slate-400">Informational analysis only. Not legal advice.</p>
    </div>
  )
}

export function AppShell() {
  const { user, logout } = useAuth()
  const { current, loading } = useWorkspace()
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => setMobileOpen(false), [location.pathname])

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[248px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">Skip to content</a>
      <aside className="hidden border-r border-slate-200 bg-slate-50/80 lg:block">
        <div className="sticky top-0 h-screen"><Sidebar /></div>
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setMobileOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 w-72 bg-white shadow-xl">
            <button className="absolute right-3 top-3 rounded p-1 text-slate-500 hover:bg-slate-100" onClick={() => setMobileOpen(false)} aria-label="Close menu"><X className="size-5" /></button>
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu"><MenuIcon /></Button>
          <div className="lg:hidden"><Logo compact /></div>
          <div className="flex-1" />
          <Button size="sm" onClick={() => navigate('/app/upload')} className="hidden sm:inline-flex"><FileUp />Upload</Button>
          <Notifications />
          <Menu
            trigger={
              <button className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-slate-100" aria-label="Account menu">
                <span className="flex size-8 items-center justify-center rounded-full bg-slate-200 text-sm font-semibold text-slate-700">
                  {(user?.full_name ?? '?').slice(0, 1).toUpperCase()}
                </span>
                <span className="hidden max-w-40 truncate text-sm font-medium text-slate-700 md:block">{user?.full_name}</span>
              </button>
            }
            items={[
              { label: 'Profile & settings', icon: <Settings />, onSelect: () => navigate('/app/settings') },
              'separator',
              { label: 'Sign out', icon: <LogOut />, onSelect: async () => { await logout(); navigate('/login') } },
            ]}
          />
        </header>
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {loading ? <Spinner label="Loading workspace" /> : current ? <Outlet /> : <Spinner label="Preparing workspace" />}
        </main>
      </div>
    </div>
  )
}
