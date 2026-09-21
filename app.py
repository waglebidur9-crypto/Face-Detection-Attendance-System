import base64
import csv
import io
import cv2
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, flash, render_template, request, jsonify, redirect, url_for, session, make_response
from werkzeug.security import check_password_hash, generate_password_hash
from database import (
    init_db, get_db, save_student, update_student_with_id_change, delete_student,
    update_face_encoding, get_all_encodings, mark_attendance_if_allowed,
    get_daily_attendance_summary, get_attendance_by_exact_date,
    get_attendance_by_date_range, get_dashboard_metrics, get_monthly_attendance_summary,
    get_date_range_student_summary, get_student_range_logs
)
from face_engine import FaceEngine

app = Flask(__name__)
app.secret_key = "super_secret_face_attendance_key"

# --- SAFE LAUNCH INITIALIZATION ---
# This prevents the app from crashing on boot if the cloud database DNS is temporarily unreachable
try:
    init_db()
    print("Database tables initialized successfully!")
except Exception as e:
    print(f"WARNING: Initial database migration deferred: {e}")

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
        print(f"Admin seeding deferred (Database offline/unreachable): {e}")

try:
    seed_default_admin()
except Exception:
    pass

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
        
        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            conn.close()

            if user and check_password_hash(user["password_hash"], password):
                session["user"] = user["username"]
                return redirect(url_for("dashboard"))
        except Exception as e:
            print(f"Login database error: {e}")
            
        return render_template("login.html", error="Invalid credentials or database offline")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))
    
    try:
        metrics = get_dashboard_metrics()
    except Exception as e:
        print(f"Dashboard metrics error: {e}")
        metrics = {"total_students": 0, "today_attendance": 0, "system_accuracy": 98.5, "overall_rate": 0}
    
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
    
    search_query = request.args.get('q', '').strip()
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        if search_query:
            # Search by name or student_id
            query = "SELECT * FROM students WHERE name LIKE %s OR student_id LIKE %s ORDER BY name ASC"
            like_term = f"%{search_query}%"
            cursor.execute(query, (like_term, like_term))
        else:
            cursor.execute("SELECT * FROM students ORDER BY name ASC")
            
        student_list = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Students fetch error: {e}")
        student_list = []
        
    return render_template("students.html", students=student_list, search_query=search_query)

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

    try:
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
    except Exception as e:
        flash(f"Database error: {str(e)}", "danger")
        return redirect(url_for("students"))

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
        
        # FAST OPTIMIZATION: Resize frame down for rapid backend processing (e.g., width 320)
        height, width = img.shape[:2]
        scale_factor = 1.0
        if width > 320:
            scale_factor = 320.0 / width
            new_width = 320
            new_height = int(height * scale_factor)
            img_small = cv2.resize(img, (new_width, new_height))
        else:
            img_small = img

        faces = engine.detect_and_align(img_small)
        if faces is None or len(faces) == 0:
            return jsonify({"status": "no_face", "faces": []})

        known_faces = get_all_encodings()
        results = []

        for face in faces:
            # Extract features on the downscaled frame for instant matching
            features = engine.extract_features(img_small, face)
            match, score = engine.match_face(features, known_faces)
            
            # Scale box coordinates back up to original resolution if needed
            box_coords = face[:4]
            if scale_factor != 1.0:
                box_coords = [int(coord / scale_factor) for coord in box_coords]
            else:
                box_coords = box_coords.astype(int).tolist()

            if match:
                results.append({
                    "box": box_coords,
                    "name": match["name"],
                    "student_id": match["student_id"],
                    "score": round(float(score) * 100, 1)
                })
            else:
                results.append({
                    "box": box_coords,
                    "name": "Unknown",
                    "student_id": None,
                    "score": 0.0
                })

        return jsonify({"status": "success", "faces": results})

    except Exception as e:
        print(f"Frame processing error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/manual_attendance", methods=["POST"])
def manual_attendance_api():
    if "user" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    try:
        data = request.get_json()
        student_id = data.get("student_id")
        password = data.get("password")
        
        if not student_id or not password:
            return jsonify({"success": False, "message": "Missing student ID or password"}), 400

        username = session["user"]
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"success": False, "message": "Incorrect password. Override denied."}), 400

        marked, msg = mark_attendance_if_allowed(student_id, 1.0)
        if marked:
            return jsonify({"success": True, "message": "Manual attendance marked successfully!"})
        else:
            return jsonify({"success": False, "message": msg})
            
    except Exception as e:
        if "1452" in str(e) or "foreign key constraint fails" in str(e).lower():
            return jsonify({"success": False, "message": "Error: This Student ID is not registered in the database. Please register the student first."}), 400
        return jsonify({"success": False, "message": str(e)}), 500
    
@app.route("/api/mark_attendance", methods=["POST"])
def mark_attendance_api():
    try:
        data = request.get_json()
        student_id = data.get("student_id")
        confidence = data.get("confidence", 1.0)
        
        if not student_id:
            return jsonify({"success": False, "message": "Missing student ID"}), 400

        marked, msg = mark_attendance_if_allowed(student_id, confidence)
        return jsonify({"success": marked, "message": msg})
    except Exception as e:
        if "1452" in str(e) or "foreign key constraint fails" in str(e).lower():
            return jsonify({"success": False, "message": "Error: This Student ID is not registered in the database. Please register the student first."}), 400
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/import_students", methods=["POST"])
def import_students_api():
    if "user" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "message": "No selected file"}), 400
    
    if not file.filename.endswith('.csv'):
        return jsonify({"success": False, "message": "Please upload a valid CSV file"}), 400

    try:
        stream = io.TextIOWrapper(file.stream, encoding="utf-8")
        reader = csv.DictReader(stream)
        
        conn = get_db()
        cursor = conn.cursor()
        
        imported_count = 0
        for row in reader:
            student_id = row.get("student_id", "").strip()
            name = row.get("name", "").strip()
            department = row.get("department", "").strip()
            
            if student_id and name:
                cursor.execute("""
                    INSERT INTO students (student_id, name, department) 
                    VALUES (%s, %s, %s) 
                    ON DUPLICATE KEY UPDATE name=%s, department=%s
                """, (student_id, name, department, name, department))
                imported_count += 1
                
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": f"Successfully imported {imported_count} student(s)!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error parsing CSV: {str(e)}"}), 500

