import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { Role, Severity } from './types'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'informational']

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  informational: 'Info',
}

export const RISK_CATEGORY_LABEL: Record<string, string> = {
  financial: 'Financial',
  legal: 'Legal',
  operational: 'Operational',
  data_protection: 'Data protection',
  intellectual_property: 'Intellectual property',
  ambiguity: 'Ambiguity',
  unusual: 'Unusual / unfavourable',
}

export const CLAUSE_LABEL: Record<string, string> = {
  parties: 'Parties',
  term: 'Term',
  renewal: 'Renewal',
  payment: 'Payment',
  confidentiality: 'Confidentiality',
  intellectual_property: 'Intellectual property',
  liability: 'Liability',
  indemnification: 'Indemnification',
  termination: 'Termination',
  dispute_resolution: 'Dispute resolution',
  governing_law: 'Governing law',
  data_protection: 'Data protection',
  non_compete: 'Non-compete',
  exclusivity: 'Exclusivity',
  warranties: 'Warranties',
  force_majeure: 'Force majeure',
  assignment: 'Assignment',
  notices: 'Notices',
  general: 'General',
}

const RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2, owner: 3 }

export function hasRole(role: Role | undefined, minimum: Role) {
  return role !== undefined && RANK[role] >= RANK[minimum]
}

export function formatDate(value: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }) {
  if (!value) return '—'
  const d = value.length === 10 ? new Date(`${value}T00:00:00`) : new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : new Intl.DateTimeFormat(undefined, opts).format(d)
}

export function formatDateTime(value: string | null | undefined) {
  return formatDate(value, { dateStyle: 'medium', timeStyle: 'short' })
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function daysUntil(isoDate: string, today = new Date()) {
  const d = new Date(`${isoDate}T00:00:00`)
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  return Math.round((d.getTime() - t.getTime()) / 86_400_000)
}

export function relativeDays(isoDate: string) {
  const n = daysUntil(isoDate)
  if (n === 0) return 'today'
  if (n === 1) return 'tomorrow'
  if (n === -1) return 'yesterday'
  return n > 0 ? `in ${n} days` : `${-n} days ago`
}

export function reference(section?: string | null, page?: number | null) {
  const parts = [section ? `§ ${section}` : null, page ? `p. ${page}` : null].filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

export function isoDate(d: Date) {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export const SEVERITY_COLOR: Record<Severity, string> = {
  critical: '#b91c1c',
  high: '#ea580c',
  medium: '#d97706',
  low: '#059669',
  informational: '#94a3b8',
}
