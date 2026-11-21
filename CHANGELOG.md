# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] - 2026-11-21

### Added
- FastAPI backend with PostgreSQL, SQLAlchemy 2 and Alembic migrations (users, workspaces, memberships, invitations,
  folders, contracts, documents, clauses, chunks, analysis results, risk findings, obligations, deadlines, reminders,
  conversations, messages, reports, notifications, audit events, jobs).
- Authentication with Argon2id, JWT access tokens, rotating refresh tokens with reuse detection, password reset,
  profile, logout everywhere; workspaces with Owner/Administrator/Analyst/Viewer roles and invitations.
- Secure upload pipeline for PDF, DOCX and TXT with signature/structure validation, isolated extraction with timeout,
  optional OCR, clause segmentation and retrieval chunks.
- Deterministic extraction and risk engine with evidence, transparent scoring and automatic deadlines.
- Provider-independent AI layer (OpenAI, Anthropic, Gemini) with schema validation, quote verification and
  prompt-injection hardening; per-workspace encrypted API keys.
- Document-grounded assistant (AI with verified citations, or passage search without AI).
- Contract comparison, deadline calendar with reminders, PDF/CSV/JSON reports, dashboard, notifications, audit log,
  retention policies and secure deletion.
- React + TypeScript SPA (Tailwind, Radix, TanStack Query, Recharts) with responsive layouts and empty/error states.
- Private storage backends: PostgreSQL, S3-compatible, local disk.
- Docker image, Docker Compose, Render Blueprint, GitHub Actions CI, Windows and macOS launchers, smoke test script.

### Security / audit fixes before release
- Upgraded `cryptography` 46.0.7 → 50.0.2 (known vulnerabilities reported by pip-audit).
- Upgraded `vitest` to 4.1.12 (critical advisory in a transitive dev dependency).
- Fixed evidence quotes that included clause headings, and governing-law extraction truncating multi-word
  jurisdictions ("England and Wales"); regression tests added.
- Resolved all mypy errors, removed an N+1 query in workspace listing, disabled chart animations, improved mobile
  layout of the contract list.
