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

    # --- DEFAULT ADMIN CREATION ---
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        hashed_password = generate_password_hash('admin123')
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            ('admin', hashed_password, 'admin')
        )
    # -----------------------------

    conn.commit()
    conn.close()

def save_student(student_id, name, department):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT student_id FROM students WHERE student_id = %s", (student_id,))
    if cursor.fetchone():
        conn.close()
        return False, "Student ID cannot be same."

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
    
    if old_student_id != new_student_id:
        cursor.execute("SELECT student_id FROM students WHERE student_id = %s", (new_student_id,))
        if cursor.fetchone():
            conn.close()
            return False, "Student ID cannot be same (already exists for another student)."

    try:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        
        if old_student_id != new_student_id:
            cursor.execute("UPDATE attendance SET student_id = %s WHERE student_id = %s", (new_student_id, old_student_id))
            cursor.execute("UPDATE students SET student_id = %s, name = %s, department = %s WHERE student_id = %s", 
                           (new_student_id, name, department, old_student_id))
        else:
            cursor.execute("UPDATE students SET name = %s, department = %s WHERE student_id = %s", 
                           (name, department, old_student_id))
        
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        conn.commit()
        conn.close()
        return True, "Student updated successfully!"
        
    except Exception as e:
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
    
    cursor.execute(
        "SELECT id FROM attendance WHERE student_id = %s AND DATE(timestamp) = CURDATE()",
        (student_id,)
    )
    existing_entry = cursor.fetchone()

    if existing_entry:
        conn.close()
        return False, "Attendance already marked for today."

    current_time = datetime.now()
    cursor.execute(
        "INSERT INTO attendance (student_id, confidence, timestamp) VALUES (%s, %s, %s)",
        (student_id, float(confidence), current_time)
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

def get_attendance_by_date_range(start_date, end_date):
    """Fetch attendance logs between a start and end date."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT 
            a.id, 
            a.student_id, 
            s.name, 
            s.department, 
            a.timestamp,
            a.confidence
        FROM attendance a
        JOIN students s ON a.student_id = s.student_id
        WHERE DATE(a.timestamp) BETWEEN %s AND %s
        ORDER BY a.timestamp DESC
    """
    cursor.execute(query, (start_date, end_date))
    logs = cursor.fetchall()
    conn.close()
    
    for row in logs:
        val = row["timestamp"]
        if val and hasattr(val, "strftime"):
            row["timestamp"] = val.strftime('%Y-%m-%d %H:%M:%S')
        elif val:
            row["timestamp"] = str(val)
            
    return logs

def get_monthly_attendance_summary(year, month):
    """Fetch monthly summary grouped by student for a given year and month."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT 
            s.student_id, 
            s.name, 
            s.department, 
            COUNT(DISTINCT DATE(a.timestamp)) as total_present
        FROM students s
        LEFT JOIN attendance a ON s.student_id = a.student_id 
               AND YEAR(a.timestamp) = %s AND MONTH(a.timestamp) = %s
        GROUP BY s.student_id, s.name, s.department
        ORDER BY total_present DESC
    """
    cursor.execute(query, (year, month))
    summary = cursor.fetchall()
    conn.close()
    return summary

def get_dashboard_metrics():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT COUNT(*) as total FROM students")
    total_students = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(DISTINCT student_id) as count FROM attendance WHERE DATE(timestamp) = CURDATE()")
    today_attendance = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as registered FROM students WHERE face_encoding IS NOT NULL")
    registered_count = cursor.fetchone()['registered']
    
    system_accuracy = round((registered_count / total_students * 100), 1) if total_students > 0 else 0.0
    overall_rate = round((today_attendance / total_students * 100), 1) if total_students > 0 else 0.0
    
    conn.close()
    return {
        "total_students": total_students,
        "today_attendance": today_attendance,
        "system_accuracy": system_accuracy,
        "overall_rate": overall_rate
    }

def get_date_range_student_summary(start_date, end_date):
    """Fetch attendance summary grouped by student for a custom date range."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT 
            s.student_id, 
            s.name, 
            s.department, 
            COUNT(DISTINCT DATE(a.timestamp)) as total_present
        FROM students s
        JOIN attendance a ON s.student_id = a.student_id 
        WHERE DATE(a.timestamp) BETWEEN %s AND %s
        GROUP BY s.student_id, s.name, s.department
        ORDER BY total_present DESC
    """
    cursor.execute(query, (start_date, end_date))
    summary = cursor.fetchall()
    conn.close()
    return summary

def get_student_range_logs(student_id, start_date, end_date):
    """Fetch detailed logs for a specific student within a date range."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT timestamp, confidence 
        FROM attendance 
        WHERE student_id = %s AND DATE(timestamp) BETWEEN %s AND %s
        ORDER BY timestamp DESC
    """
    cursor.execute(query, (student_id, start_date, end_date))
    logs = cursor.fetchall()
    conn.close()
    
    for row in logs:
        val = row["timestamp"]
        if val and hasattr(val, "strftime"):
            row["timestamp"] = val.strftime('%Y-%m-%d %H:%M:%S')
        elif val:
            row["timestamp"] = str(val)
            
    return logs

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