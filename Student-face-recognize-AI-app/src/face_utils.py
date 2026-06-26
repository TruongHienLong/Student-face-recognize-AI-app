"""
MODULE 3 – face_utils.py
Trách nhiệm: Xử lý nhận diện khuôn mặt, camera, và stream video.
Người phụ trách: Member 3
"""

import time
import threading
import logging
import cv2
import face_recognition
import numpy as np

from config import app, _camera_lock, _users_lock, users_data, recent_recognitions

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────
FACE_TOLERANCE = 0.55   # lower = stricter match
SKIP_FRAMES = 3         # process every N-th frame
RESIZE_FACTOR = 0.4     # downscale for detection speed
DEDUP_SECONDS = 5       # ignore same person within this window

# ── Camera ────────────────────────────────────────────────────────────────────
video_capture = None


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


# ── Face helpers ──────────────────────────────────────────────────────────────
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
    # Import here to avoid circular import
    from models import Student

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


# ── Recognition ───────────────────────────────────────────────────────────────
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


def _attendance_bg(student_id: str):
    """Write attendance inside app context (runs in daemon thread)."""
    from attendance_utils import mark_attendance
    with app.app_context():
        marked = mark_attendance(student_id)
        if marked:
            logger.info("Auto-attendance saved for %s", student_id)


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
                cv2.putText(frame, name,              (left, top - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
                cv2.putText(frame, f"Roll: {roll}",   (left, top - 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1)
                cv2.putText(frame, f"ID: {student_id}", (left, top - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1)

                is_new = _push_recent(student_id, name, roll)
                if is_new:
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


# ── Missing import (needed by load_all_students_encodings) ────────────────────
import os