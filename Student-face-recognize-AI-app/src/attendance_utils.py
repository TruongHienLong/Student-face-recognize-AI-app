"""
MODULE 4 – attendance_utils.py
Trách nhiệm: Logic nghiệp vụ điểm danh (ghi/truy vấn Attendance).
Người phụ trách: Member 4
"""

import logging
from datetime import datetime, date

from config import db, logger

logger = logging.getLogger(__name__)


def mark_attendance(student_id: str) -> bool:
    """
    Insert an Attendance row for today if one doesn't already exist.
    Returns True if a new record was created, False if already marked.
    """
    from models import Attendance  # avoid circular import at module level

    today = date.today()
    existing = Attendance.query.filter_by(student_id=student_id, date=today).first()
    if existing:
        return False

    record = Attendance(
        student_id=student_id,
        date=today,
        time_in=datetime.now(),
    )
    try:
        db.session.add(record)
        db.session.commit()
        logger.info("Attendance marked: %s on %s", student_id, today)
        return True
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to save attendance for %s: %s", student_id, exc)
        return False


def get_attendance_records(filter_date=None):
    """
    Query attendance joined with Student.
    filter_date: a date object or None (returns all).
    Returns list of (Attendance, Student) tuples.
    """
    from models import Attendance, Student  # avoid circular import

    query = (
        db.session.query(Attendance, Student)
        .join(Student, Attendance.student_id == Student.studentID)
        .order_by(Attendance.date.desc(), Attendance.time_in.desc())
    )
    if filter_date:
        query = query.filter(Attendance.date == filter_date)
    return query.all()
