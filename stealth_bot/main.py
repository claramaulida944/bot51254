"""
Entrypoint CLI Interaktif Stealth Bot V2.
Dirancang khusus dengan arsitektur anti-deteksi multi-lapisan:
- Human WPM Reading Timing
- Camouflage & Discovery Path Emulation
- Spaced Poisson Registration
- AppsFlyer Attribution Sync
"""

import asyncio
import os
import sys
from pathlib import Path

# Pastikan modul internal terbaca
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from stealth_bot.config import (
    ACCOUNTS_FILE,
    DEFAULT_TARGET_NOVEL_ID,
    CAMOUFLAGE_NOVEL_RATIO,
    MIN_WPM,
    MAX_WPM,
    APP_VERSION,
)
from stealth_bot.client import StealthApiClient
from stealth_bot.profile import AccountProfile, ProfileGenerator
from stealth_bot.proxy import default_proxy_manager
from stealth_bot.scheduler import StealthScheduler
from stealth_bot.worker import StealthWorker, StealthGuestWorker

console = Console()


def print_banner():
    console.clear()
    banner = (
        "[bold cyan]╔═══════════════════════════════════════════════════════════════╗[/]\n"
        "[bold cyan]║[/]        [bold white]STEALTH BOT V2 - ADVANCED ANTI-FRAUD BYPASS[/]            [bold cyan]║[/]\n"
        "[bold cyan]║[/]   [dim]Engine Pembaca Organik, WPM Dinamis & Penyamaran Katalog[/]    [bold cyan]║[/]\n"
        f"[bold cyan]║[/]   [dim]Target Versi Android: v{APP_VERSION} | Atribusi: AppsFlyer Verified[/]   [bold cyan]║[/]\n"
        "[bold cyan]╚═══════════════════════════════════════════════════════════════╝[/]"
    )
    console.print(banner)


def show_accounts_summary(scheduler: StealthScheduler):
    accounts = scheduler.load_accounts()
    proxy_desc = (
        f"[green]{len(default_proxy_manager.proxies)} Proxy (Aktif)[/]"
        if (default_proxy_manager.enabled and default_proxy_manager.proxies)
        else "[bold yellow]Direct (Tanpa Proxy)[/]"
    )

    table = Table(title="[bold yellow]Ringkasan Sistem Stealth Bot[/]", show_header=True)
    table.add_column("Parameter", style="cyan")
    table.add_column("Nilai Saat Ini", style="green")

    table.add_row("Total Akun Tersimpan (akun_stealth.txt)", f"{len(accounts)} Akun")
    table.add_row("Mode Proxy Jaringan", proxy_desc)
    table.add_row("Rasio Kamuflase (Novel Lain)", f"{int(CAMOUFLAGE_NOVEL_RATIO * 100)}% Kamuflase : {int((1-CAMOUFLAGE_NOVEL_RATIO)*100)}% Target")
    table.add_row("Rentang Kecepatan Manusia (WPM)", f"{MIN_WPM} - {MAX_WPM} Words/Minute")
    table.add_row("Versi Aplikasi Android", f"v{APP_VERSION} (Official)")

    console.print(table)


