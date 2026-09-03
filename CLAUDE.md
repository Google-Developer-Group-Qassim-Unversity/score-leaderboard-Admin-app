# score-leaderboard-Admin-app

Admin app for the GDG Qassim score leaderboard. `Backend/` is FastAPI +
SQLAlchemy + Alembic on MySQL; `Frontend/` is Next.js with Clerk auth.

## When something is broken in production

**Read [Backend/docs/LOGGING.md](Backend/docs/LOGGING.md) first.** It covers
where the logs are, how to trace a single request end to end, and when to use
Sentry instead of SSH.

The short version: the backend runs under PM2 as `GDG-backend` on a VPS, logs to
stdout, and every line carries the worker pid and a request id:

```
INFO [pid:1996265] [req:6cefc82ee87c] app.middleware: GET /health -> 200 in 3.9ms
```

SSH host, user and key live in Infisical under `prod` / `/VPS` - the runbook has
a copy-pasteable helper that loads them without writing anything to disk, and
handles the fact that `pm2` is not on the default non-interactive PATH.

Every response carries that id in the `X-Request-ID` header, and it is set as a
Sentry tag, so a Sentry issue links straight to its log lines.

## Backend conventions

These are enforced by tests, not just preference:

- **Database sessions** come from the `session: DB` dependency
  (`app/dependencies.py`). Never construct a session in a handler. Code outside
  the request cycle - background tasks, scripts - uses `db_session()` from
  `app/DB/main.py`.
- **Errors** are raised, not translated. `app/error_handlers.py` owns the
  mapping to HTTP responses; handlers should not wrap themselves in
  `try/except` to produce a 500.
- **Auth** is a dependency, not a parameter. Use
  `dependencies=[Depends(admin_guard)]` on the route or router when the handler
  does not need the token, and `member: CurrentMember` when it needs the caller.
  `tests/test_route_auth.py` pins the guard on all 103 routes and fails if one
  changes.
- **Every route declares a `response_model`.** `response_model` silently drops
  undeclared keys, and the leaderboard app (a separate repository) consumes
  several of these endpoints, so `tests/test_response_models.py` checks each
  model against the keys its handler actually returns.
- **Logging** is stdlib `logging` with a module-scoped
  `logger = logging.getLogger(__name__)`. `print()`, `write_log` and `LogFile`
  are banned and the test suite fails if they reappear.

## Action IDs are hardcoded

`app/DB/logs.py` and `app/routers/action.py` reference production `actions`
primary keys as literal lists. They decide which events can be created and
which can be attended, they have already drifted apart, and they are wrong in
every database that is not production - which is why the attendance tests
patch the lookup out. Read
[Backend/docs/HARDCODED_ACTION_IDS.md](Backend/docs/HARDCODED_ACTION_IDS.md)
before changing anything in that area; it has the fix plan and a checklist.

## Commands

```bash
cd Backend
uv run pytest                 # needs docker (testcontainers) or DATABASE_URL
uv run ruff check --fix .
uv run ruff format .
uv run pyright .
uv run poe dev                # runs via infisical, port 7001
```

CI runs `ruff format --check` as its own job, separate from `ruff check`. Run
`uv run ruff format .` before every commit that touches `Backend/` - `ruff
check --fix` does not rewrite formatting (line wraps, collapsing short
dicts/calls onto one line), so a change that only shortens or lengthens a
string can flip the format check even when `ruff check` is clean.

The conventions above came out of a FastAPI-idioms refactor; see
[Backend/docs/FASTAPI_REFACTOR.md](Backend/docs/FASTAPI_REFACTOR.md) for what
changed, the numbers, and what was deliberately left alone.

> This file is intentionally short and covers only what is easy to get wrong.
> Run `/init` if you want a fuller codebase overview generated.
