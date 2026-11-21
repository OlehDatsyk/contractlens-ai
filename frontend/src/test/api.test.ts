import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, getAccessToken, setAccessToken } from '@/lib/api'

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.restoreAllMocks()
  setAccessToken(null)
})

describe('api client', () => {
  it('sends the bearer token and CSRF header', async () => {
    setAccessToken('tok-1')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(200, { ok: true }))
    await api('/api/x')
    const headers = fetchMock.mock.calls[0][1]!.headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer tok-1')
    expect(headers.get('X-Requested-With')).toBe('ContractLens')
  })

  it('refreshes once on 401 and retries', async () => {
    setAccessToken('expired')
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json(401, { detail: 'Not authenticated' }))
      .mockResolvedValueOnce(json(200, { access_token: 'fresh' }))
      .mockResolvedValueOnce(json(200, { value: 42 }))
    const result = await api<{ value: number }>('/api/data')
    expect(result.value).toBe(42)
    expect(String(fetchMock.mock.calls[1][0])).toContain('/api/auth/refresh')
    expect(getAccessToken()).toBe('fresh')
  })

  it('raises ApiError with the server message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(422, { detail: 'Unsupported file type' }))
    await expect(api('/api/upload', { method: 'POST' })).rejects.toMatchObject({ status: 422, message: 'Unsupported file type' })
  })

  it('formats validation errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(422, { detail: 'Validation error', errors: [{ loc: ['body', 'email'], msg: 'not an email' }] }))
    const err = (await api('/api/auth/login', { method: 'POST' }).catch((e: unknown) => e)) as ApiError
    expect(err).toBeInstanceOf(ApiError)
    expect(err.message).toBe('email: not an email')
  })

  it('never persists tokens in web storage', () => {
    setAccessToken('secret-token')
    expect(JSON.stringify({ ...localStorage })).not.toContain('secret-token')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('secret-token')
  })
})
