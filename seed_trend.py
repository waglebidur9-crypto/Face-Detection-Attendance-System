from datetime import datetime, timedelta, time
import random
from database import get_db, init_db

def seed_mock_attendance():
    # Ensure database tables exist first
    init_db()
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    print("🧹 Force-clearing old student and attendance records for a fresh start...")
    # Disable foreign key checks temporarily to clear cleanly
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
    cursor.execute("DELETE FROM attendance")
    cursor.execute("DELETE FROM students")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
    conn.commit()

    print("ℹ️ Inserting 50 new student records with your app's exact departments...")
    
    # Exact departments from your actual application
    departments = [
        "Computer Science", 
        "Electronics", 
        "BCA", 
        "B.Sc. CSIT", 
        "BBA"
    ]
    
    first_names = [
        "Liam", "Olivia", "Noah", "Emma", "Aria", "James", "Sophia", "Lucas", 
        "Mia", "Ethan", "Harper", "Mason", "Evelyn", "Logan", "Abigail", 
        "Alexander", "Emily", "Benjamin", "Charlotte", "Elijah", "Amelia", 
        "Oliver", "Isabella", "William", "Harper", "Daniel", "Luna", "Henry", 
        "Camila", "Aiden", "Gianna", "Matthew", "Elizabeth", "Jackson", "Ella", 
        "Sebastian", "Sofia", "David", "Avery", "Carter", "Scarlett", "Wyatt", 
        "Victoria", "Jayden", "Aria", "Gabriel", "Grace", "Julian", "Chloe", "Nathan"
    ]
    
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", 
        "Garcia", "Rodriguez", "Wilson", "Martinez", "Anderson", "Taylor", 
        "Thomas", "Hernandez", "Moore", "Martin", "Jackson", "Thompson", "White", 
        "Lopez", "Lee", "Gonzalez", "Harris", "Clark", "Lewis", "Robinson", 
        "Walker", "Perez", "Hall", "Young", "Allen", "Sanchez", "Wright", 
        "King", "Scott", "Green", "Baker", "Adams", "Nelson", "Hill", 
        "Ramirez", "Campbell", "Mitchell", "Roberts", "Carter", "Phillips", 
        "Evans", "Turner", "Torres"
    ]

    mock_students = []
    for i in range(1, 51):
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
    
    # Fetch the newly inserted students
    cursor.execute("SELECT student_id, department FROM students")
    students = cursor.fetchall()

    today = datetime.now().date()
    print("⏳ Generating varied attendance records for the past 7 days...")

    # Loop through the last 7 days to create distinct trends per day
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        
        # Randomize how many students show up each day (e.g., between 25 and 45 out of 50)
        daily_count = random.randint(25, 45)
        attending_students = random.sample(students, k=daily_count)
        
        for s in attending_students:
            hour = random.randint(8, 16)
            minute = random.randint(0, 59)
            timestamp = datetime.combine(d, time(hour, minute))
            confidence = round(random.uniform(0.85, 0.99), 2)
            
            try:
                cursor.execute(
                    """
                    INSERT INTO attendance (student_id, timestamp, confidence) 
                    VALUES (%s, %s, %s)
                    """,
                    (s['student_id'], timestamp, confidence)
                )
            except Exception:
                pass

    conn.commit()
    conn.close()
    print("✅ Successfully generated 50 students and fresh attendance records!")

if __name__ == "__main__":
    seed_mock_attendance()