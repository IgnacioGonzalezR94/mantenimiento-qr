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

# Clave de sesión (cámbiala en producción)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-super-secreta")

# Contraseña admin (puedes ponerla como variable de entorno en Render)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

DATABASE = "db.sqlite3"


# -------------------- BD --------------------
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Secciones / máquinas
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT
        );
        """
    )

    # Técnicos
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS technicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )

    # Órdenes de trabajo
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS work_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            technician_id INTEGER,
            date TEXT NOT NULL,
            type TEXT,
            failure_type TEXT,
            component TEXT,
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
        """
    )

    # Por si la tabla ya existía y le faltan columnas nuevas
    alter_statements = [
        "ALTER TABLE work_orders ADD COLUMN failure_type TEXT;",
        "ALTER TABLE work_orders ADD COLUMN component TEXT;",
        "ALTER TABLE work_orders ADD COLUMN resolved INTEGER DEFAULT 0;",
        "ALTER TABLE work_orders ADD COLUMN resolution_description TEXT;",
        "ALTER TABLE work_orders ADD COLUMN resolution_at TEXT;",
    ]
    for stmt in alter_statements:
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            # columna ya existe
            pass

    # Subpartes / componentes configurables
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS components (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_code TEXT NOT NULL,
            name TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        """
    )

    # Adjuntos (fotos / videos)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_order_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(work_order_id) REFERENCES work_orders(id)
        );
        """
    )

    conn.commit()
    conn.close()


def seed_data():
    """Datos iniciales básicos (si no existen)."""
    conn = get_db()
    cur = conn.cursor()

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
        cur.execute(
            """
            INSERT OR IGNORE INTO sections (code, name, description)
            VALUES (?, ?, ?)
            """,
            (code, name, desc),
        )

    technicians = [
        ("Walker", "Técnico"),
        ("Jose", "Técnico"),
        ("Ignacio", "Jefe de línea"),
    ]
    for name, role in technicians:
        cur.execute("SELECT id FROM technicians WHERE name = ?;", (name,))
        if cur.fetchone() is None:
            cur.execute(
                """
                INSERT INTO technicians (name, role, active)
                VALUES (?, ?, 1)
                """,
                (name, role),
            )

    conn.commit()
    conn.close()


# Inicializar (local y en Render)
init_db()
seed_data()


# -------------------- Decorador admin --------------------
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)

    return wrapper


# -------------------- Modo técnico / página principal --------------------
@app.route("/")
def index():
    """Página de inicio: lista secciones/máquinas (modo técnico simple)."""
    conn = get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY name;").fetchall()
    conn.close()

    if not sections:
        init_db()
        seed_data()
        conn = get_db()
        sections = conn.execute("SELECT * FROM sections ORDER BY name;").fetchall()
        conn.close()

    return render_template("index.html", sections=sections)


@app.route("/m/<section_code>")
def section_view(section_code):
    """Historial de una sección."""
    conn = get_db()
    section = conn.execute(
        "SELECT * FROM sections WHERE code = ?;", (section_code,)
    ).fetchone()
    if not section:
        conn.close()
        return f"Sección no encontrada: {section_code}", 404

    work_orders = conn.execute(
        """
        SELECT w.*, t.name as technician_name
        FROM work_orders w
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.section_id = ?
        ORDER BY w.date DESC
        LIMIT 50;
        """,
        (section["id"],),
    ).fetchall()
    conn.close()

    return render_template(
        "section.html",
        section=section,
        work_orders=work_orders,
    )


