import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///monitor.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TIMEZONE = os.getenv("TIMEZONE", "America/Lima")
    BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")
    SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "static/screenshots")

    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASS = os.getenv("SMTP_PASS")
    FROM_EMAIL = os.getenv("FROM_EMAIL", "monitor@example.com")
    TO_EMAILS = [e.strip() for e in os.getenv("TO_EMAILS", "").split(",") if e.strip()]
