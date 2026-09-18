import mysql.connector
from werkzeug.security import generate_password_hash

# Match credentials with your database.py settings
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root123',  # Replace with your MySQL password
    'database': 'face_attendance_new_db',
    'port': 3306
}

conn = mysql.connector.connect(**MYSQL_CONFIG)
cursor = conn.cursor()

# Generate valid password hash for 'admin123'
new_hash = generate_password_hash('admin123')

# Reset the admin password inside MySQL
cursor.execute("DELETE FROM users WHERE username = 'admin'")
cursor.execute(
    "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
    ('admin', new_hash, 'admin')
)

conn.commit()
conn.close()

print("Admin user successfully reset! You can now login using:")
print("Username: admin")
print("Password: admin123")