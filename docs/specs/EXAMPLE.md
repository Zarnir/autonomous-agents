# Example: Todo List App

This file is a **complete, working example** of the canonical spec format. Use it as a copy-paste starter when authoring real specs.

The format is documented in `AUTHORING_GUIDE.md`. This file shows what a finished, valid spec set looks like. To see it parse cleanly, you can place these files at:

- `docs/specs/index.yaml`
- `docs/specs/epics/01-core-todos.md`
- `docs/specs/epics/02-categories.md`

Then run:

```bash
aa-orchestrator validate
```

You should see `OK: spec validates clean.`

---

## `docs/specs/index.yaml`

```yaml
project: todo-app
description: A simple todo list with categories and persistence
methodology: structured
epic_order:
  - 01-core-todos.md
  - 02-categories.md
```

---

## `docs/specs/epics/01-core-todos.md`

````markdown
---
id: EPIC-core-todos
title: Core Todo Management
priority: high
depends_on: []
---

# Core Todo Management

The foundational CRUD layer for todos. Every other epic depends on this.

## Story: STORY-create-todo
title: Create a new todo
complexity: small
depends_on: []

As a user, I want to create a todo with a title and optional description so I can track things I need to do.

### Acceptance Criteria
- [ ] AC1: POST /todos with valid title returns 201 and the created todo with an id
- [ ] AC2: POST /todos with empty title returns 400 with error "title is required"
- [ ] AC3: POST /todos with title longer than 200 chars returns 400
- [ ] AC4: Created todos default to status="pending" and created_at=now()
- [ ] AC5: Each todo gets a unique UUID id

### Tasks
- [ ] TASK-controller `src/controllers/todos.ts` (create)
- [ ] TASK-model `src/models/todo.ts` (create)
- [ ] TASK-route `src/routes/index.ts` (modify)
- [ ] TASK-validation `src/validation/todo.schema.ts` (create)

## Story: STORY-list-todos
title: List all todos
complexity: small
depends_on: [STORY-create-todo]

As a user, I want to see all my todos so I know what's outstanding.

### Acceptance Criteria
- [ ] AC1: GET /todos returns 200 with array of todos sorted by created_at desc
- [ ] AC2: Empty list returns 200 with an empty array, not 404
- [ ] AC3: Response includes pagination metadata (total, page, per_page)
- [ ] AC4: Default page size is 20, max 100

### Tasks
- [ ] TASK-list-handler `src/controllers/todos.ts` (modify)
- [ ] TASK-pagination `src/lib/pagination.ts` (create)

## Story: STORY-update-todo
title: Update a todo
complexity: small
depends_on: [STORY-create-todo]

As a user, I want to update a todo's title, description, or status.

### Acceptance Criteria
- [ ] AC1: PATCH /todos/:id with valid fields returns 200 and updated todo
- [ ] AC2: PATCH /todos/:id with unknown id returns 404
- [ ] AC3: Status can transition from pending → in_progress → done, but not skip states
- [ ] AC4: updated_at is set to now() on every successful update

### Tasks
- [ ] TASK-update-handler `src/controllers/todos.ts` (modify)
- [ ] TASK-status-machine `src/lib/status.ts` (create)

## Story: STORY-delete-todo
title: Delete a todo
complexity: small
depends_on: [STORY-create-todo]

As a user, I want to delete todos I no longer need.

### Acceptance Criteria
- [ ] AC1: DELETE /todos/:id returns 204 on success
- [ ] AC2: DELETE /todos/:id with unknown id returns 404
- [ ] AC3: Subsequent GET /todos/:id returns 404 after deletion

### Tasks
- [ ] TASK-delete-handler `src/controllers/todos.ts` (modify)
````

---

## `docs/specs/epics/02-categories.md`

````markdown
---
id: EPIC-categories
title: Categorize Todos
priority: medium
depends_on: [EPIC-core-todos]
---

# Categorize Todos

Let users group todos by category (work, personal, shopping, etc.).

## Story: STORY-create-category
title: Create a category
complexity: small
depends_on: []

As a user, I want to create named categories so I can group related todos.

### Acceptance Criteria
- [ ] AC1: POST /categories with name returns 201 and the category with id
- [ ] AC2: Category names are unique per user; duplicate returns 409
- [ ] AC3: Category names must be 1–50 chars; otherwise 400
- [ ] AC4: Each category has an optional color (hex string, validated)

### Tasks
- [ ] TASK-cat-controller `src/controllers/categories.ts` (create)
- [ ] TASK-cat-model `src/models/category.ts` (create)
- [ ] TASK-cat-route `src/routes/index.ts` (modify)

## Story: STORY-assign-category
title: Assign a category to a todo
complexity: small
depends_on: [STORY-create-category, STORY-update-todo]

As a user, I want to assign categories to todos so I can filter by category later.

### Acceptance Criteria
- [ ] AC1: PATCH /todos/:id with category_id sets the relation, returns 200
- [ ] AC2: PATCH /todos/:id with unknown category_id returns 400
- [ ] AC3: A todo can have at most one category at a time
- [ ] AC4: Setting category_id to null removes the category

### Tasks
- [ ] TASK-todo-cat-relation `src/models/todo.ts` (modify)
- [ ] TASK-assign-handler `src/controllers/todos.ts` (modify)

## Story: STORY-filter-by-category
title: Filter todos by category
complexity: small
depends_on: [STORY-assign-category, STORY-list-todos]

As a user, I want to see todos in a specific category.

### Acceptance Criteria
- [ ] AC1: GET /todos?category=<id> returns only todos in that category
- [ ] AC2: GET /todos?category=none returns todos with no category
- [ ] AC3: GET /todos?category=<unknown_id> returns 200 with empty array
- [ ] AC4: Filter combines correctly with existing pagination

### Tasks
- [ ] TASK-filter-handler `src/controllers/todos.ts` (modify)
- [ ] TASK-query-builder `src/lib/query.ts` (create)
````

---

## What this example demonstrates

| Pattern | Where |
|---|---|
| Cross-epic dependency (`depends_on: [EPIC-core-todos]`) | epic 02 frontmatter |
| Cross-story dependency within an epic | STORY-list-todos depends on STORY-create-todo |
| Cross-epic story dependency | STORY-assign-category depends on STORY-update-todo (in epic 01) |
| Multiple files per story (split into tasks) | STORY-create-todo has 4 tasks |
| File modification vs. creation | TASK-route is `(modify)`, TASK-controller is `(create)` |
| Wave 1 stories (no deps) | STORY-create-todo, STORY-create-category |
| Concrete, testable AC | "Returns 201 and the created todo" — not "creates the todo" |

The pipeline executes this as ~3 waves:
- **Wave 1**: STORY-create-todo, STORY-create-category
- **Wave 2**: STORY-list-todos, STORY-update-todo, STORY-delete-todo
- **Wave 3**: STORY-assign-category, STORY-filter-by-category
