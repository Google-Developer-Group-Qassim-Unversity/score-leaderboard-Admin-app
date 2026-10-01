from fastapi.testclient import TestClient

from tests.utils import assert_2xx, assert_not_found


def test_lists_the_seeded_departments(client: TestClient, seed_refs):
    response = client.get("/departments")

    assert_2xx(response)
    names = {d["name"] for d in response.json()}
    assert {"Business", "Design"} <= names


def test_department_payload_shape(client: TestClient, seed_refs):
    response = client.get(f"/departments/{seed_refs.dept_business.id}")

    assert_2xx(response)
    body = response.json()
    assert body["id"] == seed_refs.dept_business.id
    assert body["name"] == "Business"
    assert body["ar_name"] == "ريادة الأعمال"
    assert body["type"] == "practical"


def test_unknown_department_is_404(client: TestClient, seed_refs):
    assert_not_found(client.get("/departments/999999"))


def test_departments_are_public(client: TestClient, seed_refs):
    """The leaderboard app reads these without a token; keep them open."""
    assert_2xx(client.get("/departments"))


def test_inactive_departments_are_hidden(db_session, client: TestClient, seed_refs):
    """Event creation and friends only offer active departments."""
    seed_refs.dept_design.active = 0
    db_session.commit()

    response = client.get("/departments")

    assert_2xx(response)
    ids = {d["id"] for d in response.json()}
    assert seed_refs.dept_business.id in ids
    assert seed_refs.dept_design.id not in ids


def test_unranked_departments_are_hidden(db_session, client: TestClient, seed_refs):
    """The Board and other show_in_leaderboard = 0 departments are not selectable here."""
    seed_refs.dept_design.show_in_leaderboard = 0
    db_session.commit()

    response = client.get("/departments")

    assert_2xx(response)
    ids = {d["id"] for d in response.json()}
    assert seed_refs.dept_business.id in ids
    assert seed_refs.dept_design.id not in ids
