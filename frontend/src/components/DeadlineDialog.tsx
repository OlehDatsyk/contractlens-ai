import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import type { ContractPage, Deadline } from '@/lib/types'
import { useWorkspace } from '@/lib/workspace'
import { Button } from './ui/button'
import { Dialog } from './ui/dialog'
import { Field, Input, Select, Textarea } from './ui/primitives'

const TYPES = ['expiration', 'renewal', 'payment', 'notice', 'obligation', 'custom'] as const

export function DeadlineDialog({ open, onOpenChange, contractId, deadline, defaultDate }: {
  open: boolean
  onOpenChange: (o: boolean) => void
  contractId?: string
  deadline?: Deadline | null
  defaultDate?: string
}) {
  const { path, current } = useWorkspace()
  const qc = useQueryClient()
  const [form, setForm] = useState({ title: '', due_date: '', deadline_type: 'custom', description: '', contract_id: contractId ?? '', reminders: '30, 7, 1' })
  useEffect(() => {
    if (!open) return
    setForm(deadline
      ? { title: deadline.title, due_date: deadline.due_date, deadline_type: deadline.deadline_type, description: deadline.description ?? '', contract_id: deadline.contract_id ?? '', reminders: deadline.reminder_days.join(', ') }
      : { title: '', due_date: defaultDate ?? '', deadline_type: 'custom', description: '', contract_id: contractId ?? '', reminders: '30, 7, 1' })
  }, [open, deadline, contractId, defaultDate])

  const { data: contracts } = useQuery({
    queryKey: ['contracts', current?.id, 'picker'],
    queryFn: () => api<ContractPage>(path('/contracts?page_size=100&sort=title')),
    enabled: open && !contractId,
  })
  const reminderDays = form.reminders.split(',').map((s) => s.trim()).filter(Boolean).map(Number)
  const remindersValid = reminderDays.every((n) => Number.isInteger(n) && n >= 0 && n <= 365) && reminderDays.length <= 6

  const save = useMutation({
    mutationFn: () => {
      const body = { title: form.title.trim(), due_date: form.due_date, deadline_type: form.deadline_type, description: form.description.trim() || null, reminder_days: reminderDays }
      return deadline
        ? api<Deadline>(path(`/deadlines/${deadline.id}`), { method: 'PATCH', body })
        : api<Deadline>(path('/deadlines'), { method: 'POST', body: { ...body, contract_id: form.contract_id || null } })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['deadlines'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); onOpenChange(false); toast.success('Deadline saved') },
    onError: (e: Error) => toast.error(e.message),
  })
  const valid = form.title.trim() && form.due_date && remindersValid

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={deadline ? 'Edit deadline' : 'Add deadline'} description="Reminders are shown in the app; email is sent only when an email provider is configured."
      footer={<Button loading={save.isPending} disabled={!valid} onClick={() => save.mutate()}>Save</Button>}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate() }}>
        <Field label="Title" htmlFor="dl-title"><Input id="dl-title" maxLength={200} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Due date" htmlFor="dl-date"><Input id="dl-date" type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></Field>
          <Field label="Type" htmlFor="dl-type"><Select id="dl-type" value={form.deadline_type} onChange={(e) => setForm({ ...form, deadline_type: e.target.value })}>
            {TYPES.map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
          </Select></Field>
        </div>
        {!contractId && !deadline && (
          <Field label="Contract (optional)" htmlFor="dl-contract"><Select id="dl-contract" value={form.contract_id} onChange={(e) => setForm({ ...form, contract_id: e.target.value })}>
            <option value="">Not linked to a contract</option>
            {contracts?.items.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </Select></Field>
        )}
        <Field label="Remind me (days before)" htmlFor="dl-rem" error={remindersValid ? null : 'Use up to 6 whole numbers between 0 and 365, separated by commas'} hint="For example: 30, 7, 1">
          <Input id="dl-rem" value={form.reminders} onChange={(e) => setForm({ ...form, reminders: e.target.value })} aria-invalid={!remindersValid} />
        </Field>
        <Field label="Notes" htmlFor="dl-desc"><Textarea id="dl-desc" maxLength={2000} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
      </form>
    </Dialog>
  )
}
