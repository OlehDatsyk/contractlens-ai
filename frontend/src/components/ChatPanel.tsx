import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, MessageSquarePlus, Send, Trash2, User as UserIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import type { Contract, Conversation, Message } from '@/lib/types'
import { cn, formatDateTime, reference } from '@/lib/utils'
import { useWorkspace } from '@/lib/workspace'
import { AiStatusNotice, Callout } from './domain'
import { Button } from './ui/button'
import { EmptyState, Spinner, Textarea } from './ui/primitives'

const SUGGESTIONS = [
  'When does this contract expire?',
  'Can either party terminate the agreement early?',
  'What happens if payment is late?',
  'Who owns the intellectual property?',
  'Are there automatic renewal conditions?',
  'What obligations do I have?',
  'Which clauses should I review carefully?',
]

function AnswerMeta({ m }: { m: Message }) {
  if (m.role !== 'assistant') return null
  if (m.answer_found === false) return <Callout tone="warn" title="Not found in the document" />
  return null
}

function MessageBubble({ m }: { m: Message }) {
  const assistant = m.role === 'assistant'
  return (
    <li className={cn('flex gap-3', !assistant && 'flex-row-reverse')}>
      <span className={cn('mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full', assistant ? 'bg-brand-100 text-brand-800' : 'bg-slate-200 text-slate-700')} aria-hidden>
        {assistant ? <Bot className="size-4" /> : <UserIcon className="size-4" />}
      </span>
      <div className={cn('min-w-0 max-w-[85%] space-y-2 rounded-lg px-3.5 py-2.5 text-sm', assistant ? 'border border-slate-200 bg-white' : 'bg-brand-700 text-white')}>
        {assistant && (
          <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
            {m.mode === 'ai' ? 'From the document (AI, citations verified)' : 'Passage search (no AI)'}
          </p>
        )}
        <p className="whitespace-pre-wrap leading-relaxed [overflow-wrap:anywhere]">{m.content}</p>
        <AnswerMeta m={m} />
        {m.interpretation && (
          <div className="rounded-md bg-violet-50 px-3 py-2 text-violet-900">
            <p className="text-[11px] font-semibold uppercase tracking-wide">AI interpretation — not a statement of the document, not legal advice</p>
            <p className="mt-1 whitespace-pre-wrap">{m.interpretation}</p>
          </div>
        )}
        {m.citations.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Supporting excerpts</p>
            {m.citations.map((c, i) => (
              <figure key={i} className="rounded-md border-l-2 border-brand-200 bg-slate-50 px-3 py-1.5">
                <blockquote className="text-slate-700 [overflow-wrap:anywhere]">“{c.quote}”</blockquote>
                <figcaption className="text-xs text-slate-500">{reference(c.section, c.page) ?? c.chunk_id}</figcaption>
              </figure>
            ))}
          </div>
        )}
        <p className={cn('text-[11px]', assistant ? 'text-slate-400' : 'text-brand-100')}>{formatDateTime(m.created_at)}</p>
      </div>
    </li>
  )
}

