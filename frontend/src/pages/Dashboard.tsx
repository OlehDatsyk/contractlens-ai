import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CalendarClock, CheckCircle2, FileText, FileUp, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AiStatusNotice, LegalDisclaimer, RiskBadge, StatusBadge } from '@/components/domain'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, EmptyState, ErrorState, PageHeader, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import type { Dashboard } from '@/lib/types'
import { formatDate, relativeDays, SEVERITY_COLOR, SEVERITY_LABEL } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'

function Stat({ label, value, icon, tone = 'slate', to }: { label: string; value: number; icon: ReactNode; tone?: 'slate' | 'red' | 'amber' | 'emerald' | 'brand'; to?: string }) {
  const tones = { slate: 'bg-slate-100 text-slate-600', red: 'bg-red-50 text-red-700', amber: 'bg-amber-50 text-amber-700', emerald: 'bg-emerald-50 text-emerald-700', brand: 'bg-brand-50 text-brand-700' }
  const body = (
    <Card className="flex items-center gap-3 p-4 transition-colors hover:border-slate-300">
      <span className={`flex size-9 shrink-0 items-center justify-center rounded-md [&_svg]:size-4 ${tones[tone]}`}>{icon}</span>
      <span className="min-w-0">
        <span className="block text-2xl font-semibold leading-tight text-slate-900 tabular">{value}</span>
        <span className="block truncate text-xs font-medium text-slate-500">{label}</span>
      </span>
    </Card>
  )
  return to ? <Link to={to} className="rounded-lg">{body}</Link> : body
}

export default function DashboardPage() {
  const { path, current, capabilities } = useWorkspace()
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dashboard', current?.id],
    queryFn: () => api<Dashboard>(path('/dashboard')),
    refetchInterval: (q) => ((q.state.data?.totals.processing ?? 0) > 0 ? 5000 : false),
  })

  if (error) return <ErrorState error={error} onRetry={refetch} />
  const t = data?.totals
  const empty = t && t.contracts === 0

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={`Overview of ${current?.name}`}
        actions={<Button asChild><Link to="/app/upload"><FileUp />Upload contract</Link></Button>}
      />
      {capabilities && <div className="mb-4"><AiStatusNotice available={capabilities.ai.available} reason={capabilities.ai.reason} /></div>}

      {isLoading || !t ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[74px]" />)}</div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Total contracts" value={t.contracts} icon={<FileText />} to="/app/contracts" />
          <Stat label="Analysed" value={t.analyzed} icon={<CheckCircle2 />} tone="brand" to="/app/contracts?status=analyzed" />
          <Stat label="High risk" value={t.high_risk} icon={<ShieldAlert />} tone="red" to="/app/contracts?risk=critical&risk=high" />
          <Stat label="Medium risk" value={t.medium_risk} icon={<AlertTriangle />} tone="amber" to="/app/contracts?risk=medium" />
          <Stat label="Low risk" value={t.low_risk} icon={<ShieldCheck />} tone="emerald" to="/app/contracts?risk=low" />
          <Stat label="Deadlines (30 days)" value={t.upcoming_deadlines} icon={<CalendarClock />} to="/app/deadlines" />
        </div>
      )}

      {empty ? (
        <Card className="mt-6">
          <EmptyState
            icon={<FileUp />}
            title="No contracts yet"
            description="Upload a PDF, DOCX or TXT contract. Analysis starts automatically and usually takes a few seconds. Charts appear once you have analysed documents."
            action={<Button asChild><Link to="/app/upload">Upload your first contract</Link></Button>}
          />
        </Card>
      ) : (
        <div className="mt-6 grid gap-6 xl:grid-cols-3">
          <Card className="xl:col-span-2">
            <CardHeader title="Contract activity" description="Uploads and completed analyses per week (last 13 weeks)" />
            <CardBody className="h-64">
              {data && (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.activity} margin={{ left: -20, right: 8, top: 8 }}>
                    <CartesianGrid stroke="#e2e8f0" vertical={false} />
                    <XAxis dataKey="week" tickFormatter={(v) => formatDate(v, { month: 'short', day: 'numeric' })} tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
                    <Tooltip labelFormatter={(v) => `Week of ${formatDate(String(v))}`} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line type="monotone" dataKey="uploaded" name="Uploaded" stroke="#94a3b8" strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="analyzed" name="Analysed" stroke="#4338ca" strokeWidth={2} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Risk distribution" description="Overall level of analysed contracts" />
            <CardBody className="h-64">
              {data && data.risk_distribution.every((r) => r.count === 0) ? (
                <EmptyState title="No analysed contracts yet" className="py-8" />
              ) : data && (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.risk_distribution.map((r) => ({ ...r, label: SEVERITY_LABEL[r.level] }))} margin={{ left: -20, right: 8, top: 8 }}>
                    <CartesianGrid stroke="#e2e8f0" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
                    <Tooltip cursor={{ fill: '#f1f5f9' }} />
                    <Bar dataKey="count" name="Contracts" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                      {data.risk_distribution.map((r) => <Cell key={r.level} fill={SEVERITY_COLOR[r.level]} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardBody>
          </Card>

          <Card className="xl:col-span-2">
            <CardHeader title="Recently analysed" action={<Button variant="link" size="sm" asChild><Link to="/app/contracts">View all</Link></Button>} />
            {data && data.recent.length === 0 ? (
              <EmptyState title="Nothing analysed yet" description={t && t.processing > 0 ? `${t.processing} contract(s) are being analysed.` : undefined} />
            ) : (
              <ul className="divide-y divide-slate-100">
                {data?.recent.map((c) => (
                  <li key={c.id}>
                    <Link to={`/app/contracts/${c.id}`} className="flex flex-wrap items-center gap-3 px-5 py-3 hover:bg-slate-50">
                      <FileText className="size-4 text-slate-400" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-slate-900">{c.title}</span>
                        <span className="block text-xs text-slate-500">{c.contract_type ?? 'Contract'} · analysed {formatDate(c.analyzed_at)}</span>
                      </span>
                      <StatusBadge status={c.status} />
                      <RiskBadge level={c.risk_level} score={c.risk_score} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card>
            <CardHeader title="Upcoming deadlines" description="Next 90 days" action={<Button variant="link" size="sm" asChild><Link to="/app/deadlines">Calendar</Link></Button>} />
            {data && data.upcoming_deadlines.length === 0 ? (
              <EmptyState icon={<CalendarClock />} title="No upcoming deadlines" description="Expiration and notice dates found during analysis appear here." />
            ) : (
              <ul className="divide-y divide-slate-100">
                {data?.upcoming_deadlines.map((d) => (
                  <li key={d.id} className="px-5 py-3">
                    <p className="text-sm font-medium text-slate-900">{d.title}</p>
                    <p className="text-xs text-slate-500">{formatDate(d.due_date)} · {relativeDays(d.due_date)} · <span className="capitalize">{d.type}</span></p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
      <LegalDisclaimer compact className="mt-6" />
    </>
  )
}
