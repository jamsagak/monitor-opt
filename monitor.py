import os
import smtplib
from email.message import EmailMessage
from datetime import datetime
import pytz

import requests
from playwright.sync_api import sync_playwright
from flask import current_app

from models import db, Domain, Settings


def normalize_url(domain: str) -> str:
    # Ensure a scheme; default to https
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain.strip("/")
    return domain


def take_screenshot(url: str, out_path: str, timeout_ms: int = 20000) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1350, "height": 1024},
            device_scale_factor=1,
            ignore_https_errors=True,  # Ignorar errores SSL
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except Exception as e:
            if "net::ERR_CERT_AUTHORITY_INVALID" in str(e):
                print(f"[WARN] Certificado inválido en {url}, continuando de todas formas...")
            else:
                raise

        # 🚀 Captura solo el viewport (1024x1024)
        page.screenshot(path=out_path, type="jpeg", quality=10)

        context.close()
        browser.close()


def quick_status(url: str, timeout=12):
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "DomainMonitor/1.0"},
            verify=False,  # Ignorar SSL en requests también
        )
        return r.status_code, None
    except Exception as e:
        return None, str(e)


def check_and_capture(domain_obj: Domain):
    base_dir = current_app.config["SCREENSHOT_DIR"]
    now = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    safe_domain = domain_obj.domain.replace("://", "_").replace("/", "_")
    filename = f"{safe_domain}_{now}.jpg"
    out_path = os.path.join(base_dir, filename)

    url = normalize_url(domain_obj.domain)
    code, err = quick_status(url)

    try:
        if code and code < 400:
            take_screenshot(url, out_path)
            domain_obj.last_status = "OK"
            domain_obj.last_error = None
            domain_obj.last_error_code = None
        else:
            try:
                take_screenshot(url, out_path)
            except Exception as se:
                err = f"{err or ''} | screenshot: {se}"
            domain_obj.last_status = "ERROR"
            domain_obj.last_error = err or (f"HTTP {code}" if code else "sin respuesta")
            domain_obj.last_error_code = code
        domain_obj.last_http_code = code
        domain_obj.last_screenshot_path = out_path
        domain_obj.last_checked_at = datetime.utcnow()
        db.session.commit()
        return out_path
    except Exception as e:
        domain_obj.last_status = "ERROR"
        domain_obj.last_error = str(e)
        domain_obj.last_error_code = None
        domain_obj.last_checked_at = datetime.utcnow()
        db.session.commit()
        return None


def send_email(subject: str, html: str, attachments=None):
    cfg = current_app.config
    if not cfg.get("FROM_EMAIL") or not cfg.get("TO_EMAILS"):
        return

    api_key = cfg.get("SMTP_PASS")

    # Usar Brevo API si hay clave disponible (evita que Exim local intercepte SMTP)
    if api_key and (api_key.startswith("xsmtpsib-") or api_key.startswith("xkeysib-")):
        _send_email_brevo_api(subject, html, cfg)
        return

    # Fallback: SMTP estándar
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["FROM_EMAIL"]
    msg["To"] = ", ".join(cfg["TO_EMAILS"])
    msg.set_content("HTML email requerido.")
    msg.add_alternative(html, subtype="html")

    for path in attachments or []:
        try:
            with open(path, "rb") as f:
                data = f.read()
            filename = os.path.basename(path)
            msg.add_attachment(data, maintype="image", subtype="jpeg", filename=filename)
        except Exception:
            pass

    host = cfg.get("SMTP_HOST", "localhost")
    port = cfg.get("SMTP_PORT", 587)
    user = cfg.get("SMTP_USER")
    passwd = cfg.get("SMTP_PASS")

    with smtplib.SMTP(host, port) as s:
        s.ehlo()
        if port != 25:
            s.starttls()
            s.ehlo()
        if user and passwd:
            s.login(user, passwd)
        s.send_message(msg)


def _send_email_brevo_api(subject: str, html: str, cfg):
    """Envía email via Brevo Transactional API (HTTP), evitando SMTP interceptado."""
    api_key = cfg.get("SMTP_PASS")
    from_email = cfg.get("FROM_EMAIL", "")

    # TO_EMAILS: primero desde Settings DB, luego desde .env
    db_emails = Settings.get("to_emails", "")
    if db_emails:
        to_emails = [e.strip() for e in db_emails.split(",") if e.strip()]
    else:
        to_emails = cfg.get("TO_EMAILS", [])

    # Parsear nombre del remitente si viene como "Nombre <email>"
    import re
    match = re.match(r"^(.*?)\s*<(.+?)>$", from_email)
    if match:
        from_name, from_addr = match.group(1).strip(), match.group(2).strip()
    else:
        from_name, from_addr = "Monitor OPT", from_email

    payload = {
        "sender": {"name": from_name, "email": from_addr},
        "to": [{"email": e} for e in to_emails],
        "subject": subject,
        "htmlContent": html,
    }

    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        json=payload,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
        },
        timeout=15,
    )

    if resp.status_code not in (200, 201):
        raise Exception(f"Brevo API error {resp.status_code}: {resp.text}")


