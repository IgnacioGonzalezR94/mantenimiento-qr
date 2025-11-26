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

# Clave para sesiones (admin, etc.)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-super-secreta")

# Contraseña del modo admin (/admin)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")

# Contraseña para que el técnico pueda registrar trabajos y solicitar repuestos
TECHNICIAN_PASSWORD = os.environ.get("TECH_PASSWORD", "9999")

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
    """Crea las tablas si no existen y asegura columnas nuevas."""
    conn = get_db()
    cur = conn.cursor()

    # Tabla de secciones (máquinas / módulos con QR)
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

    # Tabla de órdenes de trabajo (mantenimientos / avisos)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL,
        technician_id INTEGER,
        date TEXT NOT NULL,
        type TEXT,
        component TEXT,
        failure_type TEXT,
        description TEXT NOT NULL,
        downtime_min INTEGER,
        machine_stopped INTEGER,
        created_at TEXT NOT NULL,
        resolved INTEGER DEFAULT 0,
        resolution_description TEXT,
        resolution_at TEXT,
        FOREIGN KEY(section_id) REFERENCES sections(id),
        FOREIGN KEY(technician_id) REFERENCES technicians(id)
    );
    """)

    # Asegurar columnas nuevas por si la tabla ya existía con menos campos
    alter_statements = [
        "ALTER TABLE work_orders ADD COLUMN component TEXT;",
        "ALTER TABLE work_orders ADD COLUMN failure_type TEXT;",
        "ALTER TABLE work_orders ADD COLUMN resolved INTEGER DEFAULT 0;",
        "ALTER TABLE work_orders ADD COLUMN resolution_description TEXT;",
        "ALTER TABLE work_orders ADD COLUMN resolution_at TEXT;"
    ]
    for stmt in alter_statements:
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            # La columna ya existe: ignorar
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

    # Tabla de archivos adjuntos de órdenes de trabajo
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

    # Tabla de solicitudes de repuestos (Modo "Otros")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS spare_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER,
        technician_id INTEGER,
        date TEXT NOT NULL,
        part_name TEXT,
        description TEXT,
        photo_path TEXT,
        photo_filename TEXT,
        photo_mime TEXT,
        status TEXT DEFAULT 'pendiente',
        created_at TEXT NOT NULL,
        FOREIGN KEY(section_id) REFERENCES sections(id),
        FOREIGN KEY(technician_id) REFERENCES technicians(id)
    );
    """)

    # Tabla de solicitudes de ayuda (Modo "Ayuda")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS help_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        name TEXT,
        contact TEXT,
        description TEXT NOT NULL,
        photo_path TEXT,
        photo_filename TEXT,
        photo_mime TEXT,
        status TEXT DEFAULT 'pendiente',
        created_at TEXT NOT NULL
    );
    """)

    conn.commit()
    conn.close()


def seed_data():
    """Inserta secciones y técnicos iniciales si no existen."""
    conn = get_db()
    cur = conn.cursor()

    # Secciones de la máquina KATO (puedes borrarlas luego y crear las tuyas)
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

    # Técnicos iniciales
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


# Inicializar BD al cargar el módulo
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
#  PORTAL PRINCIPAL (QR UNIVERSAL)
# -----------------------------------------
@app.route('/', endpoint='index')
def home_portal():
    """
    Portal principal del QR universal.
    Muestra los 7 botones: técnico, visualización, otros, informe,
    perfil técnico, admin, ayuda.
    """
    return render_template('home_portal.html')


# -----------------------------------------
#  LISTAS BÁSICAS (secciones / técnicos)
# -----------------------------------------
def get_all_sections():
    conn = get_db()
    cur = conn.cursor()
    sections = cur.execute(
        "SELECT * FROM sections ORDER BY name;"
    ).fetchall()
    conn.close()
    return sections


def get_all_technicians():
    conn = get_db()
    cur = conn.cursor()
    technicians = cur.execute(
        "SELECT * FROM technicians ORDER BY name;"
    ).fetchall()
    conn.close()
    return technicians


# -----------------------------------------
#  MODO TÉCNICO (REGISTRAR MANTENIMIENTOS)
# -----------------------------------------
@app.route('/modo/tecnico')
def modo_tecnico():
    """Página donde el técnico elige la máquina y entra a registrar mantenimiento."""
    sections = get_all_sections()
    return render_template('modo_tecnico.html', sections=sections)


@app.route('/m/<section_code>')
def section_view(section_code):
    """Vista de una sección específica (historial)."""
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
        LIMIT 50;
    """, (section['id'],)).fetchall()

    # Adjuntos por orden de trabajo
    attachments_rows = conn.execute("""
        SELECT * FROM attachments
        WHERE work_order_id IN (
            SELECT id FROM work_orders WHERE section_id = ?
        )
    """, (section['id'],)).fetchall()

    conn.close()

    attachments_by_work = {}
    for a in attachments_rows:
        attachments_by_work.setdefault(a['work_order_id'], []).append(a)

    return render_template(
        'section.html',
        section=section,
        work_orders=work_orders,
        attachments_by_work=attachments_by_work
    )


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

    components = conn.execute(
        "SELECT * FROM components WHERE section_code = ? AND active = 1 ORDER BY name;",
        (section_code,)
    ).fetchall()

    error = None

    if request.method == 'POST':
        # Verificar contraseña de técnico
        tech_password = request.form.get('tech_password')
        if tech_password != TECHNICIAN_PASSWORD:
            conn.close()
            return "Contraseña de técnico incorrecta.", 403

        technician_id = request.form.get('technician_id') or None
        type_work = request.form.get('type')
        component = request.form.get('component')  # subparte elegida
        failure_type = request.form.get('failure_type')
        description = request.form.get('description')
        downtime_min = request.form.get('downtime_min') or 0
        machine_stopped = 1 if request.form.get('machine_stopped') == 'on' else 0

        # Estado según tipo
        if type_work == "Aviso de desperfecto":
            resolved = 0
        else:
            resolved = 1

        now = datetime.now().isoformat(timespec='minutes')

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO work_orders
            (section_id, technician_id, date, type, component, failure_type,
             description, downtime_min, machine_stopped, created_at, resolved)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            section['id'],
            technician_id,
            now,
            type_work,
            component,
            failure_type,
            description,
            int(downtime_min),
            machine_stopped,
            now,
            resolved
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
    return render_template(
        'new_work_order.html',
        section=section,
        technicians=technicians,
        components=components,
        error=error
    )


