import { useQuery } from '@tanstack/react-query'
import { MessageSquareText } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { ChatPanel } from '@/components/ChatPanel'
import { ContractPicker } from '@/components/ContractPicker'
import { LegalDisclaimer } from '@/components/domain'
import { Card, CardBody, EmptyState, Field, PageHeader, Spinner } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import type { Contract } from '@/lib/types'
import { useWorkspace } from '@/lib/workspace'

export default function AssistantPage() {
  const { path } = useWorkspace()
  const [params, setParams] = useSearchParams()
  const id = params.get('contract') ?? ''
  const { data: contract, isLoading } = useQuery({ queryKey: ['contract', id], queryFn: () => api<Contract>(path(`/contracts/${id}`)), enabled: !!id })

  return (
    <>
      <PageHeader title="AI assistant" description="Ask questions about one contract. Answers are grounded in that document and cite their sources." />
      <Card className="mb-6">
        <CardBody className="max-w-md">
          <Field label="Contract" htmlFor="assistant-contract">
            <ContractPicker id="assistant-contract" value={id} onChange={(v) => setParams(v ? { contract: v } : {})} />
          </Field>
        </CardBody>
      </Card>
      {!id ? (
        <Card><EmptyState icon={<MessageSquareText />} title="Choose a contract to start" description="The assistant only sees the contract you select, within this workspace." /></Card>
      ) : isLoading || !contract ? <Spinner /> : <ChatPanel contract={contract} />}
      <LegalDisclaimer compact className="mt-6" />
    </>
  )
}
