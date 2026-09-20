"""
Bridge penghubung antara Backend Web (FastAPI) dan Mesin Bot (core).
Menyediakan antarmuka asinkron dengan event streaming untuk Server-Sent Events (SSE),
serta pemotongan saldo real-time per log sukses.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

# Tambahkan web_app/core ke sys.path
CORE_DIR = Path(__file__).parent / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from token_manager import TokenManager, OWNER_WHATSAPP
from proxy_manager import (
    ProxyManager, default_proxy_manager, HypeProxyClient,
    get_weighted_royalty_country, SUPPORTED_QUARTERFULL_COUNTRIES, resolve_service_country
)
from auto_reader import NovelTargetResolver, MemberReaderSession, GuestReaderSession
from full_auto_runner import FullAutoWorker
from interaction_manager import SocialInteractionBot
from account_history import AccountHistoryManager

logger = logging.getLogger("WebBotBridge")

# Registry tugas aktif di memori
ACTIVE_TASKS: Dict[str, Dict[str, Any]] = {}


class WebTask:
    """Representasi tugas bot yang sedang berjalan dengan SSE queue."""

    def __init__(self, task_id: str, token_code: str, novel_id: str, mode: str, config: Dict[str, Any]):
        self.task_id = task_id
        self.token_code = token_code
        self.novel_id = novel_id
        self.mode = mode
        self.config = config
        self.subscribers: Set[asyncio.Queue] = set()
        self.logs_history: List[Dict[str, Any]] = []
        self.is_running = True
        self.is_cancelled = False
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.novel_info: Dict[str, Any] = {}
        self.stats = {
            "accounts_total": 0,
            "accounts_done": 0,
            "chapters_read": 0,
            "likes": 0,
            "bookmarks": 0,
            "follows": 0,
            "total_spent": 0,
            "current_balance": 0,
        }

    def subscribe(self) -> asyncio.Queue:
        """Mendaftarkan listener SSE baru."""
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Menghapus listener SSE saat koneksi ditutup atau refresh browser."""
        self.subscribers.discard(q)

    async def emit_log(self, message: str, level: str = "info", extra: Optional[Dict[str, Any]] = None) -> None:
        """Mengirim pesan log ke semua subscriber SSE dan menyimpan history untuk replay."""
        payload = {
            "type": "log",
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        if extra:
            payload.update(extra)
        self.logs_history.append(payload)
        if len(self.logs_history) > 300:
            self.logs_history.pop(0)

        for q in list(self.subscribers):
            try:
                await q.put(payload)
            except Exception:
                pass

    async def emit_stats(self) -> None:
        """Mengirim update metrik statistik real-time ke semua subscriber."""
        payload = {
            "type": "stats",
            "stats": self.stats,
        }
        for q in list(self.subscribers):
            try:
                await q.put(payload)
            except Exception:
                pass

    async def emit_done(self, wa_link: str) -> None:
        """Mengirim sinyal tugas selesai ke semua subscriber."""
        payload = {
            "type": "done",
            "stats": self.stats,
            "wa_link": wa_link,
        }
        for q in list(self.subscribers):
            try:
                await q.put(payload)
            except Exception:
                pass

    def cancel(self) -> None:
        """Membatalkan eksekusi tugas."""
        self.is_cancelled = True
        self.is_running = False


class BotBridge:
    """Orkestrator bot untuk antarmuka web dengan alokasi server terisolasi per token."""

    @classmethod
    def load_accounts(cls, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Membaca seluruh akun dari web_app/core/akun.txt."""
        accounts_file = CORE_DIR / "akun.txt"
        if not accounts_file.exists():
            accounts_file = Path(__file__).parent.parent / "akun.txt"
        
        accounts = []
        if accounts_file.exists():
            with open(accounts_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        try:
                            acc = json.loads(line)
                            if acc.get("access_token") and acc.get("user_id"):
                                accounts.append(acc)
                        except Exception:
                            pass
        if limit and limit > 0:
            return accounts[:limit]
        return accounts

    @classmethod
    def get_token_account_allocation(
        cls,
        token_code: str,
        count: int = 5,
        target_country: Optional[str] = None,
        novel_id: Optional[str] = None,
        exclude_used: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Mengalokasikan partisi akun server yang unik dan terisolasi untuk tiap token.
        Mencegah tabrakan akun antar token berbeda menggunakan consistent token hashing.
        Mendukung pemfilteran pool akun berdasarkan lock country (misal: ID, US, JP, dll).
        Mendukung pengecualian akun yang sudah pernah berinteraksi jika novel_id & exclude_used diaktifkan.
        """
        all_accounts = cls.load_accounts()
        if not all_accounts:
            return []
        
        # Filter berdasarkan negara jika user memilih lock country spesifik
        if target_country and target_country.upper().strip() not in ("RANDOM", "ALL", "AUTO"):
            req_cc = target_country.upper().strip()
            if req_cc not in SUPPORTED_QUARTERFULL_COUNTRIES:
                req_cc = "ID"
            matching_accounts = [acc for acc in all_accounts if acc.get("country", "").upper() == req_cc]
            candidate_pool = matching_accounts if matching_accounts else all_accounts
        else:
            candidate_pool = all_accounts

        if novel_id and exclude_used:
            fresh, _ = AccountHistoryManager.filter_fresh_accounts(candidate_pool, novel_id)
            if fresh:
                candidate_pool = fresh

        if not candidate_pool:
            return []
        
        # Hitung hash unik dari token_code
        token_hash = int(hashlib.sha256(token_code.strip().upper().encode()).hexdigest(), 16)
        total_pool = len(candidate_pool)
        
        # Tentukan titik offset awal partisi berdasarkan token hash
        start_offset = token_hash % total_pool
        
        # Ambil subset akun secara berurutan dengan rotasi circular
        allocated = []
        for i in range(min(count, total_pool)):
            idx = (start_offset + i) % total_pool
            allocated.append(candidate_pool[idx])
            
        return allocated

    @classmethod
    def get_token_dedicated_proxy(cls, token_code: str, target_country: Optional[str] = None) -> Optional[str]:
        """
        Mengalokasikan node proxy dengan Slot Lease Governor agar lalu lintas jaringan
        terbagi merata pada slot yang minim beban, menghindari tabrakan dan perebutan port.
        """
        if not default_proxy_manager or not default_proxy_manager.has_proxies:
            return None

        req_cc = None
        if target_country and target_country.upper().strip() not in ("RANDOM", "ALL", "AUTO"):
            req_cc = target_country.upper().strip()
            if req_cc not in SUPPORTED_QUARTERFULL_COUNTRIES:
                req_cc = "ID"

        sess_id = f"tok_{token_code.lower().replace('-', '')[:8]}"
        return default_proxy_manager.acquire_proxy_slot(country_code=req_cc, session_id=sess_id)

    @classmethod
    async def get_novel_info(cls, raw_url_or_id: str) -> Dict[str, Any]:
        """Mengambil data novel (judul, author, cover, readable chapters) dari link atau ID."""
        novel_id = NovelTargetResolver.extract_novel_id(raw_url_or_id)
        if not novel_id:
            return {"ok": False, "error": "URL atau ID Novel tidak valid (harus berisi 16 karakter hash ID)"}

        try:
            proxy_url = default_proxy_manager.get_proxy() if default_proxy_manager.has_proxies else None
            try:
                details = await NovelTargetResolver.fetch_novel_details(novel_id, proxy=proxy_url)
            except Exception:
                details = await NovelTargetResolver.fetch_novel_details(novel_id, proxy=None)
            
            origin_cc = details.get("origin_country", "ID")
            try:
                chapters = await NovelTargetResolver.fetch_readable_chapters(novel_id, origin_country=origin_cc, proxy=proxy_url)
            except Exception:
                chapters = await NovelTargetResolver.fetch_readable_chapters(novel_id, origin_country=origin_cc, proxy=None)
            
            author_data = details.get("author", {})
            author_pid = author_data.get("profile_id") or author_data.get("hash_id") or author_data.get("id") or details.get("author_id")
            return {
                "ok": True,
                "novel_id": novel_id,
                "title": details.get("title", f"Novel #{novel_id}"),
                "author": author_data.get("pen_name") or author_data.get("name") or author_data.get("nickname") or "Penulis",
                "author_id": author_data.get("hash_id") or author_data.get("id"),
                "author_profile_id": author_pid,
                "cover_url": details.get("cover_url") or details.get("cover") or details.get("thumbnail"),
                "total_chapters": len(chapters),
                "synopsis": str(details.get("synopsis", details.get("description", "")))[:250],
                "origin_country": origin_cc,
                "is_adult_only": bool(details.get("is_adult_only", False)),
                "chapters": chapters,
                "chapters_preview": [{"id": c.get("id") or c.get("hash_id"), "num": c.get("chapter_num"), "title": c.get("title")} for c in chapters[:10]],
            }
        except Exception as exc:
            return {"ok": False, "error": f"Gagal mengambil data novel: {exc}", "novel_id": novel_id}

    @classmethod
    async def run_task_lifecycle(cls, task: WebTask) -> None:
        """Siklus hidup eksekusi bot di background dengan pemotongan saldo per sukses."""
        ACTIVE_TASKS[task.task_id] = {"task": task, "status": "running"}
        token_code = task.token_code
        mode = task.mode
        cfg = task.config
        is_free_trial = (mode == "free_trial" or token_code == "FREE_TRIAL")

        try:
            # 1. Verifikasi saldo awal jika bukan free trial
            if not is_free_trial:
                token = TokenManager.get_token(token_code)
                if not token:
                    await task.emit_log("Token akses tidak valid atau tidak ditemukan!", level="error")
                    return

                task.stats["current_balance"] = token.get("current_balance", 0)
                await task.emit_log(f"Token terverifikasi: {token_code} (Saldo: Rp {task.stats['current_balance']:,})", level="info")
                await task.emit_stats()
            else:
                task.stats["current_balance"] = 0
                await task.emit_log("🎁 Memulai sesi Free Trial Gratis (Tanpa Token Akses)...", level="info")
                await task.emit_stats()

            # Pastikan proxy ter-update jika owner baru saja menambah/membeli proxy baru
            if default_proxy_manager:
                default_proxy_manager.reload_if_modified()
                if default_proxy_manager.is_hypeproxy:
                    threading.Thread(target=default_proxy_manager.sync_from_hypeproxy, daemon=True).start()

            # 2. Resolusi Info Novel
            await task.emit_log(f"Menginspeksi novel target ID: {task.novel_id}...", level="info")
            novel_info = await cls.get_novel_info(task.novel_id)
            if not novel_info.get("ok"):
                await task.emit_log(f"Gagal memproses novel: {novel_info.get('error')}", level="error")
                return

            task.novel_info = novel_info
            await task.emit_log(
                f"Target Novel: \"{novel_info['title']}\" oleh {novel_info['author']} "
                f"({novel_info['total_chapters']} Bab Tersedia)",
                level="success"
            )

            # 3. Eksekusi Berdasarkan Mode
            if is_free_trial:
                await cls._execute_free_trial(task)
            elif mode in ("full_auto", "member_read"):
                await cls._execute_member_readers(task)
            elif mode == "guest_read":
                await cls._execute_guest_readers(task)
            elif mode in ("like_only", "bookmark_only", "follow_only"):
                await cls._execute_single_interactions(task)
            else:
                await task.emit_log(f"Mode '{mode}' tidak dikenali.", level="error")

        except asyncio.CancelledError:
            await task.emit_log("Tugas dibatalkan oleh pengguna.", level="warning")
        except Exception as exc:
            await task.emit_log(f"Terjadi kesalahan internal: {exc}", level="error")
        finally:
            task.is_running = False
            if not is_free_trial:
                token = TokenManager.get_token(token_code)
                final_balance = token.get("current_balance", 0) if token else 0
                task.stats["current_balance"] = final_balance
                await task.emit_log(
                    f"Tugas selesai. Total sesi sukses: {task.stats['accounts_done']} | "
                    f"Total bab dibaca: {task.stats['chapters_read']} | Sisa saldo: Rp {final_balance:,}",
                    level="success"
                )
                await task.emit_done(TokenManager.get_whatsapp_url(token_code, 20000))
            else:
                await task.emit_log(
                    f"🎉 Free Trial Selesai! Sesi sukses: {task.stats['accounts_done']} akun | "
                    f"Like: {task.stats['likes']} | Follow: {task.stats['follows']}",
                    level="success"
                )
                await task.emit_done(TokenManager.get_whatsapp_url("FREE_TRIAL", 20000))
            if task.task_id in ACTIVE_TASKS:
                ACTIVE_TASKS[task.task_id]["status"] = "completed"
            asyncio.create_task(cls._cleanup_task_later(task.task_id, delay=300))

    @classmethod
    async def _cleanup_task_later(cls, task_id: str, delay: int = 300) -> None:
        """Membersihkan task selesai dari memori setelah delay tertentu."""
        try:
            await asyncio.sleep(delay)
            ACTIVE_TASKS.pop(task_id, None)
        except Exception:
            pass

    @classmethod
    async def _execute_member_readers(cls, task: WebTask) -> None:
        """
        Menjalankan pembaca akun Valid Readers (baca bab + like + bookmark + follow).
        Dilengkapi logika proteksi anti-duplikasi:
        Jika sebuah akun terdeteksi sudah pernah membaca, menyukai (like), atau menyimpan
        (bookmark) buku ini, maka akun tersebut OTOMATIS TIDAK DIGUNAKAN LAGI dan dilewati (Skip),
        lalu sistem beralih ke akun segar (fresh) berikutnya hingga target sesi terpenuhi.
        """
        target_country = (task.config.get("country") or "RANDOM").upper().strip()
        is_locked = target_country not in ("RANDOM", "ALL", "AUTO")
        if is_locked and target_country not in SUPPORTED_QUARTERFULL_COUNTRIES:
            await task.emit_log(
                f"[GEO] Target negara '{target_country}' tidak didukung Quarterfull. Dialihkan otomatis ke 'ID'.",
                level="warning"
            )
            target_country = "ID"
        country_display = target_country if is_locked else "Global / Multi-Negara"

        target_sessions = min(int(task.config.get("accounts_count", 5)), 100)
        novel_id = task.novel_id

        # 1. Ambil seluruh kandidat akun yang cocok dengan alokasi token & negara
        all_candidates = cls.get_token_account_allocation(
            task.token_code,
            count=9999,
            target_country=target_country
        )
        if not all_candidates:
            await task.emit_log("Antrean node sedang penuh atau belum ada akun terdaftar.", level="error")
            return

        # 2. Filter akun yang belum pernah berinteraksi berdasarkan database riwayat lokal
        fresh_accounts, used_accounts = AccountHistoryManager.filter_fresh_accounts(all_candidates, novel_id)
        if used_accounts:
            await task.emit_log(
                f"🛡️ Anti-Duplikasi: {len(used_accounts)} akun dilewati karena terdeteksi sudah pernah membaca/like/simpan buku ini.",
                level="info"
            )

        if not fresh_accounts:
            await task.emit_log(
                f"⚠️ Seluruh akun yang tersedia ({len(all_candidates)} akun) sudah pernah membaca/menyukai/menyimpan buku ini. Tidak ada akun baru yang dapat digunakan.",
                level="warning"
            )
            return

        task.stats["accounts_total"] = min(target_sessions, len(fresh_accounts))
        proxy_mgr = default_proxy_manager
        delay_sec = float(task.config.get("reading_delay", 5.0))

        await task.emit_log(
            f"Memulai sesi Valid Readers (Target: {target_sessions} Sesi Unik | Tersedia: {len(fresh_accounts)} Akun Segar) | "
            f"Target Wilayah: {country_display} | Termasuk Baca Bab + Suka + Simpan + Ikuti Penulis",
            level="info"
        )

        successful_sessions = 0
        candidate_idx = 0

        for acc in fresh_accounts:
            if task.is_cancelled:
                await task.emit_log("Tugas dibatalkan oleh pengguna. Saldo sesi yang telah membaca bab tetap dipotong.", level="warning")
                break

            if successful_sessions >= target_sessions:
                break

            candidate_idx += 1

            # Cek saldo token apakah masih mencukupi tarif 1 sesi
            valid_reader_rate = TokenManager.get_rate("valid_reader")
            token = TokenManager.get_token(task.token_code)
            curr_bal = token.get("current_balance", 0) if token else 0
            if curr_bal < valid_reader_rate:
                await task.emit_log(
                    f"Saldo tidak mencukupi (Sisa: Rp {curr_bal:,}). Dibutuhkan Rp {valid_reader_rate:,} untuk sesi berikutnya. "
                    f"Sesi dihentikan. Hubungi WhatsApp untuk isi ulang: wa.me/{OWNER_WHATSAPP}",
                    level="warning",
                    extra={"balance_exhausted": True, "wa_url": TokenManager.get_whatsapp_url(task.token_code, 20000)}
                )
                break

            acc_data = dict(acc)
            if is_locked:
                acc_data["country"] = target_country

            proxy_country = target_country if is_locked else acc_data.get("country")
            sess_id = f"mbr_{task.token_code[:6]}_{candidate_idx}_{secrets.token_hex(2)}"
            proxy_url = proxy_mgr.acquire_proxy_slot(country_code=proxy_country, session_id=sess_id) if (proxy_mgr and proxy_mgr.has_proxies) else None

            session = MemberReaderSession(
                worker_id=f"W-{successful_sessions + 1:02d}",
                account_data=acc_data,
                novel_title=task.novel_info.get("title", "Novel"),
                proxy=proxy_url,
                proxy_manager=proxy_mgr,
            )

            try:
                # Validasi Real-time ke Server: Pastikan akun belum pernah membaca/like/bookmark di server
                try:
                    client = await session.get_client()
                    server_check = await AccountHistoryManager.check_server_interaction(client, acc_data, novel_id)
                    if server_check.get("already_interacted"):
                        await task.emit_log(
                            f"Akun #{candidate_idx} ({acc.get('email')}) dilewati (Skip): Terdeteksi {server_check['reason']} di server. Mencari akun segar berikutnya...",
                            level="warning"
                        )
                        continue
                except Exception as e_check:
                    logger.debug("Pre-check server gagal, melanjutkan dengan akun: %s", e_check)

                # Akun Segar Terverifikasi -> Jalankan Sesi
                curr_worker = successful_sessions + 1
                await task.emit_log(
                    f"[{curr_worker}/{target_sessions}] Menjalankan sesi pembaca unik #{curr_worker} (Akun: {acc.get('email')}, Wilayah: {proxy_country})...",
                    level="info"
                )

                # Ambil bab (gunakan cache hasil inspeksi atau fetch dengan origin country)
                chapters = (task.novel_info or {}).get("chapters")
                if not chapters:
                    origin_cc = (task.novel_info or {}).get("origin_country", "ID")
                    chapters = await NovelTargetResolver.fetch_readable_chapters(novel_id, origin_country=origin_cc, proxy=proxy_url)
                if not chapters:
                    await task.emit_log(f"[{curr_worker}/{target_sessions}] Tidak ada bab yang dapat dibaca.", level="warning")
                    continue

                max_ch = min(len(chapters), int(task.config.get("max_chapters", 5)))
                target_chapters = chapters[:max_ch]

                # Interaksi Sosial (Like, Bookmark, Follow)
                try:
                    client = await session.get_client()

                    # A. Like Novel
                    try:
                        r_like = await client.post(f"/api/v1/novels/{novel_id}/like")
                        if r_like.status_code == 200:
                            l_data = r_like.json()
                            if not l_data.get("is_liked", True):
                                await asyncio.sleep(0.3)
                                await client.post(f"/api/v1/novels/{novel_id}/like")
                            AccountHistoryManager.record_interaction(acc_data, novel_id, liked=True)
                            task.stats["likes"] += 1
                            await task.emit_log(f"[{curr_worker}/{target_sessions}] Berhasil menyukai (Like) novel.", level="info")
                        else:
                            await task.emit_log(f"[{curr_worker}/{target_sessions}] Respon Like server: HTTP {r_like.status_code}", level="warning")
                    except Exception as ex_like:
                        await task.emit_log(f"[{curr_worker}/{target_sessions}] Catatan Like: {ex_like}", level="warning")

                    await asyncio.sleep(0.5)

                    # B. Bookmark Novel
                    try:
                        r_bm = await client.post(f"/api/v1/novels/{novel_id}/bookmark")
                        if r_bm.status_code == 200:
                            b_data = r_bm.json()
                            if not b_data.get("is_saved", True):
                                await asyncio.sleep(0.3)
                                await client.post(f"/api/v1/novels/{novel_id}/bookmark")
                            AccountHistoryManager.record_interaction(acc_data, novel_id, bookmarked=True)
                            task.stats["bookmarks"] += 1
                            await task.emit_log(f"[{curr_worker}/{target_sessions}] Berhasil menyimpan (Bookmark) novel.", level="info")
                        else:
                            await task.emit_log(f"[{curr_worker}/{target_sessions}] Respon Bookmark server: HTTP {r_bm.status_code}", level="warning")
                    except Exception as ex_bm:
                        await task.emit_log(f"[{curr_worker}/{target_sessions}] Catatan Bookmark: {ex_bm}", level="warning")

                    await asyncio.sleep(0.5)

                    # C. Follow Author jika profile_id tersedia
                    author_pid = (task.novel_info or {}).get("author_profile_id")
                    if author_pid:
                        try:
                            r_fol = await client.put(f"/api/v1/social/profiles/{author_pid}/follow")
                            if r_fol.status_code in (200, 204):
                                task.stats["follows"] += 1
                                await task.emit_log(f"[{curr_worker}/{target_sessions}] Berhasil mengikuti (Follow) penulis novel.", level="info")
                        except Exception as ex_fol:
                            await task.emit_log(f"[{curr_worker}/{target_sessions}] Catatan Follow: {ex_fol}", level="warning")

                except Exception as e_social:
                    await task.emit_log(f"[{curr_worker}/{target_sessions}] Catatan interaksi sosial: {e_social}", level="warning")

                # Eksekusi Pembacaan Bab
                first_ch_read = False
                session_read_success = False

                for ch_idx, ch in enumerate(target_chapters, 1):
                    if task.is_cancelled:
                        break
                    try:
                        ok, msg = await session.read_chapter(novel_id, ch, reading_delay_sec=delay_sec)
                        if ok:
                            task.stats["chapters_read"] += 1
                            session_read_success = True
                            ch_num_val = ch.get("chapter_num", ch_idx)
                            AccountHistoryManager.record_interaction(acc_data, novel_id, read=True, chapter_num=ch_num_val)
                            await task.emit_log(f"[{curr_worker}/{target_sessions}] Sukses membaca Bab #{ch_num_val}", level="info")

                            # Potong saldo segera saat bab pertama berhasil dibaca
                            if not first_ch_read:
                                rate = TokenManager.get_rate("valid_reader")
                                deduct_ok, new_bal, d_msg = TokenManager.deduct_balance(
                                    task.token_code,
                                    item_type="valid_reader",
                                    quantity=1,
                                    note=f"Sesi akun #{curr_worker} ({acc.get('email')}) membaca bab {ch_num_val}"
                                )
                                if deduct_ok:
                                    task.stats["total_spent"] += rate
                                    task.stats["current_balance"] = new_bal
                                    await task.emit_log(f"[{curr_worker}/{target_sessions}] Pembayaran sesi sukses (-Rp {rate:,}). Sisa Saldo: Rp {new_bal:,}", level="success")
                                first_ch_read = True

                            await task.emit_stats()
                        else:
                            await task.emit_log(f"[{curr_worker}/{target_sessions}] Bab #{ch.get('chapter_num', ch_idx)} tidak tercatat: {msg}", level="warning")
                    except Exception as e:
                        await task.emit_log(f"[{curr_worker}/{target_sessions}] Kesalahan membaca bab: {e}", level="warning")

                    await asyncio.sleep(delay_sec)

                if session_read_success:
                    successful_sessions += 1
                    task.stats["accounts_done"] = successful_sessions
                    await task.emit_stats()
                    if proxy_url and proxy_mgr:
                        proxy_mgr.mark_used(proxy_url)
            finally:
                if proxy_url and proxy_mgr:
                    proxy_mgr.release_proxy_slot(proxy_url)
                try:
                    await session.close()
                except Exception:
                    pass

            # Jeda antar sesi pembaca unik
            await asyncio.sleep(1.5)

        if successful_sessions >= target_sessions:
            await task.emit_log(f"Target {target_sessions} sesi pembaca unik berhasil diselesaikan secara sempurna!", level="success")
        elif not task.is_cancelled:
            await task.emit_log(f"Selesai dengan {successful_sessions}/{target_sessions} sesi unik. Seluruh stok akun segar untuk buku ini telah selesai digunakan.", level="info")

    @classmethod
    async def _execute_guest_readers(cls, task: WebTask) -> None:
        """Menjalankan pembaca tamu (Guest Heartbeat Simulator) masif tanpa otentikasi login."""
        guest_count = int(task.config.get("accounts_count", 20))
        target_country = (task.config.get("country") or "RANDOM").upper().strip()
        is_locked = target_country not in ("RANDOM", "ALL", "AUTO")
        if is_locked and target_country not in SUPPORTED_QUARTERFULL_COUNTRIES:
            await task.emit_log(
                f"[GEO] Target negara '{target_country}' tidak didukung resmi oleh Quarterfull. Mengalihkan otomatis ke 'ID'.",
                level="warning"
            )
            target_country = "ID"
        country_display = target_country if is_locked else "Global / Multi-Negara"

        task.stats["accounts_total"] = guest_count
        novel_id = task.novel_id
        proxy_mgr = default_proxy_manager

        await task.emit_log(
            f"Memulai sesi Pembaca Tamu ({guest_count} Sesi) | Target Wilayah: {country_display} | Simulasi Heartbeat & Dwell...",
            level="info"
        )

        for i in range(1, guest_count + 1):
            if task.is_cancelled:
                await task.emit_log("Tugas dibatalkan oleh pengguna.", level="warning")
                break

            if is_locked:
                proxy_cc = target_country
            else:
                try:
                    proxy_cc = get_weighted_royalty_country()
                except Exception:
                    proxy_cc = "ID"

            sess_id = f"gst_{task.token_code[:6]}_{i}_{secrets.token_hex(2)}"
            proxy_url = proxy_mgr.acquire_proxy_slot(country_code=proxy_cc, session_id=sess_id) if (proxy_mgr and proxy_mgr.has_proxies) else None

            session = GuestReaderSession(
                worker_id=f"G-{i:03d}",
                country=proxy_cc,
                novel_title=task.novel_info.get("title", "Novel"),
                proxy=proxy_url,
                proxy_manager=proxy_mgr,
            )

            try:
                chapters = (task.novel_info or {}).get("chapters")
                if not chapters:
                    origin_cc = (task.novel_info or {}).get("origin_country", "ID")
                    chapters = await NovelTargetResolver.fetch_readable_chapters(novel_id, origin_country=origin_cc, proxy=proxy_url)
                if not chapters:
                    await task.emit_log(f"[{i}/{guest_count}] Tidak ada bab yang dapat dibaca.", level="warning")
                    continue

                target_ch = chapters[0]

                ok, msg = await session.read_guest_session(novel_id, target_ch, dwell_seconds=float(task.config.get("reading_delay", 4.0)))
                if ok:
                    task.stats["accounts_done"] += 1
                    task.stats["chapters_read"] += 1

                    gr_rate = TokenManager.get_rate("guest_reader")
                    deduct_ok, new_bal, d_msg = TokenManager.deduct_balance(
                        task.token_code,
                        item_type="guest_reader",
                        quantity=1,
                        note=f"Sesi pembaca tamu #{task.stats['accounts_done']}"
                    )
                    if deduct_ok:
                        task.stats["total_spent"] += gr_rate
                        task.stats["current_balance"] = new_bal
                        await task.emit_log(f"[{i}/{guest_count}] Sesi Tamu #{i} sukses (-Rp {gr_rate:,}). Sisa Saldo: Rp {new_bal:,}", level="success")
                    else:
                        await task.emit_log(f"[{i}/{guest_count}] Saldo token tidak mencukupi untuk melanjutkan sesi tamu.", level="error")
                        break
                    await task.emit_stats()
                    if proxy_url and proxy_mgr:
                        proxy_mgr.mark_used(proxy_url)
                else:
                    await task.emit_log(f"[{i}/{guest_count}] Sesi Tamu #{i} gagal: {msg}", level="warning")
            except Exception as e:
                await task.emit_log(f"[{i}/{guest_count}] Exception sesi tamu #{i}: {e}", level="warning")
            finally:
                if proxy_url and proxy_mgr:
                    proxy_mgr.release_proxy_slot(proxy_url)
                try:
                    await session.close()
                except Exception:
                    pass

            await asyncio.sleep(0.5)

    @classmethod
    async def _execute_single_interactions(cls, task: WebTask) -> None:
        """Menjalankan like/bookmark/follow massal dengan akun partisi token, dengan proteksi anti-duplikasi."""
        target_country = (task.config.get("country") or "RANDOM").upper().strip()
        is_locked = target_country not in ("RANDOM", "ALL", "AUTO")
        if is_locked and target_country not in SUPPORTED_QUARTERFULL_COUNTRIES:
            await task.emit_log(
                f"[GEO] Target negara '{target_country}' tidak didukung Quarterfull. Dialihkan otomatis ke 'ID'.",
                level="warning"
            )
            target_country = "ID"
        country_display = target_country if is_locked else "Global / Multi-Negara"

        target_count = min(int(task.config.get("accounts_count", 5)), 100)
        novel_id = task.novel_id
        all_candidates = cls.get_token_account_allocation(task.token_code, count=9999, target_country=target_country)
        fresh_accounts, _ = AccountHistoryManager.filter_fresh_accounts(all_candidates, novel_id)

        mode = task.mode
        item_name = "Like" if mode == "like_only" else ("Bookmark" if mode == "bookmark_only" else "Follow")
        await task.emit_log(f"Memulai pengiriman {item_name} (Target: {target_count} Akun Unik | Tersedia: {len(fresh_accounts)}) | Target Wilayah: {country_display}...", level="info")

        proxy_mgr = default_proxy_manager

        successful_count = 0
        for acc in fresh_accounts:
            if task.is_cancelled or successful_count >= target_count:
                break
            acc_data = dict(acc)
            if is_locked:
                acc_data["country"] = target_country

            proxy_country = target_country if is_locked else acc_data.get("country")
            sess_id = f"si_{task.token_code[:6]}_{successful_count + 1}_{secrets.token_hex(2)}"
            proxy_url = proxy_mgr.acquire_proxy_slot(country_code=proxy_country, session_id=sess_id) if (proxy_mgr and proxy_mgr.has_proxies) else None
            session = MemberReaderSession(
                worker_id=f"I-{successful_count + 1:02d}",
                account_data=acc_data,
                novel_title=task.novel_info.get("title", "Novel"),
                proxy=proxy_url,
                proxy_manager=proxy_mgr,
            )

            try:
                client = await session.get_client()
                server_check = await AccountHistoryManager.check_server_interaction(client, acc_data, novel_id)
                if server_check.get("already_interacted"):
                    await task.emit_log(f"Akun ({acc.get('email')}) dilewati: Terdeteksi {server_check['reason']}.", level="warning")
                    continue

                success = False
                if mode == "like_only":
                    r = await client.post(f"/api/v1/novels/{novel_id}/like")
                    if r.status_code == 200:
                        AccountHistoryManager.record_interaction(acc_data, novel_id, liked=True)
                        task.stats["likes"] += 1
                        success = True
                elif mode == "bookmark_only":
                    r = await client.post(f"/api/v1/novels/{novel_id}/bookmark")
                    if r.status_code == 200:
                        AccountHistoryManager.record_interaction(acc_data, novel_id, bookmarked=True)
                        task.stats["bookmarks"] += 1
                        success = True
                elif mode == "follow_only":
                    author_pid = (task.novel_info or {}).get("author_profile_id")
                    if author_pid:
                        r = await client.put(f"/api/v1/social/profiles/{author_pid}/follow")
                        if r.status_code in (200, 204):
                            task.stats["follows"] += 1
                            success = True
                    else:
                        await task.emit_log("Profile ID penulis tidak ditemukan.", level="warning")
                        break

                if success:
                    successful_count += 1
                    task.stats["accounts_done"] = successful_count
                    rate_key = "like" if mode == "like_only" else ("bookmark" if mode == "bookmark_only" else "follow")
                    interaction_rate = TokenManager.get_rate(rate_key)
                    
                    if interaction_rate > 0:
                        deduct_ok, new_bal, d_msg = TokenManager.deduct_balance(
                            task.token_code,
                            item_type=rate_key,
                            quantity=1,
                            note=f"Kirim {item_name} via akun #{successful_count}"
                        )
                        if deduct_ok:
                            task.stats["total_spent"] += interaction_rate
                            task.stats["current_balance"] = new_bal
                            await task.emit_log(f"[{successful_count}/{target_count}] Berhasil mengirim {item_name} via akun ({acc.get('email')}) (-Rp {interaction_rate:,}). Sisa Saldo: Rp {new_bal:,}", level="success")
                        else:
                            await task.emit_log(f"Saldo token tidak mencukupi untuk interaksi {item_name}.", level="error")
                            break
                    else:
                        await task.emit_log(f"[{successful_count}/{target_count}] Berhasil mengirim {item_name} via akun ({acc.get('email')})", level="info")
                    
                    await task.emit_stats()
                    if proxy_url and proxy_mgr:
                        proxy_mgr.mark_used(proxy_url)
                else:
                    await task.emit_log(f"Respon server {item_name} tidak sukses via ({acc.get('email')})", level="warning")
            except Exception as e:
                await task.emit_log(f"Gagal kirim {item_name}: {e}", level="warning")
            finally:
                if proxy_url and proxy_mgr:
                    proxy_mgr.release_proxy_slot(proxy_url)
                try:
                    await session.close()
                except Exception:
                    pass

            await asyncio.sleep(1.0)

    @classmethod
    async def _execute_free_trial(cls, task: WebTask) -> None:
        """
        Menjalankan sesi Free Trial tanpa token:
        - Login N akun resmi (dinamis dari owner config)
        - Like novel
        - Follow profil penulis
        - Emisi progress real-time ke Live Console client
        """
        novel_id = task.novel_id
        trial_cfg = TokenManager.get_pricing_config().get("free_trial", {})
        target_accounts = int(task.config.get("accounts_count") or trial_cfg.get("accounts_count", 5))
        do_like = bool(task.config.get("do_like", trial_cfg.get("do_like", True)))
        do_follow = bool(task.config.get("do_follow", trial_cfg.get("do_follow", True)))

        all_candidates = cls.get_token_account_allocation("FREE_TRIAL", count=9999)
        if not all_candidates:
            all_candidates = cls.load_accounts()

        fresh_accounts, _ = AccountHistoryManager.filter_fresh_accounts(all_candidates, novel_id)
        if not fresh_accounts:
            fresh_accounts = all_candidates

        if not fresh_accounts:
            await task.emit_log("Pool akun server sedang tidak tersedia untuk Free Trial. Hubungi Admin.", level="error")
            return

        task.stats["accounts_total"] = min(target_accounts, len(fresh_accounts))
        await task.emit_log(
            f"🎁 Menjalankan Free Trial: Target {target_accounts} Akun Resmi | "
            f"Suka (Like): {'Ya' if do_like else 'Tidak'} | Ikuti (Follow): {'Ya' if do_follow else 'Tidak'}",
            level="info"
        )

        proxy_mgr = default_proxy_manager
        author_pid = (task.novel_info or {}).get("author_profile_id") or (task.novel_info or {}).get("author_id")

        successful_count = 0
        for idx, acc in enumerate(fresh_accounts, 1):
            if task.is_cancelled or successful_count >= target_accounts:
                break

            curr_worker = successful_count + 1
            acc_data = dict(acc)
            sess_id = f"trial_{curr_worker}_{secrets.token_hex(2)}"
            proxy_url = proxy_mgr.acquire_proxy_slot(session_id=sess_id) if (proxy_mgr and proxy_mgr.has_proxies) else None

            session = MemberReaderSession(
                worker_id=f"TRIAL-{curr_worker:02d}",
                account_data=acc_data,
                novel_title=task.novel_info.get("title", "Novel"),
                proxy=proxy_url,
                proxy_manager=proxy_mgr,
            )

            try:
                client = await session.get_client()
                email_raw = acc.get("email", f"akun_{idx}")
                if "@" in email_raw:
                    name_p, dom_p = email_raw.split("@", 1)
                    email_masked = f"{name_p[:3]}***@{dom_p}"
                else:
                    email_masked = f"{email_raw[:5]}***"

                await task.emit_log(f"[{curr_worker}/{target_accounts}] Akun {email_masked} berhasil Login.", level="info")

                # Like Novel
                if do_like:
                    try:
                        r_like = await client.post(f"/api/v1/novels/{novel_id}/like")
                        if r_like.status_code == 200:
                            task.stats["likes"] += 1
                            AccountHistoryManager.record_interaction(acc_data, novel_id, liked=True)
                            await task.emit_log(f"[{curr_worker}/{target_accounts}] Akun {email_masked} berhasil menyukai (Like) novel.", level="info")
                        else:
                            await task.emit_log(f"[{curr_worker}/{target_accounts}] Respon Like server: HTTP {r_like.status_code}", level="warning")
                    except Exception as e_lk:
                        await task.emit_log(f"[{curr_worker}/{target_accounts}] Catatan Like: {e_lk}", level="warning")

                await asyncio.sleep(0.5)

                # Follow Author
                if do_follow and author_pid:
                    try:
                        r_fol = await client.put(f"/api/v1/social/profiles/{author_pid}/follow")
                        if r_fol.status_code in (200, 204):
                            task.stats["follows"] += 1
                            await task.emit_log(f"[{curr_worker}/{target_accounts}] Akun {email_masked} berhasil mengikuti (Follow) penulis.", level="info")
                        else:
                            await task.emit_log(f"[{curr_worker}/{target_accounts}] Respon Follow server: HTTP {r_fol.status_code}", level="warning")
                    except Exception as e_fl:
                        await task.emit_log(f"[{curr_worker}/{target_accounts}] Catatan Follow: {e_fl}", level="warning")

                successful_count += 1
                task.stats["accounts_done"] = successful_count
                await task.emit_stats()
                if proxy_url and proxy_mgr:
                    proxy_mgr.mark_used(proxy_url)

            except Exception as exc:
                await task.emit_log(f"[{curr_worker}/{target_accounts}] Akun gagal menjalankan sesi trial: {exc}", level="warning")
            finally:
                if proxy_url and proxy_mgr:
                    proxy_mgr.release_proxy_slot(proxy_url)
                try:
                    await session.close()
                except Exception:
                    pass

            await asyncio.sleep(1.0)

        if successful_count >= target_accounts:
            await task.emit_log(
                f"🎉 Free Trial Berhasil! {successful_count} Akun telah selesai Login, Memberi Like, & Follow Penulis.",
                level="success"
            )
        else:
            await task.emit_log(
                f"Free Trial selesai: {successful_count}/{target_accounts} akun berhasil diproses.",
                level="info"
            )

