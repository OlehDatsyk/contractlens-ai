import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, KeyRound, LogOut, Trash2, UserPlus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Callout, LegalDisclaimer } from '@/components/domain'
import { Button } from '@/components/ui/button'
import { ConfirmDialog, Dialog } from '@/components/ui/dialog'
import { Badge, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, Input, PageHeader, Select, Spinner, Tabs } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { AuditEvent, Invitation, Member, Role, WorkspaceSettings } from '@/lib/types'
import { formatDate, formatDateTime, hasRole } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'
import { passwordIssues } from '@/lib/password'

type Tab = 'profile' | 'workspace' | 'members' | 'audit'

function Profile() {
  const { user, reloadUser, logout } = useAuth()
  const navigate = useNavigate()
  const [name, setName] = useState(user?.full_name ?? '')
  const [notify, setNotify] = useState(user?.email_notifications ?? true)
  const [pw, setPw] = useState({ current: '', next: '' })
  const issues = passwordIssues(pw.next)
  const save = useMutation({
    mutationFn: () => api('/api/auth/me', { method: 'PATCH', body: { full_name: name, email_notifications: notify } }),
    onSuccess: async () => { await reloadUser(); toast.success('Profile saved') },
    onError: (e: Error) => toast.error(e.message),
  })
  const changePw = useMutation({
    mutationFn: () => api('/api/auth/change-password', { method: 'POST', body: { current_password: pw.current, new_password: pw.next } }),
    onSuccess: async () => { toast.success('Password changed. Please sign in again.'); await logout(); navigate('/login') },
    onError: (e: Error) => toast.error(e.message),
  })
  const logoutAll = useMutation({
    mutationFn: () => api('/api/auth/logout-all', { method: 'POST' }),
    onSuccess: async () => { await logout(); navigate('/login') },
  })
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Profile" description={user?.email} />
        <CardBody>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
            <Field label="Full name" htmlFor="pf-name"><Input id="pf-name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />Email me deadline reminders (when email is configured)</label>
            <Button type="submit" loading={save.isPending} disabled={!name.trim()}>Save profile</Button>
          </form>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Security" />
        <CardBody>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); changePw.mutate() }}>
            <Field label="Current password" htmlFor="pw-cur"><Input id="pw-cur" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
            <Field label="New password" htmlFor="pw-new" hint={pw.next && issues.length ? `Needs ${issues.join(' and ')}` : undefined}><Input id="pw-new" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" loading={changePw.isPending} disabled={!pw.current || !pw.next || issues.length > 0}><KeyRound />Change password</Button>
              <Button type="button" variant="secondary" loading={logoutAll.isPending} onClick={() => logoutAll.mutate()}><LogOut />Sign out everywhere</Button>
            </div>
          </form>
        </CardBody>
      </Card>
    </div>
  )
}

