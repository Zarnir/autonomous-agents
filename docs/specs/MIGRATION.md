# Migrating Existing Specs

If you already have SCRUM, waterfall, or other unstructured spec docs in this project (typical filenames: `00-PRODUCT-VISION.md`, `05-PRODUCT-BACKLOG.md`, `06-SPRINT-PLAN.md`, etc.), this guide adapts them into the canonical format the orchestrator consumes.

The canonical format is documented in `AUTHORING_GUIDE.md`. The reason for the migration is that the orchestrator's `aa-orchestrator validate` and the deterministic spec parser need a consistent structure with stable IDs, declared dependencies, and testable acceptance criteria. Free-prose specs require an LLM round-trip to interpret, which is unreliable.

---

## Three migration paths

Pick whichever matches your situation:

### Path A — Manual rewrite (recommended for important projects)

You sit down with your existing docs and translate them into structured epic files. **Best when:** the project will run for weeks and the spec quality matters.

1. Read `AUTHORING_GUIDE.md` and `EXAMPLE.md`
2. For each high-level area in your old docs (auth, billing, users, etc.), create a file in `docs/specs/epics/NN-name.md`
3. For each user story in your old backlog, write a `## Story: STORY-id` block
4. For each acceptance criterion, prefix with `AC<n>:` and make it testable
5. For each task you can identify, list it under `### Tasks` with the file it touches
6. Run `aa-orchestrator validate` and fix every error/warning
7. Move the old docs to `docs/specs/_legacy/` (or delete them) so the parser doesn't try to interpret them

### Path B — AI-assisted translation

Use a planning tool (Claude Code, Cursor, Gemini CLI, etc.) to do the rewrite. **Best when:** you want speed and your old docs are reasonably detailed.

Tell the AI tool exactly this:

> "Read `docs/specs/AUTHORING_GUIDE.md`. Read `docs/specs/EXAMPLE.md`. Then read all the existing markdown files in `docs/specs/` (the ones that aren't AUTHORING_GUIDE, EXAMPLE, or MIGRATION). Translate them into the canonical epic format and write them to `docs/specs/epics/NN-name.md` files. Keep the legacy files in place but move them to `docs/specs/_legacy/` so they're preserved but not parsed. After writing, run `aa-orchestrator validate` and report the result."

