import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileText, Loader2, UploadCloud, XCircle } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { AiStatusNotice, Callout, LegalDisclaimer } from '@/components/domain'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, Field, PageHeader, Select } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import type { Contract, Folder } from '@/lib/types'
import { cn, formatBytes, hasRole } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'

interface Item {
  key: string
  file: File
  state: 'pending' | 'uploading' | 'done' | 'error'
  error?: string
  contract?: Contract
}

const ACCEPT = '.pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain'

export default function UploadPage() {
  const { path, current, capabilities } = useWorkspace()
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [items, setItems] = useState<Item[]>([])
  const [folder, setFolder] = useState('')
  const [drag, setDrag] = useState(false)
  const { data: folders = [] } = useQuery({ queryKey: ['folders', current?.id], queryFn: () => api<Folder[]>(path('/folders')) })
  const maxMb = capabilities?.max_upload_mb ?? 20

  if (!hasRole(current?.role, 'analyst')) {
    return <><PageHeader title="Upload contracts" /><Callout tone="warn" title="Viewers cannot upload documents">Ask a workspace administrator for the analyst role.</Callout></>
  }

  function add(files: FileList | File[]) {
    const next: Item[] = []
    for (const f of Array.from(files)) {
      const ext = f.name.split('.').pop()?.toLowerCase()
      const error = !['pdf', 'docx', 'txt'].includes(ext ?? '') ? 'Unsupported type — use PDF, DOCX or TXT'
        : f.size > maxMb * 1024 * 1024 ? `Larger than ${maxMb} MB` : f.size === 0 ? 'The file is empty' : undefined
      next.push({ key: `${f.name}-${f.size}-${f.lastModified}-${Math.random()}`, file: f, state: error ? 'error' : 'pending', error })
    }
    setItems((prev) => [...prev, ...next].slice(-20))
  }

  async function uploadAll() {
    const pending = items.filter((i) => i.state === 'pending')
    for (const item of pending) {
      setItems((prev) => prev.map((x) => (x.key === item.key ? { ...x, state: 'uploading' } : x)))
      const fd = new FormData()
      fd.append('file', item.file)
      if (folder) fd.append('folder_id', folder)
      fd.append('analyze', 'true')
      try {
        const c = await api<Contract>(path('/contracts'), { method: 'POST', body: fd })
        setItems((prev) => prev.map((x) => (x.key === item.key ? { ...x, state: 'done', contract: c } : x)))
      } catch (e) {
        setItems((prev) => prev.map((x) => (x.key === item.key ? { ...x, state: 'error', error: e instanceof Error ? e.message : 'Upload failed' } : x)))
      }
    }
    qc.invalidateQueries({ queryKey: ['contracts'] })
    qc.invalidateQueries({ queryKey: ['dashboard'] })
    if (pending.length) toast.success('Upload finished. Analysis runs in the background.')
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDrag(false)
    if (e.dataTransfer.files.length) add(e.dataTransfer.files)
  }
  const busy = items.some((i) => i.state === 'uploading')

  return (
    <>
      <PageHeader title="Upload contracts" description={`PDF, DOCX or TXT · up to ${maxMb} MB each · up to ${capabilities?.max_pdf_pages ?? 300} PDF pages`} />
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <Card>
          <CardBody className="space-y-4">
            <div
              onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
              onDragLeave={() => setDrag(false)}
              onDrop={onDrop}
              className={cn('flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors', drag ? 'border-brand-500 bg-brand-50' : 'border-slate-300 bg-slate-50')}
            >
              <UploadCloud className="size-8 text-slate-400" aria-hidden />
              <p className="mt-3 text-sm font-medium text-slate-900">Drag and drop contracts here</p>
              <p className="mt-1 text-sm text-slate-500">or</p>
              <Button className="mt-3" variant="secondary" onClick={() => input.current?.click()}>Choose files</Button>
              <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" aria-label="Choose files to upload"
                onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = '' }} />
            </div>
            {items.length > 0 && (
              <ul className="divide-y divide-slate-100 rounded-md border border-slate-200">
                {items.map((i) => (
                  <li key={i.key} className="flex items-center gap-3 px-3 py-2.5">
                    <FileText className="size-4 shrink-0 text-slate-400" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-slate-900">{i.file.name}</span>
                      <span className={cn('block text-xs', i.state === 'error' ? 'text-red-700' : 'text-slate-500')}>
                        {i.state === 'error' ? i.error : i.state === 'done' ? 'Uploaded — analysis queued' : formatBytes(i.file.size)}
                      </span>
                    </span>
                    {i.state === 'uploading' && <Loader2 className="size-4 animate-spin text-brand-700" aria-label="Uploading" />}
                    {i.state === 'done' && i.contract && <Button variant="link" size="sm" asChild><Link to={`/app/contracts/${i.contract.id}`}>Open</Link></Button>}
                    {i.state === 'done' && <CheckCircle2 className="size-4 text-emerald-600" aria-label="Uploaded" />}
                    {i.state === 'error' && <XCircle className="size-4 text-red-600" aria-hidden />}
                    {(i.state === 'pending' || i.state === 'error') && (
                      <Button variant="ghost" size="sm" onClick={() => setItems((p) => p.filter((x) => x.key !== i.key))}>Remove</Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-56">
                <Field label="Folder (optional)" htmlFor="up-folder">
                  <Select id="up-folder" value={folder} onChange={(e) => setFolder(e.target.value)}>
                    <option value="">No folder</option>
                    {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                  </Select>
                </Field>
              </div>
              <Button onClick={uploadAll} loading={busy} disabled={!items.some((i) => i.state === 'pending')}>Upload and analyse</Button>
            </div>
          </CardBody>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="What happens next" />
            <CardBody className="space-y-2 text-sm text-slate-600">
              <p>1. The file type, signature and structure are validated. Macros, scripts and embedded objects are never executed.</p>
              <p>2. Text is extracted{capabilities?.ocr ? ' (with OCR for scanned pages)' : ''} and split into clauses.</p>
              <p>3. Transparent rules{capabilities?.ai.available ? ' and the configured AI provider' : ''} extract key terms and flag clauses for review.</p>
              <p>4. Deadlines found in the document are added to the calendar.</p>
            </CardBody>
          </Card>
          {capabilities && <AiStatusNotice available={capabilities.ai.available} reason={capabilities.ai.reason} />}
          {capabilities && !capabilities.ocr && <Callout title="OCR is not installed on this server">Scanned PDFs without a text layer cannot be read.</Callout>}
          <LegalDisclaimer compact />
        </div>
      </div>
    </>
  )
}
