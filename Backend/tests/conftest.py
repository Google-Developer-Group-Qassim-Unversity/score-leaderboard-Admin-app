"""
Pytest configuration and fixtures for testing.
Fixture chain (scope):

    database_url (session)
        |
    engine (session) ─── runs Alembic migrations
        |
    seed_core_data (session) ─── seeds the DB
        |
    _test_db_connection (function) ─── opens a connection + begins transaction (shared by client & db_session)
        |
        ├── db_session (function) ─── per-test session bound to _test_db_connection
        |
        └── client (function) ─── FastAPI TestClient with get_db overridden to bind to _test_db_connection
                |
                └── clerk_client (function) ─── bypasses authenticated_guard (Clerk credentials with member metadata)
                        |
                        ├── admin_client (function) ─── also bypasses admin_guard
                        |
                        └── super_admin_client (function) ─── also bypasses super_admin_guard
"""

import os
import pytest
from typing import Generator

from testcontainers.mysql import MySqlContainer

from sqlalchemy import create_engine
from alembic.config import Config
from alembic import command
from sqlalchemy.orm import Session

from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_clerk_auth import HTTPAuthorizationCredentials as ClerkHTTPAuthorizationCredentials
# A bunch more import are done insdie fixtures to avoid the problimatic pattern in the code which evaluates sessions and envirnoment varibles at import time,
# so we have to delay importing those modules until after the environment variables are set and the database is ready, otherwise we will get errors about missing env vars

# Set environment variables BEFORE importing app
# These must be set before any app modules are imported
required_env_vars = {
    "ENV": "testing",
    "CLERK_JWKS_URL": "https://test.clerk.dev/.well-known/jwks.json",
    "GOOGLE_CLIENT_ID": "test_client_id",
    "GOOGLE_CLIENT_SECRET": "test_client_secret",
    "JWT_SECRET": "test_jwt_secret_for_testing_only",
    "CERTIFICATE_API_URL": "http://localhost:8000",
}

for key, value in required_env_vars.items():
    os.environ[key] = value  # Always set, override any existing


@pytest.fixture(scope="session")
def database_url():
    """
    Provide the test database URL.

    - If DATABASE_URL env var is set (CI mode), use it
    - Otherwise, start a MySQL testcontainer

    Yields the database URL string.
    """
    existing_url = os.getenv("DATABASE_URL")

    if existing_url:
        print("[conftest] Using provided DATABASE_URL")
        yield existing_url
        return

    print("[conftest] Starting MySQL testcontainer...")
    container = MySqlContainer("mysql:8.0", dbname="test")
    container.start()

    url = container.get_connection_url()
    url = url.replace("mysql://", "mysql+pymysql://")
    print(f"[conftest] MySQL testcontainer started: {url}")

    os.environ["DATABASE_URL"] = url

    yield url

    print("[conftest] Stopping MySQL testcontainer...")
    container.stop()


@pytest.fixture(scope="session")
def engine(database_url):
    """
    Create SQLAlchemy engine and run migrations.
    This ensures tests run against the same database structure as production.
    """

    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)

    print("[conftest] Running Alembic migrations...")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"[conftest] ❌ Migration failed: {e}")
        raise RuntimeError(
            f"Database migrations failed. "
            f"This is NOT a test failure - migrations are broken.\n"
            f"Fix your migration files first.\n"
            f"Original error: {e}"
        ) from e

    print("[conftest] ✓ Migrations completed")

    yield engine


@pytest.fixture(scope="session")
def seed_core_data(engine):
    from app.DB.schema import Actions, Departments, Members, ActionsActionType, DepartmentsType, MembersGender

    # CAUTION: Don't update default unless you know what you're doing
    # a lot of tests assume these default values and changing them might break the tests
    with Session(engine) as session:
        session.add_all(
            [
                Actions(
                    action_name="organized an on-site course",
                    points=10,
                    action_type=ActionsActionType.DEPARTMENT,
                    ar_action_name="تنظيم دورة حضورية",
                ),
                Actions(
                    action_name="on-site course attendance",
                    points=5,
                    action_type=ActionsActionType.MEMBER,
                    ar_action_name="حضور دورة حضورية",
                ),
                Departments(name="Business", type=DepartmentsType.PRACTICAL, ar_name="ريادة الأعمال"),
                Departments(name="Design", type=DepartmentsType.ADMINISTRATIVE, ar_name="التصميم"),
                Members(
                    name="Ahmed Ali",
                    email="ahmed@example.com",
                    phone_number="0501234567",
                    uni_id="111111111",
                    gender=MembersGender.MALE,
                    uni_level=4,
                    uni_college="Engineering",
                ),
                Members(
                    name="Sara Khalid",
                    email="sara@example.com",
                    phone_number="0509876543",
                    uni_id="222222222",
                    gender=MembersGender.FEMALE,
                    uni_level=3,
                    uni_college="Science",
                ),
            ]
        )
        session.commit()
        print("[conftest] ✓ Core data seeded")


