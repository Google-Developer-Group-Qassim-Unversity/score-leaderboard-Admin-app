# Migration Plan: `with SessionLocal()` → FastAPI `Depends(get_db)`

> **Status:** Phase 0 (planning) — ready to execute.
> **Branch:** create `refactor/db-dependency-injection` from `main` before starting.
> **Scope:** ~45 `with SessionLocal() as session:` blocks across 11 router files + 1 test fixture rewrite.

---

## 1. Why we're doing this

The backend currently opens DB sessions manually inside every route handler:

```python
# current pattern — repeated in every router
def get_all_departments():
    with SessionLocal() as session:
        departments = departments_queries.get_departments(session)
    return departments
```

This bypasses FastAPI's dependency injection (DI) system — one of the framework's core features — and forces the test harness to monkeypatch `SessionLocal.configure(bind=…)` globally (see `tests/conftest.py` lines 163–187). That hack is fragile, leaks state between tests, and only works because every module imports the same `SessionLocal` singleton.

The goal: replace this with a FastAPI dependency that yields a session per request, and override that dependency in tests with `app.dependency_overrides[get_db]`. Business logic stays identical; only the session acquisition mechanism changes.

## 2. Decisions already made

These were decided with the user before this document was written — **do not re-litigate them**:

1. **Non-route helpers** (background jobs in `emails.py`, `sync_form_submissions` in `submissions.py`, `sync_manual_form_submissions` in `submissions_manual.py`, `get_from_address`, SSE batch-fetch closures in `emails.py`, `fetch_schema`/`get_uni_id_question_id`/`fetch_form_responses` in `submissions.py`): **STAY on `SessionLocal()` direct**. They have no request scope, so DI doesn't apply. Document the policy in `AGENTS.md`.
2. **`get_db` lives in `app/DB/main.py`** alongside `SessionLocal` and `engine`. Routers already import from that module.
3. **Three commits:**
   - **Commit 1:** introduce `get_db` + `SessionDep` in `app/DB/main.py`, pilot migration on `departments.py` only, write this plan + Phase 0 baseline results to `docs/migrations/db-dependency-migration/phase-0-plan-and-result.txt`.
   - **Commit 2:** migrate every remaining router (10 files). Helpers in #1 above stay untouched.
   - **Commit 3:** rewrite the `tests/conftest.py` `client` and `db_session` fixtures to use `app.dependency_overrides[get_db]` instead of the `SessionLocal.configure(bind=…)` hack.
