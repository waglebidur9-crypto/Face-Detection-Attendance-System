import base64
import cv2
import numpy as np
from datetime import datetime
from flask import Flask, flash, render_template, request, jsonify, redirect, url_for, session
from werkzeug.security import check_password_hash, generate_password_hash
from database import get_dashboard_metrics
from database import (
    init_db, get_db, save_student, update_student_with_id_change, delete_student,
    update_face_encoding, get_all_encodings, mark_attendance_if_allowed,
    get_daily_attendance_summary, get_attendance_by_exact_date
)
from face_engine import FaceEngine

app = Flask(__name__)
app.secret_key = "super_secret_face_attendance_key"

# Initialize database tables
init_db()

# Automatically seed default admin account if not already present
def seed_default_admin():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = 'admin'")
        if not cursor.fetchone():
            hashed_pw = generate_password_hash("admin123")
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                ("admin", hashed_pw, "admin")
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Admin seeding error: {e}")

seed_default_admin()
engine = FaceEngine()

def base64_to_image(base64_string):
    if not base64_string:
        return None
    try:
        if "," in base64_string:
            base64_string = base64_string.split(",")[1]
        img_data = base64.b64decode(base64_string)
        if not img_data:
            return None
        nparr = np.frombuffer(img_data, np.uint8)
        if nparr.size == 0:
            return None
        return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"Base64 decode exception: {e}")
        return None

@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user"] = user["username"]
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))
    
    metrics = get_dashboard_metrics()
    
    return render_template(
        "dashboard.html", 
        total_students=metrics["total_students"], 
        today_attendance=metrics["today_attendance"],
        system_accuracy=metrics["system_accuracy"],
        overall_rate=metrics["overall_rate"]
    )

@app.route("/recognize")
def recognize():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("recognize.html")

@app.route("/students")
def students():
    if "user" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM students")
    student_list = cursor.fetchall()
    conn.close()
    return render_template("students.html", students=student_list)

@app.route("/add_student", methods=["GET", "POST"])
def add_student():
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        student_id = request.form.get("student_id").strip()
        name = request.form.get("name").strip()
        department = request.form.get("department").strip()

        success, message = save_student(student_id, name, department)

        if not success:
            flash(message, "danger")
            return render_template("add_student.html", student_id=student_id, name=name, department=department)

        flash(message, "success")
        return redirect(url_for("students"))

    return render_template("add_student.html")

@app.route("/edit_student/<student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    if "user" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    if request.method == "POST":
        new_student_id = request.form.get("student_id").strip()
        name = request.form.get("name").strip()
        department = request.form.get("department").strip()
        
        success, message = update_student_with_id_change(student_id, new_student_id, name, department)
        
        if not success:
            flash(message, "danger")
            cursor.execute("SELECT * FROM students WHERE student_id = %s", (student_id,))
            student = cursor.fetchone()
            conn.close()
            return render_template("edit_student.html", student=student)

        flash(message, "success")
        return redirect(url_for("students"))

    cursor.execute("SELECT * FROM students WHERE student_id = %s", (student_id,))
    student = cursor.fetchone()
    conn.close()

    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("students"))

    return render_template("edit_student.html", student=student)

@app.route("/delete_student/<student_id>", methods=["POST"])
def remove_student(student_id):
    if "user" not in session:
        return redirect(url_for("login"))
    
    delete_student(student_id)
    flash("Student deleted successfully!", "success")
    return redirect(url_for("students"))

@app.route("/face_register/<student_id>")
def face_register(student_id):
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("face_register.html", student_id=student_id)

@app.route("/api/register_face", methods=["POST"])
def api_register_face():
    try:
        data = request.get_json()
        student_id = data.get("student_id")
        image_data = data.get("image")
        
        img = base64_to_image(image_data)
        if img is None:
            return jsonify({"success": False, "message": "Failed to decode camera frame."})

        faces = engine.detect_and_align(img)
        
        if faces is None or len(faces) == 0:
            return jsonify({"success": False, "message": "No face detected in image."})
        if len(faces) > 1:
            return jsonify({"success": False, "message": "Multiple faces detected. Ensure only one face is visible."})

        features = engine.extract_features(img, faces[0])
        update_face_encoding(student_id, features)
        return jsonify({"success": True, "message": "Face registered successfully!"})
    except Exception as e:
        print(f"Face Registration Error: {e}")
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

@app.route("/api/process_frame", methods=["POST"])
def process_frame():
    try:
        data = request.get_json()
        if not data or "image" not in data:
            return jsonify({"status": "error", "message": "No image payload provided"}), 400

        img = base64_to_image(data.get("image"))
        if img is None:
            return jsonify({"status": "error", "message": "Invalid image data"}), 400
        
        faces = engine.detect_and_align(img)
        if faces is None or len(faces) == 0:
            return jsonify({"status": "no_face", "faces": []})

        known_faces = get_all_encodings()
        results = []

        for face in faces:
            features = engine.extract_features(img, face)
            match, score = engine.match_face(features, known_faces)
            
            box = face[:4].astype(int).tolist()
            if match:
                results.append({
                    "box": box,
                    "name": match["name"],
                    "student_id": match["student_id"],
                    "score": round(float(score) * 100, 1)
                })
            else:
                results.append({
                    "box": box,
                    "name": "Unknown",
                    "student_id": None,
                    "score": 0.0
                })

        return jsonify({"status": "success", "faces": results})

    except Exception as e:
        print(f"Frame processing error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/mark_attendance", methods=["POST"])
def mark_attendance_api():
    try:
        data = request.get_json()
        student_id = data.get("student_id")
        if not student_id:
            return jsonify({"success": False, "message": "Missing student ID"}), 400

        marked, msg = mark_attendance_if_allowed(student_id, 1.0)
        return jsonify({"success": marked, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/attendance")
def attendance():
    if "user" not in session:
        return redirect(url_for("login"))
    
    selected_date = request.args.get("date", datetime.now().strftime('%Y-%m-%d'))
    daily_logs = get_attendance_by_exact_date(selected_date)
    daily_summary = get_daily_attendance_summary()
    
    return render_template(
        "attendance.html", 
        logs=daily_logs, 
        selected_date=selected_date,
        summary=daily_summary
    )

@app.route("/reports")
def reports():
    if "user" not in session:
        return redirect(url_for("login"))
    
    daily_summary = get_daily_attendance_summary()
    return render_template("reports.html", summary=daily_summary)

# --- GLOBAL ERROR HANDLERS TO PREVENT FULL CRASHES ---

@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "message": "API endpoint not found."}), 404
    return render_template("login.html", error="The page you are looking for does not exist."), 404

@app.errorhandler(500)
def internal_error(error):
    print(f"Server 500 Error: {error}")
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "message": "A server error occurred while processing your request. Please try again."}), 500
    return render_template("login.html", error="An internal server error occurred. Please try again later."), 500

@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    print(f"Unhandled Exception: {error}")
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "message": f"Unexpected error: {str(error)}"}), 500
    return render_template("login.html", error="Something went wrong. Please try again."), 500
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)