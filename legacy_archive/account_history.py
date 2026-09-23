"""
Account Novel History Manager
Mencatat dan memverifikasi riwayat interaksi akun (Membaca, Like, Simpan/Bookmark)
terhadap setiap novel target.

Aturan Utama:
Jika sebuah akun terdeteksi sudah pernah membaca, menyukai (like), atau menyimpan
(bookmark) sebuah novel, maka akun tersebut TIDAK AKAN DIGUNAKAN LAGI untuk novel yang sama.
Hal ini menjaga keaslian pembaca unik, mencegah duplikasi interaksi, dan mengoptimalkan
penggunaan saldo klien.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

logger = logging.getLogger("AccountHistoryManager")

# Path berkas penyimpanan history (tersimpan di web_app dan disinkronkan ke root jika ada)
BASE_WEB_APP = Path(__file__).parent.parent
HISTORY_FILE = BASE_WEB_APP / "account_novel_history.json"
ROOT_HISTORY_FILE = BASE_WEB_APP.parent / "account_novel_history.json"


class AccountHistoryManager:
    _lock = threading.RLock()
    _history: Dict[str, Dict[str, Dict[str, Any]]] = {}
    _loaded = False

    @classmethod
    def _ensure_loaded(cls) -> None:
        """Memuat database riwayat interaksi dari berkas JSON jika belum dimuat."""
        with cls._lock:
            if cls._loaded:
                return

            cls._history = {}
            target_path = HISTORY_FILE
            if not target_path.exists() and ROOT_HISTORY_FILE.exists():
                target_path = ROOT_HISTORY_FILE

            if target_path.exists():
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            cls._history = data
                            logger.info(f"Dimuat riwayat interaksi untuk {len(cls._history)} akun.")
                except Exception as exc:
                    logger.warning(f"Gagal membaca berkas riwayat novel akun: {exc}")

            cls._loaded = True

    @classmethod
    def _save(cls) -> None:
        """Menyimpan database riwayat interaksi ke berkas JSON secara aman."""
        with cls._lock:
            try:
                # Simpan ke web_app/account_novel_history.json
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(cls._history, f, indent=2, ensure_ascii=False)

                # Sinkronkan juga ke root direktori
                try:
                    with open(ROOT_HISTORY_FILE, "w", encoding="utf-8") as f:
                        json.dump(cls._history, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            except Exception as exc:
                logger.error(f"Gagal menyimpan berkas riwayat novel akun: {exc}")

    @classmethod
    def get_account_key(cls, account: Dict[str, Any]) -> str:
        """Menghasilkan kunci pengenal unik untuk akun (user_id atau email)."""
        uid = str(account.get("user_id") or "").strip()
        if uid and uid not in ("None", "0"):
            return f"uid_{uid}"
        email = str(account.get("email") or "").strip().lower()
        if email:
            return f"email_{email}"
        token = str(account.get("access_token") or "")[:20]
        return f"tok_{token}"

    @classmethod
    def clean_novel_id(cls, novel_id: str) -> str:
        """Membersihkan dan menstandarkan ID novel target."""
        return str(novel_id or "").strip()

    @classmethod
    def has_interacted(
        cls,
        account: Dict[str, Any],
        novel_id: str,
        require_any: bool = True
    ) -> bool:
        """
        Memeriksa apakah akun ini tercatat sudah pernah berinteraksi dengan novel ini.
        Jika require_any=True (default):
        Mengembalikan True jika akun sudah PERNAH MEMBACA, PERNAH LIKE, ATAU PERNAH SIMPAN/BOOKMARK.
        """
        cls._ensure_loaded()
        acc_key = cls.get_account_key(account)
        n_id = cls.clean_novel_id(novel_id)
        if not acc_key or not n_id:
            return False

        with cls._lock:
            acc_records = cls._history.get(acc_key, {})
            record = acc_records.get(n_id)
            if not record:
                return False

            if require_any:
                has_read = bool(record.get("read") or len(record.get("chapters", [])) > 0)
                has_liked = bool(record.get("liked"))
                has_saved = bool(record.get("bookmarked") or record.get("saved"))
                return has_read or has_liked or has_saved

            return True

    @classmethod
    def get_interaction_details(cls, account: Dict[str, Any], novel_id: str) -> Optional[Dict[str, Any]]:
        """Mengambil rincian interaksi akun terhadap novel target."""
        cls._ensure_loaded()
        acc_key = cls.get_account_key(account)
        n_id = cls.clean_novel_id(novel_id)
        with cls._lock:
            return cls._history.get(acc_key, {}).get(n_id)

    @classmethod
    def record_interaction(
        cls,
        account: Dict[str, Any],
        novel_id: str,
        read: Optional[bool] = None,
        liked: Optional[bool] = None,
        bookmarked: Optional[bool] = None,
        chapter_num: Optional[int] = None,
        note: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Mencatat atau memperbarui interaksi akun terhadap novel.
        Mencatat waktu interaksi dan daftar bab yang dibaca.
        """
        cls._ensure_loaded()
        acc_key = cls.get_account_key(account)
        n_id = cls.clean_novel_id(novel_id)
        now_str = datetime.now(timezone.utc).isoformat()

        with cls._lock:
            if acc_key not in cls._history:
                cls._history[acc_key] = {}

            if n_id not in cls._history[acc_key]:
                cls._history[acc_key][n_id] = {
                    "novel_id": n_id,
                    "email": account.get("email", ""),
                    "user_id": account.get("user_id"),
                    "read": False,
                    "liked": False,
                    "bookmarked": False,
                    "chapters": [],
                    "first_interacted": now_str,
                    "last_interacted": now_str,
                    "notes": []
                }

            rec = cls._history[acc_key][n_id]
            rec["last_interacted"] = now_str
            if read is not None and read:
                rec["read"] = True
            if liked is not None and liked:
                rec["liked"] = True
            if bookmarked is not None and bookmarked:
                rec["bookmarked"] = True

            if chapter_num is not None:
                ch_list = rec.setdefault("chapters", [])
                if chapter_num not in ch_list:
                    ch_list.append(chapter_num)
                rec["read"] = True

            if note:
                notes = rec.setdefault("notes", [])
                notes.append(f"[{now_str[:19]}] {note}")

            cls._save()
            return rec

    @classmethod
    def filter_fresh_accounts(
        cls,
        accounts: List[Dict[str, Any]],
        novel_id: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Memisahkan daftar akun menjadi:
        1. fresh_accounts: Akun yang BELUM PERNAH berinteraksi (belum baca, like, atau bookmark).
        2. used_accounts: Akun yang SUDAH PERNAH berinteraksi dengan novel ini.
        """
        cls._ensure_loaded()
        n_id = cls.clean_novel_id(novel_id)
        fresh: List[Dict[str, Any]] = []
        used: List[Dict[str, Any]] = []

        for acc in accounts:
            if cls.has_interacted(acc, n_id):
                used.append(acc)
            else:
                fresh.append(acc)

        return fresh, used

    @classmethod
    async def check_server_interaction(
        cls,
        client: httpx.AsyncClient,
        account: Dict[str, Any],
        novel_id: str
    ) -> Dict[str, Any]:
        """
        Memeriksa secara langsung ke server Quarterfull apakah akun ini:
        1. Sudah menyukai (Like) novel ini (is_liked == True).
        2. Sudah menyimpan/bookmark novel ini (is_saved == True atau is_bookmarked == True).
        3. Sudah pernah membaca bab (is_read == True atau reading_progress > 0).

        Jika terdeteksi salah satunya di server:
        - Otomatis mencatat status tersebut ke database riwayat lokal.
        - Mengembalikan already_interacted = True beserta alasan detailnya.
        """
        n_id = cls.clean_novel_id(novel_id)
        email = account.get("email", "unknown")

        result = {
            "already_interacted": False,
            "is_liked": False,
            "is_saved": False,
            "is_read": False,
            "reason": ""
        }

        # 1. Cek metadata novel & status like/bookmark
        try:
            r_novel = await client.get(f"/api/v1/novels/{n_id}")
            if r_novel.status_code == 200:
                n_data = r_novel.json()
                is_liked = bool(n_data.get("is_liked", False))
                is_saved = bool(n_data.get("is_saved") or n_data.get("is_bookmarked") or False)
                result["is_liked"] = is_liked
                result["is_saved"] = is_saved

                if is_liked or is_saved:
                    reasons = []
                    if is_liked:
                        reasons.append("sudah menyukai (Like)")
                    if is_saved:
                        reasons.append("sudah menyimpan (Bookmark)")
                    result["already_interacted"] = True
                    result["reason"] = " & ".join(reasons)
                    
                    # Simpan ke riwayat lokal agar tidak perlu tanya API lagi
                    cls.record_interaction(
                        account,
                        n_id,
                        liked=is_liked,
                        bookmarked=is_saved,
                        note=f"Terdeteksi di server: {result['reason']}"
                    )
                    return result
        except Exception as exc:
            logger.debug(f"Gagal cek status novel di server untuk {email}: {exc}")

        # 2. Cek riwayat baca bab (reading progress)
        try:
            r_ch = await client.get(
                f"/api/v1/novels/{n_id}/chapters",
                params={"order": "asc", "page": 1, "per_page": 50, "include_read_progress": "true"}
            )
            if r_ch.status_code == 200:
                ch_data = r_ch.json()
                items = ch_data.get("items", [])
                read_chapters = []
                for item in items:
                    is_r = item.get("is_read", False)
                    prog = float(item.get("reading_progress", 0.0) or 0.0)
                    if is_r or prog > 0.05:
                        read_chapters.append(item.get("chapter_num", 1))

                if read_chapters:
                    result["is_read"] = True
                    result["already_interacted"] = True
                    result["reason"] = f"sudah pernah membaca bab ({len(read_chapters)} bab)"
                    cls.record_interaction(
                        account,
                        n_id,
                        read=True,
                        note=f"Terdeteksi di server: sudah membaca bab {read_chapters[:5]}"
                    )
                    return result
        except Exception as exc:
            logger.debug(f"Gagal cek riwayat baca di server untuk {email}: {exc}")

        return result

    @classmethod
    def get_stats_for_novel(cls, novel_id: str, all_accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Menghitung ringkasan statistik akun untuk novel target tertentu."""
        cls._ensure_loaded()
        n_id = cls.clean_novel_id(novel_id)
        fresh, used = cls.filter_fresh_accounts(all_accounts, n_id)
        return {
            "novel_id": n_id,
            "total_accounts": len(all_accounts),
            "fresh_accounts_count": len(fresh),
            "used_accounts_count": len(used),
        }