# -----------------------------------------
#  MODO VISUALIZACIÓN (SOLO LECTURA)
# -----------------------------------------
@app.route('/modo/visualizacion')
def modo_visualizacion():
    """Cualquier persona puede ver el historial de la máquina que elija."""
    sections = get_all_sections()
    return render_template('modo_visualizacion.html', sections=sections)


# -----------------------------------------
#  MODO OTROS (SOLICITUD DE REPUESTOS)
# -----------------------------------------
@app.route('/modo/otros', methods=['GET', 'POST'])
def modo_otros():
    """
    El técnico solicita repuestos:
    - Elige máquina
    - Describe repuesto
    - Sube foto
    - Ingresa contraseña de técnico
    """
    sections = get_all_sections()
    technicians = get_all_technicians()

    if request.method == 'POST':
        tech_password = request.form.get('tech_password')
        if tech_password != TECHNICIAN_PASSWORD:
            return "Contraseña de técnico incorrecta para solicitar repuesto.", 403

        section_id = request.form.get('section_id') or None
        technician_id = request.form.get('technician_id') or None
        part_name = request.form.get('part_name')
        description = request.form.get('description')

        now = datetime.now().isoformat(timespec='minutes')

        photo = request.files.get('photo')
        photo_path = None
        photo_filename = None
        photo_mime = None

        if photo and photo.filename:
            photo_filename = photo.filename
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
            base, ext = os.path.splitext(photo_filename)
            i = 1
            while os.path.exists(photo_path):
                photo_filename = f"{base}_{i}{ext}"
                photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
                i += 1
            photo.save(photo_path)
            photo_mime = photo.mimetype

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO spare_requests
            (section_id, technician_id, date, part_name, description,
             photo_path, photo_filename, photo_mime, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            section_id,
            technician_id,
            now,
            part_name,
            description,
            photo_path,
            photo_filename,
            photo_mime,
            "pendiente",
            now
        ))
        conn.commit()
        conn.close()

        return render_template('modo_otros_ok.html')

    return render_template('modo_otros.html',
                           sections=sections,
                           technicians=technicians)


