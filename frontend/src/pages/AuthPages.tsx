import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, MailCheck } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Callout } from '@/components/domain'
import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { passwordIssues } from '@/lib/password'
import { useWorkspace } from '@/lib/workspace'

function safeNext(next: string | null) {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : '/app'
}

function AuthLayout({ title, subtitle, children, footer }: { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-4 py-12">
      <Link to="/" className="mb-8"><Logo /></Link>
      <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-xs">
        <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
        <div className="mt-6">{children}</div>
      </div>
      {footer && <div className="mt-4 text-sm text-slate-600">{footer}</div>}
    </div>
  )
}

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(email.trim(), password)
      navigate(safeNext(params.get('next')), { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout title="Sign in" subtitle="Welcome back to ContractLens AI." footer={<>New here? <Link className="font-medium text-brand-700 hover:underline" to="/register">Create an account</Link></>}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <Callout tone="danger" title={error} />}
        <Field label="Email" htmlFor="email">
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" htmlFor="password">
          <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button type="submit" className="w-full" loading={busy} disabled={!email || !password}>Sign in</Button>
        <p className="text-center text-sm"><Link to="/forgot-password" className="text-brand-700 hover:underline">Forgot your password?</Link></p>
      </form>
    </AuthLayout>
  )
}

export function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ full_name: '', email: '', password: '', workspace_name: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [accepted, setAccepted] = useState(false)
  const issues = passwordIssues(form.password)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (issues.length) return setError(`Password needs ${issues.join(' and ')}.`)
    setBusy(true)
    try {
      await register({ ...form, email: form.email.trim(), workspace_name: form.workspace_name.trim() || undefined })
      toast.success('Account created')
      navigate('/app', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setBusy(false)
    }
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value })
  return (
    <AuthLayout title="Create your account" subtitle="A private workspace is created for you." footer={<>Already have an account? <Link className="font-medium text-brand-700 hover:underline" to="/login">Sign in</Link></>}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <Callout tone="danger" title={error} />}
        <Field label="Full name" htmlFor="full_name"><Input id="full_name" autoComplete="name" required maxLength={120} value={form.full_name} onChange={set('full_name')} /></Field>
        <Field label="Email" htmlFor="reg-email"><Input id="reg-email" type="email" autoComplete="email" required value={form.email} onChange={set('email')} /></Field>
        <Field label="Password" htmlFor="reg-password" hint={form.password && issues.length ? `Needs ${issues.join(' and ')}` : 'At least 10 characters, mixing letters, digits or symbols.'}>
          <Input id="reg-password" type="password" autoComplete="new-password" required value={form.password} onChange={set('password')} aria-invalid={!!form.password && issues.length > 0} />
        </Field>
        <Field label="Workspace name (optional)" htmlFor="ws"><Input id="ws" maxLength={120} placeholder="e.g. Acme Legal" value={form.workspace_name} onChange={set('workspace_name')} /></Field>
        <label className="flex items-start gap-2 text-xs text-slate-600">
          <input type="checkbox" className="mt-0.5" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
          <span>I understand ContractLens AI provides informational analysis only, is not a law firm and does not give legal advice.</span>
        </label>
        <Button type="submit" className="w-full" loading={busy} disabled={!accepted || !form.email || !form.full_name || !form.password}>Create account</Button>
      </form>
    </AuthLayout>
  )
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [done, setDone] = useState<string | null>(null)
  const m = useMutation({
    mutationFn: () => api<{ detail: string }>('/api/auth/forgot-password', { method: 'POST', body: { email: email.trim() } }),
    onSuccess: (r) => setDone(r.detail),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <AuthLayout title="Reset your password" subtitle="We’ll email a reset link if an account exists." footer={<Link className="text-brand-700 hover:underline" to="/login">Back to sign in</Link>}>
      {done ? (
        <div className="flex flex-col items-center gap-2 text-center">
          <MailCheck className="size-8 text-emerald-600" />
          <p className="text-sm text-slate-700">{done}</p>
          <p className="text-xs text-slate-500">If email delivery is not configured on this server, ask your administrator.</p>
        </div>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); m.mutate() }} className="space-y-4">
          <Field label="Email" htmlFor="fp-email"><Input id="fp-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Button type="submit" className="w-full" loading={m.isPending} disabled={!email}>Send reset link</Button>
        </form>
      )}
    </AuthLayout>
  )
}

export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [pw, setPw] = useState('')
  const [done, setDone] = useState(false)
  const issues = passwordIssues(pw)
  const m = useMutation({
    mutationFn: () => api('/api/auth/reset-password', { method: 'POST', body: { token, new_password: pw } }),
    onSuccess: () => setDone(true),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <AuthLayout title="Choose a new password">
      {!token ? <Callout tone="warn" title="This reset link is incomplete">Open the link from your email again.</Callout> : done ? (
        <div className="flex flex-col items-center gap-3 text-center">
          <CheckCircle2 className="size-8 text-emerald-600" />
          <p className="text-sm text-slate-700">Your password was changed. All other sessions were signed out.</p>
          <Button asChild><Link to="/login">Sign in</Link></Button>
        </div>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); if (!issues.length) m.mutate() }} className="space-y-4">
          <Field label="New password" htmlFor="np" hint={pw && issues.length ? `Needs ${issues.join(' and ')}` : undefined}>
            <Input id="np" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" loading={m.isPending} disabled={!pw || issues.length > 0}>Update password</Button>
        </form>
      )}
    </AuthLayout>
  )
}

export function InvitePage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { select } = useWorkspace()
  const { user } = useAuth()
  const m = useMutation({
    mutationFn: () => api<{ id: string; name: string }>('/api/invitations/accept', { method: 'POST', body: { token } }),
    onSuccess: async (ws) => {
      await qc.invalidateQueries({ queryKey: ['workspaces'] })
      select(ws.id)
      toast.success(`You joined ${ws.name}`)
      navigate('/app')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <AuthLayout title="Workspace invitation" subtitle={<>Signed in as <strong>{user?.email}</strong>. The invitation must have been sent to this address.</>}>
      {token ? <Button className="w-full" loading={m.isPending} onClick={() => m.mutate()}>Accept invitation</Button>
        : <Callout tone="warn" title="This invitation link is incomplete" />}
    </AuthLayout>
  )
}
