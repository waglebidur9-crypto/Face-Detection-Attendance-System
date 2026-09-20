import os
import random
import mysql.connector
from datetime import datetime, timedelta, time

def get_db():
    return mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'altaria.proxy.rlwy.net'),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD', 'rXllXrZeeROURwlmiXfazokKquxkxrgo'),
        database=os.environ.get('DB_NAME', 'railway'),
        port=int(os.environ.get('DB_PORT', 29150)),
        ssl_disabled=os.environ.get('MYSQL_SSL_DISABLED', 'False').lower() == 'true',
        ssl_verify_cert=False
    )

def seed_trend_data():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    print("🧹 Force-clearing old student and attendance records for a clean slate...")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
    cursor.execute("DELETE FROM attendance")
    cursor.execute("DELETE FROM students")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
    conn.commit()

    print("ℹ️ Inserting 30 new student records across your app's departments...")
    
    # Aligned with your form dropdown choices
    departments = [
        "BCA", 
        "BSc.CSIT", 
        "BIM", 
        "BBIT"
    ]
    
    first_names = [
        "Liam", "Olivia", "Noah", "Emma", "Aria", "James", "Sophia", "Lucas", 
        "Mia", "Ethan", "Harper", "Mason", "Evelyn", "Logan", "Abigail", 
        "Alexander", "Emily", "Benjamin", "Charlotte", "Elijah", "Amelia", 
        "Oliver", "Isabella", "William", "Harper", "Daniel", "Luna", "Henry", 
        "Camila", "Aiden"
    ]
    
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", 
        "Garcia", "Rodriguez", "Wilson", "Martinez", "Anderson", "Taylor", 
        "Thomas", "Hernandez", "Moore", "Martin", "Jackson", "Thompson", "White", 
        "Lopez", "Lee", "Gonzalez", "Harris", "Clark", "Lewis", "Robinson", 
        "Walker", "Perez", "Hall"
    ]

    mock_students = []
    for i in range(1, 31):
        s_id = f"STU2026{i:03d}"
        name = f"{first_names[i-1]} {last_names[i-1]}"
        dept = departments[(i - 1) % len(departments)]
        mock_students.append((s_id, name, dept))

    for s_id, name, dept in mock_students:
        cursor.execute(
            "INSERT INTO students (student_id, name, department) VALUES (%s, %s, %s)",
            (s_id, name, dept)
        )
    conn.commit()
    
    # Fetch the newly inserted students back
    cursor.execute("SELECT student_id, department FROM students")
    students = cursor.fetchall()

    today = datetime.now().date()
    print("⏳ Generating attendance records and trends for the past 30 days...")

    # Loop through the last 30 days (skipping Sundays)
    for day_offset in range(30, 0, -1):
        d = today - timedelta(days=day_offset)
        
        if d.weekday() == 6:  # Skip Sundays
            continue
            
        # Randomize how many students show up each day (between 18 and 28 out of 30)
        daily_count = random.randint(18, 28)
        attending_students = random.sample(students, k=daily_count)
        
        for s in attending_students:
            hour = random.randint(8, 16)
            minute = random.randint(0, 59)
            timestamp = datetime.combine(d, time(hour, minute))
            confidence = round(random.uniform(85.0, 99.0), 2)
            
            try:
                cursor.execute(
                    """
                    INSERT INTO attendance (student_id, timestamp, confidence) 
                    VALUES (%s, %s, %s)
                    """,
                    (s['student_id'], timestamp, confidence)
                )
            except Exception:
                pass  # Ignore duplicate constraints if any overlap occurs

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Successfully generated 30 students and 30 days of comprehensive cloud attendance data!")

if __name__ == "__main__":
    seed_trend_data()