function WorkspaceSettingsTab() {
  const { path, current } = useWorkspace()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const isAdmin = hasRole(current?.role, 'admin')
  const isOwner = current?.role === 'owner'
  const { data, isLoading, error } = useQuery({ queryKey: ['ws-settings', current?.id], queryFn: () => api<WorkspaceSettings>(path('/settings')) })
  const [form, setForm] = useState({ name: '', retention: '', provider: 'server', model: '', key: '', ai_enabled: true })
  const [confirmName, setConfirmName] = useState('')
  const [deleteOpen, setDeleteOpen] = useState(false)
  useEffect(() => {
    if (data) setForm({ name: data.name, retention: data.retention_days ? String(data.retention_days) : '', provider: data.ai.workspace_provider ?? 'server', model: data.ai.workspace_model ?? '', key: '', ai_enabled: data.ai.ai_enabled })
  }, [data])
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<WorkspaceSettings>(path('/settings'), { method: 'PATCH', body }),
    onSuccess: (d) => { qc.setQueryData(['ws-settings', current?.id], d); qc.invalidateQueries({ queryKey: ['workspaces'] }); qc.invalidateQueries({ queryKey: ['capabilities'] }); toast.success('Settings saved'); setForm((f) => ({ ...f, key: '' })) },
    onError: (e: Error) => toast.error(e.message),
  })
  if (error) return <ErrorState error={error} />
  if (isLoading || !data) return <Spinner />
  const retention = form.retention.trim() ? Number(form.retention) : null
  const retentionValid = retention === null || (Number.isInteger(retention) && retention >= 1 && retention <= 3650)

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="General" />
        <CardBody>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(retention === null ? { name: form.name, clear_retention: true } : { name: form.name, retention_days: retention }) }}>
            <Field label="Workspace name" htmlFor="ws-n"><Input id="ws-n" maxLength={120} disabled={!isAdmin} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Retention period (days)" htmlFor="ws-ret" error={retentionValid ? null : 'Enter a whole number between 1 and 3650, or leave empty'}
              hint="Contracts older than this are permanently deleted automatically, with their files and reports. Leave empty to keep documents until deleted.">
              <Input id="ws-ret" inputMode="numeric" disabled={!isAdmin} value={form.retention} onChange={(e) => setForm({ ...form, retention: e.target.value })} placeholder="No automatic deletion" />
            </Field>
            {isAdmin && <Button type="submit" loading={save.isPending} disabled={!form.name.trim() || !retentionValid}>Save</Button>}
          </form>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="AI provider" description={data.ai.available ? `Active: ${data.ai.provider} · ${data.ai.model} (${data.ai.source} configuration)` : 'AI is currently unavailable for this workspace'} />
        <CardBody className="space-y-4">
          {!data.ai.available && <Callout title={data.ai.reason ?? 'Not configured'}>Rule-based analysis still runs for every contract.</Callout>}
          <form className="space-y-4" onSubmit={(e) => {
            e.preventDefault()
            const body: Record<string, unknown> = { ai_enabled: form.ai_enabled, ai_provider: form.provider, ai_model: form.model }
            if (form.key.trim()) body.ai_api_key = form.key.trim()
            save.mutate(body)
          }}>
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" disabled={!isOwner} checked={form.ai_enabled} onChange={(e) => setForm({ ...form, ai_enabled: e.target.checked })} />Allow AI analysis for this workspace</label>
            <Field label="Provider" htmlFor="ai-prov" hint={`Server default: ${data.ai.server_provider ?? 'none'}`}>
              <Select id="ai-prov" disabled={!isOwner} value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>
                <option value="server">Use server configuration</option>
                <option value="openai">OpenAI (workspace key)</option>
                <option value="anthropic">Anthropic (workspace key)</option>
                <option value="gemini">Google Gemini (workspace key)</option>
              </Select>
            </Field>
            <Field label="Model (optional)" htmlFor="ai-model" hint="Leave empty for the provider's default model.">
              <Input id="ai-model" disabled={!isOwner} maxLength={100} value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="e.g. gpt-4.1-mini" />
            </Field>
            {form.provider !== 'server' && (
              <Field label="API key" htmlFor="ai-key" hint={data.ai.workspace_key_set ? `A key is stored (${data.ai.workspace_key_hint}). Enter a new key to replace it.` : 'Stored encrypted. It is never shown again or sent to the browser.'}>
                <Input id="ai-key" type="password" autoComplete="off" disabled={!isOwner} value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} />
              </Field>
            )}
            {isOwner ? (
              <div className="flex flex-wrap gap-2">
                <Button type="submit" loading={save.isPending}>Save AI settings</Button>
                {data.ai.workspace_key_set && <Button type="button" variant="secondary" onClick={() => save.mutate({ clear_ai_key: true })}>Remove stored key</Button>}
              </div>
            ) : <p className="text-xs text-slate-500">Only workspace owners can change AI settings.</p>}
            <p className="text-xs text-slate-500">When AI is enabled, contract text is sent to the selected provider for analysis. Review the provider’s data handling terms before enabling it for confidential documents.</p>
          </form>
        </CardBody>
      </Card>
      {isOwner && (
        <Card className="border-red-200 lg:col-span-2">
          <CardHeader title="Delete workspace" description="Permanently deletes every contract, file, report, conversation and deadline in this workspace." />
          <CardBody><Button variant="danger" onClick={() => setDeleteOpen(true)}><Trash2 />Delete workspace</Button></CardBody>
        </Card>
      )}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen} title="Delete this workspace?" description={<>This cannot be undone. Type <strong>{data.name}</strong> to confirm.</>}
        footer={<Button variant="danger" disabled={confirmName !== data.name} onClick={async () => {
          try {
            await api(path(''), { method: 'DELETE', body: { confirm_name: confirmName } })
            await qc.invalidateQueries({ queryKey: ['workspaces'] })
            toast.success('Workspace deleted')
            setDeleteOpen(false)
            navigate('/app')
          } catch (e) { toast.error(e instanceof Error ? e.message : 'Delete failed') }
        }}>Delete permanently</Button>}>
        <Input aria-label="Workspace name" value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />
      </Dialog>
    </div>
  )
}

