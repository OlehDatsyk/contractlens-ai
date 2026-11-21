import { useQuery } from '@tanstack/react-query'
import { ArrowRightLeft, GitCompare, Minus, Plus, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ContractPicker } from '@/components/ContractPicker'
import { Callout, LegalDisclaimer, RiskBadge } from '@/components/domain'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, PageHeader, Spinner, Tabs } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import type { Comparison, ComparisonClause } from '@/lib/types'
import { cn, reference } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'

const STATUS_LABEL = { identical: 'Identical text', different: 'Different', only_in_a: 'Only in A', only_in_b: 'Only in B', not_found_in_either: 'Not found' }
const STATUS_CLS = {
  identical: 'border-emerald-200 bg-emerald-50 text-emerald-800', different: 'border-amber-200 bg-amber-50 text-amber-900',
  only_in_a: 'border-slate-200 bg-slate-50 text-slate-700', only_in_b: 'border-sky-200 bg-sky-50 text-sky-800', not_found_in_either: 'border-slate-200 bg-white text-slate-400',
}

function ClauseBox({ c, tone }: { c: ComparisonClause; tone?: 'add' | 'remove' }) {
  return (
    <div className={cn('rounded-md border px-3 py-2', tone === 'add' ? 'border-emerald-200 bg-emerald-50/50' : tone === 'remove' ? 'border-red-200 bg-red-50/40' : 'border-slate-200')}>
      <p className="text-xs font-medium text-slate-500">{c.category_label}{c.heading ? ` · ${c.heading}` : ''} {reference(c.section_ref, c.page) && `· ${reference(c.section_ref, c.page)}`}</p>
      <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700 [overflow-wrap:anywhere]">{c.text}</p>
    </div>
  )
}

type Tab = 'summary' | 'topics' | 'modified' | 'added' | 'removed'