4. **Safety nets active during Phase 6 of commits 2 and 3:**
   - OpenAPI snapshot diff (byte-identical expected — generator deps don't surface in schema).
   - Endpoint inventory diff (`rg -n "@router\.(get|post|put|delete|patch)" app/routers/`) — identical expected.
   - Coverage gate — `app/routers/` line coverage must not drop below Phase 0 baseline.

## 3. Pre-existing context the new agent must know

### 3.1 Commands (from `AGENTS.md`)

All commands run from the **`Backend/`** directory (this is your cwd):

```bash
uv run ruff format .        # format first
uv run ruff check --fix .   # lint fix
uv run pytest               # tests (uses testcontainers MySQL 8.0 by default)
uv run mypy .               # typecheck (pre-existing errors OK to ignore)
uv run alembic upgrade head # apply migrations
```

Always run in the order: `ruff format` → `ruff check --fix` → `pytest`. Only investigate `mypy`/`ruff` errors on lines you changed.

### 3.2 Required env vars for tests

`tests/conftest.py` lines 41–48 sets these before any app import — don't touch:

```python
required_env_vars = {
    "ENV": "testing",
    "CLERK_JWKS_URL": "https://test.clerk.dev/.well-known/jwks.json",
    "GOOGLE_CLIENT_ID": "test_client_id",
    "GOOGLE_CLIENT_SECRET": "test_client_secret",
    "JWT_SECRET": "test_jwt_secret_for_testing_only",
    "CERTIFICATE_API_URL": "http://localhost:8000",
}
```

### 3.3 Current `app/DB/main.py` (the file you'll modify in Commit 1)

```python
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker

from app.config import config


DB_POOL_SIZE = 5
DB_MAX_OVERFLOW = 5
DB_POOL_TIMEOUT_SECONDS = 8
DB_POOL_RECYCLE_SECONDS = 600
DB_CONNECT_TIMEOUT_SECONDS = 5
DB_READ_TIMEOUT_SECONDS = 20
DB_WRITE_TIMEOUT_SECONDS = 20


def _build_connect_args(database_url: str) -> dict[str, int]:
    url = make_url(database_url)
    if not url.drivername.startswith("mysql"):
        return {}
    return {
        "connect_timeout": DB_CONNECT_TIMEOUT_SECONDS,
        "read_timeout": DB_READ_TIMEOUT_SECONDS,
        "write_timeout": DB_WRITE_TIMEOUT_SECONDS,
    }


engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=DB_POOL_RECYCLE_SECONDS,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT_SECONDS,
    pool_use_lifo=True,
    connect_args=_build_connect_args(config.DATABASE_URL),
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, expire_on_commit=False)
```

### 3.4 Per-router occurrence counts (`with SessionLocal() as session:`)

Use this to size the work per file:

| File | Occurrences | Notes |
|---|---|---|
| `app/routers/departments.py` | 2 | **Pilot — Commit 1** |
| `app/routers/forms.py` | 2 | one is `with LogFile(...) as log, SessionLocal() as session:` |
| `app/routers/action.py` | 6 | one has nested `session.commit()` + `session.refresh()` |
| `app/routers/points.py` | 4 | each block ends with logic *after* the `with` — keep that logic outside the (now removed) block |
| `app/routers/health.py` | 1 | keep `engine` direct import — pool status uses `engine.pool.*` |
| `app/routers/members.py` | 5 | one is `with LogFile(...) as log, SessionLocal() as session:` |
| `app/routers/attendance.py` | 1 | biggest route handler; nested helpers take `session` as param already |
| `app/routers/events.py` | 6 | one is `with LogFile(...) as log, SessionLocal() as session:`; `create_event`/`update_event` have `session.refresh(new_event)` after commit — keep them |
| `app/routers/submissions.py` | 6 | **route handlers only** — helpers (`fetch_schema`, `get_uni_id_question_id`, `fetch_form_responses`, `sync_form_submissions`) stay on `SessionLocal()` |
| `app/routers/custom.py` | many | several `with LogFile(...) as log, SessionLocal() as session:` |
| `app/routers/emails.py` | 11 | **route handlers only** — background-job closures (`send_certificates_by_event_id`, `send_manual_certificates_job`), `get_from_address`, SSE batch closures stay on `SessionLocal()` |
| `app/routers/submissions_manual.py` | 1 | **helper only — leave entirely untouched** (`sync_manual_form_submissions` is not a route) |

### 3.5 Current test fixture (the file you'll rewrite in Commit 3)

`tests/conftest.py` lines 163–187 — `client` fixture:

```python
@pytest.fixture(scope="function")
def client(engine, seed_core_data) -> Generator:
    """
    Provide a FastAPI test client with transaction rollback.

    Reconfigures SessionLocal **in-place** so every module that imported it
    (via ``from app.DB.main import SessionLocal``) will create sessions bound
    to a single test-scoped connection.  After the test the transaction is
    rolled back, undoing every INSERT/UPDATE/DELETE the routes committed.
    """
    import app.DB.main as db_main
    from app.main import app

    connection = engine.connect()
    transaction = connection.begin()

    original_bind = db_main.SessionLocal.kw["bind"]
    db_main.SessionLocal.configure(bind=connection)

    yield TestClient(app)

    db_main.SessionLocal.configure(bind=original_bind)
    if transaction.is_active:
        transaction.rollback()
    connection.close()
```

`tests/conftest.py` lines 262–276 — `db_session` fixture (test-side direct DB access):

```python
@pytest.fixture(scope="function")
def db_session(client):
    """
    Provide a SQLAlchemy session bound to the test transaction.

    Use this fixture when tests need direct DB access (e.g., inserting test data).
    All changes will be rolled back after the test via the client fixture's transaction.
    """
    from app.DB.main import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

The auth-guard override chain (`clerk_client` / `admin_client` / `super_admin_client` at lines 224–259) is **orthogonal** — do not touch.

---

## 4. Phase 0 — Baseline (run ONCE, before any code edits)

>The user already tried to run this and hit environment issues (running uv from the wrong directory). The new agent starts with cwd = `Backend/`, so the commands should "just work". If `uv run pytest` slows you down, remember testcontainers needs Docker — run `docker ps` first to confirm.

Create the baseline directory outside the repo (so it isn't committed):

```bash
mkdir -p /tmp/opencode/baseline
```

Run these in parallel from `Backend/`:

```bash
# 1. pytest with JUnit XML (gives you pass/fail counts)
uv run pytest --junitxml=/tmp/opencode/baseline/junit.xml > /tmp/opencode/baseline/pytest.log 2>&1

# 2. ruff baseline (pre-existing errors are fine)
uv run ruff check . > /tmp/opencode/baseline/ruff.log 2>&1

# 3. mypy baseline (many pre-existing errors are fine)
uv run mypy . > /tmp/opencode/baseline/mypy.log 2>&1

# 4. endpoint inventory — exact list of route decorators
rg -n "@router\.(get|post|put|delete|patch)" app/routers/ > /tmp/opencode/baseline/endpoints.txt

# 5. coverage report (pytest already adds --cov=app via pyproject.toml addopts)
# the pytest.log above has the coverage summary; extract the TOTAL line:
grep -A2 "TOTAL" /tmp/opencode/baseline/pytest.log > /tmp/opencode/baseline/coverage.txt || true
```

Capture the OpenAPI snapshot with a one-off script at `/tmp/opencode/baseline/capture_openapi.py`:

```python
# Run from Backend/ as: uv run python /tmp/opencode/baseline/capture_openapi.py
import json
import os

# Match conftest.py required_env_vars
os.environ.update({
    "ENV": "testing",
    "CLERK_JWKS_URL": "https://test.clerk.dev/.well-known/jwks.json",
    "GOOGLE_CLIENT_ID": "test_client_id",
    "GOOGLE_CLIENT_SECRET": "test_client_secret",
    "JWT_SECRET": "test_jwt_secret_for_testing_only",
    "CERTIFICATE_API_URL": "http://localhost:8000",
    # DATABASE_URL with a dummy value — app import only reads config, doesn't connect
    "DATABASE_URL": "mysql+pymysql://dummy:dummy@localhost:9999/dummy",
})

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

schema = app.openapi()
with open("/tmp/opencode/baseline/openapi.json", "w") as f:
    json.dump(schema, f, indent=2, sort_keys=True)
print(f"OpenAPI: {len(schema.get('paths', {}))} paths written")
```

Then run:

```bash
uv run python /tmp/opencode/baseline/capture_openapi.py
```

Record these baseline numbers in `docs/migrations/db-dependency-migration/phase-0-plan-and-result.txt` (the file this plan should be Mirrored into — see Phase 1 step 4 below):

- pytest: total / passed / failed / errors / skipped
- mypy: error count
- ruff: error count
- OpenAPI: number of paths
- endpoint inventory: number of `@router.*` decorators
- coverage: `TOTAL` line from pytest summary

---

## 5. Phase 1 — Commit 1: Introduce dependency + pilot

Make sure you're on a new branch:

```bash
git checkout -b refactor/db-dependency-injection
```

### Step 1.1 — Add `get_db` and `SessionDep` to `app/DB/main.py`

Append to the existing `app/DB/main.py`:

```python
from collections.abc import Generator
from typing import Annotated
from sqlalchemy.orm import Session
from fastapi import Depends


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a session per request and close it on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Convenience alias — uses Annotated so it can be used as a parameter type
# without needing Depends() inline at every call site.
SessionDep = Annotated[Session, Depends(get_db)]
```

Note: imports `from collections.abc import Generator` (Python 3.13 prefers this over `typing.Generator`).
Note: `expire_on_commit=False` is already set on `SessionLocal`, so `session.refresh(obj)` after commit keeps working.

### Step 1.2 — Migrate `app/routers/departments.py` (pilot)

**Before:**

```python
from fastapi import APIRouter, HTTPException, status
from app.DB import departments as departments_queries
from app.DB.main import SessionLocal
from app.routers.models import Department_model, NotFoundResponse

router = APIRouter()


@router.get("", status_code=status.HTTP_200_OK, response_model=list[Department_model])
def get_all_departments():
    with SessionLocal() as session:
        departments = departments_queries.get_departments(session)
    return departments


@router.get(
    "/{department_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Department_model,
    responses={404: {"model": NotFoundResponse, "description": "Department not found"}},
)
def get_department_by_id(department_id: int):
    with SessionLocal() as session:
        department = departments_queries.get_department_by_id(session, department_id)
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} not found"
            )
    return department
