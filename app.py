from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template,
    send_from_directory,
    session,
)
import sqlite3
import os
from datetime import datetime
from functools import wraps

app = Flask(__name__)

# Clave para sesiones (modo admin). Cámbiala por algo tuyo.
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-super-secreta")

# Contraseña del modo admin (para /admin). Cámbiala también.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")

# Carpeta donde se guardan fotos y videos (solo local)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Archivo de la base de datos (Render y local usan este nombre)
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

    # Tabla de secciones (lo que tiene QR)
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
        component TEXT,
        FOREIGN KEY(section_id) REFERENCES sections(id),
        FOREIGN KEY(technician_id) REFERENCES technicians(id)
    );
    """)

    # Asegurar columna 'component' (por si la tabla ya existía)
    try:
        cur.execute("ALTER TABLE work_orders ADD COLUMN component TEXT;")
    except sqlite3.OperationalError:
        pass

    # Tabla de subpartes / componentes configurables por sección
    cur.execute("""
    CREATE TABLE IF NOT EXISTS components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_code TEXT NOT NULL,
        name TEXT NOT NULL,
        active INTEGER DEFAULT 1
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


# Inicializar BD al cargar el módulo (sirve local y en Render)
init_db()
seed_data()


# -----------------------------------------
#  DECORADOR PARA MODO ADMIN
# -----------------------------------------
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated_function


# -----------------------------------------
#  RUTAS PARA TÉCNICOS (USO NORMAL)
# -----------------------------------------
@app.route('/')
def index():
    """Página principal: lista todas las secciones."""
    conn = get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY name;").fetchall()
    conn.close()

    # Si no hay secciones por alguna razón, resembramos
    if not sections:
        init_db()
        seed_data()
        conn = get_db()
        sections = conn.execute("SELECT * FROM sections ORDER BY name;").fetchall()
        conn.close()

    return render_template('index.html', sections=sections)


@app.route('/m/<section_code>')
def section_view(section_code):
    """Vista de una sección específica (cuando escanean el QR)."""
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

    # Subpartes configurables de esta sección
    components = conn.execute(
        "SELECT * FROM components WHERE section_code = ? AND active = 1 ORDER BY name;",
        (section_code,)
    ).fetchall()

    if request.method == 'POST':
        technician_id = request.form.get('technician_id') or None
        type_work = request.form.get('type')
        component = request.form.get('component')  # subparte elegida
        description = request.form.get('description')
        downtime_min = request.form.get('downtime_min') or 0
        machine_stopped = 1 if request.form.get('machine_stopped') == 'on' else 0

        now = datetime.now().isoformat(timespec='minutes')

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO work_orders
            (section_id, technician_id, date, type, component, description,
             downtime_min, machine_stopped, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            section['id'],
            technician_id,
            now,
            type_work,
            component,
            description,
            int(downtime_min),
            machine_stopped,
            now
        ))
        work_order_id = cur.lastrowid

        # Manejo de archivos adjuntos (solo funciona local, en Render habría que
        # usar almacenamiento externo, pero dejamos la lógica base)
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
    return render_template(
        'new_work_order.html',
        section=section,
        technicians=technicians,
        components=components
    )


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Servir archivos subidos (fotos/videos)."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# -----------------------------------------
#  MODO ADMINISTRADOR
# -----------------------------------------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Pantalla de login para modo admin."""
    error = None
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_home'))
        else:
            error = "Contraseña incorrecta."

    return render_template('admin_login.html', error=error)


@app.route('/admin/logout')
@admin_required
def admin_logout():
    """Salir del modo admin."""
    session.pop('is_admin', None)
    return redirect(url_for('index'))


@app.route('/admin')
@admin_required
def admin_home():
    """Menú principal del modo administrador."""
    conn = get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY name;").fetchall()
    technicians = conn.execute("SELECT * FROM technicians ORDER BY name;").fetchall()
    conn.close()
    return render_template('admin_home.html', sections=sections, technicians=technicians)


@app.route('/admin/technicians', methods=['GET', 'POST'])
@admin_required
def admin_technicians():
    """Alta / baja de técnicos."""
    conn = get_db()
    cur = conn.cursor()

    # Desactivar técnico (soft delete)
    deactivate_id = request.args.get('deactivate_id')
    if deactivate_id:
        cur.execute("UPDATE technicians SET active = 0 WHERE id = ?;", (deactivate_id,))
        conn.commit()

    if request.method == 'POST':
        name = request.form.get('name')
        role = request.form.get('role')
        if name:
            cur.execute("""
                INSERT INTO technicians (name, role, active)
                VALUES (?, ?, 1)
            """, (name, role))
            conn.commit()

    technicians = cur.execute(
        "SELECT * FROM technicians ORDER BY active DESC, name;"
    ).fetchall()
    conn.close()

    return render_template('admin_technicians.html', technicians=technicians)


@app.route('/admin/components/<section_code>', methods=['GET', 'POST'])
@admin_required
def admin_components(section_code):
    """Configurar subpartes de una sección (sub-clasificaciones del QR)."""
    conn = get_db()
    cur = conn.cursor()

    section = cur.execute(
        "SELECT * FROM sections WHERE code = ?;",
        (section_code,)
    ).fetchone()
    if not section:
        conn.close()
        return f"Sección no encontrada: {section_code}", 404

    # Desactivar subparte
    deactivate_id = request.args.get('deactivate_id')
    if deactivate_id:
        cur.execute(
            "UPDATE components SET active = 0 WHERE id = ?;",
            (deactivate_id,)
        )
        conn.commit()

    if request.method == 'POST':
        name = request.form.get('name')
        if name:
            cur.execute("""
                INSERT INTO components (section_code, name, active)
                VALUES (?, ?, 1)
            """, (section_code, name))
            conn.commit()

    components = cur.execute("""
        SELECT * FROM components
        WHERE section_code = ?
        ORDER BY active DESC, name;
    """, (section_code,)).fetchall()

    conn.close()
    return render_template(
        'admin_components.html',
        section=section,
        components=components
    )


# -----------------------------------------
#  EJECUCIÓN LOCAL
# -----------------------------------------
if __name__ == '__main__':
    init_db()
    seed_data()
    app.run(host='0.0.0.0', port=5000, debug=True)
