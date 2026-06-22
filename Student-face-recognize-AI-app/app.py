import os
import logging
import time
import threading
from datetime import datetime, date
from flask import Flask, render_template, redirect, url_for, Response, send_from_directory, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, FileField, DateField
from wtforms.validators import DataRequired, Optional
import cv2
import face_recognition
import numpy as np

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── App & config ──────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite:///students.db",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=os.environ.get("SECRET_KEY", "change-me-in-production"),
    UPLOAD_FOLDER="media",
    ALLOWED_EXTENSIONS={"jpg", "jpeg", "png", "gif"},
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16 MB
)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)

# ── Thread-safety ─────────────────────────────────────────────────────────────
_camera_lock = threading.Lock()
_users_lock = threading.RLock()

# ── State ─────────────────────────────────────────────────────────────────────
video_capture = None
# {studentID: {'name': str, 'roll_no': str, 'encoding': np.ndarray}}
users_data: dict = {}
# Recent recognitions shown on /live page (max 10, deduplicated per 5 s)
recent_recognitions: list = []

# ── Models ────────────────────────────────────────────────────────────────────
class Student(db.Model):
    __tablename__ = "student"
    id = db.Column(db.Integer, primary_key=True)
    studentID = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    roll_no = db.Column(db.String(20), nullable=False)
    class_name = db.Column(db.String(50), nullable=False)
    photo = db.Column(db.String(100), nullable=True)
    attendances = db.relationship("Attendance", backref="student", lazy=True,
                                  cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Student {self.studentID}: {self.name}>"


class Attendance(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(10), db.ForeignKey("student.studentID"),
                           nullable=False, index=True)
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

# ── Helpers ───────────────────────────────────────────────────────────────────
def allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


def get_face_encoding(image_path: str):
    """Return face encoding (np.ndarray) from file, or None if no clear face."""
    try:
        image = face_recognition.load_image_file(image_path)
        encodings = face_recognition.face_encodings(image)
        if not encodings:
            logger.warning("No face found in %s", image_path)
            return None
        if len(encodings) > 1:
            logger.info("Multiple faces in %s; using first", image_path)
        return encodings[0]
    except Exception as exc:
        logger.error("Error processing %s: %s", image_path, exc)
        return None


def load_all_students_encodings():
    """Reload all face encodings from disk into users_data (thread-safe)."""
    new_data = {}
    students = Student.query.all()
    for student in students:
        if not student.photo:
            continue
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], student.photo)
        if not os.path.exists(file_path):
            logger.warning("Photo missing: %s", file_path)
            continue
        encoding = get_face_encoding(file_path)
        if encoding is not None:
            new_data[student.studentID] = {
                "name": student.name,
                "roll_no": student.roll_no,
                "encoding": encoding,
            }
            logger.info("Loaded encoding for %s (ID: %s)", student.name, student.studentID)
        else:
            logger.warning("No face detected in %s – skipping", student.photo)

    with _users_lock:
        users_data.clear()
        users_data.update(new_data)
    logger.info("Encodings loaded: %d students", len(new_data))


def mark_attendance(student_id: str):
    """
    Insert an Attendance row for today if one doesn't already exist.
    Returns True if a new record was created, False if already marked.
    """
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


# ── Camera ────────────────────────────────────────────────────────────────────
def get_camera():
    global video_capture
    with _camera_lock:
        if video_capture is None or not video_capture.isOpened():
            video_capture = cv2.VideoCapture(0)
            if not video_capture.isOpened():
                logger.error("Cannot open camera")
                return None
            video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            logger.info("Camera opened at 320×240")
        return video_capture


def release_camera():
    global video_capture
    with _camera_lock:
        if video_capture is not None:
            video_capture.release()
            video_capture = None
            logger.info("Camera released")


# ── Recognition ───────────────────────────────────────────────────────────────
FACE_TOLERANCE = 0.55   # lower = stricter match
SKIP_FRAMES = 3         # process every N-th frame
RESIZE_FACTOR = 0.4     # downscale for detection speed
DEDUP_SECONDS = 5       # ignore same person within this window


def _recognize_from_encoding(face_enc: np.ndarray):
    """Return (student_id, data_dict) for the best match, or (None, None)."""
    best_id = None
    best_dist = FACE_TOLERANCE
    with _users_lock:
        snapshot = list(users_data.items())
    for sid, data in snapshot:
        dist = face_recognition.face_distance([data["encoding"]], face_enc)[0]
        if dist < best_dist:
            best_dist = dist
            best_id = sid
    if best_id:
        with _users_lock:
            return best_id, users_data.get(best_id)
    return None, None


