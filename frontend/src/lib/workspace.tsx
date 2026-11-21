import { useQuery } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { api } from './api'
import type { Capabilities, Workspace } from './types'

interface WorkspaceState {
  workspaces: Workspace[]
  current: Workspace | null
  loading: boolean
  select: (id: string) => void
  capabilities: Capabilities | undefined
  /** Builds a workspace-scoped API path. */
  path: (p?: string) => string
}

const Ctx = createContext<WorkspaceState | null>(null)
const KEY = 'contractlens.workspace'

function readStored() {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<string | null>(readStored)
  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api<Workspace[]>('/api/workspaces'),
  })
  const current = workspaces.find((w) => w.id === selected) ?? workspaces[0] ?? null
  const { data: capabilities } = useQuery({
    queryKey: ['capabilities', current?.id],
    queryFn: () => api<Capabilities>(`/api/workspaces/${current!.id}/capabilities`),
    enabled: !!current,
    staleTime: 60_000,
  })
  const select = useCallback((id: string) => {
    setSelected(id)
    try {
      localStorage.setItem(KEY, id)
    } catch {
      /* storage unavailable */
    }
  }, [])
  const path = useCallback((p = '') => `/api/workspaces/${current?.id}${p}`, [current?.id])
  const value = useMemo(
    () => ({ workspaces, current, loading: isLoading, select, capabilities, path }),
    [workspaces, current, isLoading, select, capabilities, path],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWorkspace() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useWorkspace must be used inside WorkspaceProvider')
  return ctx
}
