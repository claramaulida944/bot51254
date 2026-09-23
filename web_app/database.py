"""
Database SQLite & State Management untuk RinaraDev Web SaaS.
Menyediakan modul penyimpanan persisten untuk:
- Akun pengguna web (Register, Login, Password Hash PBKDF2, Saldo Rupiah)
- Proxy Pool (Status IDLE, BUSY, DEAD, EXPIRED, Locking per worker)
- Bot Accounts Pool (Akun Quarterfull dari stealth_bot)
- Account History (Auto-skip 1x per book/author untuk READ, LIKE, BOOKMARK, FOLLOW)
- Tasks (Antrean dan progres multi-terminal)
- Transaksi (Topup & Pemotongan Saldo)
"""

import hashlib
import json
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("RinaraDatabase")

DB_PATH = Path(__file__).parent / "rinara_saas.db"
STEALTH_DIR = Path(__file__).parent.parent / "stealth_bot"


def get_db_connection() -> sqlite3.Connection:
    """Membuka koneksi SQLite dengan mode WAL untuk konkurensi tinggi."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db_session():
    """Context manager transaksi database."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Mengenkripsi password menggunakan PBKDF2-HMAC-SHA256 bawaan hashlib."""
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return key.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Memverifikasi kecocokan password."""
    calc_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(calc_hash, stored_hash)


def init_database() -> None:
    """Membuat semua tabel dan indeks yang dibutuhkan."""
    with db_session() as conn:
        cursor = conn.cursor()

        # 1. Tabel Users (Pengguna Web)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            balance INTEGER DEFAULT 0,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP NULL
        );
        """)

        # 2. Tabel Sessions (Autentikasi Login Web)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)

        # 3. Tabel Proxies (State Pool: IDLE, BUSY, DEAD, EXPIRED)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proxy_url TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'IDLE',
            current_user_id INTEGER NULL,
            current_task_id TEXT NULL,
            current_worker_id TEXT NULL,
            failed_count INTEGER DEFAULT 0,
            last_checked_at TIMESTAMP NULL,
            expires_at TIMESTAMP NULL,
            latency_ms INTEGER DEFAULT 0
        );
        """)

        # 4. Tabel Bot Accounts (Akun Stealth Quarterfull)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nickname TEXT DEFAULT '',
            access_token TEXT DEFAULT '',
            refresh_token TEXT DEFAULT '',
            country TEXT DEFAULT 'ID',
            device_id TEXT DEFAULT '',
            anonymous_id TEXT DEFAULT '',
            user_agent TEXT DEFAULT 'okhttp/4.12.0',
            q_balance INTEGER DEFAULT 0,
            last_q_claim_date TEXT DEFAULT '',
            status TEXT DEFAULT 'ACTIVE',
            daily_read_seconds INTEGER DEFAULT 0,
            last_read_date TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 5. Tabel Account History (Anti-Duplikasi: 1x like, bookmark, follow, read per novel/author)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_email TEXT NOT NULL,
            book_id TEXT NOT NULL,
            author_id TEXT DEFAULT '',
            action_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_email, book_id, action_type)
        );
        """)

        # 6. Tabel Tasks (Pekerjaan Multi-Terminal Pembaca)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            novel_id TEXT NOT NULL,
            novel_title TEXT DEFAULT '',
            target_readers INTEGER NOT NULL,
            completed_readers INTEGER DEFAULT 0,
            concurrent_terminals INTEGER DEFAULT 1,
            price_per_reader INTEGER DEFAULT 450,
            mode TEXT DEFAULT 'valid_read_25',
            status TEXT DEFAULT 'RUNNING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)

        # 7. Tabel Transactions (Riwayat Saldo Rupiah)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT DEFAULT '',
            reference_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)

        # Create indexes for high query performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_proxies_status ON proxies(status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_accounts_status ON bot_accounts(status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_lookup ON account_history(bot_email, book_id, action_type);")
        # 8. Tabel System Settings (Konfigurasi Dinamis Admin)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT DEFAULT ''
        );
        """)

        default_settings = [
            ("price_valid_reader", "450", "Tarif per pembaca valid tamat 25 bab (Rp)"),
            ("price_guest_reader", "50", "Tarif per pembaca tamu anonim (Rp)"),
            ("addon_price_per_terminal", "500", "Biaya addon per slot terminal tambahan (Rp)"),
            ("addon_price_guest_conversion", "150", "Biaya addon konversi tamu ke member terdaftar OTP (Rp)"),
            ("min_deposit", "5000", "Minimal top up saldo QRIS (Rp)"),
            ("reading_delay_seconds", "140", "Delay membaca per bab (detik)"),
            ("max_concurrent_terminals", "5", "Maksimal terminal sesi per tugas"),
        ]
        for k, v, d in default_settings:
            cursor.execute("INSERT OR IGNORE INTO system_settings (key, value, description) VALUES (?, ?, ?);", (k, v, d))

        # Buat user admin default jika belum ada
        cursor.execute("SELECT id FROM users WHERE username = 'admin';")
        if not cursor.fetchone():
            h, s = hash_password("100401naraA!")
            cursor.execute("""
            INSERT INTO users (username, email, password_hash, password_salt, balance, role)
            VALUES (?, ?, ?, ?, ?, ?);
            """, ("admin", "admin@rinara.dev", h, s, 1000000, "admin"))

    logger.info("Database SQLite berhasil diinisialisasi.")


def get_setting(key: str, default: Any = "") -> str:
    """Mengambil nilai setting sistem."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_settings WHERE key = ?;", (key,))
        row = cursor.fetchone()
        return row["value"] if row else str(default)