async def run_organic_reading_flow(scheduler: StealthScheduler):
    accounts = scheduler.load_accounts()
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun_stealth.txt'![/]")
        console.print("[yellow]Silakan buat akun baru terlebih dahulu via Menu [3].[/]")
        Prompt.ask("\nTekan Enter untuk kembali")
        return

    console.print("\n[bold cyan]>>> Konfigurasi Sesi Pembaca Organik[/]\n")
    target_novel = Prompt.ask("Masukkan Novel ID Target", default=DEFAULT_TARGET_NOVEL_ID)
    worker_count = IntPrompt.ask(f"Jumlah Akun yang Dijalankan (Maks {len(accounts)})", default=min(3, len(accounts)))
    worker_count = max(1, min(worker_count, len(accounts)))
    chapters_per_account = IntPrompt.ask("Maksimal Bab Target per Akun", default=4)

    console.print(f"\n[bold green]Memulai {worker_count} pekerja pembaca organik untuk novel target '{target_novel}'...[/]\n")

    selected_accs = accounts[:worker_count]

    async def run_single_worker(idx: int, acc: dict):
        profile = AccountProfile(
            email=acc.get("email", ""),
            password=acc.get("password", ""),
            first_name=acc.get("nickname", "User").split()[0],
            last_name="Reader",
            nickname=acc.get("nickname", "Reader"),
            birth_date=acc.get("created_at", "2000-01-01"),
            gender="prefer_not_to_say",
            country=acc.get("country", "ID"),
            timezone="Asia/Jakarta",
            accept_language="id",
            device_id=acc.get("device_id", ""),
            anonymous_id=acc.get("anonymous_id", ""),
            user_agent=acc.get("user_agent", "okhttp/4.12.0"),
        )
        client = StealthApiClient(
            profile=profile,
            access_token=acc.get("access_token", ""),
            refresh_token=acc.get("refresh_token", ""),
            proxy_manager=default_proxy_manager,
        )

        def log_cb(msg: str):
            console.print(msg)

        worker = StealthWorker(
            worker_id=idx,
            client=client,
            target_novel_id=target_novel,
            status_cb=log_cb,
        )
        try:
            await worker.run_stealth_session(max_chapters_target=chapters_per_account)
        finally:
            await client.close()

    tasks = [run_single_worker(i, acc) for i, acc in enumerate(selected_accs, start=1)]
    await asyncio.gather(*tasks)

    console.print("\n[bold green]Semua sesi pembaca organik telah selesai dengan aman dan wajar![/]")
    Prompt.ask("\nTekan Enter untuk kembali")


async def run_guest_reading_flow():
    console.print("\n[bold cyan]>>> Sesi Pembaca Tamu Organik (Guest Mode)[/]\n")
    console.print("[dim]Keunggulan Mode Tamu: Tidak memerlukan akun (Bebas 100% dari 'Registration Clustering').[/]")
    console.print("[dim]Setiap tamu menggunakan profil, proxy terisolasi & cold-start resmi Android.[/]\n")

    target_novel = Prompt.ask("Masukkan Novel ID Target", default=DEFAULT_TARGET_NOVEL_ID)
    guest_count = IntPrompt.ask("Berapa sesi tamu yang ingin dijalankan bergantian?", default=5)
    chapters_per_guest = IntPrompt.ask("Maksimal Bab yang Dibaca per Tamu", default=3)
    country = Prompt.ask("Kode Negara Tamu (contoh: ID, US, KR, JP)", default="ID").upper()

    console.print(f"\n[bold green]Memulai {guest_count} sesi pembaca tamu untuk novel '{target_novel}'...[/]\n")

    import random

    for i in range(1, guest_count + 1):
        profile = ProfileGenerator.generate_profile(country_code=country)
        proxy = default_proxy_manager.pop_proxy(country)
        client = StealthApiClient(
            profile=profile,
            proxy_manager=default_proxy_manager,
            current_proxy=proxy,
        )

        def log_cb(msg: str):
            console.print(msg)

        worker = StealthGuestWorker(
            guest_index=i,
            client=client,
            target_novel_id=target_novel,
            status_cb=log_cb,
        )

        try:
            await worker.run_guest_session(max_chapters=chapters_per_guest)
        finally:
            await client.close()

        if i < guest_count:
            delay = random.uniform(10.0, 25.0)
            console.print(f"\n[dim]Menunggu jeda kedatangan tamu berikutnya ({int(delay)}s)...[/]\n")
            await asyncio.sleep(delay)

    console.print("\n[bold green]Semua sesi pembaca tamu telah selesai secara alami![/]")
    Prompt.ask("\nTekan Enter untuk kembali")