def format_local(dt: datetime) -> str:
    """Convierte UTC a hora local configurada"""
    if not dt:
        return ""
    tz = pytz.timezone(current_app.config.get("TIMEZONE", "America/Lima"))
    return dt.replace(tzinfo=pytz.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def email_digest(domains):
    rows = []
    for d in domains:
        status_color = "#16a34a" if d.last_status == "OK" else "#dc2626"
        status_dot = (
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'border-radius:50%;background:{status_color};margin-right:6px;vertical-align:middle;"></span>'
            f'<strong style="color:{status_color}">{d.last_status or "N/A"}</strong>'
        )
        days = d.days_to_expiry()
        expiry_txt = f"{d.expiry_date}" if d.expiry_date else "—"
        if days is not None and d.expiry_date:
            expiry_color = "#dc2626" if days < 30 else "#ca8a04" if days < 60 else "inherit"
            expiry_txt += f' <span style="color:{expiry_color}">({days} días)</span>'
        last_check = format_local(d.last_checked_at)
        error_txt = f'<span style="color:#dc2626">{d.last_error}</span>' if d.last_error else "—"
        rows.append(f"""
        <tr>
          <td><a href="https://{d.domain}" style="color:#5ca638;">{d.domain}</a></td>
          <td>{d.client}</td>
          <td>{d.provider or "—"}</td>
          <td>{expiry_txt}</td>
          <td>{status_dot}</td>
          <td>{d.last_http_code or "—"}</td>
          <td style="font-size:12px;">{error_txt}</td>
          <td style="color:#666;font-size:12px;">{last_check}</td>
        </tr>""")

    tz = pytz.timezone(current_app.config.get("TIMEZONE", "America/Lima"))
    local_now = datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(tz)
    ok_count = sum(1 for d in domains if d.last_status == "OK")
    err_count = len(domains) - ok_count

    html = f"""
<html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
  <div style="max-width:900px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <div style="background:#1a1f29;padding:20px 24px;border-bottom:3px solid #5ca638;">
      <h2 style="margin:0;color:#5ca638;font-size:20px;">🔍 Reporte de Monitoreo de Sitios</h2>
      <p style="margin:4px 0 0;color:#9aa6b6;font-size:13px;">Generado: {local_now.strftime("%d/%m/%Y %H:%M:%S %Z")}</p>
    </div>
    <div style="padding:16px 24px;background:#f8f9fa;border-bottom:1px solid #e2e8f0;">
      <span style="margin-right:20px;">✅ <strong>{ok_count}</strong> operativos</span>
      <span>❌ <strong>{err_count}</strong> con problemas</span>
    </div>
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="background:#141922;color:#eaeef5;">
          <th style="padding:12px 14px;text-align:left;font-size:13px;">Dominio</th>
          <th style="padding:12px 14px;text-align:left;font-size:13px;">Cliente</th>
          <th style="padding:12px 14px;text-align:left;font-size:13px;">Proveedor</th>
          <th style="padding:12px 14px;text-align:left;font-size:13px;">Vencimiento</th>
          <th style="padding:12px 14px;text-align:left;font-size:13px;">Estado</th>
          <th style="padding:12px 14px;text-align:left;font-size:13px;">HTTP</th>
          <th style="padding:12px 14px;text-align:left;font-size:13px;">Error</th>
          <th style="padding:12px 14px;text-align:left;font-size:13px;">Último chequeo</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <div style="padding:16px 24px;background:#f8f9fa;text-align:center;color:#9aa6b6;font-size:12px;border-top:1px solid #e2e8f0;">
      OPT MEDIA LATAM · Monitor de Sitios · <a href="{current_app.config.get('BASE_URL','')}" style="color:#5ca638;">Ver panel</a>
    </div>
  </div>
</body></html>
"""
    return html


def _clean_error(msg: str, max_len: int = 120) -> str:
    """Toma solo la primera línea del error y recorta los logs de Playwright."""
    if not msg:
        return ""
    # Quitar todo lo que venga después de los separadores de logs de Playwright
    for sep in ["=====", "navigating to", " | screenshot:"]:
        if sep in msg:
            msg = msg[:msg.index(sep)]
    # Solo la primera línea
    msg = msg.split("\n")[0].strip()
    return msg[:max_len] + ("…" if len(msg) > max_len else "")


def send_google_chat(domains):
    webhook_url = Settings.get("gchat_webhook")
    if not webhook_url:
        return

    tz = pytz.timezone(current_app.config.get("TIMEZONE", "America/Lima"))
    local_now = datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(tz)

    ok = [d for d in domains if d.last_status == "OK"]
    err = [d for d in domains if d.last_status != "OK"]

    lines = [f"🔍 *Monitoreo de sitios — {local_now.strftime('%d/%m/%Y %H:%M')} (Lima)*\n"]

    for d in domains:
        icon = "🟢" if d.last_status == "OK" else "🔴"
        code = f" — HTTP {d.last_http_code}" if d.last_http_code else ""
        # Mostrar dominio como texto plano (sin https://) para evitar previews de imagen
        error = ""
        if d.last_status != "OK" and d.last_error:
            error = f" — _{_clean_error(d.last_error)}_"
        lines.append(f"{icon} *{d.client}* ({d.domain}){code}{error}")

    lines.append(f"\n✅ Operativos: {len(ok)}   ❌ Con problemas: {len(err)}")

    payload = {"text": "\n".join(lines)}
    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[WARN] Google Chat HTTP {resp.status_code}: {resp.text[:200]}")
        else:
            print("[INFO] Google Chat enviado OK")
    except Exception as e:
        print(f"[WARN] Google Chat error: {e}")


def run_full_cycle():
    domains = Domain.query.order_by(Domain.client.asc()).all()
    if not domains:
        return
    for d in domains:
        check_and_capture(d)
    html = email_digest(domains)
    send_email("Monitoreo de sitios — OPT MEDIA LATAM", html)
    send_google_chat(domains)
