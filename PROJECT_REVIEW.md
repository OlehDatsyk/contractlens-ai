# ContractLens AI — Project Review and Audit Report

Review date: 2026-11-21 · Version: 1.0.0 · Scope: full repository (backend, frontend, migrations, infrastructure, docs, tests)

## 1. Overall readiness assessment

**Ready for public GitHub publication and for a demonstration / pilot deployment.** All automated checks pass, no
critical or high-severity issue remains open, and the main workflows were verified end-to-end against a real PostgreSQL
database, a production-mode Docker container and in the browser.

It is **not** claimed to be fully secure or legally compliant. Before processing real confidential contracts at scale,
address the items in §7 (paid database with backups, edge rate limiting, MFA, antivirus scanning, provider data terms).

## 2. Architecture review

| Area | Assessment |
| --- | --- |
| Backend structure | FastAPI routers per domain; business logic in `app/services` (extraction, rules, scoring, AI, grounding, retrieval, comparison, reports, storage); thin routes. |
| Data model | 24 tables, UUID keys, FK cascades, check constraints, composite indexes for the hot filters (`workspace_id` + status/risk/created/due date), a partial unique index for job de-duplication. Every workspace-owned row carries `workspace_id`. |
| Migrations | Single initial Alembic revision; verified upgrade → downgrade → upgrade on PostgreSQL and used by the test-suite (tests run the real migrations). |
| Background jobs | Durable DB queue with `FOR UPDATE SKIP LOCKED`, retries with back-off, stale-lock recovery, de-duplication, periodic tasks coordinated through `scheduler_state`. No Redis required. |
| Storage | Pluggable private storage (PostgreSQL / S3 / local). Production refuses ephemeral local storage. |
| AI | Provider-independent HTTP clients, Pydantic-validated JSON, retry with validation feedback, grounding verification, token caps, timeouts. |
| Frontend | Route-level code splitting, TanStack Query, accessible tabs/dialogs (Radix), consistent empty/loading/error states, toasts, responsive layouts. |
| Deployment | Single Docker image serving API + SPA from one origin (first-party cookies, simple CORS), Render Blueprint, Compose stack with separate worker. |

Duplicated logic / dead code: none found after review; password policy is intentionally mirrored in the frontend for
instant feedback (the server remains authoritative). No unnecessary dependencies (no SDKs for AI providers; plain HTTPS).

## 3. Security findings

| # | Severity | Finding | Status |
| --- | --- | --- | --- |
| S1 | High | `cryptography==46.0.7` affected by known vulnerabilities (PYSEC-2026-3552/3553/3554, GHSA-537c-gmf6-5ccf). | **Fixed** — upgraded to 50.0.2; `pip-audit` reports no known vulnerabilities. |
| S2 | High (dev only) | Critical advisory in `tinypool` via `vitest` 3.x (development dependency, not shipped). | **Fixed** — `vitest` 4.1.12; `npm audit` reports 0 vulnerabilities (all deps). |
| S3 | Medium | Refresh cookie is `Secure` in production, so session refresh does not work over plain HTTP. | **By design** — HTTPS is required in production (Render provides it). Smoke test reports this explicitly. |
| S4 | Low | Account lockout returns 429 for an existing locked account vs 401 for unknown emails, and registration of an existing email skips password hashing (timing). Both allow slow account enumeration. | **Accepted / documented** — per-IP and per-account rate limits apply. Consider uniform responses if enumeration matters. |
| S5 | Low | Rate limiting is in-process (per instance). Behind Render's proxy the right-most `X-Forwarded-For` entry is used; if the proxy chain changes, several users could share one IP bucket. | **Documented** in SECURITY.md; per-account limits are unaffected. |
| S6 | Low | `bandit`: hard-coded development default secret (B105) and `xml.sax.saxutils.escape` import (B406). | **Reviewed** — the dev secret is rejected in production; `escape` only escapes output, nothing is parsed. Annotated with `nosec`. |
| S7 | Info | No antivirus scanning, MFA or registration email verification. | Open — recommended future work. |

Verified by automated tests (direct API requests, not just UI):
broken authentication (tampered/forged/`alg=none`/expired/wrong-type JWTs, token version invalidation), refresh-token
rotation and reuse detection, CSRF header enforcement, IDOR and cross-workspace access to every resource type (contracts,
analysis, clauses, findings, obligations, conversations, reports, deadlines, compare, download links), per-user conversation
privacy, RBAC escalation attempts, last-owner protection, invitation token + email binding, signed-link tampering/expiry/
revocation after membership removal, SQL-injection-shaped search strings and LIKE wildcards, XSS (document text rendered as
text; lint rule bans raw HTML), path traversal and malicious filenames, MIME spoofing and misleading extensions, oversized
bodies (413), validation errors not echoing secrets, CORS rejection of foreign origins, rate limiting, production config
refusing insecure defaults, CSV formula injection. SSRF: the server makes outbound requests only to fixed AI provider and
configured S3/SMTP endpoints — no user-supplied URLs are fetched.

## 4. Document processing security

