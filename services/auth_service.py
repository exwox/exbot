"""Authentication Service - user login/register/logout"""
import hashlib, secrets
from datetime import datetime
from typing import Optional

class AuthService:
    def __init__(self, db):
        self.db = db
        cur = self.db.conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login TEXT)""")
        self.db.conn.commit()

    def _hash(self, pwd, salt=None):
        if salt is None: salt = secrets.token_hex(32)
        h = hashlib.pbkdf2_hmac('sha256', pwd.encode(), salt.encode(), 100000).hex()
        return h, salt

    def register(self, username, password):
        if not username or not password: return {'success': False, 'error': 'Username and password required'}
        if len(password) < 6: return {'success': False, 'error': 'Password min 6 chars'}
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM users WHERE username=?", (username,))
        if cur.fetchone(): return {'success': False, 'error': 'Username exists'}
        h, salt = self._hash(password)
        now = datetime.now().isoformat()
        cur.execute("INSERT INTO users (username,password_hash,salt,created_at) VALUES(?,?,?,?)", (username,h,salt,now))
        self.db.conn.commit()
        return {'success': True, 'user_id': cur.lastrowid, 'username': username}

    def login(self, username, password):
        if not username or not password: return {'success': False, 'error': 'Username and password required'}
        cur = self.db.conn.cursor()
        cur.execute("SELECT id,username,password_hash,salt FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if not row: return {'success': False, 'error': 'Invalid credentials'}
        uid, uname, db_h, db_s = row
        h, _ = self._hash(password, db_s)
        if h != db_h: return {'success': False, 'error': 'Invalid credentials'}
        now = datetime.now().isoformat()
        cur.execute("UPDATE users SET last_login=? WHERE id=?", (now, uid))
        self.db.conn.commit()
        return {'success': True, 'user_id': uid, 'username': uname}

    def get_user(self, user_id):
        cur = self.db.conn.cursor()
        cur.execute("SELECT id,username,created_at,last_login FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        if row: return {'id': row[0], 'username': row[1], 'created_at': row[2], 'last_login': row[3]}
        return None

    def user_count(self):
        cur = self.db.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]