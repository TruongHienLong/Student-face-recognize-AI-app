"""
MODULE 2 – models.py
Trách nhiệm: Định nghĩa database models (Student, Attendance) và WTForms.
Người phụ trách: Member 2
"""

from flask_wtf import FlaskForm
from wtforms import StringField, FileField, DateField
from wtforms.validators import DataRequired, Optional
from config import db


# ── Models ────────────────────────────────────────────────────────────────────
class Student(db.Model):
    __tablename__ = "student"
    id = db.Column(db.Integer, primary_key=True)
    studentID = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    roll_no = db.Column(db.String(20), nullable=False)
    class_name = db.Column(db.String(50), nullable=False)
    photo = db.Column(db.String(100), nullable=True)
    attendances = db.relationship(
        "Attendance", backref="student", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Student {self.studentID}: {self.name}>"


class Attendance(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.String(10), db.ForeignKey("student.studentID"), nullable=False, index=True
    )
    date = db.Column(db.Date, nullable=False, index=True)
    time_in = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("student_id", "date", name="uq_attendance_per_day"),
    )

    def __repr__(self):
        return f"<Attendance {self.student_id} @ {self.date}>"


# ── Forms ─────────────────────────────────────────────────────────────────────
class StudentForm(FlaskForm):
    studentID = StringField("Student ID", validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired()])
    roll_no = StringField("Roll No", validators=[DataRequired()])
    class_name = StringField("Class", validators=[DataRequired()])
    photo = FileField("Photo")


class AttendanceFilterForm(FlaskForm):
    filter_date = DateField("Date", validators=[Optional()], format="%Y-%m-%d")