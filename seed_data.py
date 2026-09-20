import os
import random
import math
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Automatically load environment variables from a .env file if it exists
load_dotenv()

from database import get_db

def seed_database():
    # --- ENVIRONMENT DEBUG CHECK ---
    print("--- ENVIRONMENT CHECK ---")
    print(f"Host: {os.environ.get('DB_HOST')}")
    print(f"User: {os.environ.get('DB_USER')}")
    print(f"Database: {os.environ.get('DB_NAME')}")
    print(f"Port: {os.environ.get('DB_PORT')}")
    pwd = os.environ.get('DB_PASSWORD', '')
    masked_pwd = pwd[:4] + "..." + pwd[-4:] if len(pwd) > 8 else "NOT SET/TOO SHORT"
    print(f"Password Check: {masked_pwd}")
    print("-------------------------")

    print("Connecting to database...")
    try:
        conn = get_db()
    except Exception as e:
        print(f"Connection failed: {e}")
        print("\nTip: Make sure your database credentials are correct and saved in your .env file.")
        return

    cursor = conn.cursor()
    
    print("Clearing existing data...")
    # Clear tables safely respecting foreign keys
    cursor.execute("DELETE FROM attendance")
    cursor.execute("DELETE FROM students")
    conn.commit()
    
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

    print("Generating 30 new students...")
    students = []
    for i in range(1, 31):
        student_id = f"STU-{1000 + i}"
        name = f"{random.choice(first_names)} {random.choice(last_names)}"
        department = random.choice(departments)
        students.append((student_id, name, department))
        
        cursor.execute(
            "INSERT INTO students (student_id, name, department) VALUES (%s, %s, %s)",
            (student_id, name, department)
        )
    conn.commit()

    print("Generating fluctuating 7-day attendance trend data...")
    today = datetime.now().date()
    
    for day_offset in range(6, -1, -1):
        target_date = today - timedelta(days=day_offset)
        
        # Smooth fluctuating attendance using a sine wave + variance for organic graph lines
        wave_factor = math.sin(day_offset * 1.2) * 6
        target_count = int(21 + wave_factor + random.randint(-2, 2))
        target_count = max(12, min(30, target_count)) # Keeps bounds between 12 and 30 students
        
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
    print("Successfully seeded 30 students and a smooth, fluctuating 7-day attendance trend!")

if __name__ == "__main__":
    seed_database()