# -----------------------------------------
#  MODO INFORME (HISTORIAL POR RANGO DE FECHAS)
# -----------------------------------------
@app.route('/modo/informe', methods=['GET', 'POST'])
def modo_informe():
    """
    Cualquier persona puede ver el historial completo filtrando por:
    - rango de fechas
    - máquina
    Y luego usar "Imprimir / guardar como PDF" desde el navegador.
    """
    sections = get_all_sections()
    resultados = []
    filtros = {}

    if request.method == 'POST':
        section_id = request.form.get('section_id') or None
        start_date = request.form.get('start_date') or ""
        end_date = request.form.get('end_date') or ""

        filtros = {
            "section_id": section_id,
            "start_date": start_date,
            "end_date": end_date
        }

        conn = get_db()
        cur = conn.cursor()

        query = """
            SELECT w.*, s.name as section_name, t.name as technician_name
            FROM work_orders w
            JOIN sections s ON w.section_id = s.id
            LEFT JOIN technicians t ON w.technician_id = t.id
            WHERE 1=1
        """
        params = []

        if section_id:
            query += " AND w.section_id = ?"
            params.append(section_id)

        if start_date:
            query += " AND w.date >= ?"
            params.append(start_date + "T00:00")

        if end_date:
            query += " AND w.date <= ?"
            params.append(end_date + "T23:59")

        query += " ORDER BY w.date DESC;"

        resultados = cur.execute(query, params).fetchall()
        conn.close()

    return render_template('modo_informe.html',
                           sections=sections,
                           resultados=resultados,
                           filtros=filtros)


# -----------------------------------------
#  MODO PERFIL TÉCNICO
# -----------------------------------------
@app.route('/modo/perfil-tecnico')
def modo_perfil_tecnico():
    """Lista de técnicos para ver sus trabajos."""
    technicians = get_all_technicians()
    return render_template('modo_perfil_tecnico.html', technicians=technicians)


@app.route('/modo/perfil-tecnico/<int:tech_id>')
def modo_perfil_tecnico_detalle(tech_id):
    """Trabajos realizados por un técnico específico."""
    conn = get_db()
    cur = conn.cursor()

    tech = cur.execute(
        "SELECT * FROM technicians WHERE id = ?;",
        (tech_id,)
    ).fetchone()

    if not tech:
        conn.close()
        return f"Técnico no encontrado (ID {tech_id})", 404

    work_orders = cur.execute("""
        SELECT w.*, s.name as section_name
        FROM work_orders w
        JOIN sections s ON w.section_id = s.id
        WHERE w.technician_id = ?
        ORDER BY w.date DESC
        LIMIT 100;
    """, (tech_id,)).fetchall()

    conn.close()
    return render_template('modo_perfil_tecnico_detalle.html',
                           technician=tech,
                           work_orders=work_orders)


# -----------------------------------------
#  MODO AYUDA
# -----------------------------------------
@app.route('/modo/ayuda', methods=['GET', 'POST'])
def modo_ayuda():
    """
    Cualquier persona puede subir una foto y registrar un problema general.
    No requiere contraseña.
    """
    if request.method == 'POST':
        name = request.form.get('name')
        contact = request.form.get('contact')
        description = request.form.get('description')

        now = datetime.now().isoformat(timespec='minutes')

        photo = request.files.get('photo')
        photo_path = None
        photo_filename = None
        photo_mime = None

        if photo and photo.filename:
            photo_filename = photo.filename
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
            base, ext = os.path.splitext(photo_filename)
            i = 1
            while os.path.exists(photo_path):
                photo_filename = f"{base}_{i}{ext}"
                photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
                i += 1
            photo.save(photo_path)
            photo_mime = photo.mimetype

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO help_requests
            (date, name, contact, description,
             photo_path, photo_filename, photo_mime, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            now,
            name,
            contact,
            description,
            photo_path,
            photo_filename,
            photo_mime,
            "pendiente",
            now
        ))
        conn.commit()
        conn.close()

        return render_template('modo_ayuda_ok.html')

    return render_template('modo_ayuda.html')


# -----------------------------------------
#  ADMIN: LOGIN / LOGOUT / HOME
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
    return redirect(url_for('home_portal'))


@app.route('/admin')
@admin_required
def admin_home():
    """Menú principal del modo administrador."""
    sections = get_all_sections()
    technicians = get_all_technicians()
    return render_template('admin_home.html',
                           sections=sections,
                           technicians=technicians)


# -----------------------------------------
#  ADMIN: TÉCNICOS
# -----------------------------------------
@app.route('/admin/technicians', methods=['GET', 'POST'])
@admin_required
def admin_technicians():
    """Alta / baja de técnicos."""
    conn = get_db()
    cur = conn.cursor()

    # Desactivar técnico
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


# -----------------------------------------
#  ADMIN: MÁQUINAS / SECCIONES
# -----------------------------------------
@app.route('/admin/sections', methods=['GET', 'POST'])
@admin_required
def admin_sections():
    """Gestionar máquinas/secciones: agregar nuevas y ver las existentes."""
    conn = get_db()
    cur = conn.cursor()

    if request.method == 'POST':
        code = (request.form.get('code') or "").strip().upper()
        name = (request.form.get('name') or "").strip()
        description = (request.form.get('description') or "").strip()

        if code and name:
            cur.execute("""
                INSERT OR IGNORE INTO sections (code, name, description)
                VALUES (?, ?, ?)
            """, (code, name, description))
            conn.commit()

    sections = cur.execute(
        "SELECT * FROM sections ORDER BY name;"
    ).fetchall()

    conn.close()
    return render_template('admin_sections.html', sections=sections)


