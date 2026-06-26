"""
app.py – Entry point
Chạy: python app.py
"""

from config import app, db
from face_utils import load_all_students_encodings
import models   # noqa: F401 – register models with SQLAlchemy
import routes   # noqa: F401 – register all routes

with app.app_context():
    db.create_all()
    load_all_students_encodings()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000, threaded=True)