```

**After:**

```python
from fastapi import APIRouter, HTTPException, status
from app.DB import departments as departments_queries
from app.DB.main import SessionDep
from app.routers.models import Department_model, NotFoundResponse

router = APIRouter()


@router.get("", status_code=status.HTTP_200_OK, response_model=list[Department_model])
def get_all_departments(session: SessionDep):
    departments = departments_queries.get_departments(session)
    return departments


@router.get(
    "/{department_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Department_model,
    responses={404: {"model": NotFoundResponse, "description": "Department not found"}},
)
def get_department_by_id(department_id: int, session: SessionDep):
    department = departments_queries.get_department_by_id(session, department_id)
    if not department:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} not found"
        )
    return department
```

Note: import changed from `from app.DB.main import SessionLocal` → `from app.DB.main import SessionDep`.

### Step 1.3 — Verify the pilot

```bash
uv run ruff format .
uv run ruff check --fix .
uv run pytest tests/routers/test_events.py  # sanity (no test file for departments)
uv run pytest                                # full suite — must match Phase 0 baseline
```

Investigate any new `mypy`/`ruff` errors on lines you touched (per AGENTS.md, pre-existing errors elsewhere are OK).

> ⚠️ **At this point the test harness is still using the old `SessionLocal.configure(bind=…)` hack in `conftest.py`. That's intentional — commit 3 will rewrite it. The hack still works because `app.DB.main.SessionLocal` is still imported and used by every other router AND by the `get_db` function (which is the whole point: `get_db()` calls `SessionLocal()`). Rebinding `SessionLocal` to a test connection rebinds `get_db`'s session source too. Tests must pass identically to Phase 0.**

### Step 1.4 — Mirror this PLAN.md into Phase 0 results file

Create `docs/migrations/db-dependency-migration/phase-0-plan-and-result.txt` with:

- A copy/paste of the Phase 0 baseline numbers captured in Section 4.
- A reference to this `PLAN.md`.
- A short note that commit 1 only touched `departments.py` and `app/DB/main.py`.

### Step 1.5 — Commit 1

```bash
git add app/DB/main.py app/routers/departments.py docs/migrations/db-dependency-migration/
git commit -m "refactor(db): introduce get_db dependency and pilot on departments router