def get_all_settings() -> Dict[str, Any]:
    """Mengambil semua setting sistem dalam bentuk dictionary bertipe tepat."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value, description FROM system_settings;")
        res = {}
        for r in cursor.fetchall():
            k = r["key"]
            v = r["value"]
            if v.isdigit():
                res[k] = int(v)
            else:
                try:
                    res[k] = float(v)
                except ValueError:
                    res[k] = v
        return res


def update_setting(key: str, value: Any) -> bool:
    """Memperbarui nilai setting sistem."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO system_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;
        """, (key, str(value)))
        return True


def sync_initial_assets() -> None:
    """Mengimpor data dari proxies.txt dan akun_stealth.txt ke SQLite jika tabel masih kosong."""
    init_database()

    with db_session() as conn:
        cursor = conn.cursor()

        # 1. Sync Proxies
        proxy_file = STEALTH_DIR / "proxies.txt"
        if not proxy_file.exists():
            proxy_file = Path(__file__).parent.parent / "proxies.txt"
        
        if proxy_file.exists():
            with open(proxy_file, "r", encoding="utf-8") as f:
                for line in f:
                    proxy = line.strip()
                    if proxy and not proxy.startswith("#"):
                        if not proxy.startswith("http"):
                            proxy_url = f"http://{proxy}"
                        else:
                            proxy_url = proxy
                        cursor.execute("""
                        INSERT OR IGNORE INTO proxies (proxy_url, status)
                        VALUES (?, 'IDLE');
                        """, (proxy_url,))

        # 2. Sync Bot Accounts
        acc_file = STEALTH_DIR / "akun_stealth.txt"
        if acc_file.exists():
            with open(acc_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        try:
                            acc = json.loads(line)
                            email = acc.get("email")
                            if email:
                                cursor.execute("""
                                INSERT INTO bot_accounts (
                                    email, password, nickname, access_token, refresh_token,
                                    country, device_id, anonymous_id, user_agent,
                                    q_balance, last_q_claim_date, status
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
                                ON CONFLICT(email) DO UPDATE SET
                                    access_token = excluded.access_token,
                                    refresh_token = excluded.refresh_token,
                                    nickname = excluded.nickname,
                                    q_balance = excluded.q_balance,
                                    last_q_claim_date = excluded.last_q_claim_date;
                                """, (
                                    email,
                                    acc.get("password", ""),
                                    acc.get("nickname", ""),
                                    acc.get("access_token", ""),
                                    acc.get("refresh_token", ""),
                                    acc.get("country", "ID"),
                                    acc.get("device_id", ""),
                                    acc.get("anonymous_id", ""),
                                    acc.get("user_agent", "okhttp/4.12.0"),
                                    acc.get("q_balance", 0),
                                    acc.get("last_q_claim_date", ""),
                                ))
                        except Exception as e:
                            logger.warning(f"Gagal parse akun: {e}")


# Jalankan inisialisasi saat modul dimuat
init_database()
