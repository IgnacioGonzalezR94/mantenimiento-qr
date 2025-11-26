import qrcode

# ⚠️ USA TU URL ONLINE DE RENDER
BASE_URL = "https://mantenimiento-qr-pyck.onrender.com/"

sections = [
    ("VOLCADOR", "Volcador"),
    ("ELEVADOR", "Elevador de fruta"),
    ("ACUMULACION", "Acumulación"),
    ("SINGULACION", "Singulación"),
    ("ACELERACION", "Aceleración"),
    ("TECHMODULE", "Tech Module"),
    ("SELECTIONMODULE", "Selection Module"),
    ("CADENAS", "Cadenas y rollers"),
    ("TABLEROS", "Tableros eléctricos"),
]

print("Generando códigos QR...")

for code, name in sections:
    url = BASE_URL + code
    img = qrcode.make(url)
    filename = f"qr_{code}.png"
    img.save(filename)
    print(f"✅ QR generado para {name} ({code}): {filename} -> {url}")

print("🚀 Listo. Los QR están en esta misma carpeta.")
