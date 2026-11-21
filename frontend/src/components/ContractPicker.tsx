import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { ContractPage } from '@/lib/types'
import { useWorkspace } from '@/lib/workspace'
import { Select } from './ui/primitives'

export function ContractPicker({ id, value, onChange, analysedOnly = true, exclude, placeholder = 'Choose a contract' }: {
  id: string
  value: string
  onChange: (v: string) => void
  analysedOnly?: boolean
  exclude?: string
  placeholder?: string
}) {
  const { path, current } = useWorkspace()
  const { data, isLoading } = useQuery({
    queryKey: ['contracts', current?.id, 'picker', analysedOnly],
    queryFn: () => api<ContractPage>(path(`/contracts?page_size=100&sort=title${analysedOnly ? '&status=analyzed' : ''}`)),
  })
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={isLoading}>
      <option value="">{isLoading ? 'Loading…' : data?.items.length ? placeholder : 'No analysed contracts yet'}</option>
      {data?.items.filter((c) => c.id !== exclude).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
    </Select>
  )
}