def _push_recent(student_id: str, name: str, roll_no: str):
    """Add to recent_recognitions if not seen within DEDUP_SECONDS."""
    now = time.time()
    for rec in recent_recognitions:
        if rec["student_id"] == student_id and (now - rec["timestamp"]) < DEDUP_SECONDS:
            return False
    recent_recognitions.append(
        {"student_id": student_id, "name": name, "roll_no": roll_no, "timestamp": now}
    )
    if len(recent_recognitions) > 10:
        recent_recognitions.pop(0)
    return True


def generate_frames():
    """MJPEG generator — face detection + recognition + auto attendance."""
    cap = get_camera()
    if cap is None:
        return

    frame_counter = 0
    last_face_locations: list = []

    while True:
        with _camera_lock:
            success, frame = cap.read()
        if not success:
            logger.warning("Failed to read frame – retrying…")
            time.sleep(0.05)
            continue

        frame_counter += 1

        # ── Face detection (every SKIP_FRAMES) ───────────────────────────────
        if frame_counter % SKIP_FRAMES == 0:
            small = cv2.resize(frame, (0, 0), fx=RESIZE_FACTOR, fy=RESIZE_FACTOR)
            rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            small_locs = face_recognition.face_locations(rgb_small)
            inv = 1.0 / RESIZE_FACTOR
            last_face_locations = [
                (int(t * inv), int(r * inv), int(b * inv), int(l * inv))
                for (t, r, b, l) in small_locs
            ]

        # ── Per-face recognition ──────────────────────────────────────────────
        for (top, right, bottom, left) in last_face_locations:
            face_crop = frame[top:bottom, left:right]
            if face_crop.size == 0:
                continue

            rgb_crop = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
            encs = face_recognition.face_encodings(rgb_crop)
            if not encs:
                continue

            student_id, data = _recognize_from_encoding(encs[0])

            if student_id and data:
                name, roll = data["name"], data["roll_no"]
                color = (0, 200, 50)
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                cv2.putText(frame, name,         (left, top - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
                cv2.putText(frame, f"Roll: {roll}", (left, top - 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1)
                cv2.putText(frame, f"ID: {student_id}", (left, top - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1)

                # Auto-attendance + dedup for recent list
                is_new = _push_recent(student_id, name, roll)
                if is_new:
                    # Run DB write in a background thread to avoid blocking stream
                    threading.Thread(
                        target=_attendance_bg,
                        args=(student_id,),
                        daemon=True,
                    ).start()
            else:
                cv2.rectangle(frame, (left, top), (right, bottom), (0, 0, 220), 2)
                cv2.putText(frame, "Unknown", (left, top - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 220), 1)

        # ── Encode & yield ────────────────────────────────────────────────────
        ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ret:
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n")


def _attendance_bg(student_id: str):
    """Write attendance inside app context (runs in daemon thread)."""
    with app.app_context():
        marked = mark_attendance(student_id)
        if marked:
            logger.info("Auto-attendance saved for %s", student_id)


# ── Routes ────────────────────────────────────────────────────────────────────
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
    """View attendance records, optionally filtered by date."""
    form = AttendanceFilterForm(request.args)
    query = (
        db.session.query(Attendance, Student)
        .join(Student, Attendance.student_id == Student.studentID)
        .order_by(Attendance.date.desc(), Attendance.time_in.desc())
    )
    filter_date = None
    if form.filter_date.data:
        filter_date = form.filter_date.data
        query = query.filter(Attendance.date == filter_date)

    records = query.all()
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


# ── JSON APIs ─────────────────────────────────────────────────────────────────
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
    """
    Return attendance records as JSON.
    Optional query param: ?date=YYYY-MM-DD
    """
    date_str = request.args.get("date")
    query = db.session.query(Attendance, Student).join(
        Student, Attendance.student_id == Student.studentID
    )
    if date_str:
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            query = query.filter(Attendance.date == filter_date)
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    records = query.order_by(Attendance.time_in.desc()).all()
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
    students = Student.query.order_by(Student.studentID).all()
    return jsonify([
        {
            "studentID": s.studentID,
            "name": s.name,
            "roll_no": s.roll_no,
            "class_name": s.class_name,
            "photo": s.photo,
        }
        for s in students
    ])


# ── Startup ───────────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    load_all_students_encodings()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000, threaded=True)