import base64
import csv
import io
import cv2
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, flash, render_template, request, jsonify, redirect, url_for, session, make_response
from werkzeug.security import check_password_hash, generate_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import requests
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

# --- GLOBAL MEMORY CACHE FOR LIGHTNING-FAST RECOGNITION ---
CACHED_KNOWN_FACES = []

def refresh_face_cache():
    """Loads all student face encodings from MySQL into RAM once to avoid database query lag per frame."""
    global CACHED_KNOWN_FACES
    try:
        CACHED_KNOWN_FACES = get_all_encodings()
        print(f"[INFO] Face encoding cache refreshed: {len(CACHED_KNOWN_FACES)} student(s) loaded into RAM.")
    except Exception as e:
        print(f"[WARNING] Failed to refresh face cache: {e}")
        CACHED_KNOWN_FACES = []

# --- SAFE LAUNCH INITIALIZATION ---
try:
    init_db()
    print("Database tables initialized successfully!")
    refresh_face_cache()  # Populate cache on boot
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

# --- AUTOMATIC STUDENT ID GENERATOR ---
def generate_student_id(department):
    clean_dept = ''.join(filter(str.isalnum, department)).upper()
    prefix = clean_dept[:4] if clean_dept else "STU"
    
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        seq = 1
        while True:
            cursor.execute("SELECT COUNT(*) as count FROM students WHERE department = %s", (department,))
            res = cursor.fetchone()
            base_count = (res['count'] if res else 0) + seq
            candidate_id = f"{prefix}-{1000 + base_count}"
            
            cursor.execute("SELECT 1 FROM students WHERE student_id = %s", (candidate_id,))
            if not cursor.fetchone():
                conn.close()
                return candidate_id
            seq += 1
    except Exception as e:
        print(f"Error generating student ID: {e}")
        return f"{prefix}-1001"

# --- SMS & 10:00 AM ABSENT CHECK LOGIC ---
def log_sms(student_id, phone_number, message, status):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sms_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                student_id VARCHAR(50),
                phone_number VARCHAR(20),
                message TEXT,
                status VARCHAR(50),
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            INSERT INTO sms_logs (student_id, phone_number, message, status) 
            VALUES (%s, %s, %s, %s)
        """, (student_id, phone_number, message, status))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error recording SMS log: {e}")

def send_sms(phone_number, message, student_id=None):
    if not phone_number:
        return False
    try:
        api_url = "https://api.sms-provider.com/v1/send"
        payload = {
            'token': 'YOUR_API_KEY',
            'to': phone_number,
            'message': message
        }
        response = requests.post(api_url, data=payload, timeout=5)
        success = (response.status_code == 200)
        
        status_str = "Success" if success else "Failed"
        log_sms(student_id, phone_number, message, status_str)
        
        return success
    except Exception as e:
        print(f"SMS sending error to {phone_number}: {e}")
        log_sms(student_id, phone_number, message, "Failed")
        return False

def check_absent_students_at_10am():
    with app.app_context():
        today_dt = datetime.now()
        today_str = today_dt.strftime('%Y-%m-%d')
        
        # 1. Skip if today is Saturday (Nepal's official weekend)
        # In Python's weekday(): Monday is 0, Saturday is 5, Sunday is 6.
        if today_dt.weekday() == 5:
            print("[INFO] Today is Saturday (Weekend). Skipping absent SMS check.")
            return

        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True)
            
            # Ensure holidays table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS holidays (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    holiday_date DATE UNIQUE,
                    description VARCHAR(255)
                )
            """)
            
            # 2. Skip if today is registered as a public holiday
            cursor.execute("SELECT * FROM holidays WHERE holiday_date = %s", (today_str,))
            holiday = cursor.fetchone()
            if holiday:
                print(f"[INFO] Today is a public holiday ({holiday['description']}). Skipping absent SMS check.")
                conn.close()
                return

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leaves (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id VARCHAR(50),
                    leave_date DATE,
                    reason VARCHAR(255),
                    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
                )
            """)
            
            cursor.execute("SELECT * FROM students")
            students = cursor.fetchall()
            
            for student in students:
                s_id = student["student_id"]
                
                cursor.execute("SELECT * FROM attendance WHERE student_id = %s AND DATE(timestamp) = %s", (s_id, today_str))
                attendance = cursor.fetchone()
                
                cursor.execute("SELECT * FROM leaves WHERE student_id = %s AND leave_date = %s", (s_id, today_str))
                leave = cursor.fetchone()
                
                if not attendance and not leave:
                    msg = f"Alert: Dear Parent, your child {student['name']} is absent from college today without prior notice."
                    
                    if student.get("phone"):
                        send_sms(student["phone"], msg, student_id=s_id)
                    if student.get("parent_phone"):
                        send_sms(student["parent_phone"], msg, student_id=s_id)
            
            conn.close()
            print("[INFO] 10:00 AM absent SMS check executed successfully.")
        except Exception as e:
            print(f"[ERROR] Failed during 10:00 AM absent check: {e}")

@app.route("/holidays", methods=["GET", "POST"])
def holidays():
    if "user" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Ensure table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holidays (
            id INT AUTO_INCREMENT PRIMARY KEY,
            holiday_date DATE UNIQUE,
            description VARCHAR(255)
        )
    """)
    
    if request.method == "POST":
        holiday_date = request.form.get("holiday_date")
        description = request.form.get("description", "").strip()
        
        if holiday_date:
            try:
                cursor.execute("""
                    INSERT INTO holidays (holiday_date, description) 
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE description=%s
                """, (holiday_date, description, description))
                conn.commit()
                flash("Public holiday added successfully!", "success")
            except Exception as e:
                flash(f"Error adding holiday: {e}", "danger")
        return redirect(url_for("holidays"))
        
    cursor.execute("SELECT * FROM holidays ORDER BY holiday_date DESC")
    holiday_list = cursor.fetchall()
    conn.close()
    
    return render_template("holidays.html", holidays=holiday_list)

