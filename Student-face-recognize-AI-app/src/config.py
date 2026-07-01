"""
MODULE 1 – config.py
Trách nhiệm: Khởi tạo Flask app, cấu hình, SQLAlchemy, logging.
Người phụ trách: Member 1
"""

import os
import logging
import threading
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── App & config ──────────────────────────────────────────────────────────────
app = Flask(__name__, 
            template_folder="../templates",
            static_folder="../static")
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

# ── Thread-safety locks ───────────────────────────────────────────────────────
_camera_lock = threading.Lock()
_users_lock = threading.RLock()

# ── Shared state ──────────────────────────────────────────────────────────────
# {studentID: {'name': str, 'roll_no': str, 'encoding': np.ndarray}}
users_data: dict = {}

# Recent recognitions shown on /live page (max 10, deduplicated per 5 s)
recent_recognitions: list = []