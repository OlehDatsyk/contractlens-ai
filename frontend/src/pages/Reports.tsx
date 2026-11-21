import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileBarChart, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ContractPicker } from '@/components/ContractPicker'
import { LegalDisclaimer } from '@/components/domain'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/dialog'
import { Badge, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, PageHeader, Select, Spinner } from '@/components/ui/primitives'
import { api, openSignedLink } from '@/lib/api'
import type { Report } from '@/lib/types'
import { formatBytes, formatDateTime, hasRole } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'

export default function ReportsPage() {
  const { path, current } = useWorkspace()
  const qc = useQueryClient()
  const canEdit = hasRole(current?.role, 'analyst')
  const [contract, setContract] = useState('')
  const [format, setFormat] = useState<'pdf' | 'csv' | 'json'>('pdf')
  const [deleting, setDeleting] = useState<Report | null>(null)
  const { data = [], isLoading, error, refetch } = useQuery({ queryKey: ['reports', current?.id], queryFn: () => api<Report[]>(path('/reports')) })
  const create = useMutation({
    mutationFn: () => api<Report>(path('/reports'), { method: 'POST', body: { contract_id: contract, format } }),
    onSuccess: async (r) => {
      qc.invalidateQueries({ queryKey: ['reports'] })
      toast.success('Report generated')
      await openSignedLink(path(`/reports/${r.id}/download-link`)).catch(() => undefined)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <>
      <PageHeader title="Reports" description="Export an analysis as a PDF report, CSV for spreadsheets or JSON for other tools." />
      {canEdit && (
        <Card className="mb-6">
          <CardHeader title="Generate a report" description="Includes overview, key information, risk summary, clauses, financial obligations, deadlines, review points and source references." />
          <CardBody>
            <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); if (contract) create.mutate() }}>
              <div className="min-w-64 flex-1"><Field label="Contract" htmlFor="rep-contract"><ContractPicker id="rep-contract" value={contract} onChange={setContract} /></Field></div>
              <div className="w-36"><Field label="Format" htmlFor="rep-format"><Select id="rep-format" value={format} onChange={(e) => setFormat(e.target.value as typeof format)}>
                <option value="pdf">PDF</option><option value="csv">CSV</option><option value="json">JSON</option>
              </Select></Field></div>
              <Button type="submit" loading={create.isPending} disabled={!contract}><FileBarChart />Generate</Button>
            </form>
          </CardBody>
        </Card>
      )}
      <Card>
        <CardHeader title="Generated reports" />
        {error ? <ErrorState error={error} onRetry={refetch} /> : isLoading ? <Spinner /> : data.length === 0 ? (
          <EmptyState icon={<FileBarChart />} title="No reports yet" description="Reports you generate are stored privately in this workspace." />
        ) : (
          <ul className="divide-y divide-slate-100">
            {data.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <Badge className="border-slate-200 bg-slate-50 uppercase text-slate-700">{r.format}</Badge>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-slate-900">{r.title}</span>
                  <span className="block text-xs text-slate-500">
                    <Link to={`/app/contracts/${r.contract_id}`} className="hover:underline">{r.contract_title}</Link> · {formatDateTime(r.created_at)} · {formatBytes(r.size_bytes)}
                  </span>
                </span>
                <Button size="sm" variant="secondary" onClick={() => openSignedLink(path(`/reports/${r.id}/download-link`)).catch((e) => toast.error(e.message))}><Download />Download</Button>
                {canEdit && <Button size="icon" variant="ghost" aria-label={`Delete report ${r.title}`} onClick={() => setDeleting(r)}><Trash2 /></Button>}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <LegalDisclaimer compact className="mt-6" />
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} danger title="Delete report?" description="The stored report file will be permanently deleted." confirmLabel="Delete"
        onConfirm={async () => {
          try { await api(path(`/reports/${deleting!.id}`), { method: 'DELETE' }); qc.invalidateQueries({ queryKey: ['reports'] }); toast.success('Report deleted') } catch (e) { toast.error(e instanceof Error ? e.message : 'Delete failed') }
        }} />
    </>
  )
}