@app.route('/admin/sections/<int:section_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_section(section_id):
    """Editar una máquina/sección existente (nombre y descripción)."""
    conn = get_db()
    cur = conn.cursor()

    section = cur.execute(
        "SELECT * FROM sections WHERE id = ?;",
        (section_id,)
    ).fetchone()

    if not section:
        conn.close()
        return f"Sección no encontrada (ID {section_id})", 404

    if request.method == 'POST':
        name = (request.form.get('name') or "").strip()
        description = (request.form.get('description') or "").strip()

        if name:
            cur.execute("""
                UPDATE sections
                SET name = ?, description = ?
                WHERE id = ?
            """, (name, description, section_id))
            conn.commit()
            conn.close()
            return redirect(url_for('admin_sections'))

    conn.close()
    return render_template('admin_edit_section.html', section=section)


# -----------------------------------------
#  ADMIN: SUBPARTES (COMPONENTES) POR SECCIÓN
# -----------------------------------------
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
#  ADMIN: AVISOS DE DESPERFECTO
# -----------------------------------------
@app.route('/admin/issues')
@admin_required
def admin_issues():
    """Listado de avisos de desperfecto pendientes y resueltos."""
    conn = get_db()
    cur = conn.cursor()

    pendientes = cur.execute("""
        SELECT w.*, s.name as section_name, t.name as technician_name
        FROM work_orders w
        JOIN sections s ON w.section_id = s.id
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.type = 'Aviso de desperfecto'
          AND (w.resolved IS NULL OR w.resolved = 0)
        ORDER BY w.date DESC;
    """).fetchall()

    resueltos = cur.execute("""
        SELECT w.*, s.name as section_name, t.name as technician_name
        FROM work_orders w
        JOIN sections s ON w.section_id = s.id
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.type = 'Aviso de desperfecto'
          AND w.resolved = 1
        ORDER BY w.date DESC
        LIMIT 50;
    """).fetchall()

    conn.close()
    return render_template('admin_issues.html',
                           pendientes=pendientes,
                           resueltos=resueltos)


@app.route('/admin/issues/<int:issue_id>/resolver', methods=['GET', 'POST'])
@admin_required
def admin_resolve_issue(issue_id):
    """Marcar un aviso de desperfecto como solucionado y subir evidencia."""
    conn = get_db()
    cur = conn.cursor()

    issue = cur.execute("""
        SELECT w.*, s.name as section_name, t.name as technician_name
        FROM work_orders w
        JOIN sections s ON w.section_id = s.id
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.id = ?
    """, (issue_id,)).fetchone()

    if not issue:
        conn.close()
        return f"Aviso no encontrado (ID {issue_id})", 404

    if request.method == 'POST':
        resolution_description = request.form.get('resolution_description')
        now = datetime.now().isoformat(timespec='minutes')

        # Marcar como resuelto
        cur.execute("""
            UPDATE work_orders
            SET resolved = 1,
                resolution_description = ?,
                resolution_at = ?
            WHERE id = ?
        """, (resolution_description, now, issue_id))

        # Guardar evidencia en attachments
        files = request.files.getlist('attachments')
        for f in files:
            if f and f.filename:
                filename = f.filename
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
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
                    issue_id,
                    filename,
                    f.mimetype,
                    save_path,
                    now
                ))

        conn.commit()
        conn.close()
        return redirect(url_for('admin_issues'))

    conn.close()
    return render_template('admin_resolve_issue.html', issue=issue)


# -----------------------------------------
#  API PARA GENERAR QRS (OPCIONAL)
# -----------------------------------------
@app.route('/api/sections')
def api_sections():
    """Devuelve una lista JSON de máquinas/secciones para generar QR por código."""
    key = request.args.get("key")
    if key != "123456":
        return {"error": "unauthorized"}, 401

    conn = get_db()
    cur = conn.cursor()

    sections = cur.execute("""
        SELECT id, code, name, description
        FROM sections
        ORDER BY name
    """).fetchall()

    conn.close()
    return [dict(row) for row in sections]


# -----------------------------------------
#  SERVIR ARCHIVOS SUBIDOS
# -----------------------------------------
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Servir archivos subidos (fotos/videos)."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# -----------------------------------------
#  EJECUCIÓN LOCAL
# -----------------------------------------
if __name__ == '__main__':
    init_db()
    seed_data()
    app.run(host='0.0.0.0', port=5000, debug=True)

