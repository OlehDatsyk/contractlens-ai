import { AlertCircle, Bot, CheckCircle2, CircleDashed, Clock, Info, Loader2, Scale, ShieldAlert, Wand2, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn, reference, SEVERITY_LABEL } from '@/lib/utils'
import type { ContractStatus, Severity } from '@/lib/types'
import { Badge } from './ui/primitives'

const SEV_STYLE: Record<Severity, string> = {
  critical: 'border-red-200 bg-red-50 text-red-800',
  high: 'border-orange-200 bg-orange-50 text-orange-800',
  medium: 'border-amber-200 bg-amber-50 text-amber-800',
  low: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  informational: 'border-slate-200 bg-slate-50 text-slate-700',
}
const SEV_DOT: Record<Severity, string> = {
  critical: 'bg-red-600',
  high: 'bg-orange-500',
  medium: 'bg-amber-500',
  low: 'bg-emerald-600',
  informational: 'bg-slate-400',
}


export function RiskBadge({ level, score, className }: { level: Severity | null | undefined; score?: number | null; className?: string }) {
  if (!level) return <Badge className={cn('border-slate-200 bg-white text-slate-500', className)}>Not analysed</Badge>
  return (
    <Badge className={cn(SEV_STYLE[level], className)}>
      <span className={cn('size-1.5 rounded-full', SEV_DOT[level])} aria-hidden />
      {SEVERITY_LABEL[level]}
      {score !== undefined && score !== null && <span className="tabular opacity-75">· {score}</span>}
    </Badge>
  )
}

const STATUS: Record<ContractStatus, { label: string; icon: ReactNode; cls: string }> = {
  uploaded: { label: 'Uploaded', icon: <CircleDashed className="size-3" />, cls: 'border-slate-200 bg-white text-slate-600' },
  queued: { label: 'Queued', icon: <Clock className="size-3" />, cls: 'border-sky-200 bg-sky-50 text-sky-800' },
  processing: { label: 'Analysing', icon: <Loader2 className="size-3 animate-spin" />, cls: 'border-brand-200 bg-brand-50 text-brand-800' },
  analyzed: { label: 'Analysed', icon: <CheckCircle2 className="size-3" />, cls: 'border-emerald-200 bg-emerald-50 text-emerald-800' },
  failed: { label: 'Failed', icon: <XCircle className="size-3" />, cls: 'border-red-200 bg-red-50 text-red-800' },
}

export function StatusBadge({ status }: { status: ContractStatus }) {
  const s = STATUS[status]
  return <Badge className={s.cls}>{s.icon}{s.label}</Badge>
}

export function SourceBadge({ source, verified = true }: { source: 'rule' | 'ai' | string; verified?: boolean }) {
  if (source === 'ai') {
    return (
      <Badge
        className={verified ? 'border-violet-200 bg-violet-50 text-violet-800' : 'border-amber-300 bg-amber-50 text-amber-900'}
        title={verified ? 'AI assessment; the quoted evidence was found in the document' : 'AI assessment; the quoted evidence could NOT be found in the document'}
      >
        <Bot className="size-3" />
        {verified ? 'AI · evidence verified' : 'AI · unverified'}
      </Badge>
    )
  }
  return (
    <Badge className="border-slate-200 bg-slate-50 text-slate-700" title="Deterministic rule: a transparent text pattern matched in the document">
      <Wand2 className="size-3" />
      Rule
    </Badge>
  )
}

export function EvidenceQuote({ quote, section, page, className }: { quote?: string | null; section?: string | null; page?: number | null; className?: string }) {
  if (!quote) return null
  const ref = reference(section, page)
  return (
    <figure className={cn('rounded-md border-l-2 border-brand-200 bg-slate-50 px-3 py-2', className)}>
      <blockquote className="text-sm leading-relaxed text-slate-700 [overflow-wrap:anywhere]">“{quote}”</blockquote>
      <figcaption className="mt-1 text-xs text-slate-500">{ref ?? 'No page reference available for this format'}</figcaption>
    </figure>
  )
}

export function LegalDisclaimer({ compact, className }: { compact?: boolean; className?: string }) {
  return (
    <div className={cn('flex gap-2.5 rounded-md border border-slate-200 bg-white px-3 py-2.5 text-xs leading-relaxed text-slate-600', className)} role="note">
      <Scale className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
      <p>
        {compact
          ? 'Informational analysis only — not legal advice. Automated findings may be incomplete or wrong; verify them in the document.'
          : 'ContractLens AI is an informational document analysis tool. It is not a law firm and does not provide legal advice. Rule-based and AI analysis may be incomplete or incorrect — verify important findings in the original document and have important legal decisions reviewed by a qualified legal professional.'}
      </p>
    </div>
  )
}

export function AiStatusNotice({ available, reason }: { available: boolean; reason?: string | null }) {
  if (available) return null
  return (
    <div className="flex gap-2.5 rounded-md border border-sky-200 bg-sky-50 px-3 py-2.5 text-sm text-sky-900" role="status">
      <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>
        <p className="font-medium">AI analysis is not available</p>
        <p className="mt-0.5 text-sky-800">
          {reason ?? 'No AI provider is configured.'} Deterministic rule-based extraction, risk detection, comparison and passage search still work.
        </p>
      </div>
    </div>
  )
}

export function Callout({ tone = 'info', title, children }: { tone?: 'info' | 'warn' | 'danger'; title: string; children?: ReactNode }) {
  const styles = {
    info: 'border-slate-200 bg-slate-50 text-slate-800',
    warn: 'border-amber-200 bg-amber-50 text-amber-900',
    danger: 'border-red-200 bg-red-50 text-red-900',
  }[tone]
  const Icon = tone === 'danger' ? ShieldAlert : AlertCircle
  return (
    <div className={cn('flex gap-2.5 rounded-md border px-3 py-2.5 text-sm', styles)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>
        <p className="font-medium">{title}</p>
        {children && <div className="mt-0.5 opacity-90">{children}</div>}
      </div>
    </div>
  )
}
