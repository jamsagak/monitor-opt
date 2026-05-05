import os
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from apscheduler.schedulers.background import BackgroundScheduler
from flask_migrate import Migrate
import pytz
from flask_login import (
    LoginManager, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from models import db, Domain, User, Settings
from monitor import run_full_cycle, check_and_capture


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Inicializar DB y migraciones
    db.init_app(app)
    migrate = Migrate(app, db)

    with app.app_context():
        db.create_all()

    # 🔐 Login Manager
    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Scheduler
    scheduler = BackgroundScheduler(timezone=app.config.get("TIMEZONE", "America/Lima"))
    scheduler.start()
    app.scheduler = scheduler

    with app.app_context():
        reschedule_jobs(app)

    # 🔹 Filtro Jinja para mostrar hora local
    @app.template_filter("localtime")
    def localtime_filter(value):
        if not value:
            return "—"
        tz = pytz.timezone(app.config.get("TIMEZONE", "America/Lima"))
        return value.replace(tzinfo=pytz.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")

    # -------------------
    # 🔐 Rutas de login
    # -------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password_hash, password):
                login_user(user)
                flash("Inicio de sesión exitoso", "success")
                return redirect(url_for("index"))
            else:
                flash("Credenciales inválidas", "error")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Has cerrado sesión", "success")
        return redirect(url_for("login"))

    # -------------------
    # Rutas principales
    # -------------------
    @app.route("/")
    @login_required
    def index():
        domains = Domain.query.order_by(Domain.client.asc()).all()
        return render_template("index.html", domains=domains)

    @app.route("/register", methods=["GET", "POST"])
    @login_required
    def register():
        if request.method == "POST":
            domain = request.form.get("domain", "").strip()
            client = request.form.get("client", "").strip()
            contact = request.form.get("contact", "").strip()
            provider = request.form.get("provider", "").strip()
            expiry = request.form.get("expiry", "").strip()

            if not domain or not client:
                flash("Dominio y Cliente son obligatorios.", "error")
                return redirect(url_for("register"))

            try:
                expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date() if expiry else None
            except ValueError:
                flash("Fecha de caducidad inválida. Use formato AAAA-MM-DD.", "error")
                return redirect(url_for("register"))

            d = Domain(
                domain=domain,
                client=client,
                contact=contact,
                provider=provider,
                expiry_date=expiry_date
            )
            db.session.add(d)
            db.session.commit()
            flash("Dominio registrado.", "success")
            return redirect(url_for("index"))
        return render_template("register.html")

    @app.route("/run-now", methods=["POST"])
    @login_required
    def run_now():
        job_runner(app)
        flash("Monitoreo completo ejecutado. Notificaciones enviadas.", "success")
        return redirect(url_for("index"))

    @app.route("/check/<int:domain_id>", methods=["POST"])
    @login_required
    def manual_check(domain_id):
        d = Domain.query.get_or_404(domain_id)
        check_and_capture(d)
        flash(f"Monitoreo ejecutado para {d.domain}.", "success")
        return redirect(url_for("index"))

    @app.route("/edit/<int:domain_id>", methods=["GET", "POST"])
    @login_required
    def edit_domain(domain_id):
        d = Domain.query.get_or_404(domain_id)
        if request.method == "POST":
            d.domain = request.form.get("domain", "").strip()
            d.client = request.form.get("client", "").strip()
            d.contact = request.form.get("contact", "").strip()
            d.provider = request.form.get("provider", "").strip()
            expiry = request.form.get("expiry", "").strip()

            if not d.domain or not d.client:
                flash("Dominio y Cliente son obligatorios.", "error")
                return redirect(url_for("edit_domain", domain_id=d.id))

            try:
                d.expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date() if expiry else None
            except ValueError:
                flash("Fecha de caducidad inválida.", "error")
                return redirect(url_for("edit_domain", domain_id=d.id))

            db.session.commit()
            flash("Dominio actualizado.", "success")
            return redirect(url_for("index"))
        return render_template("edit_domain.html", domain=d)

    @app.route("/delete/<int:domain_id>", methods=["POST"])
    @login_required
    def delete_domain(domain_id):
        d = Domain.query.get_or_404(domain_id)
        db.session.delete(d)
        db.session.commit()
        flash("Dominio eliminado.", "success")
        return redirect(url_for("index"))

    # -------------------
    # Admin: User management
    # -------------------
    @app.route("/admin/users")
    @admin_required
    def admin_users():
        users = User.query.order_by(User.username.asc()).all()
        return render_template("admin_users.html", users=users)

    @app.route("/admin/users/create", methods=["GET", "POST"])
    @admin_required
    def admin_create_user():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            is_admin = request.form.get("is_admin") == "on"

            if not username or not password:
                flash("Usuario y contraseña son obligatorios.", "error")
                return redirect(url_for("admin_create_user"))

            if User.query.filter_by(username=username).first():
                flash("Ese usuario ya existe.", "error")
                return redirect(url_for("admin_create_user"))

            u = User(
                username=username,
                password_hash=generate_password_hash(password),
                is_admin=is_admin,
            )
            db.session.add(u)
            db.session.commit()
            flash(f"Usuario '{username}' creado.", "success")
            return redirect(url_for("admin_users"))
        return render_template("admin_user_form.html", user=None)

    @app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_edit_user(user_id):
        u = User.query.get_or_404(user_id)
        if request.method == "POST":
            u.username = request.form.get("username", "").strip()
            is_admin = request.form.get("is_admin") == "on"
            password = request.form.get("password", "").strip()

            if not u.username:
                flash("El nombre de usuario es obligatorio.", "error")
                return redirect(url_for("admin_edit_user", user_id=u.id))

            u.is_admin = is_admin
            if password:
                u.password_hash = generate_password_hash(password)

            db.session.commit()
            flash(f"Usuario '{u.username}' actualizado.", "success")
            return redirect(url_for("admin_users"))
        return render_template("admin_user_form.html", user=u)

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(user_id):
        u = User.query.get_or_404(user_id)
        if u.id == current_user.id:
            flash("No puedes eliminarte a ti mismo.", "error")
            return redirect(url_for("admin_users"))
        db.session.delete(u)
        db.session.commit()
        flash(f"Usuario '{u.username}' eliminado.", "success")
        return redirect(url_for("admin_users"))

    # -------------------
    # Admin: Settings
    # -------------------
    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_required
    def admin_settings():
        if request.method == "POST":
            # Horarios
            hour1 = request.form.get("hour1", "8").strip()
            min1  = request.form.get("min1",  "0").strip()
            hour2 = request.form.get("hour2", "16").strip()
            min2  = request.form.get("min2",  "0").strip()

            Settings.set("schedule_hour1", hour1)
            Settings.set("schedule_min1",  min1)
            Settings.set("schedule_hour2", hour2)
            Settings.set("schedule_min2",  min2)

            # Webhook Google Chat
            gchat = request.form.get("gchat_webhook", "").strip()
            Settings.set("gchat_webhook", gchat)

            reschedule_jobs(app)
            flash("Configuración guardada.", "success")
            return redirect(url_for("admin_settings"))

        cfg = {
            "hour1": Settings.get("schedule_hour1", "8"),
            "min1":  Settings.get("schedule_min1",  "0"),
            "hour2": Settings.get("schedule_hour2", "16"),
            "min2":  Settings.get("schedule_min2",  "0"),
            "gchat_webhook": Settings.get("gchat_webhook", ""),
        }
        return render_template("admin_settings.html", cfg=cfg)

    return app


def reschedule_jobs(app):
    scheduler = app.scheduler
    tz = app.config.get("TIMEZONE", "America/Lima")

    h1 = int(Settings.get("schedule_hour1", "8"))
    m1 = int(Settings.get("schedule_min1",  "0"))
    h2 = int(Settings.get("schedule_hour2", "16"))
    m2 = int(Settings.get("schedule_min2",  "0"))

    for job_id, hour, minute in [("job1", h1, m1), ("job2", h2, m2)]:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        scheduler.add_job(
            lambda: job_runner(app),
            "cron",
            hour=hour, minute=minute,
            timezone=tz,
            id=job_id,
        )


def job_runner(app):
    with app.app_context():
        run_full_cycle()


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
