import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, refreshSession, setAccessToken, setUnauthorizedHandler } from './api'
import type { User } from './types'

interface AuthState {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (data: { email: string; password: string; full_name: string; workspace_name?: string }) => Promise<void>
  logout: () => Promise<void>
  reloadUser: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const qc = useQueryClient()

  const reloadUser = useCallback(async () => {
    setUser(await api<User>('/api/auth/me'))
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAccessToken(null)
      setUser(null)
      qc.clear()
    })
    ;(async () => {
      try {
        if (await refreshSession()) await reloadUser()
      } catch {
        setUser(null)
      } finally {
        setLoading(false)
      }
    })()
    return () => setUnauthorizedHandler(null)
  }, [qc, reloadUser])

  const login = useCallback(async (email: string, password: string) => {
    const t = await api<{ access_token: string }>('/api/auth/login', { method: 'POST', body: { email, password } })
    setAccessToken(t.access_token)
    await reloadUser()
  }, [reloadUser])

  const register = useCallback(async (data: { email: string; password: string; full_name: string; workspace_name?: string }) => {
    const t = await api<{ access_token: string }>('/api/auth/register', { method: 'POST', body: data })
    setAccessToken(t.access_token)
    await reloadUser()
  }, [reloadUser])

  const logout = useCallback(async () => {
    try {
      await api('/api/auth/logout', { method: 'POST', retry: false })
    } finally {
      setAccessToken(null)
      setUser(null)
      qc.clear()
    }
  }, [qc])

  const value = useMemo(() => ({ user, loading, login, register, logout, reloadUser }), [user, loading, login, register, logout, reloadUser])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
