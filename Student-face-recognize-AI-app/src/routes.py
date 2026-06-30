"""
MODULE 5 – routes.py
Trách nhiệm: Tất cả Flask routes (HTML pages + JSON APIs).
Người phụ trách: Member 5
"""

import os
import logging
from datetime import datetime

from flask import (
    render_template, redirect, url_for, Response,
    send_from_directory, request, flash, jsonify,
)

from config import app, db, _users_lock, users_data, recent_recognitions
from models import Student, Attendance, StudentForm, AttendanceFilterForm
from face_utils import (
    release_camera, generate_frames,
    get_face_encoding, load_all_students_encodings,
)
from attendance_utils import get_attendance_records

logger = logging.getLogger(__name__)


def allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


# ── HTML Routes ───────────────────────────────────────────────────────────────
@app.route("/")
def home():
    release_camera()
    return render_template("Home.html")


@app.route("/add_student", methods=["GET", "POST"])
def add_student():
    form = StudentForm()
    if form.validate_on_submit():
        if Student.query.filter_by(studentID=form.studentID.data).first():
            flash("Student ID already exists!", "danger")
            return redirect(url_for("add_student"))

        student = Student(
            studentID=form.studentID.data,
            name=form.name.data,
            roll_no=form.roll_no.data,
            class_name=form.class_name.data,
        )

        file = form.photo.data
        if not (file and allowed_file(file.filename)):
            flash("Please upload a valid photo (jpg, jpeg, png, gif).", "danger")
            return redirect(url_for("add_student"))

        filename = f"{form.studentID.data}_{form.name.data}_{form.roll_no.data}.jpg"
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(file_path)

        encoding = get_face_encoding(file_path)
        if encoding is None:
            os.remove(file_path)
            flash("No clear face detected in the photo. Please try again.", "danger")
            return redirect(url_for("add_student"))

        student.photo = filename
        db.session.add(student)
        db.session.commit()

        with _users_lock:
            users_data[student.studentID] = {
                "name": student.name,
                "roll_no": student.roll_no,
                "encoding": encoding,
            }

        flash("Student added successfully!", "success")
        return redirect(url_for("students"))

    return render_template("add_student.html", form=form)


@app.route("/delete_student/<int:id>", methods=["POST"])
def delete_student(id):
    student = Student.query.get_or_404(id)
    with _users_lock:
        users_data.pop(student.studentID, None)
    if student.photo:
        fp = os.path.join(app.config["UPLOAD_FOLDER"], student.photo)
        if os.path.exists(fp):
            os.remove(fp)
    db.session.delete(student)
    db.session.commit()
    flash("Student deleted.", "success")
    return redirect(url_for("students"))


@app.route("/students")
def students():
    all_students = Student.query.order_by(Student.studentID).all()
    return render_template("students.html", students=all_students)


@app.route("/live")
def live():
    return render_template("video_feed_live.html")


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/attendance")
def attendance():
    form = AttendanceFilterForm(request.args)
    filter_date = form.filter_date.data if form.filter_date.data else None
    records = get_attendance_records(filter_date)
    return render_template("attendance.html", records=records, form=form,
                           filter_date=filter_date)


@app.route("/reload_encodings")
def reload_encodings():
    load_all_students_encodings()
    flash("Face encodings reloaded.", "success")
    return redirect(url_for("students"))


@app.route("/media/<filename>")
def media(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ── JSON API Routes ───────────────────────────────────────────────────────────
@app.route("/api/recent")
def api_recent():
    """Return recently recognised students (last 10, no timestamp)."""
    result = [
        {"student_id": r["student_id"], "name": r["name"], "roll_no": r["roll_no"]}
        for r in recent_recognitions
    ]
    return jsonify(result)


@app.route("/api/attendance")
def api_attendance():
    """Return attendance records as JSON. Optional: ?date=YYYY-MM-DD"""
    date_str = request.args.get("date")
    filter_date = None
    if date_str:
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    records = get_attendance_records(filter_date)
    result = [
        {
            "id": att.id,
            "student_id": att.student_id,
            "name": stu.name,
            "roll_no": stu.roll_no,
            "class_name": stu.class_name,
            "date": att.date.isoformat(),
            "time_in": att.time_in.strftime("%H:%M:%S"),
        }
        for att, stu in records
    ]
    return jsonify(result)


@app.route("/api/students")
def api_students():
    """Return all students as JSON."""
    all_students = Student.query.order_by(Student.studentID).all()
    return jsonify([
        {
            "studentID": s.studentID,
            "name": s.name,
            "roll_no": s.roll_no,
            "class_name": s.class_name,
            "photo": s.photo,
        }
        for s in all_students
    ])