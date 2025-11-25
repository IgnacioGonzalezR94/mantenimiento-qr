import qrcode

# ⚠️ CAMBIA ESTA IP POR LA DE TU PC ⚠️
# Usa la misma IP que usas para entrar desde el navegador en el celular.
BASE_URL = "http://172.23.4.31:5000/m/"  # ejemplo, cámbiala

# Lista fija de secciones (códigos deben coincidir con los de la app)
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

print("✅ Listo. Los archivos PNG están en esta misma carpeta.")
