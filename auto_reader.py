"""
Modul Auto Readers (Simulasi Pembaca Otomatis) Toodat / Quarterfull.

Modul ini menyediakan simulasi membaca novel digital bab per bab secara natural
untuk dua jenis entitas sekaligus:
1. Pengguna Terdaftar (Member): Menggunakan token autentikasi dari `akun.txt`
   dan mengirimkan event telemetri royalti view `POST /api/reading/v2/logs/post-view`.
2. Pembaca Tamu (Guest Reader): Menginisiasi sesi tamu resmi via
   `POST /api/guest-reading/session` dan mengirimkan heartbeat progres membaca
   `PUT /api/guest-reading/progress`.

Fitur Tambahan:
- Antarmuka Terminal Interaktif Modern dengan library `rich`.
- Concurrency control via `asyncio.Semaphore`.
- Dukungan rotasi proxy dari `proxies.txt` (opsional).
- Filter bab gratis (non-premium & terbit).
- Pelacakan progress real-time per worker.
"""

import asyncio
import json
import logging
import random
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

# Pastikan encoding stdout/stderr di Windows mendukung UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
from rich.console import Console
from rich.live import Live
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
from rich.table import Table
from rich.text import Text

from auto_signup import UserAgentGenerator
from session_manager import IdentifierGenerator
from proxy_manager import ProxyManager, ProxyInfo, default_proxy_manager

# Konfigurasi logger dasar
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("AutoReader")
console = Console(highlight=False)


class NovelTargetResolver:
    """Menguraikan URL atau ID Novel dan mengambil daftar bab yang valid untuk dibaca."""

    BASE_URL: str = "https://api.quarterfull.io"
    NOVEL_ID_REGEX = re.compile(r"([a-zA-Z0-9]{16})")

    @classmethod
    def extract_novel_id(cls, raw_input: str) -> Optional[str]:
        """Mengekstrak 16-karakter hash ID dari string teks atau URL novel."""
        match = cls.NOVEL_ID_REGEX.search(raw_input.strip())
        return match.group(1) if match else None

    @classmethod
    async def fetch_novel_details(cls, novel_id: str) -> Dict[str, Any]:
        """Mengambil metadata novel (judul, deskripsi, penulis)."""
        url = f"{cls.BASE_URL}/api/v1/novels/{novel_id}"
        async with httpx.AsyncClient(http2=True, timeout=20.0) as client:
            resp = await client.get(url, headers={"user-agent": "okhttp/4.12.0"})
            resp.raise_for_status()
            return resp.json()

    @classmethod
    async def fetch_readable_chapters(cls, novel_id: str) -> List[Dict[str, Any]]:
        """
        Mengambil daftar bab novel, memfilter hanya bab yang sudah terbit
        dan non-premium (gratis), lalu mengurutkannya berdasarkan nomor bab.
        """
        url = f"{cls.BASE_URL}/api/v1/novels/{novel_id}/chapters?order=asc&include_read_progress=false"
        async with httpx.AsyncClient(http2=True, timeout=25.0) as client:
            resp = await client.get(url, headers={"user-agent": "okhttp/4.12.0"})
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items", [])
        # Filter hanya bab yang terbit dan gratis (non-premium)
        readable = [
            ch for ch in items
            if ch.get("is_published", True) and not ch.get("is_premium", False)
        ]
        # Urutkan berdasarkan chapter_num terkecil ke terbesar
        readable.sort(key=lambda x: x.get("chapter_num", 0))
        return readable


