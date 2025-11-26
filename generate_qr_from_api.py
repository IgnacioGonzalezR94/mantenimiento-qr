import qrcode
import requests
import os

# URL de tu backend online (Render)
BASE_URL = "https://mantenimiento-qr-pyck.onrender.com"

# Endpoint secreto para leer máquinas/secciones
ADMIN_API_KEY = "123456"  # Puedes cambiarlo luego
API_URL = f"{BASE_URL}/api/sections?key={ADMIN_API_KEY}"

# Carpeta donde guardar los QR
OUTPUT_FOLDER = "qr_codes"

# Crear carpeta si no existe
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

print("📡 Obteniendo máquinas desde el servidor...")

try:
    sections = requests.get(API_URL).json()
except Exception as e:
    print("❌ Error al conectar con el servidor:", e)
    exit()

print(f"Encontradas {len(sections)} máquinas/secciones\n")

for s in sections:
    code = s["code"]
    name = s["name"]

    qr_url = f"{BASE_URL}/m/{code}"
    img = qrcode.make(qr_url)

    filepath = os.path.join(OUTPUT_FOLDER, f"{code}.png")
    img.save(filepath)

    print(f"✅ QR generado → {filepath} | {qr_url}")

print("\n🎉 Listo. Todos los QR están en la carpeta 'qr_codes'.")