Adds get_db() and SessionDep to app/DB/main.py, migrates the two
route handlers in departments.py from `with SessionLocal() as session:`
to `session: SessionDep`, and captures the Phase 0 baseline (pytest,
ruff, mypy, OpenAPI snapshot, endpoint inventory, coverage) under
docs/migrations/db-dependency-migration/.

Business logic unchanged. Test harness still uses the legacy
SessionLocal.configure(bind=...) hack; commit 3 will replace it
with app.dependency_overrides[get_db]."
```

---

## 6. Phase 2 — Commit 2: Migrate remaining routers

Migrate in this exact order (smallest first, each verified before moving on):

1. `app/routers/forms.py` (2 occurrences)
2. `app/routers/action.py` (6)
3. `app/routers/points.py` (4)
4. `app/routers/health.py` (1)
5. `app/routers/members.py` (5)
6. `app/routers/attendance.py` (1)
7. `app/routers/events.py` (6)
8. `app/routers/submissions.py` (route handlers only — 6)
9. `app/routers/custom.py` (many)
10. `app/routers/emails.py` (route handlers only — 11)

### 6.1 Per-route transformation

For each `with SessionLocal() as session:` block, transform as follows:

**Pattern A — isolated block**

```python
# Before
def get_all_forms():
    with SessionLocal() as session:
        forms = form_queries.get_forms(session)
    return forms

# After
def get_all_forms(session: SessionDep):
    forms = form_queries.get_forms(session)
    return forms
```

**Pattern B — block combined with `LogFile` context manager**

```python
# Before
def update_form(form_id: int, form: Form_model, credentials=Depends(admin_guard)):
    with LogFile("update form") as log, SessionLocal() as session:
        try:
            ...
            session.commit()
            return updated_form
        except Exception as e:
            session.rollback()
            ...
        finally:
            write_log_json_to(log.file, form.model_dump())

# After — keep LogFile as the outer ctx manager; session arrives via DI
def update_form(form_id: int, form: Form_model, session: SessionDep, credentials=Depends(admin_guard)):
    with LogFile("update form") as log:
        try:
            ...
            session.commit()
            return updated_form
        except Exception as e:
            session.rollback()
            ...
        finally:
            write_log_json_to(log.file, form.model_dump())
```

> ⚠️ `LogFile` is a context manager that sets a `ContextVar` for log file paths. It is **orthogonal** to session lifetime. Keep it as the `with` block; just pull `session` out of the `with` and into the function signature.

**Pattern C — block with logic after `with` (e.g., `points.py`)**

```python
# Before
def get_member_points(member_id: int, ...):
    ...
    with SessionLocal() as session:
        member_points = points_queries.get_members_points_semester(session, start_date, end_date, member_id)
        if member_points is None:
            raise HTTPException(...)
        member_points_history = points_queries.get_member_points_history_semester(
            session, member_id, start_date, end_date
        )

    return Member_event_history_model(member=member_points, events=member_points_history)

# After
def get_member_points(member_id: int, session: SessionDep, ...):
    ...
    member_points = points_queries.get_members_points_semester(session, start_date, end_date, member_id)
    if member_points is None:
        raise HTTPException(...)
    member_points_history = points_queries.get_member_points_history_semester(
        session, member_id, start_date, end_date
    )

    return Member_event_history_model(member=member_points, events=member_points_history)