@pytest.fixture(scope="function")
def _test_db_connection(engine, seed_core_data):
    """
    Internal fixture that opens a single connection and begins a transaction.

    Both the ``client`` fixture (which overrides ``get_db``) and the
    ``db_session`` fixture (used for direct test-side DB access) share this
    connection so route-side and test-side writes are mutually visible. The
    outer transaction is rolled back at fixture teardown, undoing every
    INSERT/UPDATE/DELETE the routes or the test committed.

    This replaces the legacy ``SessionLocal.configure(bind=connection)`` hack
    with explicit connection sharing via a fixture-local closure.
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
    Provide a FastAPI test client with transaction rollback.

    Overrides the ``get_db`` dependency so every route handler receives a
    session bound to the single test-scoped connection from
    ``_test_db_connection``. After the test the outer transaction is rolled
    back, undoing every INSERT/UPDATE/DELETE the routes committed.
    """
    from collections.abc import Generator as Gen

    from app.DB.main import SessionLocal, get_db
    from app.main import app

    connection = _test_db_connection

    def override_get_db() -> Gen:
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


FAKE_CLERK_CREDENTIALS = ClerkHTTPAuthorizationCredentials(
    scheme="Bearer",
    credentials="fake-token",
    decoded={
        "metadata": {
            "uni_id": "123456789",
            "fullArabicName": "Test Member",
            "saudiPhone": "0501234567",
            "gender": "Male",
            "uniLevel": 4,
            "uniCollege": "Engineering",
            "personalEmail": "test@example.com",
        }
    },
)

FAKE_ADMIN_CREDENTIALS = ClerkHTTPAuthorizationCredentials(
    scheme="Bearer",
    credentials="fake-admin-token",
    decoded={
        "metadata": {
            "uni_id": "123456789",
            "fullArabicName": "Test Admin",
            "saudiPhone": "0501234567",
            "gender": "Male",
            "uniLevel": 4,
            "uniCollege": "Engineering",
            "personalEmail": "admin@example.com",
            "is_admin": True,
        }
    },
)


@pytest.fixture(scope="function")
def clerk_client(client) -> Generator:
    from app.main import app
    from app.helpers import authenticated_guard, optional_clerk_guard

    app.dependency_overrides[authenticated_guard] = lambda: FAKE_CLERK_CREDENTIALS
    app.dependency_overrides[optional_clerk_guard] = lambda: FAKE_CLERK_CREDENTIALS
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def super_admin_client(clerk_client) -> Generator:
    from app.main import app
    from app.helpers import super_admin_guard

    def override_super_admin_guard():
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")

    app.dependency_overrides[super_admin_guard] = override_super_admin_guard
    yield clerk_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def admin_client(clerk_client) -> Generator:
    from app.main import app
    from app.helpers import admin_guard, optional_clerk_guard

    def override_admin_guard():
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")

    app.dependency_overrides[admin_guard] = override_admin_guard
    app.dependency_overrides[optional_clerk_guard] = lambda: FAKE_ADMIN_CREDENTIALS
    yield clerk_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def db_session(_test_db_connection):
    """
    Provide a SQLAlchemy session bound to the same connection/transaction as
    the overridden ``get_db``. Use this fixture when tests need direct DB
    access (e.g., inserting test data). All changes will be rolled back after
    the test via the ``_test_db_connection`` fixture's outer transaction.
    """
    from app.DB.main import SessionLocal

    session = SessionLocal(bind=_test_db_connection)
    try:
        yield session
    finally:
        session.close()


class SeedRefs:
    """Dynamic references to seed data IDs.

    Queries the test DB for seeded entities so tests don't hardcode IDs.
    If seed data changes, these update automatically.
    """

    def __init__(self, session):
        from sqlalchemy import select
        from app.DB.schema import Actions, ActionsActionType, Departments, Members

        self.dept_action = session.scalar(select(Actions).where(Actions.action_type == ActionsActionType.DEPARTMENT))
        self.member_action = session.scalar(select(Actions).where(Actions.action_type == ActionsActionType.MEMBER))
        self.dept_business = session.scalar(select(Departments).where(Departments.name == "Business"))
        self.dept_design = session.scalar(select(Departments).where(Departments.name == "Design"))
        self.ahmed = session.scalar(select(Members).where(Members.uni_id == "111111111"))
        self.sara = session.scalar(select(Members).where(Members.uni_id == "222222222"))


@pytest.fixture(scope="function")
def seed_refs(db_session):
    return SeedRefs(db_session)


def pytest_collection_modifyitems(items):
    """Force test_database_connection to always run first."""

    # Find the test by name, remove it from its current position, and re-insert at index 0
    db_test = next((i for i in items if i.name == "test_database_connection"), None)
    if db_test:
        items.remove(db_test)
        items.insert(0, db_test)


def pytest_assertrepr_compare(config, op, left, right):
    """Customize assertion comparison output for clearer failure messages."""
    if op == "==":
        return ["Assertion failed:", f"  Expected: {right!r}", f"  Actual:   {left!r}"]
    if op == "!=":
        return ["Assertion failed:", f"  Expected NOT: {right!r}", f"  Actual:        {left!r}"]
