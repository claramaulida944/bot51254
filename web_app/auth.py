"""
Modul Autentikasi dan Manajemen Saldo Pengguna Web SaaS.
Mendukung:
- Registrasi Pengguna Baru
- Login Pengguna & Session Cookie / Bearer Token
- Pengecekan & Pengurangan Saldo Transaksional (ACID SQLite)
- Riwayat Mutasi Transaksi Saldo
"""

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from database import db_session, get_db_connection, hash_password, verify_password

logger = logging.getLogger("RinaraAuth")

SESSION_DURATION_DAYS = 30


class AuthManager:
    """Manajer autentikasi akun pengguna web."""

    @classmethod
    def register(cls, username: str, email: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Mendaftarkan akun baru."""
        username = username.strip().lower()
        email = email.strip().lower()

        if not re.match(r"^[a-zA-Z0-9_]{3,24}$", username):
            return False, "Username hanya boleh huruf, angka, dan underscore (3-24 karakter).", None
        if len(password) < 6:
            return False, "Password minimal 6 karakter.", None
        if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):
            return False, "Format email tidak valid.", None

        pwd_hash, pwd_salt = hash_password(password)

        try:
            with db_session() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO users (username, email, password_hash, password_salt, balance, role)
                VALUES (?, ?, ?, ?, 0, 'user');
                """, (username, email, pwd_hash, pwd_salt))
                user_id = cursor.lastrowid
                
                # Buat session otomatis
                token = secrets.token_urlsafe(32)
                expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DURATION_DAYS)
                cursor.execute("""
                INSERT INTO user_sessions (session_token, user_id, expires_at)
                VALUES (?, ?, ?);
                """, (token, user_id, expires.isoformat()))
                
                return True, "Registrasi berhasil!", {
                    "id": user_id,
                    "username": username,
                    "email": email,
                    "balance": 0,
                    "role": "user",
                    "session_token": token,
                }
        except Exception as e:
            err_str = str(e).lower()
            if "unique constraint" in err_str:
                if "username" in err_str:
                    return False, "Username sudah digunakan, silakan pilih yang lain.", None
                if "email" in err_str:
                    return False, "Email sudah terdaftar, silakan login.", None
            logger.error(f"Error registrasi: {e}")
            return False, "Terjadi kesalahan saat pendaftaran.", None

    @classmethod
    def login(cls, identifier: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Login menggunakan username atau email."""
        identifier = identifier.strip().lower()
        
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id, username, email, password_hash, password_salt, balance, role
            FROM users
            WHERE username = ? OR email = ?;
            """, (identifier, identifier))
            row = cursor.fetchone()
            
            if not row:
                return False, "Akun tidak ditemukan.", None
            
            user = dict(row)
            if not verify_password(password, user["password_hash"], user["password_salt"]):
                return False, "Password salah.", None
            
            # Buat session token baru
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DURATION_DAYS)
            cursor.execute("""
            INSERT INTO user_sessions (session_token, user_id, expires_at)
            VALUES (?, ?, ?);
            """, (token, user["id"], expires.isoformat()))
            
            cursor.execute("""
            UPDATE users SET last_login_at = datetime('now') WHERE id = ?;
            """, (user["id"],))

            return True, "Login berhasil!", {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "balance": user["balance"],
                "role": user["role"],
                "session_token": token,
            }

    @classmethod
    def get_user_by_session(cls, session_token: str) -> Optional[Dict[str, Any]]:
        """Mendapatkan data user dari session token."""
        if not session_token:
            return None

        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT u.id, u.username, u.email, u.balance, u.role
            FROM users u
            JOIN user_sessions s ON u.id = s.user_id
            WHERE s.session_token = ? AND s.expires_at > datetime('now');
            """, (session_token,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    @classmethod
    def logout(cls, session_token: str) -> None:
        """Menghapus session token."""
        if not session_token:
            return
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_sessions WHERE session_token = ?;", (session_token,))

    @classmethod
    def deduct_balance(cls, user_id: int, amount: int, description: str, reference_id: str = "") -> bool:
        """
        Memotong saldo user secara atomic dan thread-safe.
        Menggunakan single-statement condition untuk mencegah race condition.
        """
        if amount <= 0:
            return True

        with db_session() as conn:
            cursor = conn.cursor()
            # Atomic update: hanya kurangi jika saldo saat ini >= amount
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?;",
                (amount, user_id, amount)
            )
            if cursor.rowcount == 0:
                return False
            
            # Catat transaksi
            cursor.execute("""
            INSERT INTO transactions (user_id, type, amount, description, reference_id)
            VALUES (?, 'USAGE', ?, ?, ?);
            """, (user_id, amount, description, reference_id))
            return True

    @classmethod
    def add_balance(cls, user_id: int, amount: int, description: str, reference_id: str = "") -> bool:
        """Menambah saldo user (Top-up)."""
        if amount <= 0:
            return False

        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (amount, user_id))
            if cursor.rowcount == 0:
                return False
            cursor.execute("""
            INSERT INTO transactions (user_id, type, amount, description, reference_id)
            VALUES (?, 'TOPUP', ?, ?, ?);
            """, (user_id, amount, description, reference_id))
            return True

    @classmethod
    def get_user_transactions(cls, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Mengambil riwayat transaksi user."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id, type, amount, description, reference_id, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?;
            """, (user_id, limit))
            return [dict(r) for r in cursor.fetchall()]