```

The dedent of the post-`with` body is the only structural change — keep the same logic, return statements, etc.

**Pattern D — nested `with SessionLocal()` inside a route handler**

In `submissions.py` `create_submission` and friends there's only a top-level `with`; the helper functions (`fetch_schema`, `get_uni_id_question_id`, `fetch_form_responses`, `sync_form_submissions`) have their own `SessionLocal()` blocks. **Leave helpers alone.** Only migrate the `@router.*`-decorated functions.

### 6.2 Specific edge cases

#### `app/routers/health.py`

```python
# Before
from app.DB.main import SessionLocal, engine

def db_check():
    with SessionLocal() as session:
        times = []
        ...
        finally:
            print(... engine.pool.status() ...)  # engine still needed here

# After — keep engine import; add SessionDep import
from app.DB.main import SessionDep, engine

def db_check(session: SessionDep):
    times = []
    ...
    finally:
        print(... engine.pool.status() ...)
```

#### `app/routers/emails.py` — multiple edge cases

**Edge case 1: route handler that runs a background task** (`send_certificates`, `send_manual_certificate`)

The outer `with LogFile(...), SessionLocal() as session:` is the **route handler's** session — migrate it. The **inner** `with LogFile(...), SessionLocal() as session:` is inside the background-task closure (`send_certificates_by_event_id`, `send_manual_certificates_job`) — **leave those alone** (per decision #1).

```python
# Before (route handler)
def send_certificates(event_id: int, credentials, background_tasks: BackgroundTasks):
    def send_certificates_by_event_id(event, attendance, date_str, sent_by_id):
        with LogFile("send certificates"), SessionLocal() as session:   # ← KEEP (background task)
            ...

    # Route handler logic
    with LogFile("send certificates [JOB]"), SessionLocal() as session:   # ← MIGRATE
        write_log_title(...)
        event = events_queries.get_event_by_id(session, event_id)
        ...
        background_tasks.add_task(send_certificates_by_event_id, event, attendance, ...)
        return {...}

# After
def send_certificates(event_id: int, session: SessionDep, credentials, background_tasks: BackgroundTasks):
    def send_certificates_by_event_id(event, attendance, date_str, sent_by_id):
        with LogFile("send certificates"), SessionLocal() as session:   # ← still here
            ...

    # Route handler logic
    with LogFile("send certificates [JOB]"):   # ← only LogFile remains
        write_log_title(...)
        event = events_queries.get_event_by_id(session, event_id)
        ...
        background_tasks.add_task(send_certificates_by_event_id, event, attendance, ...)
        return {...}
```

**Edge case 2: `get_from_address` helper** (line 209) — **STAYS** as-is. It's a module-level helper called from inside background tasks (`send_certificates_by_event_id` calls it via `call_certificate_api(...)` chain) — no request scope.

**Edge case 3: SSE endpoints with closures** (`get_certificate_event_logs` line 416, `stream_enriched_email_logs` line 527) — the route handler itself has NO `with SessionLocal()` at the top level; only the inner closure (`get_logs_batch` / `get_batch`) does. The closure runs over multiple SSE iterations and cannot use the request-scoped DI session meaningfully. **Leave these alone.** However, **do** migrate any top-level `with SessionLocal() as session:` that exists in the route handler itself before the SSE generator body — verify by reading carefully.

> For the two SSE endpoints, the `with SessionLocal()` is only inside `def get_logs_batch(...)` / `def get_batch(...)` closures — keep them. The route handler signatures do NOT need to gain a `session: SessionDep` parameter.

**Edge case 4: `download_certificate` (line 590)** — straightforward route handler with a single `with SessionLocal() as session:`. Migrate it.

**Edge case 5: `send_acceptance_blasts` (line 659)** — async route handler, `with LogFile("send acceptance blasts"), SessionLocal() as session:`. Migrate the same way: keep `LogFile`, add `session: SessionDep` to signature.

**Edge case 6: `send_acceptance_test` (line 723)** — no `with SessionLocal()` at all (only `with LogFile(...)`, which is fine). Leave alone.

#### `app/routers/submissions_manual.py`

`sync_manual_form_submissions` is a non-route helper (no `@router.*` decorator). The two `@router.post` endpoints (`manual_create_google_submissions`, `manual_run_google_form_submissions`) only have `with LogFile(...) as log:` — no `SessionLocal()`. **DO NOT TOUCH THIS FILE.**

#### `app/routers/submissions.py`

**Route handlers to migrate** (those decorated with `@router.*` AND containing `with SessionLocal()`):
- `create_submission` (line 31)
- `check_submission_exists` (line 51) — `with LogFile(...), SessionLocal() as session:`
- `accept_submission` (line 77)
- `test_fetch_form_responses` (line 343) — only has `with LogFile(...)`, NO `SessionLocal()` block — leave alone
- `google_forms_webhook` (line 356) — only `with LogFile(...)`, NO `SessionLocal()` block — leave alone

**Helpers to LEAVE ALONE:**
- `fetch_schema` (line 117)
- `get_uni_id_question_id` (line 140)
- `fetch_form_responses` (line 170)
- `sync_form_submissions` (line 218)

After migrating the four route handlers, `from app.DB.main import SessionLocal` is still needed by the helpers — **do not remove it from the imports.** Just confirm it's still used; ruff will tell you if it's now unused (it won't be).

#### `app/routers/attendance.py`

Single occurrence in `mark_attendance` (line 76). Straightforward Pattern B. Helper functions `get_event_with_attendable_log`, `is_member_marked_for_day` already take `session: Session` as parameter — they don't need changes.

Other route handlers in this file (`backfill_attendance`, `get_event_attendance`, `mark_attendance_manual`, `remove_attendance_manual`) follow Pattern A or B.

Actually wait — `rg -c` reported only 1 occurrence in `attendance.py`. Re-read the file before migrating: only `mark_attendance`'s `with LogFile("mark attendance"), SessionLocal() as session:` and the other three handlers each have one `with LogFile("..."), SessionLocal() as session:` of their own. Verify with:

```bash
rg -n "with .*SessionLocal\(\)" app/routers/attendance.py
```

before assuming.

#### `app/routers/custom.py`

Many handlers, mix of Pattern A and Pattern B. Several have `with LogFile("...") as log, SessionLocal() as session:`. Go through each one carefully.

### 6.3 Per-file verification

After migrating each router:

```bash
uv run ruff format .
uv run ruff check --fix .
uv run pytest tests/routers/test_<router_name>.py    # if a test file exists
uv run pytest                                        # full suite for safety
```

If a router has no test file (e.g., departments, actions, custom, points, attendance), run the full suite after migrating that router and visually confirm no NEW failures appeared vs Phase 0 baseline.

### 6.4 Phase 6 — full verification after all routers migrated

Run the same baselines as Phase 0 and **diff**:

```bash
# 1. Tests — counts must match or improve
uv run pytest --junitxml=/tmp/opencode/post_junit.xml > /tmp/opencode/post_pytest.log 2>&1
# Compare to /tmp/opencode/baseline/junit.xml — same total/passed/failed/errors/skipped expected.

