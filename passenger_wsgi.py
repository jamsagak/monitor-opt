import sys, os

# Asegurar que el directorio de la app esté en el sys.path
sys.path.insert(0, os.path.dirname(__file__))

# Importar tu Flask app
from app import create_app
application = create_app()
