import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { Spinner } from './components/ui/primitives'
import { useAuth } from './lib/auth'
import { WorkspaceProvider } from './lib/workspace'
import { ForgotPasswordPage, InvitePage, LoginPage, RegisterPage, ResetPasswordPage } from './pages/AuthPages'
import { LandingPage } from './pages/Landing'

const DashboardPage = lazy(() => import('./pages/Dashboard'))
const ContractsPage = lazy(() => import('./pages/Contracts'))
const UploadPage = lazy(() => import('./pages/Upload'))
const ContractDetailPage = lazy(() => import('./pages/ContractDetail'))
const AssistantPage = lazy(() => import('./pages/Assistant'))
const ComparePage = lazy(() => import('./pages/Compare'))
const DeadlinesPage = lazy(() => import('./pages/Deadlines'))
const ReportsPage = lazy(() => import('./pages/Reports'))
const SettingsPage = lazy(() => import('./pages/Settings'))

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Spinner label="Checking session" className="min-h-screen" />
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  return <>{children}</>
}

function GuestOnly({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <Spinner label="Checking session" className="min-h-screen" />
  return user ? <Navigate to="/app" replace /> : <>{children}</>
}

function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <p className="text-sm font-semibold text-brand-700">404</p>
      <h1 className="mt-1 text-xl font-semibold">Page not found</h1>
      <a href="/" className="mt-4 text-sm text-brand-700 underline">Go to the home page</a>
    </div>
  )
}

export function App() {
  return (
    <Suspense fallback={<Spinner className="min-h-screen" />}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
        <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/invite" element={<RequireAuth><WorkspaceProvider><InvitePage /></WorkspaceProvider></RequireAuth>} />
        <Route path="/app" element={<RequireAuth><WorkspaceProvider><AppShell /></WorkspaceProvider></RequireAuth>}>
          <Route index element={<DashboardPage />} />
          <Route path="contracts" element={<ContractsPage />} />
          <Route path="contracts/:id" element={<ContractDetailPage />} />
          <Route path="upload" element={<UploadPage />} />
          <Route path="assistant" element={<AssistantPage />} />
          <Route path="compare" element={<ComparePage />} />
          <Route path="deadlines" element={<DeadlinesPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  )
}