# 2. Lint + format
uv run ruff format . && uv run ruff check --fix .

# 3. Typecheck — only investigate new errors on changed lines
uv run mypy . > /tmp/opencode/post_mypy.log 2>&1
diff /tmp/opencode/baseline/mypy.log /tmp/opencode/post_mypy.log | grep "^>" | head -20

# 4. OpenAPI snapshot — must be byte-identical
uv run python /tmp/opencode/baseline/capture_openapi.py  # but write to /tmp/opencode/post_openapi.json instead
# Update the script to write to /tmp/opencode/post_openapi.json OR just edit the path inline.
diff /tmp/opencode/baseline/openapi.json /tmp/opencode/post_openapi.json
# Expected: no output (identical). If there's a diff, investigate immediately —
# DI generator dependencies don't surface in the schema, so adding `session: SessionDep`
# to a route handler should NOT change the schema. If it does, something went wrong.

# 5. Endpoint inventory — must be identical
rg -n "@router\.(get|post|put|delete|patch)" app/routers/ > /tmp/opencode/post_endpoints.txt
diff /tmp/opencode/baseline/endpoints.txt /tmp/opencode/post_endpoints.txt

# 6. Coverage gate — app/routers/ coverage must not drop
grep -A2 "TOTAL" /tmp/opencode/post_pytest.log > /tmp/opencode/post_coverage.txt
diff /tmp/opencode/baseline/coverage.txt /tmp/opencode/post_coverage.txt
# Coverage should be identical or higher.

# 7. Smoke test — instantiate TestClient and hit one GET per migrated router
# (Write a quick one-off script if the suite doesn't already cover this.)
```

### 6.5 Update `AGENTS.md` Architecture section

Add `get_db`/`SessionDep` to the architecture overview and add a policy line:

> - **`app/DB/main.py`** – `engine`, `SessionLocal`, and the **`get_db()`** FastAPI dependency (yield a session per request). Use `Depends(get_db)` or the `SessionDep` alias in route handlers; use `SessionLocal()` directly in background jobs and non-route helpers (no request scope).

### 6.6 Commit 2

```bash
git add app/routers/ app/DB/main.py AGENTS.md
git commit -m "refactor(db): migrate all routers to Depends(get_db)

