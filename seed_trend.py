from datetime import datetime, timedelta, time
import random
from database import get_db

def seed_mock_attendance():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    # Fetch all registered students
    cursor.execute("SELECT student_id, department FROM students")
    students = cursor.fetchall()
    
    if not students:
        print("❌ Error: No students found in the database. Please add or import students first!")
        conn.close()
        return

    today = datetime.now().date()
    print("⏳ Generating mock attendance for the past 7 days...")

    # Loop through the last 7 days
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        # Randomly select a subset of students to attend on this day
        sample_size = random.randint(1, len(students))
        attending_students = random.sample(students, k=sample_size)
        
        for s in attending_students:
            # Assign random morning/afternoon hours and minutes for that specific day
            hour = random.randint(8, 14)
            minute = random.randint(0, 59)
            timestamp = datetime.combine(d, time(hour, minute))
            
            try:
                cursor.execute(
                    """
                    INSERT INTO attendance (student_id, timestamp, confidence) 
                    VALUES (%s, %s, %s)
                    """,
                    (s['student_id'], timestamp, 0.95)
                )
            except Exception:
                # Skip if unique constraints block duplicates for the same day
                pass

    conn.commit()
    conn.close()
    print("✅ Mock data generated successfully! Refresh your dashboard to see the live curves.")

if __name__ == "__main__":
    seed_mock_attendance()