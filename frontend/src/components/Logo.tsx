export function Logo({ compact }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="7" fill="#4338ca" />
        <path d="M10 8h9l5 5v11a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2z" fill="#fff" />
        <circle cx="15" cy="18" r="3.2" fill="none" stroke="#4338ca" strokeWidth="1.8" />
        <path d="m17.4 20.4 2.6 2.6" stroke="#4338ca" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
      {!compact && (
        <span className="text-[15px] font-semibold tracking-tight text-slate-900">
          ContractLens <span className="text-brand-700">AI</span>
        </span>
      )}
    </span>
  )
}