const ROLES: Role[] = ['owner', 'admin', 'analyst', 'viewer']
const ROLE_HELP: Record<Role, string> = {
  owner: 'Full control, including AI settings and deleting the workspace',
  admin: 'Manage members (analysts/viewers), settings and the audit log',
  analyst: 'Upload, analyse, compare, create reports and deadlines',
  viewer: 'Read contracts and analyses, ask the assistant, download',
}

function Members() {
  const { path, current } = useWorkspace()
  const { user } = useAuth()
  const qc = useQueryClient()
  const isAdmin = hasRole(current?.role, 'admin')
  const isOwner = current?.role === 'owner'
  const [invite, setInvite] = useState({ open: false, email: '', role: 'viewer' as Role, link: '' })
  const [removing, setRemoving] = useState<Member | null>(null)
  const { data: members = [], isLoading } = useQuery({ queryKey: ['members', current?.id], queryFn: () => api<Member[]>(path('/members')) })
  const { data: invitations = [] } = useQuery({ queryKey: ['invitations', current?.id], queryFn: () => api<Invitation[]>(path('/invitations')), enabled: isAdmin })
  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) => api(path(`/members/${id}`), { method: 'PATCH', body: { role } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['members'] }); toast.success('Role updated') },
    onError: (e: Error) => toast.error(e.message),
  })
  const sendInvite = useMutation({
    mutationFn: () => api<{ invite_link: string; emailed: boolean }>(path('/invitations'), { method: 'POST', body: { email: invite.email.trim(), role: invite.role } }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['invitations'] }); setInvite((s) => ({ ...s, link: r.invite_link })); toast.success(r.emailed ? 'Invitation emailed' : 'Invitation created') },
    onError: (e: Error) => toast.error(e.message),
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api(path(`/invitations/${id}`), { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['invitations'] }),
  })
  const assignable = isOwner ? ROLES : (['analyst', 'viewer'] as Role[])

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Members" description={`${members.length} member(s)`} action={isAdmin && <Button size="sm" onClick={() => setInvite({ open: true, email: '', role: 'viewer', link: '' })}><UserPlus />Invite</Button>} />
        {isLoading ? <Spinner /> : (
          <ul className="divide-y divide-slate-100">
            {members.map((m) => {
              const editable = isAdmin && m.user_id !== user?.id && (isOwner || (m.role !== 'owner' && m.role !== 'admin'))
              return (
                <li key={m.user_id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-slate-900">{m.full_name}{m.user_id === user?.id && ' (you)'}</span><span className="block text-xs text-slate-500">{m.email} · joined {formatDate(m.joined_at)}</span></span>
                  {editable ? (
                    <Select aria-label={`Role for ${m.email}`} className="w-32" value={m.role} onChange={(e) => changeRole.mutate({ id: m.user_id, role: e.target.value as Role })}>
                      {assignable.map((r) => <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>)}
                    </Select>
                  ) : <Badge className="border-slate-200 bg-slate-50 capitalize text-slate-700">{m.role}</Badge>}
                  {(editable || m.user_id === user?.id) && <Button size="sm" variant="ghost" className="text-red-700" onClick={() => setRemoving(m)}>{m.user_id === user?.id ? 'Leave' : 'Remove'}</Button>}
                </li>
              )
            })}
          </ul>
        )}
      </Card>
      {isAdmin && invitations.length > 0 && (
        <Card>
          <CardHeader title="Pending invitations" />
          <ul className="divide-y divide-slate-100">
            {invitations.map((i) => (
              <li key={i.id} className="flex items-center gap-3 px-5 py-3 text-sm">
                <span className="flex-1">{i.email} <span className="text-slate-500">· {i.role} · expires {formatDate(i.expires_at)}</span></span>
                <Button size="sm" variant="ghost" onClick={() => revoke.mutate(i.id)}>Revoke</Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card><CardHeader title="Roles" /><CardBody><dl className="grid gap-2 text-sm sm:grid-cols-2">{ROLES.map((r) => <div key={r}><dt className="font-medium capitalize text-slate-900">{r}</dt><dd className="text-slate-600">{ROLE_HELP[r]}</dd></div>)}</dl></CardBody></Card>

      <Dialog open={invite.open} onOpenChange={(o) => setInvite((s) => ({ ...s, open: o }))} title="Invite a member"
        description="The invitee signs in (or registers) with this email address and opens the invitation link."
        footer={!invite.link && <Button loading={sendInvite.isPending} disabled={!invite.email.includes('@')} onClick={() => sendInvite.mutate()}>Create invitation</Button>}>
        {invite.link ? (
          <div className="space-y-3">
            <p className="text-sm text-slate-700">Share this one-time link with <strong>{invite.email}</strong> through a trusted channel. It expires in 7 days and is shown only once.</p>
            <div className="flex gap-2"><Input readOnly value={invite.link} aria-label="Invitation link" onFocus={(e) => e.target.select()} />
              <Button variant="secondary" size="icon" aria-label="Copy link" onClick={() => navigator.clipboard.writeText(invite.link).then(() => toast.success('Copied'))}><Copy /></Button></div>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); sendInvite.mutate() }}>
            <Field label="Email" htmlFor="inv-email"><Input id="inv-email" type="email" value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} /></Field>
            <Field label="Role" htmlFor="inv-role" hint={ROLE_HELP[invite.role]}><Select id="inv-role" value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value as Role })}>
              {assignable.map((r) => <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>)}
            </Select></Field>
          </form>
        )}
      </Dialog>
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} danger title={removing?.user_id === user?.id ? 'Leave workspace?' : 'Remove member?'}
        description={removing?.user_id === user?.id ? 'You will lose access to this workspace.' : `${removing?.email} will lose access immediately, including any download links they hold.`} confirmLabel={removing?.user_id === user?.id ? 'Leave' : 'Remove'}
        onConfirm={async () => {
          try {
            await api(path(`/members/${removing!.user_id}`), { method: 'DELETE' })
            qc.invalidateQueries({ queryKey: removing!.user_id === user?.id ? ['workspaces'] : ['members'] })
            toast.success('Done')
          } catch (e) { toast.error(e instanceof Error ? e.message : 'Failed') }
        }} />
    </div>
  )
}