async def run_spaced_signup_flow(scheduler: StealthScheduler):
    console.print("\n[bold cyan]>>> Registrasi Akun Halus (Full Auto Random Stealth)[/]\n")
    console.print("[dim]Fitur Otomatis Penuh: Mengacak Negara (KR/US/JP/GB/ID), Provider Email Wajar (Outlook/Hotmail/Gmail Unik), dan Verifikasi OTP 120s.[/]\n")

    count = IntPrompt.ask("Berapa akun yang ingin didaftarkan?", default=5)

    console.print(f"\n[bold green]Memulai registrasi {count} akun (Random Country + Random Email + Auto-Verify OTP)...[/]\n")

    def log_cb(msg: str):
        console.print(msg)

    scheduler.status_cb = log_cb
    await scheduler.register_spaced_accounts(
        total_count=count,
        country_code="RANDOM",
        verify_email=True,
        email_provider="RANDOM",
    )
    Prompt.ask("\nTekan Enter untuk kembali")


def run_inspect_accounts(scheduler: StealthScheduler):
    accounts = scheduler.load_accounts()
    console.print(f"\n[bold cyan]>>> Daftar Akun Stealth Terdaftar ({len(accounts)} Akun)[/]\n")
    if not accounts:
        console.print("[yellow]Belum ada akun di akun_stealth.txt.[/]")
    else:
        table = Table(show_header=True)
        table.add_column("No", style="dim", width=4)
        table.add_column("Email", style="cyan")
        table.add_column("Status Verif", justify="center")
        table.add_column("Nickname", style="white")
        table.add_column("Negara", style="yellow")
        table.add_column("Device ID (Prefix)", style="dim")

        for i, acc in enumerate(accounts[:25], start=1):
            is_v = acc.get("is_email_verified", False)
            v_badge = "[bold green]VERIFIED[/]" if is_v else "[dim yellow]UNVERIFIED[/]"
            table.add_row(
                str(i),
                acc.get("email", "-"),
                v_badge,
                acc.get("nickname", "-"),
                acc.get("country", "-"),
                acc.get("device_id", "-")[:12] + "...",
            )
        console.print(table)
        if len(accounts) > 25:
            console.print(f"[dim]...dan {len(accounts) - 25} akun lainnya tersimpan di 'akun_stealth.txt'.[/]")

    Prompt.ask("\nTekan Enter untuk kembali")


async def async_main():
    scheduler = StealthScheduler()

    while True:
        print_banner()
        show_accounts_summary(scheduler)

        console.print(
            "\n[bold white]Pilihan Menu Utama:[/]\n"
            " [bold green][1][/] Jalankan Member Stealth Reader (Dengan Akun Terdaftar)\n"
            " [bold green][2][/] Jalankan Guest Stealth Reader (Mode Tamu Organik - Tanpa Akun)\n"
            " [bold green][3][/] Registrasi Akun Halus (Spaced / Anti-Clustering Signup)\n"
            " [bold green][4][/] Inspeksi Akun di 'akun_stealth.txt'\n"
            " [bold green][5][/] Toggle Mode Proxy (Aktif / Direct Koneksi)\n"
            " [bold red][0][/] Keluar ke Terminal\n"
        )

        choice = Prompt.ask("Pilih Menu", choices=["0", "1", "2", "3", "4", "5"], default="1")

        if choice == "1":
            await run_organic_reading_flow(scheduler)
        elif choice == "2":
            await run_guest_reading_flow()
        elif choice == "3":
            await run_spaced_signup_flow(scheduler)
        elif choice == "4":
            run_inspect_accounts(scheduler)
        elif choice == "5":
            default_proxy_manager.enabled = not default_proxy_manager.enabled
            status = "[bold green]DIAKTIFKAN[/]" if default_proxy_manager.enabled else "[bold yellow]DINONAKTIFKAN (Direct Connection)[/]"
            console.print(f"\n[bold cyan]Status Proxy:[/] {status}")
            Prompt.ask("\nTekan Enter untuk kembali")
        elif choice == "0":
            console.print("[yellow]Keluar dari Stealth Bot. Sampai jumpa![/]")
            break


def main():
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Operasi dihentikan. Keluar.[/]")


if __name__ == "__main__":
    main()
