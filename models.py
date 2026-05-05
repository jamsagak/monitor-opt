from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_login import UserMixin

db = SQLAlchemy()

class Domain(db.Model):
    __tablename__ = "domains"

    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False)
    client = db.Column(db.String(255), nullable=False)
    contact = db.Column(db.String(255), nullable=True)
    provider = db.Column(db.String(255), nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)

    # Monitoring fields
    last_status = db.Column(db.String(32), nullable=True)  # OK / ERROR
    last_http_code = db.Column(db.Integer, nullable=True)
    last_checked_at = db.Column(db.DateTime, nullable=True)
    last_screenshot_path = db.Column(db.String(512), nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    last_error_code = db.Column(db.String(50), nullable=True)  # Nuevo campo

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def days_to_expiry(self):
        if not self.expiry_date:
            return None
        return (self.expiry_date - datetime.utcnow().date()).days


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
