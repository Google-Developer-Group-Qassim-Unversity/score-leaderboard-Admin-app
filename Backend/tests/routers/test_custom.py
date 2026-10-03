"""The custom points editor keeps saved departments visible and removable.

``GET /custom/departments/{event_id}`` carries the name of every department a
saved row references - even one that was archived or taken out of the
leaderboard after the points were given - so the editor can still show the
badge for it.
"""

from fastapi.testclient import TestClient

from app.DB.schema import DepartmentsLogs, Events, EventsLocationType, EventsStatus, Logs
from tests.utils import assert_2xx, semester_id_on


def _give_and_read(db_session, client: TestClient, seed_refs, department_id: int) -> dict:
    """Award points to one department and return the editor's view of the event."""
    event = Events(
        name=f"Bonus for {department_id}",
        location_type=EventsLocationType.NONE,
        location="none",
        start_datetime="2026-07-01 10:00:00",
        end_datetime="2026-07-01 12:00:00",
        status=EventsStatus.CLOSED,
        semester_id=semester_id_on(db_session, "2026-07-01"),
    )
    db_session.add(event)
    db_session.flush()
    log = Logs(action_id=seed_refs.dept_action.id, event_id=event.id)
    db_session.add(log)
    db_session.flush()
    db_session.add(DepartmentsLogs(department_id=department_id, log_id=log.id))
    db_session.flush()

    response = client.get(f"/custom/departments/{event.id}")
    assert_2xx(response)
    return response.json()


def test_saved_rows_carry_department_names(db_session, admin_client: TestClient, seed_refs):
    body = _give_and_read(db_session, admin_client, seed_refs, seed_refs.dept_business.id)

    detail = body["point_details"][0]
    assert detail["departments_id"] == [seed_refs.dept_business.id]
    assert detail["departments"] == [{"id": seed_refs.dept_business.id, "name": "Business", "ar_name": "ريادة الأعمال"}]


def test_archived_departments_still_resolve_their_names(db_session, admin_client: TestClient, seed_refs):
    """Design is archived after the points were given; the editor must still show it."""
    department_id = seed_refs.dept_design.id
    seed_refs.dept_design.active = 0
    db_session.commit()

    body = _give_and_read(db_session, admin_client, seed_refs, department_id)

    (detail,) = body["point_details"]
    assert detail["departments_id"] == [department_id]
    assert detail["departments"] == [{"id": department_id, "name": "Design", "ar_name": "التصميم"}]
