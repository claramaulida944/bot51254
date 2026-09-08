"""
Modul Full Auto Runner (All-in-One Automation)
Mengintegrasikan:
1. Auto Like Novel (dengan deteksi & auto-skip jika sudah di-like)
2. Auto Bookmark / Simpan Novel ke Rak (dengan deteksi & auto-skip jika sudah disimpan)
3. Auto Follow Author Profil (dengan deteksi & auto-skip jika sudah di-follow)
4. Auto Reading Simulator (membaca bab per bab secara natural dengan kirim telemetri royalti post-view)

Semua berjalan secara paralel (asynchronous) dengan kontrol konkurensi Semaphore,
rotasi proxy cerdas multi-negara, dan tampilan terminal visual interaktif menggunakan library Rich.
"""

import asyncio
import logging
import random
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Column, Table

from auto_reader import (
    IdentifierGenerator,
    NovelTargetResolver,
    format_chapters_summary,
    load_accounts_from_file,
    prompt_chapter_selection,
)
from interaction_manager import TargetResolver, default_proxy_manager
from proxy_manager import ProxyManager, SUPPORTED_QUARTERFULL_COUNTRIES

# Konfigurasi logger dasar (level ERROR agar tidak merusak tata letak Rich Progress)
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("FullAutoRunner")
logger.setLevel(logging.ERROR)
for _lib in ("httpx", "httpcore"):
    logging.getLogger(_lib).setLevel(logging.ERROR)
console = Console(highlight=False)


