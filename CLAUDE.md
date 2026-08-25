# score-leaderboard-Admin-app

Admin app for the GDG Qassim score leaderboard. `Backend/` is FastAPI +
SQLAlchemy + Alembic on MySQL; `Frontend/` is Next.js with Clerk auth.

## When something is broken in production

**Read [Backend/docs/LOGGING.md](Backend/docs/LOGGING.md) first.** It covers
where the logs are, how to trace a single request end to end, and when to use
Sentry instead of SSH.

The short version: the backend runs under PM2 as `GDG-backend` on the VPS
(`ssh oracle2`), logs to stdout, and every line carries the worker pid and a
request id:

```
INFO [pid:1996265] [req:6cefc82ee87c] app.middleware: GET /health -> 200 in 3.9ms
```

```bash
ssh oracle2 'pm2 logs GDG-backend --lines 200'
ssh oracle2 'grep "req:<id>" ~/.pm2/logs/GDG-backend-*.log'
```

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
  `tests/test_route_auth.py` pins the guard on all 100 routes and fails if one
  changes.
- **Every route declares a `response_model`.** `response_model` silently drops
  undeclared keys, and the leaderboard app (a separate repository) consumes
  several of these endpoints, so `tests/test_response_models.py` checks each
  model against the keys its handler actually returns.
- **Logging** is stdlib `logging` with a module-scoped
  `logger = logging.getLogger(__name__)`. `print()`, `write_log` and `LogFile`
  are banned and the test suite fails if they reappear.

## Commands

```bash
cd Backend
uv run pytest                 # needs docker (testcontainers) or DATABASE_URL
uv run ruff check --fix .
uv run pyright .
uv run poe dev                # runs via infisical, port 7001
```

An in-progress refactor and its rationale are documented in
[Backend/docs/fastapi-refactor-plan.md](Backend/docs/fastapi-refactor-plan.md).

> This file is intentionally short and covers only what is easy to get wrong.
> Run `/init` if you want a fuller codebase overview generated.
