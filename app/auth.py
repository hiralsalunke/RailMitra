import bcrypt
from app.database import get_db_connection

def verify_admin(username: str, password: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM admins WHERE username = %s", (username,))
    admin = cursor.fetchone()
    cursor.close()
    conn.close()

    if admin and bcrypt.checkpw(password.encode('utf-8'), admin["password_hash"].encode('utf-8')):
        return True
    return False