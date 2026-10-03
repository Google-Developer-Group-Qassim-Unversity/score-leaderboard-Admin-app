from typing import Collection, Sequence

from sqlalchemy.orm import Session
from sqlalchemy import select
from .schema import Departments


def get_departments(session: Session, ranked: bool = False):
    statement = select(Departments).where(Departments.active == 1)
    if ranked:
        statement = statement.where(Departments.show_in_leaderboard == 1)
    departments = session.scalars(statement).all()
    return departments


def get_departments_by_ids(session: Session, department_ids: Collection[int]) -> Sequence[Departments]:
    """Unfiltered: also resolves departments that are archived or unranked now."""
    if not department_ids:
        return []
    statement = select(Departments).where(Departments.id.in_(department_ids))
    departments = session.scalars(statement).all()
    return departments


def get_department_by_id(session: Session, department_id: int):
    statement = select(Departments).where(Departments.id == department_id)
    department = session.scalars(statement).first()
    return department
