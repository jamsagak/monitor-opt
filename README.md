# Monitor de Sitios (Flask)

Sistema simple para monitorear sitios de clientes:
- Toma capturas del home **2 veces al día** (09:00 y 21:00 America/Lima).
- Muestra un dashboard con: Dominio, Cliente, Proveedor, Fecha de vencimiento, Estado y miniatura de la última captura.
- Envía un correo con el resumen e incluye las capturas como adjuntos.
- Formulario para registrar dominios: Dominio, Cliente, Contacto, Proveedor de dominio, Fecha de caducidad.

## Requisitos

- Python 3.10+
- Chromium para Playwright
- Acceso SMTP para enviar correos

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
# edita .env con tus valores (SMTP, TO_EMAILS, etc.)
```

## Uso

```bash
python app.py
```

- Abre http://localhost:5000
- Registra dominios en **Registrar dominio**.
- El scheduler corre en segundo plano en el mismo proceso y dispara a las 09:00 y 21:00 (zona horaria configurable en `.env` vía `TIMEZONE`).

### Ejecutar ciclo completo manualmente

Puedes forzar el ciclo completo ejecutando el job por fuera (por ejemplo, vía `flask shell`), aunque el dashboard tiene botón "Chequear ahora" por dominio.

## Producción

- Considera ejecutar con `gunicorn`/`uwsgi` y mover el scheduler a un proceso dedicado, o usar un **cron** o **Celery**.
- Monta un volumen persistente para `static/screenshots/`.
- Para detección de cambios visuales (opcional), podrías guardar diffs de imágenes (Pillow) y alertar cuando superen cierto umbral.

## Seguridad

- Protege el dashboard con autenticación si va a exponerse públicamente.
- Reemplaza SQLite por Postgres si el proyecto crece.

## Campos adicionales

- `contact`: libre, puedes guardar nombre/email/teléfono.
- Estado muestra `OK` si el último HTTP fue < 400 y la captura se tomó; `ERROR` si hubo 4xx/5xx o fallo.