@app.route("/m/<section_code>/nuevo", methods=["GET", "POST"])
def new_work_order(section_code):
    """Registrar nuevo mantenimiento / aviso en una sección."""
    conn = get_db()
    section = conn.execute(
        "SELECT * FROM sections WHERE code = ?;", (section_code,)
    ).fetchone()
    if not section:
        conn.close()
        return f"Sección no encontrada: {section_code}", 404

    technicians = conn.execute(
        "SELECT * FROM technicians WHERE active = 1 ORDER BY name;"
    ).fetchall()
    components = conn.execute(
        "SELECT * FROM components WHERE section_code = ? AND active = 1 ORDER BY name;",
        (section_code,),
    ).fetchall()

    if request.method == "POST":
        technician_id = request.form.get("technician_id") or None
        type_work = request.form.get("type")
        failure_type = request.form.get("failure_type") or None  # Tipo de falla
        component = request.form.get("component") or None
        description = request.form.get("description")
        downtime_min = request.form.get("downtime_min") or 0
        machine_stopped = 1 if request.form.get("machine_stopped") == "on" else 0

        # Aviso de desperfecto => pendiente; otros => resuelto por defecto
        if type_work == "Aviso de desperfecto":
            resolved = 0
        else:
            resolved = 1

        now = datetime.now().isoformat(timespec="minutes")

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO work_orders
            (section_id, technician_id, date, type, failure_type, component,
             description, downtime_min, machine_stopped, created_at, resolved)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                section["id"],
                technician_id,
                now,
                type_work,
                failure_type,
                component,
                description,
                int(downtime_min),
                machine_stopped,
                now,
                resolved,
            ),
        )
        work_order_id = cur.lastrowid

        # Adjuntos
        files = request.files.getlist("attachments")
        for f in files:
            if f and f.filename:
                filename = f.filename
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

                base, ext = os.path.splitext(filename)
                i = 1
                while os.path.exists(save_path):
                    filename = f"{base}_{i}{ext}"
                    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    i += 1

                f.save(save_path)

                cur.execute(
                    """
                    INSERT INTO attachments
                    (work_order_id, filename, mime_type, path, created_at)
                    VALUES (?,?,?,?,?)
                    """,
                    (work_order_id, filename, f.mimetype, save_path, now),
                )

        conn.commit()
        conn.close()
        return redirect(url_for("section_view", section_code=section_code))

    conn.close()
    return render_template(
        "new_work_order.html",
        section=section,
        technicians=technicians,
        components=components,
    )


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Servir archivos adjuntos (fotos / videos)."""
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# -------------------- Modo ADMIN --------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        password = request.form.get("password")
        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_home"))
        else:
            error = "Contraseña incorrecta."
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
@admin_required
def admin_logout():
    session.pop("is_admin", None)
    # IMPORTANTE: esta ruta existe y así evitamos el error 500
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin_home():
    conn = get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY name;").fetchall()
    technicians = conn.execute(
        "SELECT * FROM technicians ORDER BY name;"
    ).fetchall()
    conn.close()
    return render_template(
        "admin_home.html", sections=sections, technicians=technicians
    )


@app.route("/admin/technicians", methods=["GET", "POST"])
@admin_required
def admin_technicians():
    """Alta/baja de técnicos."""
    conn = get_db()
    cur = conn.cursor()

    deactivate_id = request.args.get("deactivate_id")
    if deactivate_id:
        cur.execute(
            "UPDATE technicians SET active = 0 WHERE id = ?;",
            (deactivate_id,),
        )
        conn.commit()

    if request.method == "POST":
        name = request.form.get("name")
        role = request.form.get("role")
        if name:
            cur.execute(
                """
                INSERT INTO technicians (name, role, active)
                VALUES (?, ?, 1)
                """,
                (name, role),
            )
            conn.commit()

    technicians = cur.execute(
        "SELECT * FROM technicians ORDER BY active DESC, name;"
    ).fetchall()
    conn.close()
    return render_template("admin_technicians.html", technicians=technicians)


@app.route("/admin/sections", methods=["GET", "POST"])
@admin_required
def admin_sections():
    """Gestionar máquinas / secciones (agregar y listar)."""
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()

        if code and name:
            cur.execute(
                """
                INSERT OR IGNORE INTO sections (code, name, description)
                VALUES (?, ?, ?)
                """,
                (code, name, description),
            )
            conn.commit()

    sections = cur.execute(
        "SELECT * FROM sections ORDER BY name;"
    ).fetchall()
    conn.close()
    return render_template("admin_sections.html", sections=sections)


@app.route("/admin/sections/<int:section_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_section(section_id):
    """Editar nombre/descripcion de una sección."""
    conn = get_db()
    cur = conn.cursor()

    section = cur.execute(
        "SELECT * FROM sections WHERE id = ?;", (section_id,)
    ).fetchone()
    if not section:
        conn.close()
        return f"Sección no encontrada (ID {section_id})", 404

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()

        if name:
            cur.execute(
                """
                UPDATE sections
                SET name = ?, description = ?
                WHERE id = ?
                """,
                (name, description, section_id),
            )
            conn.commit()
            conn.close()
            return redirect(url_for("admin_sections"))

    conn.close()
    return render_template("admin_edit_section.html", section=section)


@app.route("/admin/components/<section_code>", methods=["GET", "POST"])
@admin_required
def admin_components(section_code):
    """Configurar subpartes de una sección."""
    conn = get_db()
    cur = conn.cursor()

    section = cur.execute(
        "SELECT * FROM sections WHERE code = ?;", (section_code,)
    ).fetchone()
    if not section:
        conn.close()
        return f"Sección no encontrada: {section_code}", 404

    deactivate_id = request.args.get("deactivate_id")
    if deactivate_id:
        cur.execute(
            "UPDATE components SET active = 0 WHERE id = ?;",
            (deactivate_id,),
        )
        conn.commit()

    if request.method == "POST":
        name = request.form.get("name")
        if name:
            cur.execute(
                """
                INSERT INTO components (section_code, name, active)
                VALUES (?, ?, 1)
                """,
                (section_code, name),
            )
            conn.commit()

    components = cur.execute(
        """
        SELECT * FROM components
        WHERE section_code = ?
        ORDER BY active DESC, name;
        """,
        (section_code,),
    ).fetchall()

    conn.close()
    return render_template(
        "admin_components.html",
        section=section,
        components=components,
    )


@app.route("/admin/issues")
@admin_required
def admin_issues():
    """Avisos de desperfecto: pendientes y resueltos."""
    conn = get_db()
    cur = conn.cursor()

    pendientes = cur.execute(
        """
        SELECT w.*, s.name as section_name, t.name as technician_name
        FROM work_orders w
        JOIN sections s ON w.section_id = s.id
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.type = 'Aviso de desperfecto'
          AND (w.resolved IS NULL OR w.resolved = 0)
        ORDER BY w.date DESC;
        """
    ).fetchall()

    resueltos = cur.execute(
        """
        SELECT w.*, s.name as section_name, t.name as technician_name
        FROM work_orders w
        JOIN sections s ON w.section_id = s.id
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.type = 'Aviso de desperfecto'
          AND w.resolved = 1
        ORDER BY w.date DESC
        LIMIT 50;
        """
    ).fetchall()

    conn.close()
    return render_template(
        "admin_issues.html",
        pendientes=pendientes,
        resueltos=resueltos,
    )


@app.route("/admin/issues/<int:issue_id>/resolver", methods=["GET", "POST"])
@admin_required
def admin_resolve_issue(issue_id):
    """Marcar un aviso de desperfecto como resuelto + evidencia."""
    conn = get_db()
    cur = conn.cursor()

    issue = cur.execute(
        """
        SELECT w.*, s.name as section_name, t.name as technician_name
        FROM work_orders w
        JOIN sections s ON w.section_id = s.id
        LEFT JOIN technicians t ON w.technician_id = t.id
        WHERE w.id = ?
        """,
        (issue_id,),
    ).fetchone()
    if not issue:
        conn.close()
        return f"Aviso no encontrado (ID {issue_id})", 404

    if request.method == "POST":
        resolution_description = request.form.get("resolution_description")
        now = datetime.now().isoformat(timespec="minutes")

        cur.execute(
            """
            UPDATE work_orders
            SET resolved = 1,
                resolution_description = ?,
                resolution_at = ?
            WHERE id = ?
            """,
            (resolution_description, now, issue_id),
        )

        files = request.files.getlist("attachments")
        for f in files:
            if f and f.filename:
                filename = f.filename
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

                base, ext = os.path.splitext(filename)
                i = 1
                while os.path.exists(save_path):
                    filename = f"{base}_{i}{ext}"
                    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    i += 1

                f.save(save_path)

                cur.execute(
                    """
                    INSERT INTO attachments
                    (work_order_id, filename, mime_type, path, created_at)
                    VALUES (?,?,?,?,?)
                    """,
                    (issue_id, filename, f.mimetype, save_path, now),
                )

        conn.commit()
        conn.close()
        return redirect(url_for("admin_issues"))

    conn.close()
    return render_template("admin_resolve_issue.html", issue=issue)


# -------------------- API para generar QR --------------------
@app.route("/api/sections")
def api_sections():
    """API simple para que tu script local genere QR desde la BD."""
    key = request.args.get("key")
    if key != "123456":  # misma key que usas en generate_qr_from_api.py
        return {"error": "unauthorized"}, 401

    conn = get_db()
    cur = conn.cursor()
    sections = cur.execute(
        "SELECT id, code, name, description FROM sections ORDER BY name;"
    ).fetchall()
    conn.close()

    return [dict(row) for row in sections]


# -------------------- Main local --------------------
if __name__ == "__main__":
    init_db()
    seed_data()
    app.run(host="0.0.0.0", port=5000, debug=True)
