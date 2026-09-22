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
from stealth_bot.profile import AccountProfile
from stealth_bot.proxy import default_proxy_manager
from stealth_bot.scheduler import StealthScheduler
from stealth_bot.worker import StealthWorker

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
    proxy_count = len(default_proxy_manager.proxies)

    table = Table(title="[bold yellow]Ringkasan Sistem Stealth Bot[/]", show_header=True)
    table.add_column("Parameter", style="cyan")
    table.add_column("Nilai Saat Ini", style="green")

    table.add_row("Total Akun Tersimpan (akun_stealth.txt)", f"{len(accounts)} Akun")
    table.add_row("Total Proxy Aktif (HypeProxy / Pool)", f"{proxy_count} Proxy")
    table.add_row("Rasio Kamuflase (Novel Lain)", f"{int(CAMOUFLAGE_NOVEL_RATIO * 100)}% Kamuflase : {int((1-CAMOUFLAGE_NOVEL_RATIO)*100)}% Target")
    table.add_row("Rentang Kecepatan Manusia (WPM)", f"{MIN_WPM} - {MAX_WPM} Words/Minute")
    table.add_row("Versi Aplikasi Android", f"v{APP_VERSION} (Official)")

    console.print(table)


async def run_organic_reading_flow(scheduler: StealthScheduler):
    accounts = scheduler.load_accounts()
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun_stealth.txt'![/]")
        console.print("[yellow]Silakan buat akun baru terlebih dahulu via Menu [2].[/]")
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


async def run_spaced_signup_flow(scheduler: StealthScheduler):
    console.print("\n[bold cyan]>>> Registrasi Akun Halus (Spaced / Throttled Signup)[/]\n")
    console.print("[dim]Mencegah deteksi 'Registration Clustering' dengan jeda acak 60-180 detik per akun.[/]\n")

    count = IntPrompt.ask("Berapa akun yang ingin didaftarkan?", default=5)
    country = Prompt.ask("Kode Negara (contoh: ID, US, KR, JP, GB)", default="ID").upper()

    confirm = Confirm.ask(f"Mulai pendaftaran {count} akun negara {country} sekarang?", default=True)
    if not confirm:
        return

    def log_cb(msg: str):
        console.print(msg)

    scheduler.status_cb = log_cb
    await scheduler.register_spaced_accounts(total_count=count, country_code=country)
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
        table.add_column("Nickname", style="white")
        table.add_column("Negara", style="yellow")
        table.add_column("Device ID (Prefix)", style="dim")

        for i, acc in enumerate(accounts[:20], start=1):
            table.add_row(
                str(i),
                acc.get("email", "-"),
                acc.get("nickname", "-"),
                acc.get("country", "-"),
                acc.get("device_id", "-")[:12] + "...",
            )
        console.print(table)
        if len(accounts) > 20:
            console.print(f"[dim]...dan {len(accounts) - 20} akun lainnya tersimpan di 'akun_stealth.txt'.[/]")

    Prompt.ask("\nTekan Enter untuk kembali")


async def async_main():
    scheduler = StealthScheduler()

    while True:
        print_banner()
        show_accounts_summary(scheduler)

        console.print(
            "\n[bold white]Pilihan Menu Utama:[/]\n"
            " [bold green][1][/] Jalankan Organic Stealth Reader (Multi-Worker Anti-Fraud)\n"
            " [bold green][2][/] Registrasi Akun Halus (Spaced / Anti-Clustering Signup)\n"
            " [bold green][3][/] Inspeksi Akun di 'akun_stealth.txt'\n"
            " [bold red][0][/] Keluar ke Terminal\n"
        )

        choice = Prompt.ask("Pilih Menu", choices=["0", "1", "2", "3"], default="1")

        if choice == "1":
            await run_organic_reading_flow(scheduler)
        elif choice == "2":
            await run_spaced_signup_flow(scheduler)
        elif choice == "3":
            run_inspect_accounts(scheduler)
        elif choice == "0":
            console.print("[yellow]Keluar dari Stealth Bot. Sampai jumpa![/]")
            break


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Operasi dibatalkan pengguna.[/]")


if __name__ == "__main__":
    main()
