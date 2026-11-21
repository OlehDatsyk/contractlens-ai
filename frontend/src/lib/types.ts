export type Role = 'owner' | 'admin' | 'analyst' | 'viewer'
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'informational'
export type ContractStatus = 'uploaded' | 'queued' | 'processing' | 'analyzed' | 'failed'

export interface User {
  id: string
  email: string
  full_name: string
  created_at: string
  email_notifications: boolean
}

export interface Workspace {
  id: string
  name: string
  role: Role
  created_at: string
  member_count: number
}

export interface DocumentInfo {
  id: string
  original_filename: string
  file_type: 'pdf' | 'docx' | 'txt'
  size_bytes: number
  page_count: number | null
  char_count: number | null
  has_page_numbers: boolean
  extraction_method: string | null
  extraction_warnings: string[]
  created_at: string
}

export interface Contract {
  id: string
  title: string
  status: ContractStatus
  contract_type: string | null
  risk_level: Severity | null
  risk_score: number | null
  error_message: string | null
  analyzed_at: string | null
  expiration_date: string | null
  folder_id: string | null
  created_at: string
  updated_at: string
  document: DocumentInfo | null
}

export interface ContractPage {
  items: Contract[]
  total: number
  page: number
  page_size: number
}

export interface Folder {
  id: string
  name: string
  contract_count: number
}

export interface Evidence {
  source: 'rule' | 'ai'
  verified: boolean
  quote?: string
  page?: number | null
  section?: string | null
  note?: string
}

export interface ClauseOverviewEntry {
  present: boolean
  count: number
  summary?: string | null
  summary_source?: string
  source: string
  excerpt?: string
  page?: number | null
  section?: string | null
}

export interface MoneyItem {
  amount?: string | null
  description: string
  days?: number | null
  quote?: string
  page?: number | null
  section?: string | null
  source?: string
}

export interface ScoreBreakdown {
  method: string
  weights: Record<Severity, number>
  counts: Record<Severity, number>
  contributions: { title: string; severity: Severity; source: string; verified: boolean; points: number }[]
  disclaimer: string
}

export interface Analysis {
  id: string
  engine: string
  ai_used: boolean
  ai_error: string | null
  input_truncated: boolean
  summary: string | null
  contract_title: string | null
  contract_type: string | null
  parties: { name: string; role: string | null }[]
  effective_date: string | null
  expiration_date: string | null
  term_description: string | null
  renewal_terms: string | null
  auto_renewal: boolean | null
  notice_period_days: number | null
  governing_law: string | null
  jurisdiction: string | null
  payment_terms: string | null
  payment_deadlines: MoneyItem[]
  late_payment_penalties: string | null
  fees: MoneyItem[]
  financial_obligations: MoneyItem[]
  price_adjustment: string | null
  clause_overview: Record<string, ClauseOverviewEntry>
  field_evidence: Record<string, Evidence> & { _ai_notes?: Record<string, { ai_value: unknown; status: string }>; _warnings?: string[] }
  risk_score: number
  risk_level: Severity
  score_breakdown: ScoreBreakdown
  created_at: string
}

export interface Finding {
  id: string
  title: string
  description: string
  category: string
  severity: Severity
  page_number: number | null
  section_ref: string | null
  excerpt: string | null
  explanation: string
  suggested_actions: string[]
  source: 'rule' | 'ai'
  rule_id: string | null
  evidence_verified: boolean
}

export interface Clause {
  id: string
  position: number
  category: string
  section_ref: string | null
  heading: string | null
  text: string
  page_number: number | null
}

export interface Obligation {
  id: string
  party: string | null
  description: string
  obligation_type: string
  due_description: string | null
  trigger_condition: string | null
  consequence: string | null
  page_number: number | null
  section_ref: string | null
  excerpt: string | null
  source: 'rule' | 'ai'
  evidence_verified: boolean
}