class BaseReaderSession:
    """Basis client HTTP/2 untuk sesi pembaca (Member maupun Guest)."""

    BASE_URL: str = "https://api.quarterfull.io"

    def __init__(
        self,
        worker_id: str,
        device_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        country: str = "ID",
        timezone_str: str = "Asia/Jakarta",
        proxy: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.worker_id: str = worker_id
        self.device_id: str = device_id or IdentifierGenerator.generate_device_id()
        self.user_agent: str = user_agent or UserAgentGenerator.get_random_okhttp_ua()
        self.country: str = country
        self.timezone: str = timezone_str
        self.proxy: Optional[str] = proxy
        self.timeout: float = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_current_local_date(self) -> str:
        """Mengembalikan format YYYY-MM-DD sesuai zona waktu yang dikonfigurasi."""
        try:
            if ZoneInfo is not None:
                now = datetime.now(ZoneInfo(self.timezone))
            else:
                now = datetime.now()
            return now.strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def build_base_headers(self) -> Dict[str, str]:
        """Menyusun fingerprint header HTTP standar aplikasi Toodat Android."""
        return {
            "host": "api.quarterfull.io",
            "user-agent": self.user_agent,
            "accept-encoding": "gzip",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "x-timezone": self.timezone,
            "x-local-date": self._get_current_local_date(),
            "x-user-country": self.country,
            "x-user-raw-country": self.country,
            "accept-language": "id" if self.country == "ID" else "en-US,en;q=0.9",
            "x-device-id": self.device_id,
            "accept": "application/json",
        }

    async def get_client(self) -> httpx.AsyncClient:
        """Menginisialisasi httpx.AsyncClient dengan dukungan HTTP/2 (atau HTTP/1.1 jika via proxy)."""
        if self._client is None or self._client.is_closed:
            kwargs: Dict[str, Any] = {
                "base_url": self.BASE_URL,
                "http2": False if self.proxy else True,
                "headers": self.build_base_headers(),
                "timeout": httpx.Timeout(self.timeout),
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy

            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def close(self) -> None:
        """Menutup koneksi client HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class GuestReaderSession(BaseReaderSession):
    """Sesi pembaca Tamu (Guest Reader) dengan inisialisasi sesi resmi dan heartbeat baca."""

    def __init__(
        self,
        worker_id: str,
        proxy: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            device_id=IdentifierGenerator.generate_device_id(),
            user_agent=UserAgentGenerator.get_random_okhttp_ua(),
            country="ID",
            timezone_str="Asia/Jakarta",
            proxy=proxy,
            timeout=timeout,
        )
        self.guest_id: Optional[str] = None
        self.guest_token: Optional[str] = None

    async def init_guest_session(self) -> bool:
        """Memanggil POST /api/guest-reading/session untuk memperoleh guest_token."""
        client = await self.get_client()
        for attempt in range(2):
            try:
                resp = await client.post("/api/guest-reading/session", content=b"")
                resp.raise_for_status()
                data = resp.json()
                self.guest_id = data.get("guest_id")
                self.guest_token = data.get("guest_token")

                if self.guest_token:
                    client.headers["x-guest-token"] = self.guest_token
                    client.cookies.set("qf_guest_reader", self.guest_token, domain="api.quarterfull.io", path="/")
                    return True
            except Exception as exc:
                if attempt == 1:
                    logger.error("[%s] Gagal inisialisasi guest session: %s", self.worker_id, exc)
                await asyncio.sleep(1.0)
        return False

    async def read_chapter(
        self,
        novel_id: str,
        chapter: Dict[str, Any],
        reading_delay_sec: float,
    ) -> Tuple[bool, str]:
        """
        Membaca satu bab novel sebagai tamu:
        1. GET /api/v1/novels/{novel_id}/chapters/{chapter_id}?rewarded_reader=true
        2. Tunggu simulasi baca async
        3. PUT /api/guest-reading/progress
        """
        client = await self.get_client()
        ch_id = chapter.get("hash_id", "")
        ch_num = chapter.get("chapter_num", 1)

        # 1. Fetch teks bab
        event_id = IdentifierGenerator.generate_guest_event_id(ch_id)
        get_headers = {"x-guest-event-id": event_id}

        char_count = 0
        try:
            get_resp = await client.get(
                f"/api/v1/novels/{novel_id}/chapters/{ch_id}?rewarded_reader=true",
                headers=get_headers,
            )
            get_resp.raise_for_status()
            ch_data = get_resp.json()
            content = ch_data.get("content", "")
            char_count = len(content)
        except Exception as exc:
            return False, f"GET Chapter Gagal: {exc}"

        # 2. Simulasi jeda baca natural
        await asyncio.sleep(reading_delay_sec)

        # 3. Kirim heartbeat progres membaca
        progress_payload = {
            "chapter_hash_id": ch_id,
            "progress": 0.98,
            "active_reading_seconds": int(reading_delay_sec),
            "completed": True,
            "reading_session_id": IdentifierGenerator.generate_session_id("grs"),
            "session_active_reading_seconds": int(reading_delay_sec),
            "session_ended": True,
            "session_end_reason": "chapter_change",
            "platform": "android",
            "entry_source": "chapter_route",
            "attribution_session_id": IdentifierGenerator.generate_session_id("attr", random_len=8),
            "chapter_number": ch_num,
            "content_type": "novel",
            "content_character_count": char_count,
            "read_mode": "scroll",
        }

        try:
            put_resp = await client.put("/api/guest-reading/progress", json=progress_payload)
            put_resp.raise_for_status()
            return True, "200 OK (Heartbeat Tercatat)"
        except Exception as exc:
            return False, f"PUT Progress Gagal: {exc}"


class MemberReaderSession(BaseReaderSession):
    """Sesi pembaca Member (Registered User) dengan Bearer Token dan Post-View Royalti."""

    def __init__(
        self,
        worker_id: str,
        account_data: Dict[str, Any],
        novel_title: str = "Novel",
        proxy: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            device_id=account_data.get("device_id") or IdentifierGenerator.generate_device_id(),
            user_agent=account_data.get("user_agent") or UserAgentGenerator.get_random_okhttp_ua(),
            country=account_data.get("country", "ID"),
            timezone_str="Asia/Jakarta",
            proxy=proxy,
            timeout=timeout,
        )
        self.account: Dict[str, Any] = account_data
        self.access_token: str = account_data.get("access_token", "")
        self.user_id: Any = account_data.get("user_id", 0)
        self.email: str = account_data.get("email", "unknown@user.com")
        self.novel_title: str = novel_title

    def build_base_headers(self) -> Dict[str, str]:
        headers = super().build_base_headers()
        headers["authorization"] = f"Bearer {self.access_token}"
        headers["content-type"] = "application/json"
        return headers

    async def read_chapter(
        self,
        novel_id: str,
        chapter: Dict[str, Any],
        reading_delay_sec: float,
    ) -> Tuple[bool, str]:
        """
        Membaca satu bab novel sebagai Member:
        1. GET /api/v1/novels/{novel_id}/chapters/{chapter_id}
        2. Tunggu jeda baca natural
        3. POST /api/reading/v2/logs/post-view
        """
        client = await self.get_client()
        ch_id = chapter.get("hash_id", "")
        ch_title = chapter.get("title", f"Bab {chapter.get('chapter_num', 1)}")

        # 1. Mengambil konten bab
        try:
            get_resp = await client.get(f"/api/v1/novels/{novel_id}/chapters/{ch_id}")
            get_resp.raise_for_status()
        except Exception as exc:
            return False, f"GET Chapter Gagal: {exc}"

        # 2. Simulasi jeda baca natural
        await asyncio.sleep(reading_delay_sec)

        # 3. Kirim Post-View Royalti Telemetri
        now_iso = datetime.now(timezone.utc).isoformat()
        year_month = datetime.now().strftime("%Y-%m")

        post_view_payload = {
            "novel_hash_id": novel_id,
            "post_hash_id": ch_id,
            "post_title": ch_title,
            "novel_title": self.novel_title,
            "attribution": {
                "attribution_session_id": IdentifierGenerator.generate_session_id("attr", random_len=8),
                "work_id": novel_id,
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
            post_resp.raise_for_status()
            return True, "200 OK (Royalti Post-View)"
        except Exception as exc:
            return False, f"Post-View Log Gagal: {exc}"


class ReadingSimulationOrchestrator:
    """Mengatur pembagian tugas, konkurensi Semaphore, dan visualisasi Rich Progress."""

    def __init__(
        self,
        novel_id: str,
        novel_title: str,
        chapters: List[Dict[str, Any]],
        member_accounts: List[Dict[str, Any]],
        guest_count: int,
        max_concurrency: int = 5,
        proxies: Optional[List[str]] = None,
        base_delay_per_chapter: float = 8.0,
    ) -> None:
        self.novel_id: str = novel_id
        self.novel_title: str = novel_title
        self.chapters: List[Dict[str, Any]] = chapters
        self.member_accounts: List[Dict[str, Any]] = member_accounts
        self.guest_count: int = guest_count
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrency)
        if proxies is not None:
            self.proxy_manager = ProxyManager()
            self.proxy_manager.parsed_proxies = [ProxyInfo(p) for p in proxies]
        else:
            self.proxy_manager = default_proxy_manager
        self.proxies: List[str] = [p.raw_url for p in self.proxy_manager.parsed_proxies]
        self.base_delay: float = base_delay_per_chapter
        self.total_readers: int = len(member_accounts) + guest_count

    def _get_proxy_for_worker(self, country_code: str = "ID") -> Optional[str]:
        if not self.proxy_manager.has_proxies:
            return None
        return self.proxy_manager.get_proxy(country_code=country_code)

    async def _run_guest_worker(
        self,
        worker_idx: int,
        progress: Progress,
        task_id: TaskID,
    ) -> None:
        """Menjalankan satu sesi pembaca Tamu melalui seluruh bab."""
        worker_id = f"Guest-{worker_idx:02d}"
        proxy = self._get_proxy_for_worker(country_code="ID")
        session = GuestReaderSession(worker_id=worker_id, proxy=proxy)

        async with self.semaphore:
            progress.update(task_id, description=f"[cyan]{worker_id}[/] [dim]Inisialisasi sesi tamu...[/]")
            ok = await session.init_guest_session()
            if not ok:
                progress.update(task_id, description=f"[red]{worker_id}[/] [red]Gagal buat sesi tamu![/]")
                await session.close()
                return

            ident = f"Guest:{session.guest_id[:8]}" if session.guest_id else "Guest"
            progress.update(task_id, description=f"[cyan]{worker_id}[/] ({ident}) [green]Aktif[/]")

            for ch in self.chapters:
                ch_num = ch.get("chapter_num", 1)
                ch_title = ch.get("title", f"Bab {ch_num}")[:18]
                progress.update(
                    task_id,
                    description=f"[cyan]{worker_id}[/] ({ident}) [yellow]Baca Bab {ch_num}[/] [dim]({ch_title})...[/]",
                )

                # Delay baca acak proporsional (misal: 6 - 12 detik per bab)
                read_sec = random.uniform(self.base_delay * 0.8, self.base_delay * 1.3)
                success, status_msg = await session.read_chapter(self.novel_id, ch, read_sec)

                if success:
                    progress.advance(task_id, 1)
                else:
                    logger.warning("[%s] Bab %d: %s", worker_id, ch_num, status_msg)

            progress.update(task_id, description=f"[green][OK] {worker_id}[/] ({ident}) [green]Selesai Membaca![/]")
            await session.close()

    async def _run_member_worker(
        self,
        worker_idx: int,
        account: Dict[str, Any],
        progress: Progress,
        task_id: TaskID,
    ) -> None:
        """Menjalankan satu sesi pembaca Member melalui seluruh bab."""
        worker_id = f"Member-{worker_idx:02d}"
        email = account.get("email", "user")
        short_email = email.split("@")[0][:12]
        acc_country = account.get("country", "ID")
        proxy = self._get_proxy_for_worker(country_code=acc_country)
        session = MemberReaderSession(
            worker_id=worker_id,
            account_data=account,
            novel_title=self.novel_title,
            proxy=proxy,
        )

        async with self.semaphore:
            progress.update(task_id, description=f"[magenta]{worker_id}[/] ({short_email}) [green]Siap Membaca[/]")

            for ch in self.chapters:
                ch_num = ch.get("chapter_num", 1)
                ch_title = ch.get("title", f"Bab {ch_num}")[:18]
                progress.update(
                    task_id,
                    description=f"[magenta]{worker_id}[/] ({short_email}) [yellow]Baca Bab {ch_num}[/] [dim]({ch_title})...[/]",
                )

                read_sec = random.uniform(self.base_delay * 0.8, self.base_delay * 1.3)
                success, status_msg = await session.read_chapter(self.novel_id, ch, read_sec)

                if success:
                    progress.advance(task_id, 1)
                else:
                    logger.warning("[%s] Bab %d: %s", worker_id, ch_num, status_msg)

            progress.update(task_id, description=f"[green][OK] {worker_id}[/] ({short_email}) [green]Selesai Membaca![/]")
            await session.close()

    async def run(self) -> None:
        """Mengeksekusi seluruh antrean reader dengan monitor visual Progress Rich."""
        total_chapters = len(self.chapters)
        if total_chapters == 0:
            console.print("[red]Tidak ada bab gratis yang dapat dibaca pada novel ini.[/]")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(bar_width=25),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            refresh_per_second=4,
        ) as progress:
            tasks = []

            # 1. Spawn Member Tasks
            for idx, acc in enumerate(self.member_accounts, start=1):
                tid = progress.add_task(
                    f"[magenta]Member-{idx:02d}[/] [dim]Antre...[/]",
                    total=total_chapters,
                )
                tasks.append(self._run_member_worker(idx, acc, progress, tid))

            # 2. Spawn Guest Tasks
            for idx in range(1, self.guest_count + 1):
                tid = progress.add_task(
                    f"[cyan]Guest-{idx:02d}[/] [dim]Antre...[/]",
                    total=total_chapters,
                )
                tasks.append(self._run_guest_worker(idx, progress, tid))

            # Jalankan semua worker secara konkuren
            await asyncio.gather(*tasks)


def load_accounts_from_file(file_path: str = "akun.txt") -> List[Dict[str, Any]]:
    """Membaca daftar akun terdaftar dari file JSON Lines."""
    accounts = []
    p = Path(file_path)
    if not p.exists():
        return []

    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                if data.get("access_token"):
                    accounts.append(data)
            except Exception:
                continue
    return accounts


def load_proxies_from_file(file_path: str = "proxies.txt") -> List[str]:
    """Membaca daftar proxy dari file teks jika ada."""
    p = Path(file_path)
    if not p.exists():
        return []

    proxies = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            px = line.strip()
            if px and not px.startswith("#"):
                # Normalisasi prefix http jika belum ada
                if not (px.startswith("http://") or px.startswith("https://") or px.startswith("socks5://")):
                    px = f"http://{px}"
                proxies.append(px)
    return proxies


def render_banner() -> None:
    """Menampilkan banner judul aplikasi."""
    banner_text = Text()
    banner_text.append("=== TOODAT / QUARTERFULL ===\n", style="bold cyan")
    banner_text.append("AUTO READERS & STREAM VIEW SIMULATOR\n", style="bold yellow")
    banner_text.append("Simulasi Pembaca Alami (Member Royalti & Guest Heartbeat) - HTTP/2 Engine", style="dim")

    console.print(
        Panel(
            banner_text,
            border_style="bright_blue",
            padding=(1, 2),
        )
    )


async def main_async(preset_novel_id: Optional[str] = None) -> None:
    """Fungsi utama antarmuka interaktif CLI Auto Readers."""
    render_banner()

    # 1. Ringkasan Status Sistem
    accounts = load_accounts_from_file("akun.txt")
    proxies = load_proxies_from_file("proxies.txt")

    status_table = Table(title="[bold green]Status Sumber Daya Sistem[/]", border_style="dim")
    status_table.add_column("Komponen", style="cyan")
    status_table.add_column("Jumlah", style="bold yellow")
    status_table.add_column("Keterangan", style="dim")

    status_table.add_row(
        "Akun Terdaftar (akun.txt)",
        str(len(accounts)),
        "Siap untuk simulasi Member Reader" if accounts else "Belum ada akun, silakan buat via auto_signup.py",
    )
    status_table.add_row(
        "Daftar Proxy (proxies.txt)",
        str(len(proxies)),
        f"{len(proxies)} IP aktif termuat" if proxies else "Koneksi Langsung / Direct Connection",
    )

    console.print(status_table)
    console.print()

    # 2. Interaksi Input Pengguna
    novel_id = NovelTargetResolver.extract_novel_id(preset_novel_id) if preset_novel_id else None
    while not novel_id:
        raw_novel_input = Prompt.ask(
            "[bold green]?[/] Masukkan URL atau Novel ID target (contoh: Py7LDdwpEQ8e1YKX)",
            default="Py7LDdwpEQ8e1YKX",
        )
        novel_id = NovelTargetResolver.extract_novel_id(raw_novel_input)
        if novel_id:
            break
        console.print("[red]ID Novel tidak valid! Format harus berupa 16-karakter hash ID.[/]")

    # Ambil detail novel dan bab
    with console.status("[bold cyan]Mengambil detail metadata novel dan bab...[/]"):
        try:
            novel_info = await NovelTargetResolver.fetch_novel_details(novel_id)
            novel_title = novel_info.get("title", f"Novel {novel_id}")
            chapters = await NovelTargetResolver.fetch_readable_chapters(novel_id)
        except Exception as exc:
            console.print(f"[bold red]Gagal mengambil informasi novel:[/] {exc}")
            return

    console.print(f"[bold green][OK][/] Novel Ditemukan: [bold yellow]{novel_title}[/] (Total Bab Gratis: [bold cyan]{len(chapters)}[/])")

    if len(chapters) == 0:
        console.print("[red]Novel ini tidak memiliki bab gratis untuk dibaca.[/]")
        return

    # Input jumlah reader member
    max_member_available = len(accounts)
    member_count = 0
    if max_member_available > 0:
        member_count = IntPrompt.ask(
            f"[bold green]?[/] Jumlah Reader Login (Member) yang diinginkan [0 - {max_member_available}]",
            default=min(2, max_member_available),
        )
        if member_count > max_member_available:
            console.print(f"[yellow]Disesuaikan ke jumlah stok maksimum: {max_member_available}[/]")
            member_count = max_member_available
    else:
        console.print("[dim]Lewati reader login karena belum ada data di akun.txt.[/]")

    # Input jumlah reader tamu
    guest_count = IntPrompt.ask(
        "[bold green]?[/] Jumlah Guest Reader yang diinginkan",
        default=2,
    )

    if member_count == 0 and guest_count == 0:
        console.print("[yellow]Jumlah reader adalah 0. Tugas dibatalkan.[/]")
        return

    # Input concurrency
    max_workers = IntPrompt.ask(
        "[bold green]?[/] Jumlah Sesi Konkuren / Worker bersamaan",
        default=min(4, member_count + guest_count),
    )

    # Input mode bab
    console.print("\nPilihan mode bab:")
    console.print("  [1] Baca semua bab gratis yang tersedia")
    console.print("  [2] Baca N bab pertama saja")
    chapter_mode = Prompt.ask("[bold green]?[/] Pilih mode bab", choices=["1", "2"], default="1")

    selected_chapters = chapters
    if chapter_mode == "2":
        max_n = len(chapters)
        n_first = IntPrompt.ask(f"[bold green]?[/] Berapa bab pertama yang ingin dibaca? [1 - {max_n}]", default=min(3, max_n))
        selected_chapters = chapters[:max(1, min(n_first, max_n))]

    # 3. Ringkasan Tugas & Konfirmasi Eksekusi
    summary_table = Table(title="[bold yellow]Rencana Tugas Simulasi Membaca[/]", border_style="cyan")
    summary_table.add_column("Parameter", style="cyan")
    summary_table.add_column("Konfigurasi", style="bold green")

    summary_table.add_row("Target Novel", f"{novel_title} ({novel_id})")
    summary_table.add_row("Jumlah Bab per Reader", f"{len(selected_chapters)} Bab (No. {selected_chapters[0].get('chapter_num')} - {selected_chapters[-1].get('chapter_num')})")
    summary_table.add_row("Member Readers (Login)", f"{member_count} Akun")
    summary_table.add_row("Guest Readers (Tamu)", f"{guest_count} Sesi")
    summary_table.add_row("Total Sesi Reader", f"{member_count + guest_count} Readers")
    summary_table.add_row("Batas Konkurensi", f"{max_workers} Worker Paralel")
    summary_table.add_row("Proxy Digunakan", f"{len(proxies)} Proxy" if proxies else "Direct (Tanpa Proxy)")

    console.print("\n", summary_table, "\n")

    confirm = Confirm.ask("[bold green]?[/] Mulai eksekusi simulasi membaca sekarang?", default=True)
    if not confirm:
        console.print("[yellow]Simulasi dibatalkan oleh pengguna.[/]")
        return

    console.print("\n[bold green]Memulai simulasi membaca... Harap tunggu hingga seluruh reader selesai.[/]\n")

    # Ambil akun yang akan dipakai (pastikan unik)
    chosen_accounts = accounts[:member_count]

    # Jalankan Orchestrator
    orchestrator = ReadingSimulationOrchestrator(
        novel_id=novel_id,
        novel_title=novel_title,
        chapters=selected_chapters,
        member_accounts=chosen_accounts,
        guest_count=guest_count,
        max_concurrency=max_workers,
        proxies=proxies,
        base_delay_per_chapter=4.0,  # 4 detik simulasi per bab untuk efisiensi
    )

    start_time = time.time()
    await orchestrator.run()
    elapsed = time.time() - start_time

    console.print(
        Panel(
            f"[bold green][OK] SELURUH SESI PEMBACA SELESAI DENGAN SUKSES![/]\n"
            f"[dim]Total Waktu Eksekusi: {elapsed:.2f} detik | Total Reader: {member_count + guest_count}[/]",
            border_style="green",
        )
    )


def main() -> None:
    """Entry point CLI."""
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Operasi dibatalkan atau input dihentikan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi kesalahan sistem:[/] {exc}")


if __name__ == "__main__":
    main()
