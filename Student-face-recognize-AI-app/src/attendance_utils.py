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


def get_attendance_records(filter_date=None, start_date=None, end_date=None, student_id=None):
    """
    Query attendance joined with Student.

    - filter_date: lọc đúng 1 ngày (tương thích ngược, ưu tiên cao nhất nếu được truyền).
    - start_date / end_date: lọc theo khoảng ngày (dùng để xem LỊCH SỬ điểm danh).
    - student_id: lọc theo 1 sinh viên cụ thể.
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
    else:
        if start_date:
            query = query.filter(Attendance.date >= start_date)
        if end_date:
            query = query.filter(Attendance.date <= end_date)

    if student_id:
        query = query.filter(Attendance.student_id == student_id)

    return query.all()


def get_attendance_history(student_id: str, start_date=None, end_date=None):
    """
    Lấy lịch sử điểm danh của MỘT sinh viên cụ thể, có thể lọc theo khoảng ngày.
    Trả về danh sách các bản ghi Attendance (không join Student), mới nhất trước.
    """
    from models import Attendance  # avoid circular import

    query = Attendance.query.filter_by(student_id=student_id)
    if start_date:
        query = query.filter(Attendance.date >= start_date)
    if end_date:
        query = query.filter(Attendance.date <= end_date)

    return query.order_by(Attendance.date.desc(), Attendance.time_in.desc()).all()