Replaces \`with SessionLocal() as session:\` with \`session: SessionDep\` in
every route handler across forms, action, points, health, members,
attendance, events, submissions, custom, and emails.

Business logic unchanged. Background-job helpers (send_certificates_*,
send_manual_certificates_job, get_from_address, SSE batch closures,
sync_form_submissions, fetch_schema, get_uni_id_question_id,
fetch_form_responses, sync_manual_form_submissions) intentionally
remain on SessionLocal() direct — they have no request scope.

Test harness still uses the legacy SessionLocal.configure(bind=...)
hack; commit 3 will replace it with app.dependency_overrides[get_db]."
```

---

## 7. Phase 4 — Commit 3: Rewrite test fixtures

### Step 7.1 — Rewrite the `client` fixture

Replace `tests/conftest.py` lines 163–187 with:

```python
@pytest.fixture(scope="function")
def client(engine, seed_core_data) -> Generator:
    """
    Provide a FastAPI test client with transaction rollback.

    Overrides the get_db dependency so every route handler receives a session
    bound to a single test-scoped connection. After the test the outer
    transaction is rolled back, undoing every INSERT/UPDATE/DELETE the routes
    committed. This replaces the legacy SessionLocal.configure(bind=...) hack.
    """
    from app.DB.dependencies_test_helpers import get_db_override_factory  # OR inline below
    from app.DB.main import get_db, SessionLocal
    from app.main import app

    connection = engine.connect()
    transaction = connection.begin()

    def override_get_db() -> Generator:
        # Bind a Session to the test connection. Bind at the Session level
        # (not the sessionmaker) so production SessionLocal stays untouched.
        session = SessionLocal(bind=connection)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestClient(app)

    app.dependency_overrides.pop(get_db, None)
    if transaction.is_active:
        transaction.rollback()
    connection.close()
```

Note: the override closes the session at request end, but does **not** commit or rollback the outer transaction — route handlers still call `session.commit()` (which under the hood becomes a `COMMIT` on the savepoint / inner transaction on the connection), but the outer transaction rollback at fixture teardown undoes everything. This is the same semantic the old `SessionLocal.configure(bind=connection)` hack was relying on, just expressed through DI.

Verify by running tests that mutate (`test_authorized_create_event`, `test_update_event`, `test_update_event_status`, `test_delete_event`, etc.) — they must still pass.

### Step 7.2 — Rewrite the `db_session` fixture

Replace `tests/conftest.py` lines 262–276 with:

```python
@pytest.fixture(scope="function")
def db_session(client) -> Generator:
    """
    Provide a SQLAlchemy session bound to the same test connection as the
    overridden get_db. Use this when tests need direct DB access (e.g.,
    inserting test data). All changes will be rolled back after the test via
    the client fixture's outer transaction.
    """
    from app.DB.main import SessionLocal

    # The client fixture has already overridden get_db to bind sessions to
    # the test connection. We just create a Session the same way.
    # We can't easily access the connection from here without leaking state,
    # so we re-create the binding using SessionLocal(bind=...) but we need
    # the test connection. Cleanest: pop the override temporarily to grab
    # the connection is messy — instead, expose the override closure over
    # the connection via a ContextVar OR re-read the override's underlying
    # connection.
    #
    # Simpler: have the override register the connection on a fixture-scoped
    # attribute accessible here. See Section 7.3.
    ...
```

Wait — this is trickier than it looks. The `db_session` fixture (used in tests like `test_unauthorized_update_event` which adds an event via direct SQL then asserts that `PUT /events/{id}` returns 403) needs the session to share the same connection/transaction as what `get_db` will use inside the route. With the old hack that was automatic. With DI overrides we need to **explicitly share** the connection.

**Cleanest solution:** have the `client` fixture stash the test connection on a fixture-scoped mutable (e.g., a `_test_connection` module-level slot or a request-scoped container) so the `db_session` fixture can read it back.

### Step 7.3 — Concrete `client` + `db_session` rewrite

Put the connection in a fixture-local context var or a small shared structure:

```python
# tests/conftest.py — replace both fixtures with this single block

@pytest.fixture(scope="function")
def _test_db_connection(engine, seed_core_data):
    """
    Internal fixture that opens a connection and begins a transaction.
    Both the client fixture and the db_session fixture share this connection
    so route-side and test-side writes are mutually visible.
    """
    connection = engine.connect()
    transaction = connection.begin()
    yield connection
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(_test_db_connection) -> Generator:
    """
    FastAPI TestClient with get_db overridden to yield sessions bound to
    the test connection. Outer transaction is rolled back after the test.
    """
    from app.DB.main import get_db, SessionLocal
    from app.main import app

    connection = _test_db_connection

    def override_get_db() -> Generator:
        session = SessionLocal(bind=connection)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestClient(app)

    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="function")
def db_session(_test_db_connection) -> Generator:
    """
    Direct SQLAlchemy session for test-side DB access, sharing the same
    connection/transaction as the overridden get_db. Mutations are rolled
    back at fixture teardown via _test_db_connection.
    """
    from app.DB.main import SessionLocal

    session = SessionLocal(bind=_test_db_connection)
    try:
        yield session
    finally:
        session.close()
```

