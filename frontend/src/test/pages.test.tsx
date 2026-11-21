import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setAccessToken } from '@/lib/api'
import { AuthProvider } from '@/lib/auth'
import { WorkspaceProvider } from '@/lib/workspace'
import { LoginPage } from '@/pages/AuthPages'
import DashboardPage from '@/pages/Dashboard'

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter><AuthProvider>{ui}</AuthProvider></MemoryRouter></QueryClientProvider>)
}

afterEach(() => {
  vi.restoreAllMocks()
  setAccessToken(null)
})

describe('LoginPage', () => {
  it('shows the server error for invalid credentials', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/refresh')) return json(401, { detail: 'Session expired' })
      return json(401, { detail: 'Invalid email or password' })
    })
    wrap(<LoginPage />)
    await userEvent.type(screen.getByLabelText('Email'), 'a@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Invalid email or password')).toBeInTheDocument()
  })
})

describe('DashboardPage', () => {
  it('shows a real empty state instead of fabricated metrics', async () => {
    setAccessToken('t')
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/refresh')) return json(200, { access_token: 't' })
      if (url.endsWith('/api/auth/me')) return json(200, { id: 'u', email: 'a@example.com', full_name: 'A', created_at: '', email_notifications: true })
      if (url.endsWith('/api/workspaces')) return json(200, [{ id: 'w1', name: 'Acme', role: 'owner', created_at: '', member_count: 1 }])
      if (url.includes('/capabilities')) {
        return json(200, { ai: { available: false, provider: null, model: null, source: null, reason: 'No AI provider is configured.' }, ocr: false, email: false, storage: 'local', max_upload_mb: 20, max_pdf_pages: 300 })
      }
      if (url.includes('/dashboard')) {
        return json(200, {
          totals: { contracts: 0, analyzed: 0, high_risk: 0, medium_risk: 0, low_risk: 0, processing: 0, failed: 0, upcoming_deadlines: 0 },
          risk_distribution: [], activity: [], upcoming_deadlines: [], recent: [],
        })
      }
      return json(404, {})
    })
    wrap(<WorkspaceProvider><DashboardPage /></WorkspaceProvider>)
    await waitFor(() => expect(screen.getByText('No contracts yet')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('AI analysis is not available')).toBeInTheDocument())
    expect(screen.getByText('Total contracts').previousSibling).toHaveTextContent('0')
  })
})