class FullAutoWorker:
    """Eksekutor satu akun untuk alur Full Auto (Social Interactions + Reading)."""

    BASE_URL = "https://api.quarterfull.io"

    def __init__(
        self,
        worker_id: str,
        account: Dict[str, Any],
        novel_id: str,
        novel_title: str,
        author_hash_id: Optional[str],
        chapters: List[Dict[str, Any]],
        proxy: Optional[str] = None,
        base_delay_per_chapter: float = 6.0,
        do_like: bool = True,
        do_bookmark: bool = True,
        do_follow: bool = True,
        skip_already_read: bool = True,
        inter_chapter_delay: float = 3.0,
    ):
        self.worker_id = worker_id
        self.account = account
        self.email = account.get("email", "-")
        self.user_id = account.get("user_id", 0)
        self.access_token = account.get("access_token", "")
        self.device_id = account.get("device_id") or IdentifierGenerator.generate_device_id()
        self.user_agent = account.get("user_agent", "okhttp/4.12.0")

        # Validasi negara resmi Quarterfull
        raw_cc = str(account.get("country", "ID")).upper().strip()
        self.country = raw_cc if raw_cc in SUPPORTED_QUARTERFULL_COUNTRIES else "ID"
        cfg = SUPPORTED_QUARTERFULL_COUNTRIES[self.country]
        self.timezone = cfg["timezone"]
        self.lang = cfg["lang"]

        self.novel_id = novel_id
        self.novel_title = novel_title
        self.author_hash_id = author_hash_id
        self.chapters = chapters
        self.proxy = proxy
        self.base_delay = base_delay_per_chapter
        self.do_like = do_like
        self.do_bookmark = do_bookmark
        self.do_follow = do_follow
        self.skip_already_read = skip_already_read
        self.inter_chapter_delay = max(0.0, inter_chapter_delay)

        self.like_result = "-"
        self.bookmark_result = "-"
        self.follow_result = "-"
        self.chapters_read = 0
        self.status = "Inisialisasi"

    async def get_read_chapter_ids(self, client: httpx.AsyncClient) -> set:
        """
        Mengambil daftar ID bab (dan nomor bab) yang sudah pernah dibaca oleh akun ini.
        Endpoint: GET /api/v1/novels/{novel_id}/chapters?order=asc&include_read_progress=true
        Mengembalikan set yang berisi hash_id dan chapter_num dari bab yang sudah dibaca.
        """
        read_set = set()
        url = f"/api/v1/novels/{self.novel_id}/chapters"
        params = {
            "order": "asc",
            "page": 1,
            "per_page": 100,
            "include_read_progress": "true",
        }
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                for item in items:
                    is_read = item.get("is_read", False)
                    prog = float(item.get("reading_progress", 0.0) or 0.0)
                    if is_read or prog >= 0.95:
                        h_id = item.get("hash_id")
                        c_num = item.get("chapter_num")
                        if h_id:
                            read_set.add(h_id)
                        if c_num is not None:
                            read_set.add(c_num)
        except Exception as exc:
            logger.debug("[%s] Gagal fetch riwayat baca bab: %s", self.worker_id, exc)
        return read_set

    def _get_current_local_date(self) -> str:
        try:
            if ZoneInfo is not None:
                now = datetime.now(ZoneInfo(self.timezone))
            else:
                now = datetime.now()
            return now.strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def build_headers(self) -> Dict[str, str]:
        return {
            "host": "api.quarterfull.io",
            "user-agent": self.user_agent,
            "accept-encoding": "gzip",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "x-timezone": self.timezone,
            "x-local-date": self._get_current_local_date(),
            "accept-language": self.lang,
            "x-user-country": self.country,
            "x-user-raw-country": self.country,
            "x-device-id": self.device_id,
            "authorization": f"Bearer {self.access_token}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    def _save_refreshed_account(self, file_path: str = "akun.txt") -> None:
        try:
            if not os.path.exists(file_path):
                return
            lines = []
            updated = False
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        data = json.loads(line_str)
                        if data.get("email") == self.email:
                            data["access_token"] = self.access_token
                            if self.account.get("refresh_token"):
                                data["refresh_token"] = self.account["refresh_token"]
                            lines.append(json.dumps(data, ensure_ascii=False))
                            updated = True
                            continue
                    except Exception:
                        pass
                    lines.append(line_str)
            if updated:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except Exception as exc:
            logger.debug("[%s] Gagal menyimpan token ke %s: %s", self.worker_id, file_path, exc)

    async def refresh_access_token(self, client: Optional[httpx.AsyncClient] = None) -> bool:
        refresh_token = self.account.get("refresh_token")
        if not refresh_token:
            return False
        try:
            kwargs: Dict[str, Any] = {
                "base_url": self.BASE_URL,
                "timeout": httpx.Timeout(20.0),
                "headers": {
                    "user-agent": self.user_agent,
                    "x-device-id": self.device_id,
                    "content-type": "application/json",
                    "accept": "application/json",
                },
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy

            async with httpx.AsyncClient(**kwargs) as refresh_client:
                resp = await refresh_client.post("/api/auth/token/refresh", json={"refresh_token": refresh_token})
                if resp.status_code == 200:
                    data = resp.json()
                    new_access = data.get("access_token")
                    new_refresh = data.get("refresh_token")
                    if new_access:
                        self.access_token = new_access
                        self.account["access_token"] = new_access
                        if new_refresh:
                            self.account["refresh_token"] = new_refresh
                        if client is not None and not client.is_closed:
                            client.headers["authorization"] = f"Bearer {new_access}"
                        self._save_refreshed_account()
                        return True
        except Exception as exc:
            logger.debug("[%s] Gagal refresh: %s", self.worker_id, exc)
        return False

    async def login_with_password(self, client: Optional[httpx.AsyncClient] = None) -> bool:
        password = self.account.get("password")
        if not self.email or not password:
            return False
        try:
            kwargs: Dict[str, Any] = {
                "base_url": self.BASE_URL,
                "timeout": httpx.Timeout(20.0),
                "headers": {
                    "user-agent": self.user_agent,
                    "x-device-id": self.device_id,
                    "x-platform": "android",
                    "x-app-variant": "prod",
                    "x-app-version": "3.0.52",
                    "content-type": "application/json",
                    "accept": "application/json",
                },
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy

            async with httpx.AsyncClient(**kwargs) as login_client:
                resp = await login_client.post("/api/auth/login", json={"login_id": self.email, "password": password})
                if resp.status_code == 200:
                    data = resp.json()
                    new_access = data.get("access_token")
                    new_refresh = data.get("refresh_token")
                    if new_access:
                        self.access_token = new_access
                        self.account["access_token"] = new_access
                        if new_refresh:
                            self.account["refresh_token"] = new_refresh
                        if client is not None and not client.is_closed:
                            client.headers["authorization"] = f"Bearer {new_access}"
                        self._save_refreshed_account()
                        logger.info("[%s] Akun sesi habis berhasil Login Ulang secara otomatis!", self.worker_id)
                        return True
        except Exception as exc:
            logger.debug("[%s] Gagal login ulang: %s", self.worker_id, exc)
        return False

    async def execute(self, progress: Progress, task_id: TaskID) -> Dict[str, Any]:
        short_email = self.email.split("@")[0][:12]
        headers = self.build_headers()

        client_kwargs: Dict[str, Any] = {
            "base_url": self.BASE_URL,
            "http2": False if self.proxy else True,
            "headers": headers,
            "timeout": httpx.Timeout(25.0),
        }
        if self.proxy:
            client_kwargs["proxy"] = self.proxy

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                # -------------------------------------------------------------
                # TAHAP 1: DETEKSI & INTERAKSI SOSIAL (Like, Bookmark, Follow)
                # -------------------------------------------------------------
                progress.update(
                    task_id,
                    role=f"[cyan]{self.worker_id}[/]",
                    description="[dim]Cek profil & status...[/]",
                )

                novel_status_data: Optional[Dict[str, Any]] = None
                try:
                    resp = await client.get(f"/api/v1/novels/{self.novel_id}")
                    if resp.status_code == 200:
                        novel_status_data = resp.json()
                    elif resp.status_code == 401:
                        # Otomatis refresh atau login ulang
                        relogged = await self.refresh_access_token(client) or await self.login_with_password(client)
                        if relogged:
                            retry_resp = await client.get(f"/api/v1/novels/{self.novel_id}")
                            if retry_resp.status_code == 200:
                                novel_status_data = retry_resp.json()
                        if not novel_status_data:
                            self.status = "Token Expired (401)"
                            progress.update(task_id, description="[bold red]Sesi Expired (Skip)[/]")
                            return self._build_summary()
                except Exception as exc:
                    logger.debug("[%s] Gagal fetch status novel: %s", self.worker_id, exc)

                # 1.A. Auto Like (dengan deteksi & skip)
                if self.do_like:
                    is_liked = novel_status_data.get("is_liked", False) if novel_status_data else False
                    if is_liked:
                        self.like_result = "[yellow]SKIP (Sudah)[/]"
                    else:
                        try:
                            like_resp = await client.post(f"/api/v1/novels/{self.novel_id}/like")
                            if like_resp.status_code == 200:
                                self.like_result = "[green]OK (Baru)[/]"
                            else:
                                self.like_result = f"[red]Gagal ({like_resp.status_code})[/]"
                        except Exception:
                            self.like_result = "[red]Error[/]"

                # 1.B. Auto Bookmark / Simpan (dengan deteksi & skip)
                if self.do_bookmark:
                    is_saved = novel_status_data.get("is_saved", False) if novel_status_data else False
                    if is_saved:
                        self.bookmark_result = "[yellow]SKIP (Sudah)[/]"
                    else:
                        try:
                            bm_resp = await client.post(f"/api/v1/novels/{self.novel_id}/bookmark")
                            if bm_resp.status_code == 200:
                                self.bookmark_result = "[green]OK (Baru)[/]"
                            else:
                                self.bookmark_result = f"[red]Gagal ({bm_resp.status_code})[/]"
                        except Exception:
                            self.bookmark_result = "[red]Error[/]"

                # 1.C. Auto Follow Author (dengan deteksi & skip)
                if self.do_follow and self.author_hash_id:
                    try:
                        rel_resp = await client.get(f"/api/v1/social/profiles/{self.author_hash_id}/relationship")
                        if rel_resp.status_code == 200:
                            is_following = rel_resp.json().get("is_following", False)
                            if is_following:
                                self.follow_result = "[yellow]SKIP (Sudah)[/]"
                            else:
                                f_resp = await client.put(f"/api/v1/social/profiles/{self.author_hash_id}/follow")
                                if f_resp.status_code == 200:
                                    self.follow_result = "[green]OK (Baru)[/]"
                                else:
                                    self.follow_result = f"[red]Gagal ({f_resp.status_code})[/]"
                    except Exception:
                        self.follow_result = "[red]Error[/]"

                # -------------------------------------------------------------
                # TAHAP 2: SIMULASI MEMBACA (Chapters & Royalty Post-View)
                # -------------------------------------------------------------
                if self.chapters:
                    read_ids = set()
                    if self.skip_already_read:
                        progress.update(task_id, description="[dim]Cek riwayat baca...[/]")
                        read_ids = await self.get_read_chapter_ids(client)

                    for ch_idx, ch in enumerate(self.chapters, start=1):
                        ch_num = ch.get("chapter_num", 1)
                        ch_id = ch.get("hash_id", "")
                        ch_title = ch.get("title", f"Bab {ch_num}")[:15]

                        # Deteksi apakah bab sudah pernah dibaca sebelumnya
                        if self.skip_already_read and (ch_id in read_ids or ch_num in read_ids):
                            progress.update(
                                task_id,
                                description=f"[dim]Bab {ch_num} (Skip - Sudah Dibaca)[/]",
                            )
                            progress.advance(task_id, 1)
                            await asyncio.sleep(0.3)
                            continue

                        progress.update(
                            task_id,
                            description=f"[yellow]Bab {ch_num} ({ch_idx}/{len(self.chapters)})[/]",
                        )

                        # 2.A. Fetch isi bab secara dinamis dengan smart fallback unauthenticated jika region isolasi
                        try:
                            ch_url = f"/api/v1/novels/{self.novel_id}/chapters/{ch_id}"
                            ch_resp = await client.get(ch_url)
                            if ch_resp.status_code == 404:
                                clean_kwargs: Dict[str, Any] = {
                                    "base_url": self.BASE_URL,
                                    "timeout": httpx.Timeout(self.timeout),
                                    "headers": {
                                        "user-agent": self.user_agent,
                                        "x-device-id": self.device_id,
                                        "accept": "application/json",
                                    },
                                }
                                if self.proxy:
                                    clean_kwargs["proxy"] = self.proxy
                                async with httpx.AsyncClient(**clean_kwargs) as clean_client:
                                    ch_resp = await clean_client.get(ch_url)
                            ch_resp.raise_for_status()
                        except Exception as get_err:
                            if self.proxy and ("407" in str(get_err) or "proxy" in str(get_err).lower() or isinstance(get_err, (httpx.ProxyError, httpx.ConnectError))):
                                logger.warning("[%s] Proxy bermasalah (%s), beralih ke Direct Connection...", self.worker_id, get_err)
                                self.proxy = None
                                if client and not client.is_closed:
                                    await client.aclose()
                                client = httpx.AsyncClient(headers=self.build_headers(), timeout=httpx.Timeout(self.timeout), http2=True, base_url=self.BASE_URL)
                            logger.debug("[%s] Gagal GET bab %d: %s", self.worker_id, ch_num, get_err)

                        # 2.B. Simulasi scrolling membaca bertahap / heartbeat (per ~3 detik & per persen progres)
                        read_delay = max(4.0, random.uniform(self.base_delay * 0.8, self.base_delay * 1.25))
                        step_interval = random.uniform(2.5, 3.5)
                        num_steps = max(3, int(read_delay / step_interval))
                        step_time = read_delay / num_steps

                        for step in range(1, num_steps + 1):
                            await asyncio.sleep(step_time)
                            current_pct = min(1.0, round(step / num_steps, 2))
                            current_pct_display = int(current_pct * 100)

                            progress.update(
                                task_id,
                                description=f"[yellow]Bab {ch_num}/{len(self.chapters)} ({current_pct_display}%)[/]",
                            )

                            # Kirim progres membaca berkala layaknya user scrolling (tiap ~3 detik / per progress)
                            try:
                                prog_payload = {
                                    "novel_id": self.novel_id,
                                    "chapter_id": ch_id,
                                    "scroll_percent": current_pct,
                                    "reading_progress": current_pct,
                                    "read_mode": "scroll",
                                }
                                await client.post("/api/reading/progress", json=prog_payload)
                            except Exception as prog_err:
                                logger.debug("[%s] Gagal update progress bab %d (%d%%): %s", self.worker_id, ch_num, current_pct_display, prog_err)

                        # Kirim heartbeat sesi membaca dengan dwell 3-7 menit (180s - 420s)
                        target_dwell = random.uniform(180.0, 420.0)
                        reading_session_id = f"reading:{self.novel_id}:{ch_id}:{int(time.time()*1000)}:{secrets.token_hex(4)}"
                        hb_payload = {
                            "session_id": reading_session_id,
                            "novel_id": self.novel_id,
                            "chapter_id": ch_id,
                            "active_seconds": int(target_dwell),
                            "scroll_percent": 1.0,
                            "reading_progress": 1.0,
                            "chapter_num": ch_num,
                            "novel_title": self.novel_title,
                            "chapter_title": ch_title,
                            "source": "chapter_route",
                            "ended": True,
                            "completed": True,
                        }
                        try:
                            await client.post("/api/reading/sessions/heartbeat", json=hb_payload)
                        except Exception as hb_err:
                            logger.debug("[%s] Gagal heartbeat member bab %d: %s", self.worker_id, ch_num, hb_err)

                        # 2.C. Kirim Post-View Royalti Telemetri (Setelah tuntas 100%)
                        now_iso = datetime.now(timezone.utc).isoformat()
                        year_month = datetime.now().strftime("%Y-%m")
                        post_view_payload = {
                            "novel_hash_id": self.novel_id,
                            "post_hash_id": ch_id,
                            "post_title": ch_title,
                            "novel_title": self.novel_title,
                            "attribution": {
                                "attribution_session_id": IdentifierGenerator.generate_session_id("attr", random_len=8),
                                "work_id": self.novel_id,
                                "first_touch": {
                                    "discovery_method": "direct",
                                    "source_screen": "chapter_route",
                                    "touched_at": now_iso,
                                    "entry_event_id": IdentifierGenerator.generate_session_id("entry", random_len=8),
                                },
                                "last_touch": {
                                    "discovery_method": "direct",
                                    "source_screen": "chapter_route",
                                    "touched_at": now_iso,
                                    "entry_event_id": IdentifierGenerator.generate_session_id("entry", random_len=8),
                                },
                            },
                            "callback_contract": "telemetry_v2",
                            "source_event_id": f"member:{self.user_id}:{ch_id}:{year_month}",
                        }

                        try:
                            post_resp = await client.post("/api/reading/v2/logs/post-view", json=post_view_payload)
                            if post_resp.status_code in (200, 201):
                                self.chapters_read += 1
                                progress.advance(task_id, 1)
                        except Exception as log_err:
                            logger.debug("[%s] Gagal post-view bab %d: %s", self.worker_id, ch_num, log_err)

                        # 2.D. Jeda istirahat antar-bab sesuai konfigurasi user
                        if ch_idx < len(self.chapters) and self.inter_chapter_delay > 0:
                            next_ch_num = self.chapters[ch_idx].get("chapter_num", ch_num + 1)
                            progress.update(
                                task_id,
                                description=f"[cyan]Jeda Bab {ch_num}->{next_ch_num} ({self.inter_chapter_delay:.1f}s)[/]",
                            )
                            await asyncio.sleep(self.inter_chapter_delay)

                self.status = "[green]Sukses Selesai[/]"
                progress.update(
                    task_id,
                    description="[bold green]Selesai Semua Aksi [OK][/]",
                )

        except Exception as main_exc:
            self.status = f"[red]Error: {str(main_exc)[:30]}[/]"
            progress.update(
                task_id,
                description=f"[bold red]Error: {str(main_exc)[:15]}[/]",
            )

        return self._build_summary()

    def _build_summary(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "email": self.email,
            "country": self.country,
            "like": self.like_result,
            "bookmark": self.bookmark_result,
            "follow": self.follow_result,
            "chapters_read": self.chapters_read,
            "status": self.status,
        }


class FullAutoOrchestrator:
    """Orkestrator utama untuk mengeksekusi fitur Full Auto secara masif & simultan."""

    def __init__(
        self,
        novel_id: str,
        novel_title: str,
        author_hash_id: Optional[str],
        chapters: List[Dict[str, Any]],
        accounts: List[Dict[str, Any]],
        concurrency: int = 3,
        base_delay_per_chapter: float = 6.0,
        do_like: bool = True,
        do_bookmark: bool = True,
        do_follow: bool = True,
        proxies: Optional[List[str]] = None,
        skip_already_read: bool = True,
        inter_chapter_delay: float = 3.0,
    ):
        self.novel_id = novel_id
        self.novel_title = novel_title
        self.author_hash_id = author_hash_id
        self.chapters = chapters
        self.accounts = accounts
        self.concurrency = max(1, concurrency)
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.base_delay = base_delay_per_chapter
        self.do_like = do_like
        self.do_bookmark = do_bookmark
        self.do_follow = do_follow
        self.skip_already_read = skip_already_read
        self.inter_chapter_delay = max(0.0, inter_chapter_delay)

        if proxies is not None:
            self.proxy_manager = ProxyManager()
            self.proxy_manager.load_proxies()
        else:
            self.proxy_manager = default_proxy_manager

    async def _worker_wrapper(
        self,
        worker_idx: int,
        account: Dict[str, Any],
        progress: Progress,
        overall_task: TaskID,
        total_steps: int,
        slot_queue: asyncio.Queue,
    ) -> Dict[str, Any]:
        async with self.semaphore:
            slot_idx, tid = await slot_queue.get()
            try:
                acc_country = account.get("country", "ID")
                sess_id = f"fa_{worker_idx}_{secrets.token_hex(4)}"
                proxy = self.proxy_manager.get_proxy(country_code=acc_country, session_id=sess_id)
                progress.reset(tid, total=total_steps)
                progress.update(
                    tid,
                    role=f"[cyan]Akun-{worker_idx:02d}[/]",
                    description="[dim]Menyiapkan sesi...[/]",
                )
                worker = FullAutoWorker(
                    worker_id=f"Akun-{worker_idx:02d}",
                    account=account,
                    novel_id=self.novel_id,
                    novel_title=self.novel_title,
                    author_hash_id=self.author_hash_id,
                    chapters=self.chapters,
                    proxy=proxy,
                    base_delay_per_chapter=self.base_delay,
                    do_like=self.do_like,
                    do_bookmark=self.do_bookmark,
                    do_follow=self.do_follow,
                    skip_already_read=self.skip_already_read,
                    inter_chapter_delay=self.inter_chapter_delay,
                )
                return await worker.execute(progress, tid)
            finally:
                progress.advance(overall_task, 1)
                slot_queue.put_nowait((slot_idx, tid))

    async def run(self) -> List[Dict[str, Any]]:
        total_accounts = len(self.accounts)
        total_steps = len(self.chapters) if self.chapters else 1
        num_slots = min(self.concurrency, total_accounts)

        skip_info = f"  •  Auto-Skip Bab Terbaca [{'green' if self.skip_already_read else 'yellow'}]{'[✓]' if self.skip_already_read else '[✗]'}[/]" if self.chapters else ""

        console.print(
            Panel(
                f"[bold cyan]Target Novel:[/] [bold yellow]{self.novel_title}[/] (ID: [cyan]{self.novel_id}[/])\n"
                f"[bold cyan]Total Akun:[/] [bold white]{total_accounts}[/] Akun  |  "
                f"[bold cyan]Bab Dibaca:[/] [bold white]{format_chapters_summary(self.chapters)}[/]\n"
                f"[bold cyan]Konkurensi:[/] [bold green]{num_slots}[/] Slot Paralel  |  "
                f"[bold cyan]Jeda Baca:[/] [bold green]{self.base_delay:.1f}s[/] per Bab  |  "
                f"[bold cyan]Jeda Antar-Bab:[/] [bold green]{self.inter_chapter_delay:.1f}s[/]\n"
                f"[bold cyan]Fitur Aktif:[/] "
                f"Like [{'green' if self.do_like else 'red'}]{'[✓]' if self.do_like else '[✗]'}[/]  •  "
                f"Simpan/Rak [{'green' if self.do_bookmark else 'red'}]{'[✓]' if self.do_bookmark else '[✗]'}[/]  •  "
                f"Follow Author [{'green' if self.do_follow else 'red'}]{'[✓]' if self.do_follow else '[✗]'}[/]"
                f"{skip_info}",
                title="[bold green]Memulai Full Auto Bot Suite[/]",
                border_style="cyan",
            )
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.fields[role]}[/]", justify="left", table_column=Column(no_wrap=True)),
            TextColumn("{task.description}", justify="left", table_column=Column(no_wrap=True)),
            BarColumn(bar_width=16),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            refresh_per_second=4,
        ) as progress:
            overall_task = progress.add_task(
                description="[dim]Memproses antrean akun...[/]",
                total=total_accounts,
                role="[bold yellow]★ TOTAL[/]",
            )
            slot_queue: asyncio.Queue = asyncio.Queue()
            for s_idx in range(1, num_slots + 1):
                tid = progress.add_task(
                    description="[dim]Menunggu antrean...[/]",
                    total=total_steps,
                    role=f"Slot-{s_idx:02d}",
                )
                slot_queue.put_nowait((s_idx, tid))

            tasks = []
            for idx, acc in enumerate(self.accounts, start=1):
                tasks.append(self._worker_wrapper(idx, acc, progress, overall_task, total_steps, slot_queue))

            results = await asyncio.gather(*tasks)

        # -----------------------------------------------------------------
        # TABEL LAPORAN HASIL AKHIR
        # -----------------------------------------------------------------
        report_table = Table(
            title=f"[bold green]Laporan Eksekusi Full Auto ({self.novel_title})[/]",
            border_style="cyan",
        )
        report_table.add_column("No", style="dim", width=4)
        report_table.add_column("Akun / Email", style="bold white")
        report_table.add_column("Negara", style="cyan", width=8)
        report_table.add_column("♥ Like", justify="center", width=14)
        report_table.add_column("★ Simpan", justify="center", width=14)
        report_table.add_column("+ Follow", justify="center", width=14)
        report_table.add_column("Bab Dibaca", justify="right", width=12)
        report_table.add_column("Status", style="bold")

        like_new = 0
        like_skipped = 0
        bm_new = 0
        bm_skipped = 0
        fol_new = 0
        fol_skipped = 0
        total_reads = 0

        for i, r in enumerate(results, start=1):
            if "OK" in r["like"]:
                like_new += 1
            elif "SKIP" in r["like"]:
                like_skipped += 1

            if "OK" in r["bookmark"]:
                bm_new += 1
            elif "SKIP" in r["bookmark"]:
                bm_skipped += 1

            if "OK" in r["follow"]:
                fol_new += 1
            elif "SKIP" in r["follow"]:
                fol_skipped += 1

            total_reads += r["chapters_read"]

            report_table.add_row(
                str(i),
                r["email"],
                r["country"],
                r["like"],
                r["bookmark"],
                r["follow"],
                str(r["chapters_read"]),
                r["status"],
            )

        console.print()
        console.print(report_table)
        console.print()

        console.print(
            Panel(
                f"[bold green]Full Auto Berhasil Selesai Sepenuhnya![/]\n"
                f"• Total Akun Diproses: [bold cyan]{len(results)}[/] Akun\n"
                f"• [bold red]♥ Like[/]: [green]{like_new}[/] baru, [yellow]{like_skipped}[/] dilewati (sudah like)\n"
                f"• [bold yellow]★ Simpan/Rak[/]: [green]{bm_new}[/] baru, [yellow]{bm_skipped}[/] dilewati (sudah simpan)\n"
                f"• [bold green]+ Follow Author[/]: [green]{fol_new}[/] baru, [yellow]{fol_skipped}[/] dilewati (sudah follow)\n"
                f"• [bold cyan]Total Bab Dibaca[/]: [bold white]{total_reads}[/] kali pembacaan",
                title="[bold cyan]Ringkasan Statistik Full Auto[/]",
                border_style="green",
            )
        )

        return results


async def run_full_auto_cli(preset_target: Optional[str] = None) -> None:
    """Antarmuka interaktif CLI untuk fitur Full Auto."""
    console.print(
        Panel(
            "[bold white]Modul Full Auto: All-in-One Novel Automation[/]\n"
            "[dim]Membaca novel, otomatis Like, otomatis Simpan ke rak, dan otomatis Follow author\n"
            "dengan deteksi pintar (akun yang sudah like/simpan/follow otomatis di-skip).[/]",
            title="[bold cyan]★ FULL AUTO BOT ★[/]",
            border_style="cyan",
        )
    )

    accounts = load_accounts_from_file("akun.txt")
    if not accounts:
        console.print("[bold red][ERROR] Belum ada akun terdaftar di 'akun.txt'![/]")
        console.print("[yellow]Silakan buat akun terlebih dahulu menggunakan Menu [2] Auto Signup Generator.[/]")
        return

    # 1. Input Target URL / Novel ID
    raw_target = preset_target
    if not raw_target:
        raw_target = Prompt.ask(
            "[bold green]?[/] Masukkan URL atau ID Novel target",
            default="https://quarterfull.io/works/Py7LDdwpEQ8e1YKX",
        )

    target_type, novel_id = TargetResolver.clean_target(raw_target)
    if not novel_id:
        console.print("[bold red][ERROR] Novel ID tidak valid.[/]")
        return

    with console.status(f"[bold cyan]Mengambil metadata novel {novel_id}...[/]"):
        try:
            novel_info = await NovelTargetResolver.fetch_novel_details(novel_id)
            origin_country = novel_info.get("origin_country", "ID")
            chapters = await NovelTargetResolver.fetch_readable_chapters(novel_id, origin_country=origin_country)
        except Exception as exc:
            console.print(f"[bold red][ERROR] Gagal menghubungi API server:[/] {exc}")
            return

    novel_title = novel_info.get("title", f"Novel-{novel_id}")
    author_info = novel_info.get("author", {})
    author_pen_name = author_info.get("pen_name", "Author")
    author_hash_id = author_info.get("hash_id")

    console.print(
        Panel(
            f"[bold cyan]Judul Novel:[/] [bold yellow]{novel_title}[/]\n"
            f"[bold cyan]Penulis / Author:[/] [bold magenta]{author_pen_name}[/] (ID: [dim]{author_hash_id}[/])\n"
            f"[bold cyan]Bab Gratis Tersedia:[/] [bold green]{len(chapters)}[/] Bab\n"
            f"[bold cyan]Akun Tersedia:[/] [bold white]{len(accounts)}[/] Akun di 'akun.txt'",
            title="[bold green]Informasi Target Novel[/]",
            border_style="green",
        )
    )

    # 2. Pemilihan Opsi Interaksi
    account_count = IntPrompt.ask(
        f"[bold green]?[/] Berapa akun yang ingin dijalankan? [1-{len(accounts)}]",
        default=len(accounts),
    )
    account_count = max(1, min(account_count, len(accounts)))
    selected_accounts = accounts[:account_count]

    do_like = Confirm.ask(
        "[bold green]?[/] Aktifkan [bold red]Auto Like[/] novel? (Auto-skip jika sudah like)",
        default=True,
    )
    do_bookmark = Confirm.ask(
        "[bold green]?[/] Aktifkan [bold yellow]Auto Simpan / Bookmark[/] ke rak? (Auto-skip jika sudah simpan)",
        default=True,
    )
    do_follow = Confirm.ask(
        f"[bold green]?[/] Aktifkan [bold magenta]Auto Follow[/] penulis '{author_pen_name}'? (Auto-skip jika sudah follow)",
        default=True,
    )

    # 3. Opsi Membaca Bab (Fleksibel: Semua, Satu Bab, Rentang 10-25, Beberapa Bab Kustom, N Pertama, atau Lewati)
    target_chapters = prompt_chapter_selection(chapters, console, allow_skip=True)

    base_delay = 6.0
    skip_already_read = True
    inter_chapter_delay = 3.0
    if target_chapters:
        skip_already_read = Confirm.ask(
            "[bold green]?[/] Lewatkan bab yang sudah pernah dibaca oleh akun? (Auto-skip)",
            default=True,
        )
        base_delay = float(
            Prompt.ask(
                "[bold green]?[/] Jeda simulasi membaca per bab dalam detik (rekomendasi: 4-6)",
                default="5.0",
            )
        )
        inter_chapter_delay = float(
            Prompt.ask(
                "[bold green]?[/] Waktu jeda istirahat antar-bab dalam detik (rekomendasi: 2-5)",
                default="3.0",
            )
        )

    concurrency = IntPrompt.ask(
        "[bold green]?[/] Jumlah akun berjalan paralel / konkurensi (rekomendasi: 3-5)",
        default=3,
    )

    # 4. Eksekusi Orkestrasi
    orchestrator = FullAutoOrchestrator(
        novel_id=novel_id,
        novel_title=novel_title,
        author_hash_id=author_hash_id,
        chapters=target_chapters,
        accounts=selected_accounts,
        concurrency=concurrency,
        base_delay_per_chapter=base_delay,
        do_like=do_like,
        do_bookmark=do_bookmark,
        do_follow=do_follow,
        skip_already_read=skip_already_read,
        inter_chapter_delay=inter_chapter_delay,
    )

    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(run_full_auto_cli())
