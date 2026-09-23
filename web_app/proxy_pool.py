"""
Proxy Pool State Manager untuk RinaraDev Web SaaS.
Mengontrol status proxy secara real-time:
- IDLE: Siap digunakan oleh worker antrean
- BUSY: Sedang mengunci satu sesi aktif pembaca novel
- DEAD: Mengalami kegagalan koneksi berkali-kali (> 3 kali)
- EXPIRED: Masa aktif sewa dari provider sudah lewat
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import db_session, get_db_connection

logger = logging.getLogger("ProxyPool")


class ProxyPoolManager:
    """Manajer alokasi proxy dinamis (Slot Lease Governor)."""

    @classmethod
    def acquire_proxy(
        cls,
        task_id: str,
        worker_id: str,
        user_id: Optional[int] = None
    ) -> Optional[str]:
        """
        Mengunci 1 proxy berstatus IDLE untuk worker tertentu.
        Menghindari tabrakan IP/port antar sesi atau user.
        """
        with db_session() as conn:
            cursor = conn.cursor()
            
            # Cari 1 proxy IDLE dengan failed_count terendah
            cursor.execute("""
            SELECT id, proxy_url FROM proxies
            WHERE status = 'IDLE' 
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY failed_count ASC, id ASC
            LIMIT 1;
            """)
            row = cursor.fetchone()
            
            if not row:
                logger.warning(f"[{worker_id}] Semua proxy sedang BUSY atau habis!")
                return None
            
            proxy_id = row["id"]
            proxy_url = row["proxy_url"]
            
            cursor.execute("""
            UPDATE proxies
            SET status = 'BUSY',
                current_user_id = ?,
                current_task_id = ?,
                current_worker_id = ?,
                last_checked_at = datetime('now')
            WHERE id = ?;
            """, (user_id, task_id, worker_id, proxy_id))
            
            logger.info(f"[{worker_id}] Mengunci proxy {proxy_url} untuk task {task_id}")
            return proxy_url

    @classmethod
    def release_proxy(cls, proxy_url: str) -> None:
        """
        Mengembalikan proxy kembali ke status IDLE setelah sesi selesai.
        """
        if not proxy_url:
            return
            
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE proxies
            SET status = CASE WHEN status = 'BUSY' THEN 'IDLE' ELSE status END,
                current_user_id = NULL,
                current_task_id = NULL,
                current_worker_id = NULL
            WHERE proxy_url = ?;
            """, (proxy_url,))
            logger.debug(f"Proxy {proxy_url} dilepas kembali ke pool (IDLE).")

    @classmethod
    def report_failure(cls, proxy_url: str, reason: str = "") -> None:
        """
        Mencatat kegagalan proxy. Jika sudah >= 3 kali gagal, tandai DEAD.
        """
        if not proxy_url:
            return

        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id, failed_count FROM proxies WHERE proxy_url = ?;
            """, (proxy_url,))
            row = cursor.fetchone()
            if row:
                new_fails = row["failed_count"] + 1
                new_status = 'DEAD' if new_fails >= 3 else 'IDLE'
                cursor.execute("""
                UPDATE proxies
                SET failed_count = ?,
                    status = ?,
                    current_user_id = NULL,
                    current_task_id = NULL,
                    current_worker_id = NULL,
                    last_checked_at = datetime('now')
                WHERE id = ?;
                """, (new_fails, new_status, row["id"]))
                
                if new_status == 'DEAD':
                    logger.error(f"Proxy {proxy_url} ditandai DEAD (gagal {new_fails}x: {reason})")
                else:
                    logger.warning(f"Proxy {proxy_url} gagal {new_fails}x, dikembalikan ke pool ({reason})")

    @classmethod
    def report_success(cls, proxy_url: str, latency_ms: int = 0) -> None:
        """Mereset kegagalan jika koneksi proxy sukses."""
        if not proxy_url:
            return

        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE proxies
            SET failed_count = 0,
                latency_ms = ?,
                last_checked_at = datetime('now')
            WHERE proxy_url = ?;
            """, (latency_ms, proxy_url))

    @classmethod
    def get_summary(cls) -> Dict[str, int]:
        """Mengambil rekapitulasi status seluruh proxy."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'IDLE' THEN 1 ELSE 0 END) as idle,
                SUM(CASE WHEN status = 'BUSY' THEN 1 ELSE 0 END) as busy,
                SUM(CASE WHEN status = 'DEAD' THEN 1 ELSE 0 END) as dead,
                SUM(CASE WHEN status = 'EXPIRED' THEN 1 ELSE 0 END) as expired
            FROM proxies;
            """)
            row = cursor.fetchone()
            return {
                "total": row["total"] or 0,
                "idle": row["idle"] or 0,
                "busy": row["busy"] or 0,
                "dead": row["dead"] or 0,
                "expired": row["expired"] or 0,
            }

    @classmethod
    def list_proxies(cls) -> List[Dict[str, Any]]:
        """Mendapatkan daftar lengkap proxy untuk panel dashboard admin."""
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id, proxy_url, status, current_task_id, current_worker_id, failed_count, last_checked_at, expires_at
            FROM proxies
            ORDER BY id ASC;
            """)
            return [dict(r) for r in cursor.fetchall()]

    @classmethod
    def sync_from_files(cls) -> Dict[str, int]:
        """
        Membaca proxies.txt dari root, stealth_bot/, dan archive untuk sinkronisasi otomatis.
        """
        base_dir = Path(__file__).parent.parent
        files_to_check = [
            base_dir / "proxies.txt",
            base_dir / "stealth_bot" / "proxies.txt",
            base_dir / "legacy_archive" / "web_app_core" / "proxies.txt",
        ]

        all_proxies = set()
        for fpath in files_to_check:
            if fpath.exists():
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        for line in f:
                            p = line.strip()
                            if p and not p.startswith("#"):
                                if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://")):
                                    p = f"http://{p}"
                                all_proxies.add(p)
                except Exception as e:
                    logger.warning(f"Error membaca {fpath}: {e}")

        added = 0
        with db_session() as conn:
            cursor = conn.cursor()
            for p in all_proxies:
                cursor.execute("""
                INSERT OR IGNORE INTO proxies (proxy_url, status)
                VALUES (?, 'IDLE');
                """, (p,))
                if cursor.rowcount > 0:
                    added += 1

        summary = cls.get_summary()
        return {
            "added": added,
            "total": summary["total"],
            "idle": summary["idle"]
        }

    @classmethod
    def import_proxies(cls, raw_text: str) -> Dict[str, int]:
        """Memasukkan daftar proxy dari textarea teks manual."""
        lines = raw_text.strip().splitlines()
        added = 0
        with db_session() as conn:
            cursor = conn.cursor()
            for line in lines:
                p = line.strip()
                if p and not p.startswith("#"):
                    if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://")):
                        p = f"http://{p}"
                    cursor.execute("""
                    INSERT OR IGNORE INTO proxies (proxy_url, status)
                    VALUES (?, 'IDLE');
                    """, (p,))
                    if cursor.rowcount > 0:
                        added += 1
        summary = cls.get_summary()
        return {"added": added, "total": summary["total"], "idle": summary["idle"]}
