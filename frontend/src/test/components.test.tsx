import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EvidenceQuote, LegalDisclaimer, RiskBadge, SourceBadge, StatusBadge } from '@/components/domain'

describe('domain components', () => {
  it('renders risk levels with score', () => {
    render(<RiskBadge level="high" score={47} />)
    expect(screen.getByText('High')).toBeInTheDocument()
    expect(screen.getByText('· 47')).toBeInTheDocument()
  })
  it('labels unanalysed contracts', () => {
    render(<RiskBadge level={null} />)
    expect(screen.getByText('Not analysed')).toBeInTheDocument()
  })
  it('distinguishes rule, verified AI and unverified AI findings', () => {
    render(<><SourceBadge source="rule" /><SourceBadge source="ai" verified /><SourceBadge source="ai" verified={false} /></>)
    expect(screen.getByText('Rule')).toBeInTheDocument()
    expect(screen.getByText('AI · evidence verified')).toBeInTheDocument()
    expect(screen.getByText('AI · unverified')).toBeInTheDocument()
  })
  it('shows evidence references or explains their absence', () => {
    const { rerender } = render(<EvidenceQuote quote="The Client shall pay." section="3.2 Payment" page={2} />)
    expect(screen.getByText('§ 3.2 Payment · p. 2')).toBeInTheDocument()
    rerender(<EvidenceQuote quote="The Client shall pay." />)
    expect(screen.getByText(/No page reference available/)).toBeInTheDocument()
  })
  it('renders document text as text, not HTML', () => {
    render(<EvidenceQuote quote={'<img src=x onerror="alert(1)">'} />)
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument()
  })
  it('includes the legal disclaimer', () => {
    render(<LegalDisclaimer />)
    expect(screen.getByRole('note')).toHaveTextContent('does not provide legal advice')
  })
  it('renders status badges', () => {
    render(<StatusBadge status="processing" />)
    expect(screen.getByText('Analysing')).toBeInTheDocument()
  })
})
