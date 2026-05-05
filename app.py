import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from apscheduler.schedulers.background import BackgroundScheduler
from flask_migrate import Migrate
import pytz
from flask_login import (
    LoginManager, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import check_password_hash

from config import Config
from models import db, Domain, User
from monitor import run_full_cycle, check_and_capture


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

    # 🔹 Configurar el scheduler
    scheduler = BackgroundScheduler(timezone=app.config.get("TIMEZONE", "America/Lima"))

    scheduler.add_job(
        lambda: job_runner(app),
        "cron",
        hour=8, minute=0,
        id="morning_capture",
        replace_existing=True
    )

    scheduler.add_job(
        lambda: job_runner(app),
        "cron",
        hour=17, minute=0,
        id="evening_capture",
        replace_existing=True
    )

    scheduler.start()
    app.scheduler = scheduler

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

    @app.route("/check/<int:domain_id>", methods=["POST"])
    @login_required
    def manual_check(domain_id):
        d = Domain.query.get_or_404(domain_id)
        check_and_capture(d)
        flash(f"Monitoreo ejecutado para {d.domain}.", "success")
        return redirect(url_for("index"))

    @app.route("/delete/<int:domain_id>", methods=["POST"])
    @login_required
    def delete_domain(domain_id):
        d = Domain.query.get_or_404(domain_id)
        db.session.delete(d)
        db.session.commit()
        flash("Dominio eliminado.", "success")
        return redirect(url_for("index"))

    return app


def job_runner(app: Flask):
    with app.app_context():
        run_full_cycle()


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