@app.route("/api/student_range_logs", methods=["GET"])
def api_student_range_logs():
    if "user" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    student_id = request.args.get("student_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    
    if not student_id or not start_date or not end_date:
        return jsonify({"success": False, "message": "Missing parameters"}), 400
        
    logs = get_student_range_logs(student_id, start_date, end_date)
    return jsonify({"success": True, "logs": logs})

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

@app.route("/reports", methods=["GET"])
def reports():
    if "user" not in session:
        return redirect(url_for("login"))
    
    report_type = request.args.get("type", "daily")
    
    data = []
    selected_start = request.args.get("start_date", datetime.now().strftime('%Y-%m-%d'))
    selected_end = request.args.get("end_date", datetime.now().strftime('%Y-%m-%d'))
    
    current_year = datetime.now().year
    current_month = datetime.now().month

    try:
        selected_year = int(request.args.get("year", current_year))
    except (TypeError, ValueError):
        selected_year = current_year

    try:
        selected_month = int(request.args.get("month", current_month))
    except (TypeError, ValueError):
        selected_month = current_month

    if report_type == "range":
        data = get_date_range_student_summary(selected_start, selected_end)
    elif report_type == "monthly":
        data = get_monthly_attendance_summary(selected_year, selected_month)
    else:
        data = get_daily_attendance_summary()

    return render_template(
        "reports.html",
        report_type=report_type,
        data=data,
        start_date=selected_start,
        end_date=selected_end,
        year=selected_year,
        month=selected_month
    )

@app.route('/api/attendance_trend')
def attendance_trend():
    try:
        today = datetime.now().date()
        dates = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
        date_labels = [d.strftime('%b %d') for d in dates]

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT DISTINCT department FROM students WHERE department IS NOT NULL AND department != ''")
        dept_rows = cursor.fetchall()
        departments = [row['department'] for row in dept_rows]
        if not departments:
            departments = ["General"]

        datasets = []
        colors = ['#58a6ff', '#238636', '#f0883e', '#a371f7', '#db6d28', '#3fb950']

        for index, dept in enumerate(departments):
            dept_data = []
            for d in dates:
                date_str = d.strftime('%Y-%m-%d')
                cursor.execute("""
                    SELECT COUNT(*) as cnt 
                    FROM attendance a 
                    JOIN students s ON a.student_id = s.student_id 
                    WHERE s.department = %s AND DATE(a.timestamp) = %s
                """, (dept, date_str))
                row = cursor.fetchone()
                count = row['cnt'] if row else 0
                dept_data.append(count)

            color = colors[index % len(colors)]
            datasets.append({
                "label": dept,
                "data": dept_data,
                "borderColor": color,
                "backgroundColor": color,
                "borderWidth": 2,
                "fill": False,
                "tension": 0.2
            })

        conn.close()

        return jsonify({
            "success": True,
            "labels": date_labels,
            "datasets": datasets
        })
    except Exception as e:
        print(f"Error fetching department trend: {e}")
        return jsonify({
            "success": True, 
            "labels": ["Today"], 
            "datasets": [{
                "label": "General", 
                "data": [0], 
                "borderColor": "#58a6ff", 
                "borderWidth": 2, 
                "fill": False
            }]
        })

@app.route("/export_report", methods=["GET"])
def export_report():
    if "user" not in session:
        return redirect(url_for("login"))
    
    report_type = request.args.get("type", "daily")
    selected_start = request.args.get("start_date", datetime.now().strftime('%Y-%m-%d'))
    selected_end = request.args.get("end_date", datetime.now().strftime('%Y-%m-%d'))
    
    current_year = datetime.now().year
    current_month = datetime.now().month
    
    try:
        selected_year = int(request.args.get("year", current_year))
    except (TypeError, ValueError):
        selected_year = current_year

    try:
        selected_month = int(request.args.get("month", current_month))
    except (TypeError, ValueError):
        selected_month = current_month

    if report_type == "range":
        data = get_date_range_student_summary(selected_start, selected_end)
        filename = f"attendance_range_{selected_start}_to_{selected_end}.csv"
        fieldnames = ["student_id", "name", "department", "total_present"]
    elif report_type == "monthly":
        data = get_monthly_attendance_summary(selected_year, selected_month)
        filename = f"attendance_monthly_{selected_year}_{selected_month}.csv"
        fieldnames = ["student_id", "name", "department", "total_present"]
    else:
        data = get_daily_attendance_summary()
        filename = "attendance_daily_summary.csv"
        fieldnames = ["attendance_date", "total_present", "total_scans"]

    si = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for row in data:
        writer.writerow(row)
        
    output = si.getvalue()
    response = make_response(output)
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    response.headers["Content-Type"] = "text/csv"
    return response

# --- GLOBAL ERROR HANDLERS ---

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