@app.route("/delete_holiday/<int:holiday_id>", methods=["POST"])
def delete_holiday(holiday_id):
    if "user" not in session:
        return redirect(url_for("login"))
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM holidays WHERE id = %s", (holiday_id,))
        conn.commit()
        conn.close()
        flash("Holiday removed successfully.", "success")
    except Exception as e:
        flash(f"Error removing holiday: {e}", "danger")
    return redirect(url_for("holidays"))

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
        name = request.form.get("name").strip()
        department = request.form.get("department").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        parent_name = request.form.get("parent_name", "").strip()
        parent_phone = request.form.get("parent_phone", "").strip()

        # --- 10-DIGIT VALIDATION CHECK ---
        if phone and (not phone.isdigit() or len(phone) != 10):
            flash("Phone number must be exactly 10 digits.", "danger")
            return render_template("add_student.html", name=name, department=department,
                                 phone=phone, address=address, parent_name=parent_name, parent_phone=parent_phone)
        
        if parent_phone and (not parent_phone.isdigit() or len(parent_phone) != 10):
            flash("Parent's phone number must be exactly 10 digits.", "danger")
            return render_template("add_student.html", name=name, department=department,
                                 phone=phone, address=address, parent_name=parent_name, parent_phone=parent_phone)
        # ---------------------------------

        # Generate Student ID automatically based on department
        student_id = generate_student_id(department)

        success, message = save_student(student_id, name, department, phone, address, parent_name, parent_phone)

        if not success:
            flash(message, "danger")
            return render_template("add_student.html", name=name, department=department,
                                 phone=phone, address=address, parent_name=parent_name, parent_phone=parent_phone)

        refresh_face_cache()
        flash(f"Student added successfully with ID: {student_id}", "success")
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
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        parent_name = request.form.get("parent_name", "").strip()
        parent_phone = request.form.get("parent_phone", "").strip()
        
        if phone and (not phone.isdigit() or len(phone) != 10):
            flash("Phone number must be exactly 10 digits.", "danger")
            cursor.execute("SELECT * FROM students WHERE student_id = %s", (student_id,))
            student = cursor.fetchone()
            conn.close()
            return render_template("edit_student.html", student=student)
        
        if parent_phone and (not parent_phone.isdigit() or len(parent_phone) != 10):
            flash("Parent's phone number must be exactly 10 digits.", "danger")
            cursor.execute("SELECT * FROM students WHERE student_id = %s", (student_id,))
            student = cursor.fetchone()
            conn.close()
            return render_template("edit_student.html", student=student)

        success, message = update_student_with_id_change(
            student_id, new_student_id, name, department, phone, address, parent_name, parent_phone
        )
        
        conn.close()

        if not success:
            flash(message, "danger")
            return redirect(url_for("edit_student", student_id=student_id))

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
    refresh_face_cache()
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
        refresh_face_cache()
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
        
        height, width = img.shape[:2]
        scale_factor = 320.0 / width if width > 320 else 1.0
        img_small = cv2.resize(img, (320, int(height * scale_factor))) if width > 320 else img

        faces = engine.detect_and_align(img_small)
        if faces is None or len(faces) == 0:
            return jsonify({"status": "no_face", "faces": []})

        global CACHED_KNOWN_FACES
        results = []

        for face in faces:
            features = engine.extract_features(img_small, face)
            match, score = engine.match_face(features, CACHED_KNOWN_FACES)
            
            box_coords = face[:4]
            if scale_factor != 1.0:
                box_coords = [int(coord / scale_factor) for coord in box_coords]
            else:
                box_coords = box_coords.astype(int).tolist()

            if match:
                student_id = match["student_id"]
                confidence = float(score)
                
                marked, msg = mark_attendance_if_allowed(student_id, confidence)
                
                results.append({
                    "box": box_coords,
                    "name": match["name"],
                    "student_id": student_id,
                    "score": round(confidence * 100, 1),
                    "attendance_status": msg
                })
            else:
                results.append({
                    "box": box_coords,
                    "name": "Unknown",
                    "student_id": None,
                    "score": 0.0,
                    "attendance_status": "Unknown face"
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
            phone = row.get("phone", "").strip()
            address = row.get("address", "").strip()
            parent_name = row.get("parent_name", "").strip()
            parent_phone = row.get("parent_phone", "").strip()
            
            if student_id and name:
                cursor.execute("""
                    INSERT INTO students (student_id, name, department, phone, address, parent_name, parent_phone) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s) 
                    ON DUPLICATE KEY UPDATE name=%s, department=%s, phone=%s, address=%s, parent_name=%s, parent_phone=%s
                """, (student_id, name, department, phone, address, parent_name, parent_phone, 
                      name, department, phone, address, parent_name, parent_phone))
                imported_count += 1
                
        conn.commit()
        conn.close()
        
        refresh_face_cache()
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

@app.route("/add_leave", methods=["GET", "POST"])
def add_leave():
    if "user" not in session:
        return redirect(url_for("login"))
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    if request.method == "POST":
        student_id = request.form.get("student_id")
        leave_date = request.form.get("leave_date")
        reason = request.form.get("reason", "").strip()
        
        if not student_id or not leave_date:
            flash("Student ID and Leave Date are required.", "danger")
        else:
            try:
                cursor.execute("""
                    INSERT INTO leaves (student_id, leave_date, reason) 
                    VALUES (%s, %s, %s)
                """, (student_id, leave_date, reason))
                conn.commit()
                flash("Pre-informed leave registered successfully! SMS notifications will be skipped for this student on that day.", "success")
                conn.close()
                return redirect(url_for("students"))
            except Exception as e:
                flash(f"Error recording leave: {str(e)}", "danger")
    
    cursor.execute("SELECT student_id, name, department FROM students ORDER BY name ASC")
    students = cursor.fetchall()
    conn.close()
    
    return render_template("leave.html", students=students)

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

@app.route("/leaves_list")
def leaves_list():
    if "user" not in session:
        return redirect(url_for("login"))
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT l.id, l.student_id, s.name, s.department, l.leave_date, l.reason 
            FROM leaves l
            JOIN students s ON l.student_id = s.student_id
            ORDER BY l.leave_date DESC
        """)
        leaves = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Error fetching leaves: {e}")
        leaves = []
    
    return render_template("leaves_list.html", leaves=leaves)

@app.route("/delete_leave/<int:leave_id>", methods=["POST"])
def delete_leave(leave_id):
    if "user" not in session:
        return redirect(url_for("login"))
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM leaves WHERE id = %s", (leave_id,))
        conn.commit()
        conn.close()
        flash("Leave record removed successfully.", "success")
    except Exception as e:
        flash(f"Error deleting leave record: {e}", "danger")
    return redirect(url_for("leaves_list"))

@app.route("/sms_history")
def sms_history():
    if "user" not in session:
        return redirect(url_for("login"))
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT l.id, l.student_id, s.name, s.department, l.phone_number, l.message, l.status, l.timestamp 
            FROM sms_logs l
            LEFT JOIN students s ON l.student_id = s.student_id
            ORDER BY l.timestamp DESC
        """)
        logs = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Error fetching SMS history: {e}")
        logs = []
    
    return render_template("sms_history.html", logs=logs)

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

@app.errorhandler(404)
def not_nested_error(error):
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