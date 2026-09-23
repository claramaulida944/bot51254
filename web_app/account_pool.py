"""
Account Pool & History Manager untuk RinaraDev Web SaaS.
Menjamin anti-duplikasi dan kepatuhan anti-fraud:
- Auto-skip akun yang sudah pernah membaca novel (READ_VALID)
- Auto-skip like, bookmark, dan follow author jika sudah pernah dilakukan sebelumnya
- Membatasi durasi baca harian akun (1 - 3 jam per hari per akun)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from database import db_session, get_db_connection

logger = logging.getLogger("AccountPool")

MAX_DAILY_READING_SECONDS = 3 * 3600  # Maksimal 3 jam per hari per akun


class AccountPoolManager:
    """Manajer seleksi akun cerdas & pencatat riwayat interaksi novel."""

    @classmethod
    def get_eligible_account(
        cls,
        book_id: str,
        author_id: str = "",
        excluded_emails: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Memilih 1 akun aktif yang:
        1. Belum pernah membaca novel ini (tidak ada di account_history dengan READ_VALID).
        2. Durasi baca hari ini belum melebihi limit 3 jam.
        3. Tidak sedang dipakai oleh worker lain (di luar excluded_emails).
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        with db_session() as conn:
            cursor = conn.cursor()
            
            # Reset daily_read_seconds jika hari sudah berganti
            cursor.execute("""
            UPDATE bot_accounts
            SET daily_read_seconds = 0,
                last_read_date = ?
            WHERE last_read_date != ? AND last_read_date != '';
            """, (today_str, today_str))

            # Query akun yang eligible
            exclude_clause = ""
            params: List[Any] = [book_id, MAX_DAILY_READING_SECONDS]
            
            if excluded_emails and len(excluded_emails) > 0:
                placeholders = ",".join(["?"] * len(excluded_emails))
                exclude_clause = f"AND a.email NOT IN ({placeholders})"
                params.extend(excluded_emails)
            
            query = f"""
            SELECT a.* FROM bot_accounts a
            WHERE a.status = 'ACTIVE'
              AND a.email NOT IN (
                  SELECT bot_email FROM account_history 
                  WHERE book_id = ? AND action_type = 'READ_VALID'
              )
              AND (a.last_read_date != '{today_str}' OR a.daily_read_seconds < ?)
              {exclude_clause}
            ORDER BY a.daily_read_seconds ASC, a.id ASC
            LIMIT 1;
            """
            
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    @classmethod
    def has_performed_action(cls, email: str, book_id: str, action_type: str) -> bool:
        """Mengecek apakah akun sudah pernah melakukan aksi tertentu."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id FROM account_history
            WHERE bot_email = ? AND book_id = ? AND action_type = ?;
            """, (email, book_id, action_type))
            return cursor.fetchone() is not None

    @classmethod
    def record_action(cls, email: str, book_id: str, author_id: str, action_type: str) -> None:
        """Mencatat aksi ke riwayat database agar tidak pernah diulang."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR IGNORE INTO account_history (bot_email, book_id, author_id, action_type)
            VALUES (?, ?, ?, ?);
            """, (email, book_id, author_id or "", action_type))
            logger.info(f"[{email}] Aksi '{action_type}' pada buku '{book_id}' berhasil dicatat ke history.")

    @classmethod
    def update_reading_progress(cls, email: str, duration_seconds: int) -> None:
        """Menambahkan durasi baca harian akun."""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE bot_accounts
            SET daily_read_seconds = CASE 
                    WHEN last_read_date = ? THEN daily_read_seconds + ?
                    ELSE ?
                END,
                last_read_date = ?
            WHERE email = ?;
            """, (today_str, duration_seconds, duration_seconds, today_str, email))

    @classmethod
    def update_account_tokens(cls, email: str, access_token: str, refresh_token: str) -> None:
        """Memperbarui token yang baru di-refresh."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE bot_accounts
            SET access_token = ?,
                refresh_token = ?
            WHERE email = ?;
            """, (access_token, refresh_token, email))

    @classmethod
    def update_q_balance(cls, email: str, balance: int, claim_date: str) -> None:
        """Memperbarui saldo Q hasil klaim harian."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE bot_accounts
            SET q_balance = ?,
                last_q_claim_date = ?
            WHERE email = ?;
            """, (balance, claim_date, email))

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        """Mengambil ringkasan akun bot untuk dashboard admin."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) as active,
                SUM(q_balance) as total_q
            FROM bot_accounts;
            """)
            row = cursor.fetchone()
            return {
                "total": row["total"] or 0,
                "active": row["active"] or 0,
                "total_q": row["total_q"] or 0,
            }

    @classmethod
    def add_or_update_account(cls, acc_data: Dict[str, Any]) -> None:
        """Menambahkan akun baru hasil konversi tamu atau memperbarui akun yang ada."""
        email = acc_data.get("email")
        if not email:
            return
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO bot_accounts (
                email, password, nickname, access_token, refresh_token,
                country, device_id, anonymous_id, user_agent, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            ON CONFLICT(email) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                password = excluded.password,
                nickname = excluded.nickname,
                status = 'ACTIVE';
            """, (
                email,
                acc_data.get("password", ""),
                acc_data.get("nickname", ""),
                acc_data.get("access_token", ""),
                acc_data.get("refresh_token", ""),
                acc_data.get("country", "ID"),
                acc_data.get("device_id", ""),
                acc_data.get("anonymous_id", ""),
                acc_data.get("user_agent", "okhttp/4.12.0"),
            ))
            logger.info(f"Akun baru hasil konversi berhasil disimpan ke bot_accounts: {email}")

        # Simpan juga ke file akun_stealth.txt
        try:
            txt_path = Path(__file__).parent.parent / "stealth_bot" / "akun_stealth.txt"
            if txt_path.exists():
                with open(txt_path, "a", encoding="utf-8") as f:
                    f.write(f"\n{json.dumps(acc_data)}")
        except Exception as e:
            logger.warning(f"Gagal mencatat akun ke akun_stealth.txt: {e}")

    @classmethod
    def sync_from_file(cls) -> Dict[str, int]:
        """Menyinkronkan data akun dari akun_stealth.txt ke tabel bot_accounts."""
        txt_path = Path(__file__).parent.parent / "stealth_bot" / "akun_stealth.txt"
        if not txt_path.exists():
            return {"added": 0, "total": 0}

        added = 0
        with open(txt_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    acc = json.loads(line)
                    email = acc.get("email")
                    if email:
                        with db_session() as conn:
                            cursor = conn.cursor()
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
                            if cursor.rowcount > 0:
                                added += 1
                except Exception:
                    pass

        summary = cls.get_summary()
        return {"added": added, "total": summary["total"], "active": summary["active"], "total_q": summary["total_q"]}

    @classmethod
    def import_accounts(cls, raw_text: str) -> Dict[str, int]:
        """Mengimpor banyak akun dari teks JSON lines atau baris email:password."""
        lines = raw_text.strip().splitlines()
        added = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            acc_data = None
            if line.startswith("{") and line.endswith("}"):
                try:
                    acc_data = json.loads(line)
                except Exception:
                    pass
            elif ":" in line:
                parts = line.split(":", 1)
                acc_data = {
                    "email": parts[0].strip(),
                    "password": parts[1].strip(),
                    "nickname": parts[0].split("@")[0],
                }

            if acc_data and acc_data.get("email"):
                cls.add_or_update_account(acc_data)
                added += 1

        summary = cls.get_summary()
        return {"added": added, "total": summary["total"]}

