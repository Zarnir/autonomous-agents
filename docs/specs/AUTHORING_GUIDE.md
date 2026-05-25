# Spec Authoring Guide

**Audience:** humans, AI planning tools (Claude Code, Cursor, Cline, Gemini CLI, Aider, Copilot), and anything that writes into `docs/specs/`.

**Purpose:** define the exact spec format the autonomous-agents pipeline (`/develop` and `aa-orchestrator`) consumes. Following this guide guarantees the orchestrator can parse your specs deterministically — no LLM round-trip, no ambiguity, no surprises.

---

## TL;DR for AI planning tools

When the user asks you to plan, write, or edit a sprint / backlog / spec / requirements doc in this project:

1. Specs live in `docs/specs/`
2. Each **epic** is one file in `docs/specs/epics/NN-name.md` (e.g. `01-auth.md`, `02-billing.md`)
3. Each file has **YAML frontmatter** (epic metadata) and a **structured body** (stories, acceptance criteria, tasks)
4. **Story IDs**, **task IDs**, and **acceptance criterion IDs** follow strict naming patterns (see Schema below)
5. Run `aa-orchestrator validate` before claiming the spec is ready
6. Then `/develop` (in OpenCode) or `aa-orchestrator develop` runs the pipeline

**Do not invent your own format.** Even small deviations break parsing. When uncertain, copy the structure from `docs/specs/EXAMPLE.md`.

---

## Layout

```
docs/specs/
├── AUTHORING_GUIDE.md          ← this file (don't edit unless updating the format)
├── EXAMPLE.md                  ← working reference, copy-paste starter
├── MIGRATION.md                ← if you have legacy SCRUM/waterfall docs
├── index.yaml                  ← project metadata + epic ordering (optional but recommended)
└── epics/
    ├── 01-auth.md
    ├── 02-billing.md
    └── 03-notifications.md
```

The numeric prefix on epic files (`01-`, `02-`, …) sets default execution order. `index.yaml` overrides this if present.

---

## index.yaml schema

```yaml
project: my-app                  # short identifier
description: One-line project summary
methodology: structured          # always "structured" for this format
epic_order:                      # optional — overrides filename order
  - 01-auth.md
  - 02-billing.md
  - 03-notifications.md
```

If you omit `index.yaml`, epic execution order falls back to alphanumeric sort of filenames.

---

## Epic file schema

Every file in `docs/specs/epics/` MUST have this exact shape:

````markdown
---
id: EPIC-auth
title: User Authentication
priority: high
depends_on: []
---

# User Authentication

One paragraph describing the epic. This becomes the epic's `description`.

## Story: STORY-login-email
title: Login with email and password
complexity: medium
depends_on: []

As a user, I want to log in with email and password so I can access my account.

### Acceptance Criteria
- [ ] AC1: POST /login with valid credentials returns 200 with a JWT
- [ ] AC2: POST /login with wrong password returns 401
- [ ] AC3: POST /login with unknown email returns 404
- [ ] AC4: JWT tokens expire after 24 hours

### Tasks
- [ ] TASK-handler `app/Http/Controllers/AuthController.php` (create)
- [ ] TASK-route `routes/api.php` (modify)
- [ ] TASK-types `app/Types/AuthTypes.php` (create)

## Story: STORY-password-reset
title: Password reset via email
complexity: medium
depends_on: [STORY-login-email]

As a user, I want to reset my password via email so I can recover my account.

### Acceptance Criteria
- [ ] AC1: POST /password/reset sends reset email within 60 seconds
- [ ] AC2: Reset link expires after 1 hour
- [ ] AC3: New password must differ from current password
- [ ] AC4: Old reset links are invalidated when a new one is requested

### Tasks
- [ ] TASK-controller `app/Http/Controllers/PasswordResetController.php` (create)
- [ ] TASK-mailer `app/Mail/PasswordResetMail.php` (create)
- [ ] TASK-routes `routes/api.php` (modify)
````

---

## Field reference

### Epic frontmatter (required)

| Field | Type | Rules |
|---|---|---|
| `id` | string | Must start with `EPIC-`. Kebab-case after the prefix. Stable across re-runs. |
| `title` | string | Human-readable epic title. |
| `priority` | enum | `high` \| `medium` \| `low`. Default: `medium`. |
| `depends_on` | list | List of epic IDs this epic depends on. Use `[]` if none. |

### Story header — `## Story: STORY-id`

The line `## Story: STORY-some-id` opens a story. The ID:
- Must start with `STORY-`
- Must be kebab-case after the prefix
- Must be unique across the entire project (not just within the epic)
- Must be stable across re-runs

### Story fields (after the `## Story:` line, before `### Acceptance Criteria`)

```
title: Login with email and password
complexity: medium
depends_on: [STORY-some-other-story]
```

