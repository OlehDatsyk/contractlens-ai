import { describe, expect, it } from 'vitest'
import { passwordIssues } from '@/lib/password'
import { daysUntil, formatBytes, hasRole, isoDate, reference } from '@/lib/utils'

describe('utils', () => {
  it('compares roles by rank', () => {
    expect(hasRole('owner', 'admin')).toBe(true)
    expect(hasRole('analyst', 'analyst')).toBe(true)
    expect(hasRole('viewer', 'analyst')).toBe(false)
    expect(hasRole(undefined, 'viewer')).toBe(false)
  })
  it('formats references', () => {
    expect(reference('4.2 Payment', 3)).toBe('§ 4.2 Payment · p. 3')
    expect(reference(null, null)).toBeNull()
  })
  it('computes day differences in local time', () => {
    const today = new Date(2026, 0, 10)
    expect(daysUntil('2026-01-10', today)).toBe(0)
    expect(daysUntil('2026-01-17', today)).toBe(7)
    expect(isoDate(new Date(2026, 1, 3))).toBe('2026-02-03')
  })
  it('formats byte sizes', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
  })
  it('mirrors the password policy', () => {
    expect(passwordIssues('short')).toHaveLength(2)
    expect(passwordIssues('Sup3r-Secure-Pass!')).toEqual([])
  })
})
