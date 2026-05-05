from app import create_app
from monitor import run_full_cycle

app = create_app()

with app.app_context():
    print("🚀 Ejecutando ciclo de monitoreo manual...")
    run_full_cycle()
    print("✅ Correo de prueba enviado")
