import os
import mysql.connector
import json
from werkzeug.security import generate_password_hash
import numpy as np
from datetime import datetime

MYSQL_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'root123'),
    'database': os.environ.get('DB_NAME', 'face_attendance_new_db'),
    'port': int(os.environ.get('DB_PORT', 3306))
}

def get_db():
    return mysql.connector.connect(**MYSQL_CONFIG)

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) DEFAULT 'admin'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id VARCHAR(50) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            department VARCHAR(100) NOT NULL,
            face_encoding TEXT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INT AUTO_INCREMENT PRIMARY KEY,
            student_id VARCHAR(50) NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            confidence FLOAT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
        )
    """)

    # --- ADD THIS BLOCK TO CREATE DEFAULT ADMIN ---
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        hashed_password = generate_password_hash('admin123')
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            ('admin', hashed_password, 'admin')
        )
    # ---------------------------------------------

    conn.commit()
    conn.close()

def save_student(student_id, name, department):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Check if student ID already exists
    cursor.execute("SELECT student_id FROM students WHERE student_id = %s", (student_id,))
    if cursor.fetchone():
        conn.close()
        return False, "Student ID cannot be same."

    # Insert new student record
    try:
        cursor.execute(
            "INSERT INTO students (student_id, name, department) VALUES (%s, %s, %s)",
            (student_id, name, department)
        )
        conn.commit()
        conn.close()
        return True, "Student added successfully!"
    except Exception as e:
        conn.close()
        return False, f"Database Error: {str(e)}"

def update_student_with_id_change(old_student_id, new_student_id, name, department):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Check if the new ID already exists and belongs to a different student
    if old_student_id != new_student_id:
        cursor.execute("SELECT student_id FROM students WHERE student_id = %s", (new_student_id,))
        if cursor.fetchone():
            conn.close()
            return False, "Student ID cannot be same (already exists for another student)."

    try:
        # Temporarily disable foreign key checks to allow primary/foreign key migration
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        
        if old_student_id != new_student_id:
            # Update foreign key references in attendance table first
            cursor.execute("UPDATE attendance SET student_id = %s WHERE student_id = %s", (new_student_id, old_student_id))
            # Update student primary key and details in students table
            cursor.execute("UPDATE students SET student_id = %s, name = %s, department = %s WHERE student_id = %s", 
                           (new_student_id, name, department, old_student_id))
        else:
            # ID didn't change, just update name and department normally
            cursor.execute("UPDATE students SET name = %s, department = %s WHERE student_id = %s", 
                           (name, department, old_student_id))
        
        # Re-enable foreign key checks
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        conn.commit()
        conn.close()
        return True, "Student updated successfully!"
        
    except Exception as e:
        # Ensure foreign key checks are re-enabled even if an error occurs
        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        except:
            pass
        conn.close()
        return False, f"Database Error: {str(e)}"

def delete_student(student_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM students WHERE student_id = %s", (student_id,))
    conn.commit()
    conn.close()

def update_face_encoding(student_id, encoding_np):
    conn = get_db()
    cursor = conn.cursor()
    encoding_json = json.dumps(encoding_np.tolist())
    cursor.execute(
        "UPDATE students SET face_encoding = %s WHERE student_id = %s",
        (encoding_json, student_id)
    )
    conn.commit()
    conn.close()

def get_all_encodings():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT student_id, name, face_encoding FROM students WHERE face_encoding IS NOT NULL AND face_encoding != ''")
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        try:
            if row["face_encoding"]:
                encoding_data = json.loads(row["face_encoding"])
                result.append({
                    "student_id": row["student_id"],
                    "name": row["name"],
                    "encoding": np.array(encoding_data, dtype=np.float32)
                })
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Skipping invalid encoding for student {row['student_id']}: {e}")
            continue

    return result

def mark_attendance_if_allowed(student_id, confidence):
    """Enforces a strict limit of 1 attendance log per student per day."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Check if the student has already marked attendance today
    cursor.execute(
        "SELECT id FROM attendance WHERE student_id = %s AND DATE(timestamp) = CURDATE()",
        (student_id,)
    )
    existing_entry = cursor.fetchone()

    if existing_entry:
        conn.close()
        return False, "Attendance already marked for today."

    # Insert single attendance record for today
    cursor.execute(
        "INSERT INTO attendance (student_id, confidence, timestamp) VALUES (%s, %s, NOW())",
        (student_id, float(confidence))
    )
    conn.commit()
    conn.close()
    return True, "Attendance logged successfully!"

def get_daily_attendance_summary():
    """Returns total attendance counts grouped by date safely formatted for Jinja."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    query = """
        SELECT 
            DATE(a.timestamp) as attendance_date,
            COUNT(DISTINCT a.student_id) as total_present,
            COUNT(a.id) as total_scans
        FROM attendance a
        GROUP BY DATE(a.timestamp)
        ORDER BY attendance_date DESC
    """
    cursor.execute(query)
    summary = cursor.fetchall()
    conn.close()
    
    for row in summary:
        val = row["attendance_date"]
        if val:
            if hasattr(val, "strftime"):
                row["attendance_date"] = val.strftime('%Y-%m-%d')
            else:
                row["attendance_date"] = str(val)
        else:
            row["attendance_date"] = ""
            
    return summary

def get_dashboard_metrics():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Total enrolled students
    cursor.execute("SELECT COUNT(*) as total FROM students")
    total_students = cursor.fetchone()['total']
    
    # Today's unique attendance count
    cursor.execute("SELECT COUNT(DISTINCT student_id) as count FROM attendance WHERE DATE(timestamp) = CURDATE()")
    today_attendance = cursor.fetchone()['count']
    
    # Students with registered face profiles
    cursor.execute("SELECT COUNT(*) as registered FROM students WHERE face_encoding IS NOT NULL")
    registered_count = cursor.fetchone()['registered']
    
    # Calculate System Accuracy (% of students with registered face models)
    system_accuracy = round((registered_count / total_students * 100), 1) if total_students > 0 else 0.0
    
    # Calculate Overall Attendance Rate (% of total students present today)
    overall_rate = round((today_attendance / total_students * 100), 1) if total_students > 0 else 0.0
    
    conn.close()
    return {
        "total_students": total_students,
        "today_attendance": today_attendance,
        "system_accuracy": system_accuracy,
        "overall_rate": overall_rate
    }

def get_attendance_by_exact_date(selected_date):
    """Fetches detailed student logs for a specific day (YYYY-MM-DD)."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    query = """
        SELECT 
            a.id, 
            a.student_id, 
            s.name, 
            s.department, 
            TIME(a.timestamp) as log_time, 
            a.timestamp,
            a.confidence
        FROM attendance a
        JOIN students s ON a.student_id = s.student_id
        WHERE DATE(a.timestamp) = %s
        ORDER BY a.timestamp DESC
    """
    cursor.execute(query, (selected_date,))
    logs = cursor.fetchall()
    conn.close()
    return logs