export interface Deadline {
  id: string
  title: string
  deadline_type: 'expiration' | 'renewal' | 'payment' | 'notice' | 'obligation' | 'custom'
  due_date: string
  description: string | null
  status: 'open' | 'done' | 'dismissed'
  reminder_days: number[]
  source: 'manual' | 'auto'
  contract_id: string | null
  contract_title: string | null
  created_at: string
}

export interface Conversation {
  id: string
  contract_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface Citation {
  chunk_id: string
  quote: string
  page: number | null
  section: string | null
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  interpretation: string | null
  citations: Citation[]
  answer_found: boolean | null
  mode: 'ai' | 'extractive' | null
  created_at: string
}

export interface Report {
  id: string
  contract_id: string
  title: string
  format: 'pdf' | 'csv' | 'json'
  size_bytes: number
  created_at: string
  contract_title: string | null
}

export interface Notification {
  id: string
  workspace_id: string | null
  kind: string
  title: string
  body: string
  link: string | null
  read_at: string | null
  created_at: string
}

export interface Member {
  user_id: string
  email: string
  full_name: string
  role: Role
  joined_at: string
}

export interface Invitation {
  id: string
  workspace_id: string
  email: string
  role: Role
  expires_at: string
  accepted_at: string | null
  created_at: string
}

export interface AISettings {
  available: boolean
  provider: string | null
  model: string | null
  source: string | null
  reason: string | null
  ai_enabled: boolean
  workspace_provider: string | null
  workspace_model: string | null
  workspace_key_set: boolean
  workspace_key_hint: string | null
  server_provider: string | null
}

export interface WorkspaceSettings {
  id: string
  name: string
  retention_days: number | null
  ai: AISettings
}

export interface Capabilities {
  ai: { available: boolean; provider: string | null; model: string | null; source: string | null; reason: string | null }
  ocr: boolean
  email: boolean
  storage: string
  max_upload_mb: number
  max_pdf_pages: number
}

export interface AuditEvent {
  id: string
  action: string
  user_email: string | null
  target_type: string | null
  target_id: string | null
  ip_address: string | null
  details: Record<string, unknown>
  created_at: string
}

export interface Dashboard {
  totals: {
    contracts: number
    analyzed: number
    high_risk: number
    medium_risk: number
    low_risk: number
    processing: number
    failed: number
    upcoming_deadlines: number
  }
  risk_distribution: { level: Severity; count: number }[]
  activity: { week: string; uploaded: number; analyzed: number }[]
  upcoming_deadlines: { id: string; title: string; due_date: string; type: string; contract_id: string | null; contract_title: string | null }[]
  recent: Contract[]
}

export interface ComparisonClause {
  id: string
  category: string
  category_label: string
  section_ref: string | null
  heading: string | null
  page: number | null
  text: string
}

export interface Comparison {
  a: { id: string; title: string; risk_score: number | null; risk_level: Severity | null; analyzed: boolean }
  b: { id: string; title: string; risk_score: number | null; risk_level: Severity | null; analyzed: boolean }
  topics: { key: string; label: string; a: string | number | null; b: string | number | null; status: 'identical' | 'different' | 'only_in_a' | 'only_in_b' | 'not_found_in_either' }[]
  clauses: {
    added: ComparisonClause[]
    removed: ComparisonClause[]
    modified: { a: ComparisonClause; b: ComparisonClause; similarity: number; diff: { op: 'equal' | 'insert' | 'delete'; text: string }[] }[]
    identical: { a: ComparisonClause; b: ComparisonClause }[]
  }
  risks: {
    new_in_b: { title: string; severity: Severity; category: string; source: string; section: string | null; page: number | null; excerpt: string }[]
    resolved_in_b: { title: string; severity: Severity; category: string; source: string; section: string | null; page: number | null; excerpt: string }[]
    severity_changes: { title: string; a: Severity; b: Severity; worse_in_b: boolean }[]
  }
  obligations: { a: Record<string, number>; b: Record<string, number> }
  important_differences: string[]
  potentially_unfavourable: string[]
  method: string
}
