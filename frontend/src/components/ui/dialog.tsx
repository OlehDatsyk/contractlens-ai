import * as D from '@radix-ui/react-dialog'
import * as M from '@radix-ui/react-dropdown-menu'
import { X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Button } from './button'

export function Dialog({ open, onOpenChange, title, description, children, footer, wide }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  wide?: boolean
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-slate-900/40" />
        <D.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-xl',
            wide ? 'max-w-2xl' : 'max-w-md',
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
            <div>
              <D.Title className="text-base font-semibold text-slate-900">{title}</D.Title>
              {description ? <D.Description className="mt-1 text-sm text-slate-500">{description}</D.Description> : <D.Description className="sr-only">{title}</D.Description>}
            </div>
            <D.Close className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close">
              <X className="size-4" />
            </D.Close>
          </div>
          {children && <div className="px-5 py-4">{children}</div>}
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 px-5 py-3">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  )
}

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel = 'Confirm', danger, onConfirm }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => Promise<unknown> | void
}) {
  const [busy, setBusy] = useState(false)
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button
            variant={danger ? 'danger' : 'default'}
            loading={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await onConfirm()
                onOpenChange(false)
              } finally {
                setBusy(false)
              }
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    />
  )
}

export function Menu({ trigger, items, align = 'end' }: {
  trigger: ReactNode
  items: ({ label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean; disabled?: boolean } | 'separator')[]
  align?: 'start' | 'end'
}) {
  return (
    <M.Root>
      <M.Trigger asChild>{trigger}</M.Trigger>
      <M.Portal>
        <M.Content align={align} sideOffset={4} className="z-50 min-w-44 rounded-md border border-slate-200 bg-white p-1 shadow-lg">
          {items.map((item, i) =>
            item === 'separator' ? (
              <M.Separator key={i} className="my-1 h-px bg-slate-100" />
            ) : (
              <M.Item
                key={item.label}
                disabled={item.disabled}
                onSelect={item.onSelect}
                className={cn(
                  'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 data-[highlighted]:bg-slate-100 [&_svg]:size-4',
                  item.danger ? 'text-red-700' : 'text-slate-700',
                )}
              >
                {item.icon}
                {item.label}
              </M.Item>
            ),
          )}
        </M.Content>
      </M.Portal>
    </M.Root>
  )
}