**Important:** drop the old `client` fixture's body entirely (the `SessionLocal.configure(bind=...)` hack). Re-run the full test suite — verify the same pass/fail numbers as Phase 0.

### Step 7.4 — Update `tests/tests.md`

Add a short note describing the new override pattern, replacing the description of the `SessionLocal.configure(bind=...)` hack.

### Step 7.5 — Phase 6 — full verification after Commit 3

Run all six Phase 6 verifications again. The OpenAPI snapshot, endpoint inventory, and coverage gate should still match Phase 0 (these three safety nets are about routes, not test fixtures).

### Step 7.6 — Commit 3

```bash
git add tests/conftest.py tests/tests.md
git commit -m "test(conftest): replace SessionLocal.configure hack with get_db override

The client and db_session fixtures now share a single test connection via
a private _test_db_connection fixture, and override get_db with a closure
that binds SessionLocal to that connection. Mutations from both route
handlers and direct test-side writes remain mutually visible, and the
outer transaction is rolled back at fixture teardown — same semantics as
the old SessionLocal.configure(bind=connection) hack, but without the
global side effects.

All three safety nets (OpenAPI snapshot, endpoint inventory, coverage)
unchanged vs Phase 0 baseline."
```

---

## 8. Final verification & merge checklist

Before pushing or considering this done:

- [ ] `uv run ruff format .` — clean
- [ ] `uv run ruff check --fix .` — clean (no errors beyond pre-existing)
- [ ] `uv run pytest` — pass/fail counts identical to Phase 0 baseline (or better)
- [ ] `uv run mypy .` — no NEW errors on changed lines
- [ ] OpenAPI snapshot diff vs Phase 0 — byte-identical
- [ ] Endpoint inventory diff vs Phase 0 — identical
- [ ] Coverage gate — `app/routers/` line coverage not lower than Phase 0
- [ ] `uv run alembic upgrade head` against a fresh testcontainer DB still succeeds (DB schema untouched, but sanity)
- [ ] Smoke: `TestClient(app)` against one GET per router — no 500s

### CI workflows (`.github/workflows/`)

Push the branch and confirm all four `Backend/**` workflows go green on the PR:

- `backend-test.yml` — `uv run pytest`
- `backend-ruff-format.yml` — `uv run ruff format --check .`
- `backend-ruff-autofix.yml` — `uv run ruff check --fix-only --diff .`
- `backend-migration.yml` — `alembic upgrade head` in MySQL 8 service container

Do not trigger `deploy-backend.yml` until manual review.

---

## 9. Quick reference: what changes where

| File | Action | Commit |
|---|---|---|
| `app/DB/main.py` | Add `get_db()` + `SessionDep` | 1 |
| `app/routers/departments.py` | Migrate 2 routes | 1 |
| `app/routers/forms.py` | Migrate 2 routes | 2 |
| `app/routers/action.py` | Migrate 6 routes | 2 |
| `app/routers/points.py` | Migrate 4 routes | 2 |
| `app/routers/health.py` | Migrate 1 route, keep `engine` | 2 |
| `app/routers/members.py` | Migrate 5 routes | 2 |
| `app/routers/attendance.py` | Migrate (count occurrences first!) | 2 |
| `app/routers/events.py` | Migrate 6 routes, keep `session.refresh()` | 2 |
| `app/routers/submissions.py` | Migrate 4 routes, leave 4 helpers | 2 |
| `app/routers/custom.py` | Migrate all routes, leave module classes | 2 |
| `app/routers/emails.py` | Migrate route handlers, leave background tasks + SSE closures | 2 |
| `app/routers/submissions_manual.py` | DO NOT TOUCH | — |
| `app/helpers.py` | DO NOT TOUCH | — |
| `app/DB/*.py` (query modules) | DO NOT TOUCH | — |
| `AGENTS.md` | Add `get_db`/policy line | 2 |
| `tests/conftest.py` | Rewrite `client` + `db_session` fixtures | 3 |
| `tests/tests.md` | Update override-pattern doc | 3 |
| `docs/migrations/db-dependency-migration/PLAN.md` | This file | 1 |
| `docs/migrations/db-dependency-migration/phase-0-plan-and-result.txt` | New file with baseline numbers | 1 |

## 10. Sanity check before starting each commit

```bash
# Verify cwd is Backend/
pwd

# Verify git branch
git branch --show-current   # should be refactor/db-dependency-injection

# Verify docker for testcontainers
docker ps
```

If anything fails here, stop and ask the user.