import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, FileText, FileUp, Folder as FolderIcon, FolderPlus, Search, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { RiskBadge, StatusBadge } from '@/components/domain'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Card, EmptyState, ErrorState, Field, Input, PageHeader, Select, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import type { ContractPage, Folder } from '@/lib/types'
import { cn, formatBytes, formatDate, hasRole } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'

const STATUSES = ['uploaded', 'queued', 'processing', 'analyzed', 'failed']
const RISKS = ['critical', 'high', 'medium', 'low']

export default function ContractsPage() {
  const { path, current } = useWorkspace()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') ?? '')
  const [folderDialog, setFolderDialog] = useState(false)
  const [folderName, setFolderName] = useState('')
  const canEdit = hasRole(current?.role, 'analyst')

  const update = (patch: Record<string, string | string[] | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      next.delete(k)
      if (Array.isArray(v)) v.forEach((x) => next.append(k, x))
      else if (v) next.set(k, v)
    }
    if (!('page' in patch)) next.delete('page')
    setParams(next, { replace: true })
  }

  const page = Number(params.get('page') ?? 1)
  const query = new URLSearchParams(params)
  query.set('page_size', '20')
  if (params.get('q')) query.set('search_content', 'true')

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['contracts', current?.id, query.toString()],
    queryFn: () => api<ContractPage>(path(`/contracts?${query.toString()}`)),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.items.some((c) => c.status === 'queued' || c.status === 'processing') ? 4000 : false),
  })
  const { data: folders = [] } = useQuery({ queryKey: ['folders', current?.id], queryFn: () => api<Folder[]>(path('/folders')) })
  const createFolder = useMutation({
    mutationFn: () => api<Folder>(path('/folders'), { method: 'POST', body: { name: folderName } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['folders'] }); setFolderDialog(false); setFolderName(''); toast.success('Folder created') },
    onError: (e: Error) => toast.error(e.message),
  })

  const risk = params.getAll('risk')
  const activeFolder = params.get('folder_id')
  const filtersActive = ['q', 'status', 'risk', 'folder_id', 'date_from', 'date_to'].some((k) => params.get(k))
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <>
      <PageHeader
        title="Contracts"
        description={data ? `${data.total} contract${data.total === 1 ? '' : 's'}${filtersActive ? ' match your filters' : ''}` : 'Your workspace documents'}
        actions={canEdit && <Button asChild><Link to="/app/upload"><FileUp />Upload</Link></Button>}
      />
      <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
        <aside aria-label="Folders">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Folders</h2>
            {canEdit && <Button variant="ghost" size="icon" className="size-7" aria-label="New folder" onClick={() => setFolderDialog(true)}><FolderPlus /></Button>}
          </div>
          <ul className="flex gap-1 overflow-x-auto lg:flex-col">
            <li><button onClick={() => update({ folder_id: null })} className={cn('flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm', !activeFolder ? 'bg-brand-50 font-medium text-brand-800' : 'text-slate-600 hover:bg-slate-100')}>All contracts</button></li>
            {folders.map((f) => (
              <li key={f.id}>
                <button onClick={() => update({ folder_id: f.id })} className={cn('flex w-full items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm', activeFolder === f.id ? 'bg-brand-50 font-medium text-brand-800' : 'text-slate-600 hover:bg-slate-100')}>
                  <FolderIcon className="size-4 shrink-0" aria-hidden /><span className="truncate">{f.name}</span><span className="ml-auto text-xs text-slate-400 tabular">{f.contract_count}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <div className="min-w-0 space-y-4">
          <Card className="p-3">
            <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); update({ q: q.trim() || null }) }} role="search">
              <div className="relative min-w-56 flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-slate-400" aria-hidden />
                <Input aria-label="Search contracts" placeholder="Search titles, file names and contract text" className="pl-8" value={q} maxLength={200} onChange={(e) => setQ(e.target.value)} />
              </div>
              <Select aria-label="Status" className="w-36" value={params.get('status') ?? ''} onChange={(e) => update({ status: e.target.value || null })}>
                <option value="">Any status</option>
                {STATUSES.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
              </Select>
              <Select aria-label="Risk level" className="w-36" value={risk.length === 2 ? 'critical,high' : risk[0] ?? ''} onChange={(e) => update({ risk: e.target.value ? e.target.value.split(',') : null })}>
                <option value="">Any risk</option>
                <option value="critical,high">High or critical</option>
                {RISKS.map((r) => <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>)}
              </Select>
              <Input aria-label="Uploaded from" type="date" className="w-38" value={params.get('date_from') ?? ''} onChange={(e) => update({ date_from: e.target.value || null })} />
              <Input aria-label="Uploaded until" type="date" className="w-38" value={params.get('date_to') ?? ''} onChange={(e) => update({ date_to: e.target.value || null })} />
              <Select aria-label="Sort" className="w-40" value={params.get('sort') ?? 'created_desc'} onChange={(e) => update({ sort: e.target.value })}>
                <option value="created_desc">Newest first</option>
                <option value="created_asc">Oldest first</option>
                <option value="title">Title A–Z</option>
                <option value="risk">Highest risk</option>
                <option value="expiration">Expiring soonest</option>
              </Select>
              <Button type="submit" variant="secondary">Search</Button>
              {filtersActive && <Button type="button" variant="ghost" onClick={() => { setQ(''); setParams({}, { replace: true }) }}><X />Clear</Button>}
            </form>
          </Card>

          <Card className={cn(isFetching && !isLoading && 'opacity-80')}>
            {error ? <ErrorState error={error} onRetry={refetch} /> : isLoading ? (
              <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
            ) : data && data.items.length === 0 ? (
              filtersActive ? <EmptyState icon={<Search />} title="No contracts match" description="Try different search terms or clear the filters." />
                : <EmptyState icon={<FileText />} title="No contracts yet" description="Uploaded documents appear here with their analysis status and risk level."
                    action={canEdit && <Button asChild><Link to="/app/upload">Upload a contract</Link></Button>} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                    <tr><th className="px-4 py-2.5">Contract</th><th className="hidden px-4 py-2.5 sm:table-cell">Status</th><th className="hidden px-4 py-2.5 sm:table-cell">Risk</th><th className="hidden px-4 py-2.5 md:table-cell">Expires</th><th className="hidden px-4 py-2.5 md:table-cell">Uploaded</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {data?.items.map((c) => (
                      <tr key={c.id} className="hover:bg-slate-50">
                        <td className="max-w-md px-4 py-3">
                          <Link to={`/app/contracts/${c.id}`} className="block font-medium text-slate-900 hover:text-brand-700">{c.title}</Link>
                          <span className="block truncate text-xs text-slate-500">
                            {c.document?.original_filename} · {c.document ? formatBytes(c.document.size_bytes) : ''}{c.contract_type ? ` · ${c.contract_type}` : ''}
                          </span>
                          <span className="mt-1.5 flex flex-wrap gap-1 sm:hidden"><StatusBadge status={c.status} /><RiskBadge level={c.risk_level} score={c.risk_score} /></span>
                        </td>
                        <td className="hidden px-4 py-3 sm:table-cell"><StatusBadge status={c.status} /></td>
                        <td className="hidden px-4 py-3 sm:table-cell"><RiskBadge level={c.risk_level} score={c.risk_score} /></td>
                        <td className="hidden px-4 py-3 text-slate-600 md:table-cell">{formatDate(c.expiration_date)}</td>
                        <td className="hidden px-4 py-3 text-slate-600 md:table-cell">{formatDate(c.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {data && data.total > data.page_size && (
              <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2.5 text-sm text-slate-600">
                <span>Page {page} of {totalPages}</span>
                <div className="flex gap-1">
                  <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })} aria-label="Previous page"><ChevronLeft /></Button>
                  <Button variant="secondary" size="sm" disabled={page >= totalPages} onClick={() => update({ page: String(page + 1) })} aria-label="Next page"><ChevronRight /></Button>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
      <Dialog open={folderDialog} onOpenChange={setFolderDialog} title="New folder" description="Group contracts by client, vendor or category."
        footer={<Button loading={createFolder.isPending} disabled={!folderName.trim()} onClick={() => createFolder.mutate()}>Create folder</Button>}>
        <form onSubmit={(e) => { e.preventDefault(); if (folderName.trim()) createFolder.mutate() }}>
          <Field label="Folder name" htmlFor="folder-name"><Input id="folder-name" maxLength={80} value={folderName} onChange={(e) => setFolderName(e.target.value)} autoFocus /></Field>
        </form>
      </Dialog>
    </>
  )
}
