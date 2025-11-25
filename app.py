from flask import Flask, request, redirect, url_for, render_template, send_from_directory
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)

# Carpeta donde se guardan fotos y videos
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Archivo de la base de datos
DATABASE = 'db.sqlite3'


# -----------------------------------------
#  FUNCIONES BASE DE DATOS
# -----------------------------------------
def get_db():
    """Devuelve una conexión a la base de datos."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea las tablas si no existen."""
    conn = get_db()
    cur = conn.cursor()

    # Tabla de secciones
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        description TEXT
    );
    """)

    # Tabla de técnicos
    cur.execute("""
    CREATE TABLE IF NOT EXISTS technicians (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        role TEXT,
        active INTEGER DEFAULT 1
    );
    """)

    # Tabla de órdenes de trabajo
    cur.execute("""
    CREATE TABLE IF NOT EXISTS work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL,
        technician_id INTEGER,
        date TEXT NOT NULL,
        type TEXT,
        description TEXT NOT NULL,
        downtime_min INTEGER,
        machine_stopped INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(section_id) REFERENCES sections(id),
        FOREIGN KEY(technician_id) REFERENCES technicians(id)
    );
    """)

    # Tabla de archivos adjuntos
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_order_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        mime_type TEXT,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(work_order_id) REFERENCES work_orders(id)
    );
    """)

    conn.commit()
    conn.close()


def seed_data():
    """Inserta secciones y técnicos iniciales si no existen."""
    conn = get_db()
    cur = conn.cursor()

    # Secciones de la máquina KATO
    sections = [
        ("VOLCADOR", "Volcador", "Volcador de fruta / bins"),
        ("ELEVADOR", "Elevador de fruta", "Elevador desde volcador a acumulación"),
        ("ACUMULACION", "Acumulación", "Cama de acumulación de fruta"),
        ("SINGULACION", "Singulación", "Singulador de fruta"),
        ("ACELERACION", "Aceleración", "Módulo de aceleración"),
        ("TECHMODULE", "Tech Module", "Cámara + LEDs + computador (módulo óptico)"),
        ("SELECTIONMODULE", "Selection Module", "Módulo de selección / expulsores"),
        ("CADENAS", "Cadenas y rollers", "Cadenas, rodillos y transmisión"),
        ("TABLEROS", "Tableros eléctricos", "Tableros eléctricos y componentes"),
    ]

    for code, name, desc in sections:
        cur.execute("""
            INSERT OR IGNORE INTO sections (code, name, description)
            VALUES (?, ?, ?)
        """, (code, name, desc))

    # Técnicos iniciales (puedes cambiar estos nombres por los de tu equipo)
    technicians = [
        ("Walker", "Técnico"),
        ("Jose", "Técnico"),
        ("Ignacio", "Jefe de línea"),
    ]

    for name, role in technicians:
        cur.execute("SELECT id FROM technicians WHERE name = ?;", (name,))
        row = cur.fetchone()
        if row is None:
            cur.execute("""
                INSERT INTO technicians (name, role, active)
                VALUES (?, ?, 1)
            """, (name, role))

    conn.commit()
    conn.close()
    print("✅ Datos iniciales cargados (seed_data)")


# 🔴 INICIALIZAR BD AL IMPORTAR EL MÓDULO (sirve para Render y local)
init_db()
seed_data()


# -----------------------------------------
#  RUTAS PRINCIPALES
# -----------------------------------------
@app.route('/')
def index():
    """Página principal: lista todas las secciones."""
    conn = get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY name;").fetchall()
    conn.close()
    return render_template('index.html', sections=sections)


@app.route('/m/<section_code>')
def section_view(section_code):
    """Vista de una sección específica."""
    conn = get_db()
    section = conn.execute(
        "SELECT * FROM sections WHERE code = ?;",
        (section_code,)
    ).fetchone()

    if not section:
        conn.close()
        return f"Sección no encontrada: {section_code}", 404

    work_orders = conn.execute("""
        SELECT w.*, t.name as technician_name
        FROM work_orders w
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.section_id = ?
        ORDER BY w.date DESC
        LIMIT 20;
    """, (section['id'],)).fetchall()

    conn.close()
    return render_template('section.html', section=section, work_orders=work_orders)


@app.route('/m/<section_code>/nuevo', methods=['GET', 'POST'])
def new_work_order(section_code):
    """Formulario para registrar un nuevo mantenimiento en una sección."""
    conn = get_db()
    section = conn.execute(
        "SELECT * FROM sections WHERE code = ?;",
        (section_code,)
    ).fetchone()

    if not section:
        conn.close()
        return f"Sección no encontrada: {section_code}", 404

    technicians = conn.execute(
        "SELECT * FROM technicians WHERE active = 1 ORDER BY name;"
    ).fetchall()

    if request.method == 'POST':
        technician_id = request.form.get('technician_id') or None
        type_work = request.form.get('type')
        description = request.form.get('description')
        downtime_min = request.form.get('downtime_min') or 0
        machine_stopped = 1 if request.form.get('machine_stopped') == 'on' else 0

        now = datetime.now().isoformat(timespec='minutes')

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO work_orders
            (section_id, technician_id, date, type, description,
             downtime_min, machine_stopped, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            section['id'],
            technician_id,
            now,
            type_work,
            description,
            int(downtime_min),
            machine_stopped,
            now
        ))
        work_order_id = cur.lastrowid

        # Manejo de archivos adjuntos
        files = request.files.getlist('attachments')
        for f in files:
            if f and f.filename:
                filename = f.filename
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                # Evitar sobrescribir archivos
                base, ext = os.path.splitext(filename)
                i = 1
                while os.path.exists(save_path):
                    filename = f"{base}_{i}{ext}"
                    save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    i += 1

                f.save(save_path)

                cur.execute("""
                    INSERT INTO attachments
                    (work_order_id, filename, mime_type, path, created_at)
                    VALUES (?,?,?,?,?)
                """, (
                    work_order_id,
                    filename,
                    f.mimetype,
                    save_path,
                    now
                ))

        conn.commit()
        conn.close()
        return redirect(url_for('section_view', section_code=section_code))

    conn.close()
    return render_template('new_work_order.html', section=section, technicians=technicians)


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Servir archivos subidos (fotos/videos)."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# -----------------------------------------
#  EJECUCIÓN LOCAL
# -----------------------------------------
if __name__ == '__main__':
    # Para cuando lo corres en tu PC con: py app.py
    init_db()
    seed_data()
    app.run(host='0.0.0.0', port=5000, debug=True)
