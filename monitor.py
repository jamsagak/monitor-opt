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
    if api_key and api_key.startswith("xsmtpsib-"):
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
        shot_rel = d.last_screenshot_path.replace("static/", "") if d.last_screenshot_path else ""
        img_tag = (
            f'<img src="{current_app.config["BASE_URL"]}/static/{shot_rel}" '
            f'alt="{d.domain}" style="max-width:480px;height:auto;" />'
            if shot_rel
            else "(sin captura)"
        )
        status_color = "#16a34a" if d.last_status == "OK" else "#dc2626"
        status_dot = (
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'border-radius:50%;background:{status_color};margin-right:6px;"></span>'
            f'{d.last_status or "N/A"}'
        )
        days = d.days_to_expiry()
        expiry_txt = f"{d.expiry_date}" if d.expiry_date else "—"
        if days is not None and d.expiry_date:
            expiry_txt += f" ({days} días)"
        last_check = format_local(d.last_checked_at)
        rows.append(f"""
        <tr>
          <td>{d.domain}</td>
          <td>{d.client}</td>
          <td>{d.provider or ""}</td>
          <td>{expiry_txt}</td>
          <td>{status_dot}</td>
          <td>{d.last_http_code or ""}</td>
          <td>{d.last_error or ""}</td>
          <td>
            {img_tag}
            <div style="color:#666;font-size:12px;">{last_check}</div>
          </td>
        </tr>""")

    # Fecha de generación en hora local
    tz = pytz.timezone(current_app.config.get("TIMEZONE", "America/Lima"))
    local_now = datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(tz)

    html = f"""
<html><body>
  <h2>Reporte de Monitoreo de Sitios</h2>
  <table border="1" cellspacing="0" cellpadding="6">
    <thead>
      <tr>
        <th>Dominio</th>
        <th>Cliente</th>
        <th>Proveedor</th>
        <th>Vencimiento</th>
        <th>Estado</th>
        <th>Código HTTP</th>
        <th>Error</th>
        <th>Última captura</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <p>Generado: {local_now.strftime("%Y-%m-%d %H:%M:%S %Z")}</p>
</body></html>
"""
    return html


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
        error = f" — _{d.last_error}_" if d.last_status != "OK" and d.last_error else ""
        lines.append(f"{icon} *{d.client}* ({d.domain}){code}{error}")

    lines.append(f"\n✅ Operativos: {len(ok)}   ❌ Con problemas: {len(err)}")

    payload = {"text": "\n".join(lines)}
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[WARN] Google Chat error: {e}")


def run_full_cycle():
    domains = Domain.query.order_by(Domain.client.asc()).all()
    if not domains:
        return
    shots = []
    for d in domains:
        p = check_and_capture(d)
        if p:
            shots.append(p)
    html = email_digest(domains)
    send_email("Monitoreo de sitios (ejecución programada)", html, attachments=shots)
    send_google_chat(domains)
