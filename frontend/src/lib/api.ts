/**
 * API client. The short-lived access token lives only in memory; the refresh token is an httpOnly,
 * SameSite=Strict cookie scoped to /api/auth that JavaScript can never read.
 */

export class ApiError extends Error {
  status: number
  details?: unknown

  constructor(status: number, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.details = details
  }
}

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? ''

let accessToken: string | null = null
let refreshing: Promise<boolean> | null = null
let onUnauthorized: (() => void) | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function getAccessToken() {
  return accessToken
}

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
}

export function apiUrl(path: string) {
  return `${BASE}${path}`
}

async function parseError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`
  let details: unknown
  try {
    const body = await res.json()
    details = body
    if (typeof body?.detail === 'string') message = body.detail
    if (Array.isArray(body?.errors) && body.errors.length) {
      message = body.errors.map((e: { loc: string[]; msg: string }) => `${e.loc.slice(1).join('.') || 'value'}: ${e.msg}`).join('; ')
    }
  } catch {
    /* non-JSON error */
  }
  if (res.status === 429) message = 'Too many requests. Please wait a moment and try again.'
  return new ApiError(res.status, message, details)
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(apiUrl('/api/auth/refresh'), {
          method: 'POST',
          credentials: 'include',
          headers: { 'X-Requested-With': 'ContractLens' },
        })
        if (!res.ok) {
          setAccessToken(null)
          return false
        }
        const data = await res.json()
        setAccessToken(data.access_token)
        return true
      } catch {
        return false
      } finally {
        setTimeout(() => (refreshing = null), 0)
      }
    })()
  }
  return refreshing
}

type Options = Omit<RequestInit, 'body'> & { body?: unknown; retry?: boolean }

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const { body, retry = true, headers, ...rest } = opts
  const h = new Headers(headers)
  h.set('X-Requested-With', 'ContractLens')
  if (accessToken) h.set('Authorization', `Bearer ${accessToken}`)
  let payload: BodyInit | undefined
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    h.set('Content-Type', 'application/json')
    payload = JSON.stringify(body)
  }
  const res = await fetch(apiUrl(path), { ...rest, headers: h, body: payload, credentials: 'include' })
  if (res.status === 401 && retry && !path.startsWith('/api/auth/login') && !path.startsWith('/api/auth/register')) {
    if (await refreshSession()) return api<T>(path, { ...opts, retry: false })
    onUnauthorized?.()
  }
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  const type = res.headers.get('content-type') ?? ''
  return (type.includes('application/json') ? res.json() : res.text()) as Promise<T>
}

/** Requests a short-lived signed link and opens it (download or inline view). */
export async function openSignedLink(path: string, inline = false) {
  const { url } = await api<{ url: string; expires_in: number }>(path, { method: 'POST' })
  const full = apiUrl(url) + (inline ? '?inline=true' : '')
  if (inline) {
    window.open(full, '_blank', 'noopener,noreferrer')
  } else {
    const a = document.createElement('a')
    a.href = full
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }
}
