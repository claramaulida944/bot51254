"""
Bridge penghubung antara Backend Web SaaS (FastAPI) dan Mesin Bot Stealth.
Mendukung:
- Multi-Terminal (Sesi bersamaan 1 - 5 terminal per tugas)
- Dynamic Proxy Pool Lease (Locking IDLE, Release on Complete/Cancel)
- Auto-skip Akun & History (Mencegah duplikasi baca, like, follow, bookmark)
- Server-Sent Events (SSE) Live Log per Terminal
- Pemotongan Saldo Rupiah Real-time per Pembaca Sukses
"""

import asyncio
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

# Tambahkan root path & web_app ke sys.path
ROOT_DIR = Path(__file__).parent.parent.resolve()
WEB_APP_DIR = Path(__file__).parent.resolve()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(WEB_APP_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_APP_DIR))

from database import db_session, get_setting, get_all_settings
from proxy_pool import ProxyPoolManager
from account_pool import AccountPoolManager
from auth import AuthManager

# Import engine stealth bot resmi Android
from stealth_bot.client import StealthApiClient
from stealth_bot.timing import ReadingSimulator
from stealth_bot.profile import ProfileGenerator
from stealth_bot.camouflage import CamouflageEngine
from stealth_bot.email_verifier import TempTfVerifier
from stealth_bot.scheduler import format_natural_email

logger = logging.getLogger("WebBotBridge")

ACTIVE_TASKS: Dict[str, "WebTask"] = {}


