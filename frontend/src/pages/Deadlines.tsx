import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarPlus, Check, ChevronLeft, ChevronRight, Pencil, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { DeadlineDialog } from '@/components/DeadlineDialog'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/dialog'
import { Badge, Card, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import type { Deadline } from '@/lib/types'
import { cn, formatDate, hasRole, isoDate, relativeDays } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'

const TYPE_CLS: Record<string, string> = {
  expiration: 'bg-red-100 text-red-800', notice: 'bg-amber-100 text-amber-900', renewal: 'bg-sky-100 text-sky-800',
  payment: 'bg-emerald-100 text-emerald-800', obligation: 'bg-violet-100 text-violet-800', custom: 'bg-slate-100 text-slate-700',
}
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function DeadlinesPage() {
  const { path, current } = useWorkspace()
  const qc = useQueryClient()
  const canEdit = hasRole(current?.role, 'analyst')
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1) })
  const [editing, setEditing] = useState<Deadline | null>(null)
  const [creating, setCreating] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Deadline | null>(null)
  const [selectedDay, setSelectedDay] = useState<string | null>(null)

  const start = new Date(month); start.setDate(1 - ((month.getDay() + 6) % 7))
  const days = Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d })
  const range = { start: isoDate(days[0]), end: isoDate(days[41]) }

  const { data = [], isLoading, error, refetch } = useQuery({
    queryKey: ['deadlines', current?.id, range.start],
    queryFn: () => api<Deadline[]>(path(`/deadlines?start=${range.start}&end=${range.end}`)),
  })
  const today = isoDate(new Date())
  const { data: upcoming = [] } = useQuery({
    queryKey: ['deadlines', current?.id, 'upcoming'],
    queryFn: () => api<Deadline[]>(path(`/deadlines?status=open&start=${today}`)),
  })
  const byDay = useMemo(() => {
    const m = new Map<string, Deadline[]>()
    data.forEach((d) => m.set(d.due_date, [...(m.get(d.due_date) ?? []), d]))
    return m
  }, [data])

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api(path(`/deadlines/${id}`), { method: 'PATCH', body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['deadlines'] }); toast.success('Deadline updated') },
    onError: (e: Error) => toast.error(e.message),
  })

  const listItems = selectedDay ? data.filter((d) => d.due_date === selectedDay) : upcoming.slice(0, 30)

  return (
    <>
      <PageHeader title="Deadlines" description="Expiration, renewal, notice, payment and obligation dates across this workspace."
        actions={canEdit && <Button onClick={() => setCreating(selectedDay ?? today)}><CalendarPlus />Add deadline</Button>} />
      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <Card>
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-900" aria-live="polite">{month.toLocaleString(undefined, { month: 'long', year: 'numeric' })}</h2>
            <div className="flex gap-1">
              <Button variant="ghost" size="icon" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft /></Button>
              <Button variant="secondary" size="sm" onClick={() => { const d = new Date(); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)) }}>Today</Button>
              <Button variant="ghost" size="icon" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight /></Button>
            </div>
          </div>
          {error ? <ErrorState error={error} onRetry={refetch} /> : isLoading ? <Spinner /> : (
            <div className="grid grid-cols-7 text-xs" role="grid" aria-label="Calendar">
              {WEEKDAYS.map((d) => <div key={d} role="columnheader" className="border-b border-slate-100 py-2 text-center font-medium text-slate-500">{d}</div>)}
              {days.map((d) => {
                const key = isoDate(d)
                const items = byDay.get(key) ?? []
                const inMonth = d.getMonth() === month.getMonth()
                return (
                  <button key={key} role="gridcell" aria-label={`${formatDate(key)}${items.length ? `, ${items.length} deadline(s)` : ''}`} aria-selected={selectedDay === key}
                    onClick={() => setSelectedDay(selectedDay === key ? null : key)}
                    className={cn('min-h-20 border-b border-r border-slate-100 p-1.5 text-left align-top transition-colors hover:bg-slate-50 sm:min-h-24', !inMonth && 'bg-slate-50/60 text-slate-400', selectedDay === key && 'ring-2 ring-inset ring-brand-500')}>
                    <span className={cn('inline-flex size-6 items-center justify-center rounded-full tabular', key === today && 'bg-brand-700 font-semibold text-white')}>{d.getDate()}</span>
                    <span className="mt-1 block space-y-0.5">
                      {items.slice(0, 3).map((x) => <span key={x.id} className={cn('block truncate rounded px-1 py-0.5 text-[11px]', TYPE_CLS[x.deadline_type], x.status !== 'open' && 'line-through opacity-60')}>{x.title}</span>)}
                      {items.length > 3 && <span className="block px-1 text-[11px] text-slate-500">+{items.length - 3} more</span>}
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </Card>
        <Card>
          <CardHeader title={selectedDay ? formatDate(selectedDay, { dateStyle: 'full' }) : 'Upcoming'} description={selectedDay ? undefined : 'Open deadlines from today'}
            action={selectedDay && <Button variant="link" size="sm" onClick={() => setSelectedDay(null)}>Show upcoming</Button>} />
          {listItems.length === 0 ? <EmptyState title={selectedDay ? 'Nothing due on this day' : 'No upcoming deadlines'} description="Deadlines extracted from analysed contracts and ones you add appear here." /> : (
            <ul className="divide-y divide-slate-100">
              {listItems.map((d) => (
                <li key={d.id} className="space-y-1 px-5 py-3">
                  <div className="flex items-start gap-2">
                    <span className="min-w-0 flex-1">
                      <span className={cn('block text-sm font-medium text-slate-900', d.status !== 'open' && 'line-through text-slate-500')}>{d.title}</span>
                      <span className="block text-xs text-slate-500">{formatDate(d.due_date)} · {relativeDays(d.due_date)}{d.reminder_days.length ? ` · reminders ${d.reminder_days.join(', ')} days before` : ''}</span>
                      {d.contract_id && <Link to={`/app/contracts/${d.contract_id}`} className="block truncate text-xs text-brand-700 hover:underline">{d.contract_title}</Link>}
                    </span>
                    <Badge className={cn('border-transparent capitalize', TYPE_CLS[d.deadline_type])}>{d.deadline_type}</Badge>
                  </div>
                  {canEdit && (
                    <div className="flex gap-1">
                      {d.status === 'open' && <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: d.id, body: { status: 'done' } })}><Check />Done</Button>}
                      {d.status !== 'open' && <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: d.id, body: { status: 'open' } })}>Reopen</Button>}
                      <Button size="sm" variant="ghost" onClick={() => setEditing(d)}><Pencil />Edit</Button>
                      <Button size="sm" variant="ghost" className="text-red-700" onClick={() => setDeleting(d)}><Trash2 />Delete</Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <DeadlineDialog open={!!creating || !!editing} onOpenChange={(o) => { if (!o) { setCreating(null); setEditing(null) } }} deadline={editing} defaultDate={creating ?? undefined} />
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} danger title="Delete deadline?" description={`“${deleting?.title}” and its reminders will be removed.`} confirmLabel="Delete"
        onConfirm={async () => { await api(path(`/deadlines/${deleting!.id}`), { method: 'DELETE' }); qc.invalidateQueries({ queryKey: ['deadlines'] }); toast.success('Deadline deleted') }} />
    </>
  )
}