function Audit() {
  const { path, current } = useWorkspace()
  const { data = [], isLoading, error } = useQuery({ queryKey: ['audit', current?.id], queryFn: () => api<AuditEvent[]>(path('/audit?limit=200')) })
  if (error) return <ErrorState error={error} />
  return (
    <Card>
      <CardHeader title="Audit log" description="Security-relevant actions in this workspace. Document contents are never logged." />
      {isLoading ? <Spinner /> : data.length === 0 ? <EmptyState title="No events yet" /> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase text-slate-500"><tr><th className="px-4 py-2">Time</th><th className="px-4 py-2">Action</th><th className="px-4 py-2">User</th><th className="px-4 py-2">Target</th><th className="px-4 py-2">IP</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((e) => (
                <tr key={e.id}>
                  <td className="whitespace-nowrap px-4 py-2 text-slate-600">{formatDateTime(e.created_at)}</td>
                  <td className="px-4 py-2 font-mono text-xs text-slate-800">{e.action}</td>
                  <td className="px-4 py-2 text-slate-600">{e.user_email ?? '—'}</td>
                  <td className="px-4 py-2 text-xs text-slate-500">{e.target_type ? `${e.target_type} ${e.target_id?.slice(0, 8) ?? ''}` : '—'}</td>
                  <td className="px-4 py-2 text-xs text-slate-500">{e.ip_address ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

export default function SettingsPage() {
  const { current } = useWorkspace()
  const [tab, setTab] = useState<Tab>('profile')
  const tabs: { id: Tab; label: string }[] = [{ id: 'profile', label: 'Profile' }, { id: 'workspace', label: 'Workspace' }, { id: 'members', label: 'Members' }]
  if (hasRole(current?.role, 'admin')) tabs.push({ id: 'audit', label: 'Audit log' })
  return (
    <>
      <PageHeader title="Settings" description={`Your account and the workspace “${current?.name}”`} />
      <Tabs<Tab> label="Settings sections" tabs={tabs} value={tab} onChange={setTab} />
      <div className="pt-6" role="tabpanel">
        {tab === 'profile' && <Profile />}
        {tab === 'workspace' && <WorkspaceSettingsTab />}
        {tab === 'members' && <Members />}
        {tab === 'audit' && <Audit />}
      </div>
      <LegalDisclaimer className="mt-6" />
    </>
  )
}