| Field | Type | Rules |
|---|---|---|
| `title` | string | Human title. If omitted, derived from the story ID. |
| `complexity` | enum | `small` \| `medium` \| `large`. Default: `medium`. |
| `depends_on` | list | Other STORY-ids this story needs completed first. Use `[]` or omit if none. |

The next paragraph (before `### Acceptance Criteria`) is the story's **description** — write the user story here ("As a [role], I want [goal] so that [benefit]").

### Acceptance Criteria block

```
### Acceptance Criteria
- [ ] AC1: First testable assertion
- [ ] AC2: Second testable assertion
```

Rules:
- Heading must be exactly `### Acceptance Criteria`
- Each line starts with `- [ ]` (unchecked) or `- [x]` (manually marked done — orchestrator ignores the check state)
- Each line must start with `AC<n>:` where `<n>` is a positive integer (AC1, AC2, AC3, …)
- Each criterion must be testable — a behavior or contract assertion, not a vague "user-friendly UI"
- Aim for 3–6 ACs per story; fewer than 2 is suspicious, more than 8 means the story is too big

### Tasks block

```
### Tasks
- [ ] TASK-handler `app/Http/Controllers/AuthController.php` (create)
- [ ] TASK-types `app/Types/AuthTypes.php` (create)
- [ ] TASK-route `routes/api.php` (modify)
```

Rules:
- Heading must be exactly `### Tasks`
- Each line: `- [ ] TASK-<slug> \`<file-path>\` (<type>)`
- `<slug>`: kebab-case, unique per task
- `<file-path>`: backtick-wrapped, relative to project root
- `<type>`: one of `create` | `modify` | `delete` | `test` | `config`
- One file per task line (one task may touch one file)

The orchestrator uses `files_to_touch` from these task lines as the scope constraint for `@make` and `@guard`. Listing files matters — empty task lists weaken the safety net.

---

## What `aa-orchestrator validate` checks

Run before `develop` to catch errors early:

```bash
aa-orchestrator validate
```

Errors (block the pipeline):
- Missing/malformed YAML frontmatter
- IDs that don't follow the `EPIC-` / `STORY-` / `TASK-` prefix rule
- Duplicate IDs across the project
- `depends_on` references to unknown story IDs
- Dependency cycles
- Stories with zero acceptance criteria

Warnings (won't block, but degrade pipeline quality):
- Acceptance criteria with fewer than 5 words (vague — produces weak tests)
- Stories with zero tasks (no scope hints — `@guard` will allow anything)
- Epics with zero stories (likely incomplete)

---

## Common mistakes

**Mistake:** Writing prose specs with no IDs.
> *"Users should be able to log in. The system should send password reset emails."*

**Fix:** Use the structured schema above. AI tools: do not negotiate with the user on format — translate their intent into the schema.

---

**Mistake:** Putting acceptance criteria as bullets without the `AC<n>:` prefix.
> ```
> - [ ] Returns 200 on valid login
> - [ ] Returns 401 on bad password
> ```

**Fix:**
> ```
> - [ ] AC1: Returns 200 with JWT on valid login
> - [ ] AC2: Returns 401 on bad password
> ```

The orchestrator validates that every AC has a covering test by ID; missing the prefix breaks the mapping.

---

**Mistake:** Multiple files per task.
> `- [ ] TASK-auth \`src/auth.ts, src/auth.test.ts\` (create)`

**Fix:** Split into multiple tasks. One file per task line.

---

**Mistake:** Renaming an existing story.
The story ID is the durable handle. If you rename the title, keep the ID stable. If you change the ID, treat it as a new story (the orchestrator will see the old one as removed and the new one as added on next `--force`).

---

## For AI planning tools specifically

When you write or edit specs in this project, follow these rules in order:

1. **Read this guide first.** Always.
2. **Open `docs/specs/EXAMPLE.md`** to see a complete working example.
3. **Generate IDs deterministically** — pick a kebab-case slug from the title; don't randomize. Example: title "Login with email and password" → ID `STORY-login-email-password` (truncate sensibly).
4. **One file per epic.** Don't bundle multiple epics. Don't split one epic across files.
5. **Write testable acceptance criteria.** "User-friendly UI" is not testable. "Form validates email format client-side and shows error within 200ms" is.
6. **Declare dependencies explicitly.** If story B uses something story A creates, set `depends_on: [STORY-a]` on B. Don't rely on file ordering.
7. **List files in tasks** even if you have to make a best guess. The orchestrator's safety net depends on this.
8. **Run `aa-orchestrator validate`** before telling the user the specs are ready.
9. **Do not invoke `aa-orchestrator develop` yourself** unless the user explicitly asks. Planning and execution are different phases — execution should be the user's deliberate choice.

If the user has unstructured legacy specs, see `docs/specs/MIGRATION.md` for an adaptation playbook.

---

## Versioning

The current authoring schema is `v1`. The orchestrator's `progress.json` schema is `v2.0`. They are independently versioned. If the authoring schema changes incompatibly, this file's first line will say so and the parser will refuse old formats.
