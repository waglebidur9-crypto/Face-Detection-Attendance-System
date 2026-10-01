import os
import random
import math
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from database import get_db

def seed_database():
    print("--- ENVIRONMENT CHECK ---")
    print(f"Host: {os.environ.get('DB_HOST')}")
    print(f"User: {os.environ.get('DB_USER')}")
    print(f"Database: {os.environ.get('DB_NAME')}")
    print(f"Port: {os.environ.get('DB_PORT')}")
    print("-------------------------")

    print("Connecting to database...")
    try:
        conn = get_db()
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    cursor = conn.cursor()
    
    print("Safely clearing existing attendance and student data...")
    try:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        # Safely delete from core tables if they exist
        for table in ["attendance", "leaves", "sms_logs", "students"]:
            try:
                cursor.execute(f"DELETE FROM {table};")
            except Exception:
                pass # Ignore if table doesn't exist yet
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        conn.commit()
        print("Previous data cleared successfully.")
    except Exception as e:
        print(f"Error while clearing tables: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return

    departments = ["BCA", "BSc.CSIT", "BIM", "BBIT"]
    
    student_names = [
        "Aarav Adhikari", "Aayush Aryal", "Bikash Bhandari", "Deepak Bhattarai", 
        "Diya Dahal", "Ganesh Gautam", "Kiran Karki", "Manish Khadka", 
        "Nisha Maharjan", "Puja Shrestha"
    ]

    print("Generating 10 test students with phone number 9845814467...")
    students = []
    for i, name in enumerate(student_names):
        department = departments[i % len(departments)]
        student_id = f"{department}-{1001 + i}"
        
        phone = "9845814467"
        parent_phone = "9845814467"
        parent_name = f"Parent of {name.split()[0]}"
        address = "Gaindakot, Nepal"
        
        students.append((student_id, name, department))
        
        cursor.execute(
            """INSERT INTO students (student_id, name, department, phone, address, parent_name, parent_phone) 
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (student_id, name, department, phone, address, parent_name, parent_phone)
        )
    conn.commit()

    print("Generating 7-day test attendance trend data...")
    today = datetime.now().date()
    
    for day_offset in range(6, -1, -1):
        target_date = today - timedelta(days=day_offset)
        target_count = random.randint(6, 10)
        
        present_students = random.sample(students, k=target_count)
        
        for student in present_students:
            student_id = student[0]
            hour = random.randint(8, 10)
            minute = random.randint(0, 59)
            timestamp = datetime.combine(target_date, datetime.min.time()).replace(hour=hour, minute=minute)
            
            try:
                cursor.execute(
                    "INSERT INTO attendance (student_id, timestamp, confidence) VALUES (%s, %s, %s)",
                    (student_id, timestamp, round(random.uniform(0.85, 0.99), 2))
                )
            except Exception:
                pass
                
    conn.commit()
    cursor.close()
    conn.close()
    print("Successfully seeded 10 test students and attendance records!")

if __name__ == "__main__":
    seed_database()