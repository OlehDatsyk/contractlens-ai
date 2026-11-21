import { ArrowRight, CalendarClock, FileSearch, GitCompare, Lock, MessageSquareText, ShieldCheck, Wand2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { LegalDisclaimer } from '@/components/domain'
import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'

const FEATURES = [
  { icon: FileSearch, title: 'Structured extraction', text: 'Parties, dates, renewal terms, governing law, payment terms and fees — each value linked to the sentence it came from.' },
  { icon: ShieldCheck, title: 'Transparent risk review', text: 'Deterministic rules and optional AI findings, clearly labelled, with page and clause references and a documented scoring method.' },
  { icon: MessageSquareText, title: 'Document-grounded answers', text: 'Ask questions about a contract. Answers cite verbatim excerpts, and unverifiable answers are withheld rather than guessed.' },
  { icon: GitCompare, title: 'Version comparison', text: 'See added, removed and modified clauses side by side, and which changes raise or lower review risk.' },
  { icon: CalendarClock, title: 'Deadlines & obligations', text: 'Expiration, renewal-notice and obligation dates go on a shared calendar with configurable reminders.' },
  { icon: Lock, title: 'Built for confidential files', text: 'Workspace isolation, role-based access, private storage, short-lived signed downloads and an audit log.' },
]

export function LandingPage() {
  const { user } = useAuth()
  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-slate-100">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Logo />
          <nav className="flex items-center gap-2">
            {user ? (
              <Button asChild><Link to="/app">Open app</Link></Button>
            ) : (
              <>
                <Button variant="ghost" asChild><Link to="/login">Sign in</Link></Button>
                <Button asChild><Link to="/register">Create account</Link></Button>
              </>
            )}
          </nav>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl gap-12 px-4 py-16 sm:px-6 lg:grid-cols-[1.1fr_1fr] lg:py-24">
          <div>
            <p className="text-sm font-semibold text-brand-700">Contract intelligence for freelancers, small businesses and legal operations</p>
            <h1 className="mt-3 text-4xl font-semibold tracking-tight text-slate-900 sm:text-5xl">Understand a contract before you sign it.</h1>
            <p className="mt-5 max-w-xl text-lg leading-relaxed text-slate-600">
              Upload a PDF, Word or text contract. ContractLens extracts the key terms, highlights clauses worth a closer look,
              tracks your obligations and deadlines, and answers questions with citations to the document itself.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button size="lg" asChild><Link to={user ? '/app' : '/register'}>Get started <ArrowRight /></Link></Button>
              <Button size="lg" variant="secondary" asChild><Link to="/login">Sign in</Link></Button>
            </div>
            <p className="mt-6 text-sm text-slate-500">Works without an AI key using transparent rule-based analysis. Connect OpenAI, Anthropic or Google Gemini for deeper review.</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-5" aria-label="How a finding is presented">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">How findings are presented</p>
            <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4 shadow-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-800">High</span>
                <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700"><Wand2 className="mr-1 inline size-3" />Rule</span>
                <span className="text-xs text-slate-500">Operational risk</span>
              </div>
              <p className="mt-2 font-medium text-slate-900">Automatic renewal</p>
              <p className="mt-1 text-sm text-slate-600">The contract renews automatically unless notice is given. Notice period detected: 90 days.</p>
              <blockquote className="mt-3 border-l-2 border-brand-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                “This Agreement shall automatically renew for successive twelve-month periods unless either party gives ninety (90) days' prior written notice…”
                <span className="mt-1 block text-xs text-slate-500">§ 2.2 Term and Renewal · p. 1</span>
              </blockquote>
              <p className="mt-3 text-xs text-slate-500">Illustrative example based on a synthetic contract — not user data.</p>
            </div>
          </div>
        </section>

        <section className="border-t border-slate-100 bg-slate-50">
          <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
            <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Everything stays traceable to the document</h2>
            <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map(({ icon: Icon, title, text }) => (
                <div key={title} className="rounded-lg border border-slate-200 bg-white p-5">
                  <Icon className="size-5 text-brand-700" aria-hidden />
                  <h3 className="mt-3 font-semibold text-slate-900">{title}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-slate-600">{text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
          <LegalDisclaimer />
        </section>
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-500">
        ContractLens AI · Informational document analysis · Not a law firm · Not legal advice
      </footer>
    </div>
  )
}