class WebTask:
    """Representasi tugas bot pembaca dengan dukungan multi-terminal, mode tamu, dan antrean SSE."""

    def __init__(
        self,
        task_id: str,
        user_id: int,
        novel_id: str,
        mode: str,
        target_readers: int = 5,
        concurrent_terminals: int = 1,
        max_chapters: int = 25,
        reading_delay: float = 140.0,
        author_id: str = "",
        novel_title: str = "",
        rate_per_reader: int = 450,
        addon_fee: int = 0,
        addon_guest_conversion: bool = False,
    ):
        self.task_id = task_id
        self.user_id = user_id
        self.novel_id = novel_id
        self.novel_title = novel_title or f"Novel #{novel_id}"
        self.author_id = author_id
        self.mode = mode  # "valid" atau "guest"
        self.target_readers = target_readers
        self.concurrent_terminals = max(1, min(concurrent_terminals, 5))  # 1 sampai 5
        self.max_chapters = max_chapters
        self.reading_delay = reading_delay
        self.rate_per_reader = rate_per_reader
        self.addon_fee = addon_fee
        self.addon_guest_conversion = addon_guest_conversion
        
        self.subscribers: Set[asyncio.Queue] = set()
        self.logs_history: List[Dict[str, Any]] = []
        self.is_running = True
        self.is_cancelled = False
        self.created_at = datetime.now(timezone.utc).isoformat()
        
        self.stats = {
            "target_readers": target_readers,
            "completed_readers": 0,
            "chapters_read": 0,
            "likes": 0,
            "bookmarks": 0,
            "follows": 0,
            "converted_accounts": 0,
            "total_spent": 0,
            "current_balance": 0,
            "active_terminals": self.concurrent_terminals,
            "addon_guest_conversion": self.addon_guest_conversion,
        }
        
        # Tracking proxy yang sedang dipinjam oleh task ini agar aman di-release saat cancel
        self.active_proxies: Set[str] = set()
        self.active_accounts: Set[str] = set()

    def subscribe(self) -> asyncio.Queue:
        """Mendaftarkan listener SSE baru untuk live streaming log."""
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Menghapus listener SSE saat user menutup tab / refresh."""
        self.subscribers.discard(q)

    async def emit_log(self, message: str, level: str = "info", terminal_id: Optional[int] = None) -> None:
        """Mengirim pesan log ke semua subscriber SSE."""
        prefix = f"[Terminal-{terminal_id}] " if terminal_id else ""
        payload = {
            "type": "log",
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "terminal": terminal_id,
            "message": f"{prefix}{message}",
        }
        self.logs_history.append(payload)
        if len(self.logs_history) > 300:
            self.logs_history.pop(0)

        for q in list(self.subscribers):
            try:
                await q.put(payload)
            except Exception:
                pass

    async def emit_stats(self) -> None:
        """Mengirim update metrik statistik real-time dengan kalkulasi estimasi selesai (ETA)."""
        import math
        from datetime import datetime, timedelta

        remaining_readers = max(0, self.stats["target_readers"] - self.stats["completed_readers"])
        if self.mode == "guest":
            # Jika ada addon konversi, butuh waktu tambahan signup + polling OTP (~135s)
            time_per_reader = 135.0 if self.addon_guest_conversion else 75.0
        else:
            time_per_reader = self.max_chapters * self.reading_delay

        rounds = math.ceil(remaining_readers / max(1, self.concurrent_terminals))
        est_seconds_left = int(rounds * time_per_reader) if self.is_running else 0
        finish_time = (datetime.now() + timedelta(seconds=est_seconds_left)).strftime("%H:%M WIB")

        self.stats["estimated_seconds_left"] = est_seconds_left
        self.stats["estimated_finish_time"] = finish_time

        payload = {
            "type": "stats",
            "stats": self.stats,
        }
        for q in list(self.subscribers):
            try:
                await q.put(payload)
            except Exception:
                pass

    async def emit_done(self) -> None:
        """Mengirim sinyal tugas selesai ke web UI."""
        payload = {
            "type": "done",
            "stats": self.stats,
        }
        for q in list(self.subscribers):
            try:
                await q.put(payload)
            except Exception:
                pass

    def cancel(self) -> None:
        """Membatalkan tugas dan membebaskan semua proxy yang dipegang."""
        self.is_cancelled = True
        self.is_running = False
        # Release semua proxy yang sedang aktif
        for proxy_url in list(self.active_proxies):
            ProxyPoolManager.release_proxy(proxy_url)
        self.active_proxies.clear()
        logger.info(f"Task {self.task_id} berhasil dibatalkan dan semua proxy telah di-release.")


class BotBridge:
    """Orkestrator bot utama untuk eksekusi multi-terminal di web."""

    @classmethod
    def extract_novel_id(cls, raw: str) -> Optional[str]:
        """Mengekstrak ID novel 16 karakter dari link atau teks."""
        if not raw:
            return None
        raw = raw.strip()
        query_hash = re.search(r"[?&]hashId=([a-zA-Z0-9]{16})", raw)
        if query_hash:
            return query_hash.group(1)
        match = re.search(r"([a-zA-Z0-9]{16})", raw)
        return match.group(1) if match else None

    @classmethod
    async def get_novel_info(cls, raw_url_or_id: str) -> Dict[str, Any]:
        """Mengambil data novel (judul, author, cover, readable chapters)."""
        novel_id = cls.extract_novel_id(raw_url_or_id)
        if not novel_id:
            return {"ok": False, "error": "URL atau ID Novel tidak valid (harus 16 karakter)"}

        # Pinjam 1 proxy sementara jika ada
        proxy = ProxyPoolManager.acquire_proxy("info_fetch", "worker_info")
        try:
            import httpx
            kwargs = {
                "base_url": "https://api.quarterfull.io",
                "timeout": 15.0,
                "headers": {
                    "user-agent": "okhttp/4.12.0",
                    "accept": "application/json",
                    "platform": "android",
                }
            }
            if proxy:
                kwargs["proxy"] = proxy

            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(f"/api/v1/novels/{novel_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    author_data = data.get("author", {}) or {}
                    author_id = author_data.get("id") or author_data.get("hash_id") or data.get("author_id", "")
                    
                    # Ambil bab
                    c_resp = await client.get(f"/api/v1/novels/{novel_id}/chapters?order=asc")
                    ch_count = 0
                    if c_resp.status_code == 200:
                        c_data = c_resp.json()
                        ch_list = c_data.get("chapters", []) or c_data.get("items", []) or []
                        ch_count = len(ch_list)

                    return {
                        "ok": True,
                        "novel_id": novel_id,
                        "title": data.get("title", f"Novel #{novel_id}"),
                        "author": author_data.get("name") or author_data.get("nickname") or "Penulis",
                        "author_id": str(author_id),
                        "cover_url": data.get("cover_url") or data.get("cover_image_url") or "",
                        "synopsis": (data.get("synopsis") or "")[:200],
                        "total_chapters": ch_count,
                    }
                else:
                    return {"ok": False, "error": f"Server menolak: HTTP {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": f"Gagal mengambil info novel: {str(e)}"}
        finally:
            if proxy:
                ProxyPoolManager.release_proxy(proxy)

    @classmethod
    async def run_task_lifecycle(cls, task: WebTask) -> None:
        """Siklus hidup utama eksekusi tugas bot dengan multi-terminal paralel."""
        ACTIVE_TASKS[task.task_id] = {"task": task}
        
        # Ambil saldo user saat ini
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance FROM users WHERE id = ?;", (task.user_id,))
            row = cursor.fetchone()
            task.stats["current_balance"] = row["balance"] if row else 0

        # Simpan ke tabel tasks
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO tasks (
                task_id, user_id, novel_id, novel_title, target_readers,
                completed_readers, concurrent_terminals, price_per_reader, mode, status
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 'RUNNING');
            """, (
                task.task_id, task.user_id, task.novel_id, task.novel_title,
                task.target_readers, task.concurrent_terminals, task.rate_per_reader, task.mode
            ))

        mode_name = "Akun Valid (25 Bab)" if task.mode == "valid" else "Pembaca Tamu (Guest)"
        await task.emit_log(f"🚀 Memulai tugas [{mode_name}] untuk '{task.novel_title}' (Target: {task.target_readers} Pembaca, {task.concurrent_terminals} Terminal, Tarif: Rp {task.rate_per_reader:,}/pembaca)...", "info")
        await task.emit_stats()

        # Shared state antrean pembaca yang belum selesai
        remaining_lock = asyncio.Lock()
        completed_count = 0

        async def terminal_worker(term_id: int):
            nonlocal completed_count
            
            # Berikan jeda staggered start untuk tiap terminal
            if term_id > 1:
                stagger_delay = random.uniform(5.0, 15.0) * (term_id - 1)
                await task.emit_log(f"Menunggu {int(stagger_delay)} detik agar tidak bentrok dengan terminal lain...", "debug", term_id)
                await asyncio.sleep(stagger_delay)

            while task.is_running and not task.is_cancelled:
                # Cek apakah target sudah tercapai
                async with remaining_lock:
                    if completed_count >= task.target_readers:
                        break
                
                # Cek saldo user mencukupi untuk 1 pembaca
                with db_session() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT balance FROM users WHERE id = ?;", (task.user_id,))
                    u_row = cursor.fetchone()
                    bal = u_row["balance"] if u_row else 0
                    if bal < task.rate_per_reader:
                        await task.emit_log(f"⚠️ Saldo tidak mencukupi (Sisa Rp {bal:,}, butuh Rp {task.rate_per_reader:,}). Tugas dihentikan.", "warn", term_id)
                        task.cancel()
                        break

                # -------------------------------------------------------------
                # JALANKAN SESUAI MODE (GUEST VS VALID)
                # -------------------------------------------------------------
                if task.mode == "guest":
                    # Mode Tamu: Menggunakan anonymous session & rotasi proxy
                    proxy_url = ProxyPoolManager.acquire_proxy(task.task_id, f"Terminal-{term_id}", task.user_id)
                    if not proxy_url:
                        await task.emit_log("⏳ Menunggu slot proxy nganggur...", "warn", term_id)
                        await asyncio.sleep(6.0)
                        continue

                    task.active_proxies.add(proxy_url)
                    try:
                        reader_success = await cls._execute_guest_reader_session(
                            task=task,
                            term_id=term_id,
                            proxy_url=proxy_url
                        )
                        if reader_success:
                            async with remaining_lock:
                                completed_count += 1
                                task.stats["completed_readers"] = completed_count
                                task.stats["total_spent"] += task.rate_per_reader

                            AuthManager.deduct_balance(
                                user_id=task.user_id,
                                amount=task.rate_per_reader,
                                description=f"Pembaca Tamu #{completed_count} ({task.novel_title[:30]})",
                                reference_id=task.task_id
                            )

                            with db_session() as conn:
                                cursor = conn.cursor()
                                cursor.execute("SELECT balance FROM users WHERE id = ?;", (task.user_id,))
                                ubal = cursor.fetchone()["balance"]
                                task.stats["current_balance"] = ubal
                                cursor.execute("UPDATE tasks SET completed_readers = ?, updated_at = datetime('now') WHERE task_id = ?;", (completed_count, task.task_id))

                            await task.emit_log(f"✅ [SUKSES] Pembaca Tamu #{completed_count} tuntas! Saldo terpotong Rp {task.rate_per_reader:,} (Sisa: Rp {ubal:,})", "success", term_id)
                            await task.emit_stats()
                    except Exception as e:
                        await task.emit_log(f"❌ Kesalahan pada terminal tamu: {str(e)}", "error", term_id)
                    finally:
                        task.active_proxies.discard(proxy_url)
                        ProxyPoolManager.release_proxy(proxy_url)

                else:
                    # Mode Valid: Alokasi akun stealth & anti-duplikasi
                    account_data = AccountPoolManager.get_eligible_account(
                        book_id=task.novel_id,
                        author_id=task.author_id,
                        excluded_emails=list(task.active_accounts)
                    )

                    if not account_data:
                        await task.emit_log("⚠️ Tidak ada akun bot yang memenuhi syarat (semua akun sudah pernah membaca novel ini atau sedang limit harian).", "warn", term_id)
                        await asyncio.sleep(5.0)
                        break

                    bot_email = account_data["email"]
                    task.active_accounts.add(bot_email)

                    proxy_url = ProxyPoolManager.acquire_proxy(task.task_id, f"Terminal-{term_id}", task.user_id)
                    if not proxy_url:
                        await task.emit_log("⏳ Semua proxy sedang digunakan oleh sesi lain. Menunggu slot proxy nganggur...", "warn", term_id)
                        task.active_accounts.discard(bot_email)
                        await asyncio.sleep(8.0)
                        continue

                    task.active_proxies.add(proxy_url)

                    try:
                        reader_success = await cls._execute_single_reader_session(
                            task=task,
                            term_id=term_id,
                            account_data=account_data,
                            proxy_url=proxy_url
                        )

                        if reader_success:
                            async with remaining_lock:
                                completed_count += 1
                                task.stats["completed_readers"] = completed_count
                                task.stats["total_spent"] += task.rate_per_reader

                            AuthManager.deduct_balance(
                                user_id=task.user_id,
                                amount=task.rate_per_reader,
                                description=f"Pembaca Valid #{completed_count} ({task.novel_title[:30]})",
                                reference_id=task.task_id
                            )

                            with db_session() as conn:
                                cursor = conn.cursor()
                                cursor.execute("SELECT balance FROM users WHERE id = ?;", (task.user_id,))
                                ubal = cursor.fetchone()["balance"]
                                task.stats["current_balance"] = ubal

                            AccountPoolManager.record_action(bot_email, task.novel_id, task.author_id, "READ_VALID")

                            with db_session() as conn:
                                cursor = conn.cursor()
                                cursor.execute("UPDATE tasks SET completed_readers = ?, updated_at = datetime('now') WHERE task_id = ?;", (completed_count, task.task_id))

                            await task.emit_log(f"✅ [SUKSES] Pembaca Valid #{completed_count} tuntas! Saldo terpotong Rp {task.rate_per_reader:,} (Sisa: Rp {ubal:,})", "success", term_id)
                            await task.emit_stats()

                    except Exception as e:
                        await task.emit_log(f"❌ Terjadi kesalahan pada terminal: {str(e)}", "error", term_id)
                        ProxyPoolManager.report_failure(proxy_url, str(e))
                    finally:
                        task.active_proxies.discard(proxy_url)
                        task.active_accounts.discard(bot_email)
                        ProxyPoolManager.release_proxy(proxy_url)

                # Delay acak antar pembaca di terminal yang sama
                if task.is_running and not task.is_cancelled:
                    cooldown = random.uniform(4.0, 10.0)
                    await task.emit_log(f"Jeda antarpembaca {int(cooldown)}s...", "debug", term_id)
                    await asyncio.sleep(cooldown)

        # Luncurkan worker terminal sebanyak concurrent_terminals
        workers = [
            asyncio.create_task(terminal_worker(i + 1))
            for i in range(task.concurrent_terminals)
        ]

        await asyncio.gather(*workers, return_exceptions=True)

        # Finalisasi status tugas
        task.is_running = False
        final_status = "CANCELLED" if task.is_cancelled else "COMPLETED"
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE task_id = ?;
            """, (final_status, task.task_id))

        await task.emit_log(f"🏁 Tugas selesai ({final_status}). Total {task.stats['completed_readers']}/{task.target_readers} pembaca tuntas.", "info")
        await task.emit_done()
        ACTIVE_TASKS.pop(task.task_id, None)

    @classmethod
    async def _execute_single_reader_session(
        cls,
        task: WebTask,
        term_id: int,
        account_data: Dict[str, Any],
        proxy_url: str
    ) -> bool:
        """Mengeksekusi pembacaan bab oleh Akun Valid dengan engine StealthApiClient resmi Android."""
        email = account_data["email"]
        nickname = account_data.get("nickname") or email.split("@")[0]
        country = account_data.get("country", "ID")

        await task.emit_log(f"👤 Memulai sesi: {nickname} ({email}) | Asal: {country} | Proxy: {proxy_url.split('@')[-1]}", "info", term_id)

        # Inisialisasi StealthApiClient resmi Android
        profile = ProfileGenerator.generate_profile(country=country)
        bot = StealthApiClient(
            profile=profile,
            access_token=account_data.get("access_token"),
            refresh_token=account_data.get("refresh_token"),
            current_proxy=proxy_url,
            account_data=account_data,
        )

        try:
            # 1. Pastikan sesi aktif & token valid (auto refresh jika kedaluwarsa)
            is_active = await bot.ensure_active_session()
            if not is_active:
                await task.emit_log("Gagal verifikasi login akun. Melewati akun ini.", "warn", term_id)
                return False

            # Update token jika baru
            if bot.access_token != account_data.get("access_token"):
                AccountPoolManager.update_account_tokens(email, bot.access_token, bot.refresh_token or "")

            # Klaim benefit Q harian akun jika belum
            try:
                await bot.claim_daily_q()
            except Exception:
                pass

            # 2. Ambil daftar bab novel
            chapters = await bot.get_novel_chapters(task.novel_id)
            if not chapters:
                await task.emit_log("Gagal memuat daftar bab novel.", "error", term_id)
                return False

            total_avail = len(chapters)
            read_target = min(task.max_chapters, total_avail)

            # 2a. Kamuflase Organik (Discovery Trail & Anti-Fingerprinting)
            # Membaca novel populer lain acak agar akun tidak di-flag bot karena hanya membaca 1 novel terus-menerus
            if random.random() < 0.65:
                camo_list = [
                    ("VqQK9b6Z99bEvYnG", "Billionaire's Secret Bride"),
                    ("kpQJ0dNk7V8eLOvE", "Moonlight Shadow in the Dark"),
                    ("DXMVyb8jKjevAZEJ", "Silent Promises to You"),
                    ("kQWjnegmEDbwZ1p0", "Destined to Reign with Him"),
                ]
                c_id, c_title = random.choice(camo_list)
                c_delay = random.uniform(15.0, 28.0)
                await task.emit_log(
                    f"🎭 [Kamuflase Organik] Membaca Bab 1 novel acak '{c_title}' ({int(c_delay)} detik) agar riwayat akun memiliki jejak perilaku wajar dan tidak dicurigai bot oleh algoritma Quarterfull.",
                    "info", term_id
                )
                try:
                    await bot.send_reading_telemetry(novel_id=c_id, chapter_id="camo_head", chapter_num=1, reading_time_sec=c_delay, depth_percent=100, completed=True)
                except Exception:
                    pass
                
                spent_camo = 0.0
                while spent_camo < c_delay:
                    if not task.is_running or task.is_cancelled:
                        return False
                    step = min(4.0, c_delay - spent_camo)
                    await asyncio.sleep(step)
                    spent_camo += step

            await task.emit_log(f"📖 [Target Novel] Memulai pembacaan {read_target} bab novel '{task.novel_title[:30]}'...", "info", term_id)

            # 3. Interaksi Sosial (HANYA 1X per novel/author seumur hidup)
            # Cek apakah akun ini pernah like
            if not AccountPoolManager.has_performed_action(email, task.novel_id, "LIKE"):
                if random.random() < 0.70:
                    liked = await bot.like_novel(task.novel_id)
                    if liked:
                        AccountPoolManager.record_action(email, task.novel_id, task.author_id, "LIKE")
                        task.stats["likes"] += 1
                        await task.emit_log(f"❤️ [Interaksi Like] Menyukai novel '{task.novel_title[:25]}' untuk menaikkan skor engagement & ranking buku di feed rekomendasi.", "info", term_id)

            # Cek apakah akun ini pernah bookmark (simpan rak buku)
            if not AccountPoolManager.has_performed_action(email, task.novel_id, "BOOKMARK"):
                if random.random() < 0.65:
                    bmed = await bot.bookmark_novel(task.novel_id)
                    if bmed:
                        AccountPoolManager.record_action(email, task.novel_id, task.author_id, "BOOKMARK")
                        task.stats["bookmarks"] += 1
                        await task.emit_log(f"🔖 [Interaksi Simpan] Menyimpan novel ke Rak Buku pembaca untuk memperkuat retensi pembaca novel.", "info", term_id)

            # Cek apakah akun ini pernah follow author
            if task.author_id and not AccountPoolManager.has_performed_action(email, task.author_id, "FOLLOW"):
                if random.random() < 0.40:
                    fllwd = await bot.follow_author(task.author_id)
                    if fllwd:
                        AccountPoolManager.record_action(email, task.novel_id, task.author_id, "FOLLOW")
                        task.stats["follows"] += 1
                        await task.emit_log(f"➕ [Interaksi Follow] Mengikuti akun penulis untuk meningkatkan jumlah pengikut resmi author.", "info", term_id)

            total_read_seconds = 0
            simulator = ReadingSimulator()

            # 4. Loop Membaca Bab dengan Timing Manusia (140s+ per bab)
            for idx in range(read_target):
                if not task.is_running or task.is_cancelled:
                    return False

                ch = chapters[idx]
                ch_id = ch.get("id") or ch.get("hash_id")
                ch_num = idx + 1

                # Ambil teks isi bab jika tersedia untuk estimasi kata nyata (WPM)
                ch_detail = await bot.get_chapter_detail(task.novel_id, ch_id)
                content = ch_detail.get("content", "") if ch_detail else ""
                delay_sec, pace_pct = simulator.calculate_reading_duration(content)
                # Pastikan minimal batas anti-fraud task.reading_delay (default 140s)
                delay_sec = max(delay_sec, task.reading_delay)

                pace_info = f" ({'+' if pace_pct > 0 else ''}{pace_pct}% vs sebelumnya)" if pace_pct != 0 else ""
                await task.emit_log(f"📖 Membaca Bab {ch_num}/{read_target} (Durasi: {int(delay_sec)} detik{pace_info} | Standar WPM Manusia)...", "info", term_id)

                # Simulasi progres membaca bertahap (kuadran 25%, 50%, 75%, 100%)
                spent = 0.0
                milestones = [25, 50, 75, 100]
                m_idx = 0
                while spent < delay_sec:
                    if not task.is_running or task.is_cancelled:
                        return False
                    step = min(5.0, delay_sec - spent)
                    await asyncio.sleep(step)
                    spent += step
                    pct = int((spent / delay_sec) * 100)
                    if m_idx < len(milestones) and pct >= milestones[m_idx]:
                        # Kirim telemetri royalti resmi (Post-view log, Heartbeat, dan Analytics)
                        await bot.send_reading_telemetry(
                            novel_id=task.novel_id,
                            chapter_id=ch_id,
                            chapter_num=ch_num,
                            reading_time_sec=spent,
                            depth_percent=milestones[m_idx],
                            completed=(milestones[m_idx] == 100)
                        )
                        m_idx += 1

                total_read_seconds += int(delay_sec)
                task.stats["chapters_read"] += 1
                await task.emit_log(f"✔️ Bab {ch_num}/{read_target} selesai dibaca secara sah ({int(delay_sec)}s).", "debug", term_id)
                await task.emit_stats()

                # Jeda manusiawi antar bab (5 - 12 detik)
                if idx < read_target - 1:
                    await asyncio.sleep(random.uniform(5.0, 12.0))

            # Catat durasi baca ke profil akun
            AccountPoolManager.update_reading_progress(email, total_read_seconds)
            ProxyPoolManager.report_success(proxy_url)
            return True

        except Exception as e:
            logger.error(f"Error sesi pembaca {email}: {e}")
            ProxyPoolManager.report_failure(proxy_url, str(e))
            return False
        finally:
            await bot.close()

    @classmethod
    async def _execute_guest_reader_session(
        cls,
        task: WebTask,
        term_id: int,
        proxy_url: str
    ) -> bool:
        """
        Mengeksekusi sesi pembaca tamu anonim (Guest Reader) sesuai standar resmi Android Quarterfull:
        1. Emulasi Device Android & Cold-Start Handshake (/api/auth/app-version, /api/auth/flags)
        2. Inisiasi Sesi Tamu Resmi (/api/guest-reading/session) & penanaman cookie qf_guest_reader
        3. Membaca detail & sinopsis novel target (12-20s)
        4. Membaca 1 - 2 bab gratis dengan standar WPM manusia (35 - 75s per bab)
        5. Mengirimkan telemetri resmi guest progress (PUT /api/guest-reading/progress) bertahap
        """
        clean_proxy = proxy_url.split("@")[-1]
        await task.emit_log(f"🌐 [Mode Tamu] Membuka sesi Android Guest resmi via Proxy: {clean_proxy}...", "info", term_id)

        profile = ProfileGenerator.generate_profile()
        bot = StealthApiClient(profile=profile, current_proxy=proxy_url)

        try:
            # 1. Inisiasi sesi tamu resmi (Cold Start Android)
            await task.emit_log("📲 [Mode Tamu] Menjalankan Cold-Start Android & inisiasi sesi tamu resmi (/api/guest-reading/session)...", "debug", term_id)
            ok, msg = await bot.init_guest_session()
            if not ok:
                await task.emit_log(f"⚠️ Gagal inisiasi tamu via proxy ini: {msg}. Melewati...", "warn", term_id)
                ProxyPoolManager.report_failure(proxy_url, msg)
                return False

            await task.emit_log("✓ Sesi tamu aktif & Cookie resmi qf_guest_reader terpasang.", "debug", term_id)

            # 2. Buka novel target & baca sinopsis layaknya pengunjung baru
            novel_info = await bot.get_novel_detail(task.novel_id)
            title = novel_info.get("title", task.novel_title) if novel_info else task.novel_title
            syn_time = random.uniform(12.0, 20.0)
            await task.emit_log(f"👀 [Mode Tamu] Membuka halaman novel '{title[:30]}' & membaca sinopsis ({int(syn_time)}s)...", "info", term_id)

            spent = 0.0
            while spent < syn_time:
                if not task.is_running or task.is_cancelled:
                    return False
                step = min(4.0, syn_time - spent)
                await asyncio.sleep(step)
                spent += step

            # 3. Ambil daftar bab novel
            chapters = await bot.get_novel_chapters(task.novel_id)
            if not chapters:
                await task.emit_log("⚠️ Gagal memuat bab novel target.", "warn", term_id)
                return False

            # Tamu membaca 1 - 2 bab gratis (perilaku alami pengunjung anonim sebelum drop-off)
            num_guest_chapters = min(len(chapters), random.choice([1, 2]))
            simulator = ReadingSimulator(min_wpm=220, max_wpm=320)  # Skimming / reading speed wajar

            for idx in range(num_guest_chapters):
                if not task.is_running or task.is_cancelled:
                    return False

                ch = chapters[idx]
                ch_id = ch.get("id") or ch.get("hash_id")
                ch_num = idx + 1
                ch_title = ch.get("title", f"Bab {ch_num}")

                # Ambil teks isi bab jika ada
                ch_detail = await bot.get_chapter_detail(task.novel_id, ch_id)
                content = ch_detail.get("content", "") if ch_detail else ""

                # Hitung durasi baca wajar (skimming tamu: 35s - 75s per bab)
                words = simulator.estimate_effective_words(content)
                calc_duration = (words / random.uniform(240, 320)) * 60.0
                read_duration = max(35.0, min(calc_duration, 75.0))

                await task.emit_log(f"📖 [Mode Tamu] Membaca Bab {ch_num} '{ch_title[:20]}' ({int(read_duration)}s | WPM Skimming Pengunjung)...", "info", term_id)

                # Progress ticks bertahap ke /api/guest-reading/progress
                spent = 0.0
                milestones = [30, 60, 100]
                m_idx = 0
                while spent < read_duration:
                    if not task.is_running or task.is_cancelled:
                        return False
                    step = min(4.0, read_duration - spent)
                    await asyncio.sleep(step)
                    spent += step
                    pct = int((spent / read_duration) * 100)
                    if m_idx < len(milestones) and pct >= milestones[m_idx]:
                        is_last = (milestones[m_idx] == 100)
                        await bot.send_guest_progress(
                            novel_id=task.novel_id,
                            chapter_id=ch_id,
                            active_reading_seconds=spent,
                            scroll_percent=milestones[m_idx] / 100.0,
                            completed=is_last
                        )
                        m_idx += 1

                task.stats["chapters_read"] += 1
                await task.emit_log(f"✔️ [Mode Tamu] Selesai membaca Bab {ch_num} ({int(read_duration)}s).", "debug", term_id)
                await task.emit_stats()

                # Jeda sejenak antar bab tamu jika membaca bab ke-2
                if idx < num_guest_chapters - 1:
                    await asyncio.sleep(random.uniform(4.0, 8.0))

            # 4. FASE ADDON KONVERSI KE MEMBER RESMI (JIKA DIAKTIFKAN USER)
            if task.addon_guest_conversion and task.is_running and not task.is_cancelled:
                await task.emit_log("🔄 [Addon Konversi] Memulai alur konversi organik: Tamu ➔ Member Terdaftar...", "info", term_id)
                verifier = TempTfVerifier()
                
                temp_email = None
                for _ in range(5):
                    prov = random.choice(["outlook", "hotmail", "gmail"])
                    cand = await verifier.get_email(provider=prov, use_dot=(prov == "gmail"), use_plus=(prov != "gmail"))
                    if cand:
                        cand = format_natural_email(cand)
                        temp_email = cand.lower()
                        break
                    await asyncio.sleep(0.5)

                if temp_email:
                    bot.profile.email = temp_email
                    bot.profile.password = ProfileGenerator.generate_password()
                    await task.emit_log(f"📲 [Addon Konversi] Mendaftarkan akun resmi Android dari sesi ini: {temp_email}...", "info", term_id)

                    signup_ok, signup_msg = await bot.perform_organic_signup()
                    if signup_ok:
                        await task.emit_log(f"✉️ [Addon Konversi] Memicu kode verifikasi email untuk {temp_email}...", "debug", term_id)
                        send_ok, send_msg = await bot.send_email_verification()
                        if send_ok:
                            def on_otp_poll_log(msg: str):
                                asyncio.create_task(task.emit_log(f"⏳ [OTP Polling] {msg}", "debug", term_id))

                            otp_code = await verifier.poll_for_otp(temp_email, timeout_sec=75, interval_sec=3, log_callback=on_otp_poll_log)
                            if otp_code:
                                v_ok, v_msg = await bot.verify_email_code(otp_code)
                                if v_ok:
                                    task.stats["converted_accounts"] = task.stats.get("converted_accounts", 0) + 1
                                    await task.emit_log(f"🎉 [KONVERSI SUKSES] Akun resmi terverifikasi: {temp_email} (Kode OTP: {otp_code})!", "success", term_id)
                                    
                                    # Simpan ke AccountPoolManager dan database
                                    acc_data = {
                                        "email": bot.profile.email,
                                        "password": bot.profile.password,
                                        "nickname": bot.profile.nickname,
                                        "access_token": bot.access_token,
                                        "refresh_token": bot.refresh_token,
                                        "country": bot.profile.country,
                                        "device_id": bot.profile.device_id,
                                        "anonymous_id": bot.profile.anonymous_id,
                                        "user_agent": bot.profile.user_agent,
                                    }
                                    AccountPoolManager.add_or_update_account(acc_data)

                                    # Klaim koin Q harian pertama
                                    try:
                                        await bot.claim_daily_q()
                                    except Exception:
                                        pass

                                    # Interaksi Sosial Member (Like, Simpan Rak, Follow Penulis)
                                    if random.random() < 0.70:
                                        liked = await bot.like_novel(task.novel_id)
                                        if liked:
                                            AccountPoolManager.record_action(temp_email, task.novel_id, task.author_id, "LIKE")
                                            task.stats["likes"] += 1
                                            await task.emit_log(f"❤️ [Interaksi Member] Menyukai novel '{task.novel_title[:25]}' sebagai akun terdaftar.", "info", term_id)

                                    if random.random() < 0.65:
                                        bmed = await bot.bookmark_novel(task.novel_id)
                                        if bmed:
                                            AccountPoolManager.record_action(temp_email, task.novel_id, task.author_id, "BOOKMARK")
                                            task.stats["bookmarks"] += 1
                                            await task.emit_log(f"🔖 [Interaksi Member] Menyimpan novel ke Rak Buku akun terdaftar.", "info", term_id)

                                    if task.author_id and random.random() < 0.40:
                                        fllwd = await bot.follow_author(task.author_id)
                                        if fllwd:
                                            AccountPoolManager.record_action(temp_email, task.novel_id, task.author_id, "FOLLOW")
                                            task.stats["follows"] += 1
                                            await task.emit_log(f"➕ [Interaksi Member] Mengikuti akun penulis sebagai member terdaftar.", "info", term_id)

                                    # Lanjut membaca 1 bab tambahan sebagai Member terdaftar dengan telemetri royalti
                                    if len(chapters) > num_guest_chapters:
                                        m_idx = num_guest_chapters
                                        m_ch = chapters[m_idx]
                                        m_ch_id = m_ch.get("id") or m_ch.get("hash_id")
                                        m_ch_num = m_idx + 1
                                        m_delay = random.uniform(30.0, 50.0)
                                        await task.emit_log(f"📖 [Member Telemetry] Membaca Bab {m_ch_num} sebagai Member ({int(m_delay)}s | Payout Royalty Log)...", "info", term_id)
                                        await bot.send_reading_telemetry(
                                            novel_id=task.novel_id,
                                            chapter_id=m_ch_id,
                                            chapter_num=m_ch_num,
                                            reading_time_sec=m_delay,
                                            depth_percent=100,
                                            completed=True
                                        )
                                        await asyncio.sleep(min(15.0, m_delay))
                                        task.stats["chapters_read"] += 1
                                        AccountPoolManager.record_action(temp_email, task.novel_id, task.author_id, "READ_VALID")

                                    await task.emit_stats()
                                else:
                                    await task.emit_log(f"⚠️ Kode OTP ditolak server: {v_msg}", "warn", term_id)
                            else:
                                await task.emit_log("⚠️ Timeout: OTP tidak diterima dalam batas waktu. Melewati konversi.", "warn", term_id)
                        else:
                            await task.emit_log(f"⚠️ Gagal mengirim verifikasi email: {send_msg}", "warn", term_id)
                    else:
                        await task.emit_log(f"⚠️ Registrasi gagal: {signup_msg}", "warn", term_id)

            ProxyPoolManager.report_success(proxy_url)
            return True

        except Exception as e:
            logger.warning(f"Error sesi tamu: {e}")
            ProxyPoolManager.report_failure(proxy_url, str(e))
            return False
        finally:
            await bot.close()
