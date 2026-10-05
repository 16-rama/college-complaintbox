import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this-secret-key")

# Render persistent disks can be mounted at /var/data.
# Locally, the database remains in the project folder.
DATABASE = os.environ.get("DATABASE_PATH", str(Path(__file__).resolve().parent / "complaints.db"))

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


def get_db():
    db_path = Path(DATABASE)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll_no TEXT NOT NULL UNIQUE,
            department TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS complaints (
            complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT NOT NULL,
            date TEXT NOT NULL,
            priority TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            admin_response TEXT DEFAULT '',
            FOREIGN KEY(student_id) REFERENCES students(student_id)
        )
    """)
    conn.commit()
    conn.close()


def student_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "student_id" not in session:
            flash("Please login first.")
            return redirect(url_for("login"))
        return function(*args, **kwargs)

    return wrapper


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            flash("Admin login required.")
            return redirect(url_for("admin_login"))
        return function(*args, **kwargs)

    return wrapper


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        roll_no = request.form["roll_no"].strip()
        department = request.form["department"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if not all([name, roll_no, department, email, password]):
            flash("All fields are required.")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO students
                (name, roll_no, department, email, password)
                VALUES (?, ?, ?, ?, ?)
            """, (name, roll_no, department, email, hashed_password))
            conn.commit()
            flash("Registration successful. Please login.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Roll number or email already exists.")
            return redirect(url_for("register"))
        finally:
            conn.close()
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        conn = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE email = ?", (email,)
        ).fetchone()
        conn.close()
        if student and check_password_hash(student["password"], password):
            session["student_id"] = student["student_id"]
            session["student_name"] = student["name"]
            return redirect(url_for("student_dashboard"))
        flash("Invalid email or password.")
    return render_template("login.html")


@app.route("/student/dashboard")
@student_required
def student_dashboard():
    student_id = session["student_id"]
    conn = get_db()
    complaints = conn.execute("""
        SELECT * FROM complaints
        WHERE student_id = ?
        ORDER BY complaint_id DESC
    """, (student_id,)).fetchall()
    conn.close()
    total = len(complaints)
    pending = sum(c["status"] == "Pending" for c in complaints)
    progress = sum(c["status"] == "In Progress" for c in complaints)
    resolved = sum(c["status"] == "Resolved" for c in complaints)
    return render_template("student_dashboard.html",
                           complaints=complaints, total=total,
                           pending=pending, progress=progress, resolved=resolved)


@app.route("/complaint", methods=["GET", "POST"])
@student_required
def complaint():
    if request.method == "POST":
        title = request.form["title"].strip()
        category = request.form["category"].strip()
        description = request.form["description"].strip()
        location = request.form["location"].strip()

        if not all([title, category, description, location]):
            flash("All complaint fields are required.")
            return redirect(url_for("complaint"))

        if category == "Internet/Wi-Fi" and "entire" in location.lower():
            priority = "HIGH"
        elif category in ["Computer", "Electrical", "Equipment"]:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        date = datetime.now().strftime("%d-%m-%Y %H:%M")
        conn = get_db()
        conn.execute("""
            INSERT INTO complaints
            (student_id, title, category, description, location, date, priority, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session["student_id"], title, category, description,
              location, date, priority, "Pending"))
        conn.commit()
        conn.close()
        flash("Complaint submitted successfully.")
        return redirect(url_for("my_complaints"))
    return render_template("complaint.html")


@app.route("/my-complaints")
@student_required
def my_complaints():
    conn = get_db()
    complaints = conn.execute("""
        SELECT * FROM complaints
        WHERE student_id = ?
        ORDER BY complaint_id DESC
    """, (session["student_id"],)).fetchall()
    conn.close()
    return render_template("my_complaints.html", complaints=complaints)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form["username"] == ADMIN_USERNAME and request.form["password"] == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin username or password.")
    return render_template("admin_login.html")


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    search = request.args.get("search", "")
    status_filter = request.args.get("status", "")
    conn = get_db()
    query = """
        SELECT complaints.*, students.name, students.roll_no,
               students.department, students.email
        FROM complaints
        JOIN students ON complaints.student_id = students.student_id
        WHERE 1=1
    """
    parameters = []
    if search:
        query += """ AND (
            complaints.title LIKE ? OR complaints.category LIKE ?
            OR students.name LIKE ? OR students.roll_no LIKE ?
        )"""
        value = "%" + search + "%"
        parameters.extend([value, value, value, value])
    if status_filter:
        query += " AND complaints.status = ?"
        parameters.append(status_filter)
    query += " ORDER BY complaints.complaint_id DESC"
    complaints = conn.execute(query, parameters).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM complaints WHERE status='Pending'").fetchone()[0]
    progress = conn.execute("SELECT COUNT(*) FROM complaints WHERE status='In Progress'").fetchone()[0]
    resolved = conn.execute("SELECT COUNT(*) FROM complaints WHERE status='Resolved'").fetchone()[0]
    conn.close()
    return render_template("admin_dashboard.html",
                           complaints=complaints, total=total, pending=pending,
                           progress=progress, resolved=resolved,
                           search=search, status_filter=status_filter)


@app.route("/admin/update/<int:complaint_id>", methods=["POST"])
@admin_required
def update_complaint(complaint_id):
    status = request.form["status"]
    response = request.form["admin_response"]
    conn = get_db()
    conn.execute("""
        UPDATE complaints SET status = ?, admin_response = ?
        WHERE complaint_id = ?
    """, (status, response, complaint_id))
    conn.commit()
    conn.close()
    flash("Complaint updated successfully.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete/<int:complaint_id>", methods=["POST"])
@admin_required
def delete_complaint(complaint_id):
    conn = get_db()
    conn.execute("DELETE FROM complaints WHERE complaint_id = ?", (complaint_id,))
    conn.commit()
    conn.close()
    flash("Complaint deleted.")
    return redirect(url_for("admin_dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.")
    return redirect(url_for("index"))


# Gunicorn imports this module, so initialize the database at import time.
init_db()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
