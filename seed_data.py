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
        cursor.execute("DELETE FROM attendance;")
        cursor.execute("DELETE FROM students;")
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
    
    first_names = [
        "Aarav", "Aayush", "Bikash", "Deepak", "Diya", "Ganesh", "Kiran", "Manish", "Nisha", "Puja", 
        "Prabin", "Priya", "Rabin", "Rajesh", "Ritu", "Rohit", "Sajan", "Sandesh", "Sanjay",
        "Saroj", "Sita", "Suman", "Sunil", "Suraj", "Sushant", "Swastika", "Ujjwal", "Bibek", "Pooja"
    ]
    last_names = [
        "Adhikari", "Aryal", "Bhandari", "Bhattarai", "Dahal", "Gautam", "Karki", "Khadka", "Maharjan", "Shrestha",
        "Tamang", "Thapa", "Timilsina", "Koirala", "Joshi", "Poudel", "Silwal", "Rai", "Limbu", "Gurung"
    ]

    # Track sequence counters for each department independently
    dept_counters = {dept: 1000 for dept in departments}

    print("Generating 30 new students with department-specific IDs (e.g., BCA-1001)...")
    students = []
    for _ in range(30):
        department = random.choice(departments)
        dept_counters[department] += 1
        seq_num = dept_counters[department]
        
        # Format ID like BCA-1001, BBIT-1001, etc.
        student_id = f"{department}-{seq_num}"
        
        name = f"{random.choice(first_names)} {random.choice(last_names)}"
        phone = f"98{random.randint(10000000, 99999999)}"
        parent_phone = f"97{random.randint(10000000, 99999999)}"
        parent_name = f"Parent of {name.split()[0]}"
        address = "Kathmandu, Nepal"
        
        students.append((student_id, name, department))
        
        cursor.execute(
            """INSERT INTO students (student_id, name, department, phone, address, parent_name, parent_phone) 
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (student_id, name, department, phone, address, parent_name, parent_phone)
        )
    conn.commit()

    print("Generating fluctuating 7-day attendance trend data...")
    today = datetime.now().date()
    
    for day_offset in range(6, -1, -1):
        target_date = today - timedelta(days=day_offset)
        
        wave_factor = math.sin(day_offset * 1.2) * 6
        target_count = int(21 + wave_factor + random.randint(-2, 2))
        target_count = max(12, min(30, target_count))
        
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
    print("Successfully seeded students with department-based IDs and attendance trends!")

if __name__ == "__main__":
    seed_database()