export function ChatPanel({ contract }: { contract: Contract }) {
  const { path, capabilities } = useWorkspace()
  const qc = useQueryClient()
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [text, setText] = useState('')
  const listRef = useRef<HTMLUListElement>(null)
  const base = path(`/contracts/${contract.id}/conversations`)

  const { data: conversations = [], isLoading: loadingConvs } = useQuery({
    queryKey: ['conversations', contract.id],
    queryFn: () => api<Conversation[]>(base),
  })
  const activeId = conversationId ?? conversations[0]?.id ?? null
  const { data: messages = [], isLoading: loadingMsgs } = useQuery({
    queryKey: ['messages', activeId],
    queryFn: () => api<Message[]>(`${base}/${activeId}/messages`),
    enabled: !!activeId,
  })
  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: 'end' })
  }, [messages.length])

  const newConversation = useMutation({
    mutationFn: () => api<Conversation>(base, { method: 'POST', body: {} }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['conversations', contract.id] }); setConversationId(c.id) },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`${base}/${id}`, { method: 'DELETE' }),
    onSuccess: () => { setConversationId(null); qc.invalidateQueries({ queryKey: ['conversations', contract.id] }) },
  })
  const ask = useMutation({
    mutationFn: async (question: string) => {
      let id = activeId
      if (!id) {
        const c = await api<Conversation>(base, { method: 'POST', body: {} })
        id = c.id
        setConversationId(id)
      }
      return { id, msgs: await api<Message[]>(`${base}/${id}/messages`, { method: 'POST', body: { content: question } }) }
    },
    onSuccess: ({ id, msgs }) => {
      qc.setQueryData<Message[]>(['messages', id], (old = []) => [...old, ...msgs])
      qc.invalidateQueries({ queryKey: ['conversations', contract.id] })
      setText('')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (contract.status !== 'analyzed') {
    return <EmptyState icon={<Bot />} title="The assistant is available after analysis" description="Questions are answered from the analysed document text." />
  }
  const submit = (q: string) => { const v = q.trim(); if (v.length >= 2 && !ask.isPending) ask.mutate(v) }

  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
      <div className="space-y-2">
        <Button variant="secondary" size="sm" className="w-full" onClick={() => newConversation.mutate()} loading={newConversation.isPending}><MessageSquarePlus />New conversation</Button>
        {loadingConvs ? <Spinner /> : (
          <ul className="space-y-0.5" aria-label="Conversations">
            {conversations.map((c) => (
              <li key={c.id} className="group flex items-center">
                <button onClick={() => setConversationId(c.id)} className={cn('min-w-0 flex-1 truncate rounded-md px-2.5 py-1.5 text-left text-sm', c.id === activeId ? 'bg-brand-50 font-medium text-brand-800' : 'text-slate-600 hover:bg-slate-100')}>{c.title}</button>
                <button onClick={() => remove.mutate(c.id)} className="rounded p-1 text-slate-400 opacity-0 hover:text-red-700 focus:opacity-100 group-hover:opacity-100" aria-label={`Delete conversation ${c.title}`}><Trash2 className="size-3.5" /></button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex min-h-[28rem] flex-col rounded-lg border border-slate-200 bg-slate-50">
        {capabilities && !capabilities.ai.available && <div className="p-3 pb-0"><AiStatusNotice available={false} reason={capabilities.ai.reason} /></div>}
        <div className="flex-1 overflow-y-auto p-4" aria-live="polite">
          {loadingMsgs ? <Spinner /> : messages.length === 0 ? (
            <div className="py-6 text-center">
              <p className="text-sm font-medium text-slate-900">Ask about “{contract.title}”</p>
              <p className="mt-1 text-sm text-slate-500">Answers come only from this document, with page and clause references. Text inside the document cannot instruct the assistant.</p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => <button key={s} onClick={() => submit(s)} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-700 hover:border-brand-200 hover:bg-brand-50">{s}</button>)}
              </div>
            </div>
          ) : (
            <ul ref={listRef} className="space-y-4">{messages.map((m) => <MessageBubble key={m.id} m={m} />)}</ul>
          )}
          {ask.isPending && <Spinner label="Searching the document" className="py-4" />}
        </div>
        <form className="flex gap-2 border-t border-slate-200 bg-white p-3" onSubmit={(e) => { e.preventDefault(); submit(text) }}>
          <Textarea aria-label="Your question" rows={1} maxLength={1000} placeholder="Ask a question about this contract…" value={text}
            onChange={(e) => setText(e.target.value)} className="min-h-9 resize-none"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(text) } }} />
          <Button type="submit" size="icon" aria-label="Send question" loading={ask.isPending} disabled={text.trim().length < 2}>{!ask.isPending && <Send />}</Button>
        </form>
      </div>
    </div>
  )
}