| Case | Behaviour (tested) |
| --- | --- |
| Oversized file | Rejected (413 at the proxy layer or 422 at validation) |
| Invalid / truncated PDF | Contract marked `failed` with a clear message; no partial analysis rows |
| Malformed DOCX / fake ZIP | Rejected at upload |
| Unsupported formats / misleading extensions | Rejected by allow-list and signature checks (EXE renamed `.txt`, ZIP renamed `.pdf`, PDF renamed `.docx`) |
| Empty document / no extractable text | Fails cleanly with OCR guidance |
| Very large page counts | Rejected above `MAX_PDF_PAGES` |
| Embedded objects / macros / PDF JavaScript | Macro DOCX rejected; embedded objects and PDF actions flagged and never executed |
| Unexpected Unicode | Invisible and bidi-override characters removed (prevents hidden text); UTF-16 and non-Latin text supported |
| Resource exhaustion | ZIP-bomb limits, text-size limit, parsing in a child process with a hard timeout (tested) |
| Temporary files | None written: uploads are processed in memory and stored once; failed uploads delete the stored blob |

## 5. Bugs discovered and fixes implemented

| # | Severity | Bug | Fix |
| --- | --- | --- | --- |
| B1 | Medium | Evidence quotes included the clause heading (“Limitation of Liability The Contractor’s …”) because numbered heading lines were not recognised as headings. Misleading citations. | Heading detection accepts numbered headings; regression test added. |
| B2 | Medium | Governing law “England and Wales” extracted as “England” (regex stopped at “and”). Incorrect key term and false “different” results in comparisons. | Regex stops only at clause-level conjunctions; test tightened. |
| B3 | Medium | 12 static type errors (mypy), including a `None` dereference path in obligation party detection and a shadowed loop variable in clause segmentation. | All fixed; mypy clean. |
| B4 | Low | Workspace list issued one count query per workspace (N+1). | Single grouped query. |
| B5 | Low | On Windows, `localhost` in `DATABASE_URL` stalled psycopg (IPv6 first). | Defaults and docs use `127.0.0.1`. |
| B6 | Low | Relative `LOCAL_STORAGE_PATH` depended on the working directory. | Resolved against `backend/`. |
| B7 | Low | AI-unavailable notice repeated a sentence; Settings header said “workspace workspace”. | Copy fixed. |
| B8 | Low | Dashboard chart entry animation made fresh data look empty in screenshots; unnecessary animation. | Animations disabled. |
| B9 | Low | On phones the contract table hid status and risk behind horizontal scroll. | Badges shown under the title on small screens. |
| B10 | Low | Windows launcher used labels inside an `if` block (unreliable in cmd). | Database wait moved to a subroutine; launcher verified end-to-end. |

## 6. Test results (actual runs on 2026-11-21)

| Check | Result |
| --- | --- |
| Backend tests — SQLite | **114 passed** |
| Backend tests — PostgreSQL 16 | **114 passed** |
| Frontend tests (Vitest) | **19 passed** |
| Ruff lint / mypy | Pass / no issues (44 files) |
| Bandit | No issues (2 reviewed low findings annotated) |
| pip-audit / npm audit | No known vulnerabilities / 0 vulnerabilities |
| ESLint / TypeScript | Pass |
| Production build (Vite) | Pass |
| Docker image build | Pass (OCR available in the image) |
| Smoke test, local dev server (real PostgreSQL, worker) | **24/24** checks |
| Smoke test, production container (`ENVIRONMENT=production`, database storage) | **25/25** checks (refresh rotation skipped over HTTP by design) |
| Persistence across container restart | Document byte-identical and queued job completed after restart |
| Browser walkthrough | Register, dashboard empty state, upload via UI, analysis, risks, comparison, deadlines, reports, settings, mobile (375 px) — no page overflow; only console errors are the expected 401s of the silent session check before sign-in |

Workflows covered end-to-end: register, login, create workspace, upload PDF, upload DOCX, analyse, review extracted
information, inspect risks, ask the assistant, compare, create report, export report, create deadline, delete contract,
permission enforcement.

Not verified: live calls to OpenAI / Anthropic / Gemini (no keys were used and no paid requests were made; provider
request formats, errors, timeouts and invalid outputs are covered with mocked HTTP), real SMTP delivery (mocked), OCR on
a real scanned document (binary presence verified in the image), the macOS launcher (written for macOS; not executed on
Windows).

## 7. Remaining limitations

- Rule-based extraction is heuristic: unusual layouts, tables, multi-column PDFs and non-English contracts reduce recall.
  AI improves coverage but may still miss or misread clauses; results are labelled accordingly.
- DOCX page numbers exist only when the document contains explicit page breaks; otherwise references use clause numbers.
- OCR language data: English only in the Docker image.
- Comparison alignment is heuristic and may pair clauses imperfectly (stated in the UI).
- Password reset emails require SMTP in production.

## 8. Deployment risks

- Render free PostgreSQL expires after 30 days and has no backups — use a paid plan for real data.
- Free web instances sleep; the first request after idle is slow and background processing pauses while asleep (jobs persist).
- With `STORAGE_BACKEND=database`, document bytes count toward the database size (1 GB on the free plan).
- Migrations run on start; with several web instances, deploy one instance first or run migrations as a pre-deploy step.

## 9. Recommended future improvements

1. MFA (TOTP/WebAuthn) and email verification at registration.
2. Antivirus scanning of uploads (ClamAV sidecar).
3. Shared rate-limit store (Redis) or edge WAF for multi-instance deployments.
4. Error monitoring integration (logs are structured JSON with request IDs, ready for a collector).
5. Embedding-based retrieval in addition to BM25 for better recall in long contracts.
6. Per-clause redlining suggestions and negotiated-change tracking across versions.
7. Additional OCR languages and table-aware PDF extraction.