The AI should:
- Group your stories into epics by theme (auth, billing, etc.)
- Generate stable kebab-case IDs from titles (don't randomize)
- Convert vague ACs into testable assertions (ask the user when uncertain)
- Identify dependencies from the prose ("user must log in before purchasing" → STORY-purchase `depends_on: [STORY-login]`)
- List file paths from the existing component inventory or codebase if available

### Path C — Legacy-mode LLM fallback (last resort)

If you can't translate your specs and just want to try running with the existing prose docs, the orchestrator has an opt-in fallback that asks the LLM `@spec` agent to interpret them:

```bash
aa-orchestrator develop --spec-llm-fallback
```

**Caveats:**
- This was the original failure mode the project hit before the deterministic parser was added
- The `@spec` agent's output is unreliable; expect occasional parse errors
- You lose the `validate` pre-flight check
- Results may be inconsistent between runs
- IDs may be regenerated each run (breaking dependency tracking)

Use this only to **smoke-test** that the rest of the pipeline works. Switch to Path A or B before committing real work.

---

## Mapping common SCRUM artifacts to the canonical format

| Old artifact | Canonical equivalent |
|---|---|
| Product Vision (`00-PRODUCT-VISION.md`) | Top of `index.yaml` `description:` field. Keep the original file in `_legacy/` for human reference. |
| Definition of Done | Folded into each story's acceptance criteria. The DoD becomes the floor for AC quality. |
| Definition of Ready | Pre-flight checklist for human authors — not part of the parsed spec. Keep it in `_legacy/`. |
| Risk Register | Add risk-mitigation stories where the risk maps to actionable code. Move the rest to `_legacy/`. |
| Product Backlog | The set of `STORY-*` blocks in `epics/*.md`. |
| Sprint Plan | `index.yaml` `epic_order:` controls execution waves. The orchestrator's wave model replaces sprint boundaries. |
| Component Inventory | Use it to populate `files_to_touch` in tasks. Keep the source doc in `_legacy/`. |
| Team Charter | Not parsed — keep in `_legacy/`. |

---

## Mapping waterfall artifacts to the canonical format

| Old artifact | Canonical equivalent |
|---|---|
| Phase 1 / Phase 2 / Phase 3 docs | One epic file per phase: `epics/01-phase-foundation.md`, `epics/02-phase-features.md`, etc. |
| Functional Requirements (REQ-001, REQ-002…) | One story per requirement: `## Story: STORY-req-001-user-login` |
| Non-functional Requirements (NFR) | Either fold into the relevant story's AC, or create a dedicated epic `epics/99-nfr.md` |
| Test plan | Implicit — `@test` writes tests from acceptance criteria automatically |
| Architecture document | Keep in `_legacy/architecture.md`. Reference its file paths in tasks. |

---

## Worked example: SCRUM `05-PRODUCT-BACKLOG.md` → canonical

**Old (free-prose backlog):**

```markdown
## Sprint 1 Stories

### Login
Users need to be able to log in. They enter email and password and get a session.

### Forgot password
Users need to recover their password via email.

### Profile page
Users should see their info on a profile page.
```

**Canonical (`docs/specs/epics/01-auth-and-profile.md`):**

```markdown
---
id: EPIC-auth-profile
title: Authentication and Profile
priority: high
depends_on: []
---

# Authentication and Profile

User accounts: login, password recovery, profile viewing.

## Story: STORY-login
title: Login with email and password
complexity: medium
depends_on: []

As a user, I want to log in with email and password so I can access my account.

### Acceptance Criteria
- [ ] AC1: POST /login with valid credentials returns 200 with a session token
- [ ] AC2: POST /login with wrong password returns 401
- [ ] AC3: POST /login with unknown email returns 404
- [ ] AC4: Session token expires after 24 hours

### Tasks
- [ ] TASK-controller `src/controllers/auth.ts` (create)
- [ ] TASK-route `src/routes/index.ts` (modify)

## Story: STORY-forgot-password
title: Recover password via email
complexity: medium
depends_on: [STORY-login]

As a user, I want to recover my password via email when I forget it.

### Acceptance Criteria
- [ ] AC1: POST /password/forgot sends recovery email within 60 seconds
- [ ] AC2: Recovery link expires after 1 hour
- [ ] AC3: New password must differ from current password
- [ ] AC4: Old recovery links are invalidated when a new one is requested

### Tasks
- [ ] TASK-controller `src/controllers/password.ts` (create)
- [ ] TASK-mailer `src/mail/recovery.ts` (create)

## Story: STORY-profile-view
title: View own profile
complexity: small
depends_on: [STORY-login]

As a logged-in user, I want to view my profile info so I can confirm my account details.

### Acceptance Criteria
- [ ] AC1: GET /profile returns 200 with the current user's email, name, and created_at
- [ ] AC2: GET /profile without session returns 401
- [ ] AC3: Profile response does not include password_hash or any secret fields
- [ ] AC4: Response time is under 200ms for cached user data

### Tasks
- [ ] TASK-controller `src/controllers/profile.ts` (create)
- [ ] TASK-route `src/routes/index.ts` (modify)
```

Notice what changed:
- Each story got a stable ID
- ACs became numbered, testable assertions
- Dependencies became explicit (`depends_on: [STORY-login]`)
- Tasks list specific files to be touched
- Sprint structure ("Sprint 1 Stories") was dropped — wave ordering replaces it

---

## After migration

Run:

```bash
aa-orchestrator validate              # check the new specs
aa-orchestrator develop --dry-run     # generate the plan, don't implement
aa-orchestrator status                # see the parsed waves
```

Once `validate` is clean and the dry-run plan looks right, run `aa-orchestrator develop` to start the pipeline.