export default function ComparePage() {
  const { path } = useWorkspace()
  const [params, setParams] = useSearchParams()
  const [a, setA] = useState(params.get('a') ?? '')
  const [b, setB] = useState(params.get('b') ?? '')
  const [tab, setTab] = useState<Tab>('summary')
  const active = params.get('a') && params.get('b') ? { a: params.get('a')!, b: params.get('b')! } : null
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['compare', active?.a, active?.b],
    queryFn: () => api<Comparison>(path(`/compare?a=${active!.a}&b=${active!.b}`)),
    enabled: !!active,
  })

  return (
    <>
      <PageHeader title="Compare contracts" description="Select a baseline (A) and a second version or alternative (B)." />
      <Card className="mb-6">
        <CardBody>
          <form className="grid items-end gap-3 md:grid-cols-[1fr_auto_1fr_auto]" onSubmit={(e) => { e.preventDefault(); if (a && b) setParams({ a, b }) }}>
            <Field label="Contract A (baseline)" htmlFor="cmp-a"><ContractPicker id="cmp-a" value={a} onChange={setA} exclude={b} /></Field>
            <Button type="button" variant="ghost" size="icon" aria-label="Swap contracts" onClick={() => { setA(b); setB(a) }}><ArrowRightLeft /></Button>
            <Field label="Contract B (compared)" htmlFor="cmp-b"><ContractPicker id="cmp-b" value={b} onChange={setB} exclude={a} /></Field>
            <Button type="submit" disabled={!a || !b || a === b}><GitCompare />Compare</Button>
          </form>
        </CardBody>
      </Card>

      {!active ? <Card><EmptyState icon={<GitCompare />} title="Choose two analysed contracts" description="Both contracts must belong to this workspace and have finished analysis." /></Card>
        : error ? <ErrorState error={error} onRetry={refetch} /> : isLoading || !data ? <Spinner label="Comparing" /> : (
          <div className="space-y-6">
            <div className="grid gap-4 md:grid-cols-2">
              {(['a', 'b'] as const).map((k) => (
                <Card key={k} className="p-4">
                  <p className="text-xs font-semibold uppercase text-slate-500">Contract {k.toUpperCase()}</p>
                  <p className="mt-1 font-medium text-slate-900">{data[k].title}</p>
                  <div className="mt-2"><RiskBadge level={data[k].risk_level} score={data[k].risk_score} /></div>
                </Card>
              ))}
            </div>
            <Tabs<Tab> label="Comparison sections" value={tab} onChange={setTab} tabs={[
              { id: 'summary', label: 'Summary' }, { id: 'topics', label: 'Key terms', count: data.topics.filter((t) => t.status !== 'identical' && t.status !== 'not_found_in_either').length },
              { id: 'modified', label: 'Modified', count: data.clauses.modified.length }, { id: 'added', label: 'Added in B', count: data.clauses.added.length },
              { id: 'removed', label: 'Removed in B', count: data.clauses.removed.length },
            ]} />
            <div role="tabpanel">
              {tab === 'summary' && (
                <div className="grid gap-6 lg:grid-cols-2">
                  <Card>
                    <CardHeader title="Potentially unfavourable changes in B" />
                    <CardBody>{data.potentially_unfavourable.length ? <ul className="space-y-2">{data.potentially_unfavourable.map((x) => <li key={x} className="flex gap-2 text-sm text-slate-700"><TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" />{x}</li>)}</ul>
                      : <p className="text-sm text-slate-500">No new or worsened risk findings were detected in B. Review modified clauses manually.</p>}</CardBody>
                  </Card>
                  <Card>
                    <CardHeader title="Important differences" />
                    <CardBody>{data.important_differences.length ? <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">{data.important_differences.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="text-sm text-slate-500">No key-term differences detected.</p>}</CardBody>
                  </Card>
                  <Card>
                    <CardHeader title="Risk findings resolved in B" />
                    <CardBody>{data.risks.resolved_in_b.length ? <ul className="space-y-2">{data.risks.resolved_in_b.map((r) => <li key={r.title} className="flex items-center gap-2 text-sm"><RiskBadge level={r.severity} />{r.title}</li>)}</ul> : <p className="text-sm text-slate-500">None.</p>}</CardBody>
                  </Card>
                  <Card>
                    <CardHeader title="New risk findings in B" />
                    <CardBody>{data.risks.new_in_b.length ? <ul className="space-y-2">{data.risks.new_in_b.map((r) => <li key={r.title} className="flex items-center gap-2 text-sm"><RiskBadge level={r.severity} />{r.title}</li>)}</ul> : <p className="text-sm text-slate-500">None.</p>}</CardBody>
                  </Card>
                  <div className="lg:col-span-2"><Callout title="How this comparison works">{data.method}</Callout></div>
                </div>
              )}
              {tab === 'topics' && (
                <Card className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase text-slate-500"><tr><th className="px-4 py-2.5">Topic</th><th className="px-4 py-2.5">A</th><th className="px-4 py-2.5">B</th><th className="px-4 py-2.5">Result</th></tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {data.topics.map((t) => (
                        <tr key={t.key} className="align-top">
                          <td className="px-4 py-3 font-medium text-slate-900">{t.label}</td>
                          <td className="max-w-xs px-4 py-3 text-slate-600"><p className="line-clamp-4 whitespace-pre-wrap">{t.a ?? '—'}</p></td>
                          <td className="max-w-xs px-4 py-3 text-slate-600"><p className="line-clamp-4 whitespace-pre-wrap">{t.b ?? '—'}</p></td>
                          <td className="px-4 py-3"><Badge className={STATUS_CLS[t.status]}>{STATUS_LABEL[t.status]}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
              {tab === 'modified' && (data.clauses.modified.length === 0 ? <EmptyState title="No modified clauses" /> : (
                <div className="space-y-4">
                  {data.clauses.modified.map((m) => (
                    <Card key={m.a.id + m.b.id}>
                      <CardHeader title={`${m.a.category_label}${m.b.heading ? ` · ${m.b.heading}` : ''}`} description={`Text similarity ${Math.round(m.similarity * 100)}% — wording differs; review whether the meaning changed.`} />
                      <CardBody>
                        <p className="text-sm leading-relaxed text-slate-700 [overflow-wrap:anywhere]">
                          {m.diff.map((seg, i) => seg.op === 'equal' ? <span key={i}>{seg.text} </span>
                            : seg.op === 'delete' ? <del key={i} className="bg-red-100 text-red-900 decoration-red-400">{seg.text} </del>
                            : <ins key={i} className="bg-emerald-100 text-emerald-900 no-underline">{seg.text} </ins>)}
                        </p>
                        <p className="mt-2 flex gap-4 text-xs text-slate-500"><span><Minus className="inline size-3 text-red-600" /> removed from A</span><span><Plus className="inline size-3 text-emerald-600" /> added in B</span></p>
                      </CardBody>
                    </Card>
                  ))}
                </div>
              ))}
              {tab === 'added' && (data.clauses.added.length ? <div className="space-y-3">{data.clauses.added.map((c) => <ClauseBox key={c.id} c={c} tone="add" />)}</div> : <EmptyState title="No clauses added in B" />)}
              {tab === 'removed' && (data.clauses.removed.length ? <div className="space-y-3">{data.clauses.removed.map((c) => <ClauseBox key={c.id} c={c} tone="remove" />)}</div> : <EmptyState title="No clauses removed in B" />)}
            </div>
          </div>
        )}
      <LegalDisclaimer compact className="mt-6" />
    </>
  )
}
