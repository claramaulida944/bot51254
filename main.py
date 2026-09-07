"""
Central Master Controller & Multi-Feature Hub
Toodat / Quarterfull Bot Suite.

Modul ini menghubungkan seluruh fitur bot saat ini dan fitur yang akan dibangun:
1. Auto Readers Simulator (Member Royalti & Guest Heartbeat via HTTP/2).
2. Auto Signup Generator (High Entropy Profile 50 Negara & Realistic Android UA).
3. Manajemen & Validasi Akun (Inspeksi & Uji Keaktifan Token akun.txt).
4. Jelajah & Cari Novel (Pencarian Novel ID langsung dari API Backend).
5. Diagnostik Sesi Tamu (HTTP/2 Connection Test & Guest Fingerprint).
6. Pengaturan & Verifikasi Proxy (proxies.txt).
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

# Import modul yang sudah ada
import auto_reader
from auto_reader import NovelTargetResolver, load_accounts_from_file, load_proxies_from_file
from auto_signup import COUNTRY_CONFIG, HighEntropyProfileGenerator, RegistrationRunner, UserAgentGenerator
from session_manager import IdentifierGenerator, ToodatGuestClient
import interaction_manager
from interaction_manager import (
    clean_name_from_email,
    run_auto_bookmark_cli,
    run_auto_followers_cli,
    run_auto_like_cli,
    run_sync_nicknames_cli,
)
from proxy_manager import ProxyManager, default_proxy_manager, test_proxy_cli
from full_auto_runner import run_full_auto_cli

# Konfigurasi console & logging
console = Console(highlight=False)
logging.basicConfig(level=logging.WARNING)


def render_header() -> None:
    """Menampilkan banner header master control bot."""
    banner = Text()
    banner.append("===============================================================================\n", style="bright_blue")
    banner.append("                  * TOODAT / QUARTERFULL BOT SUITE *                           \n", style="bold cyan")
    banner.append("            Master Control Center & Multi-Feature Framework                   \n", style="bold yellow")
    banner.append("          Engine: HTTP/2 Native • Target API: https://api.quarterfull.io      \n", style="dim")
    banner.append("===============================================================================", style="bright_blue")

    console.print(Panel(banner, border_style="cyan", padding=(0, 1)))


def render_dashboard_stats() -> None:
    """Menampilkan ringkasan status sumber daya terkini sistem."""
    accounts = load_accounts_from_file("akun.txt")
    mgr = default_proxy_manager
    mgr.load_proxies()

    stats_table = Table(box=None, show_header=False, padding=(0, 2))
    stats_table.add_column("Key", style="bold white")
    stats_table.add_column("Val", style="bold green")
    stats_table.add_column("Sep", style="dim")
    stats_table.add_column("Key2", style="bold white")
    stats_table.add_column("Val2", style="bold yellow")

    if mgr.has_proxies:
        if mgr.is_brightdata:
            proxy_status = f"Bright Data ISP (Dynamic Geo)"
        else:
            proxy_status = f"{len(mgr.parsed_proxies)} Proxy Aktif"
    else:
        proxy_status = "Direct Connection (Tanpa Proxy)"

    stats_table.add_row(
        "[+] Total Akun Terdaftar:",
        f"{len(accounts)} Akun (akun.txt)",
        "|",
        "[+] Status Proxy:",
        proxy_status,
    )
    stats_table.add_row(
        "[+] Protokol Target:",
        "HTTP/2 (Direct) / HTTP/1.1 (Proxy)",
        "|",
        "[+] Versi Client Mobile:",
        "3.0.52 (Android prod)",
    )

    console.print(
        Panel(
            stats_table,
            title="[bold green]Status Dashboard Sistem[/]",
            border_style="dim",
            padding=(0, 1),
        )
    )


def wait_for_enter() -> None:
    """Memberikan jeda agar pengguna dapat membaca output sebelum kembali ke menu."""
    console.print()
    try:
        Prompt.ask("[dim]Tekan [bold white]Enter[/] untuk kembali ke Menu Utama...[/]", default="")
    except (KeyboardInterrupt, EOFError):
        pass


# =============================================================================
# FITUR 1: AUTO READERS SIMULATOR
# =============================================================================
def feature_auto_reader(preset_novel_id: Optional[str] = None) -> None:
    """Menjalankan modul Auto Readers (Member Royalti & Guest Heartbeat)."""
    console.print("\n[bold cyan]>>> Membuka Modul Auto Readers Simulator...[/]\n")
    try:
        asyncio.run(auto_reader.main_async(preset_novel_id=preset_novel_id))
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Sesi Auto Reader dibatalkan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[red]Terjadi kesalahan pada Auto Reader:[/] {exc}")
    wait_for_enter()


# =============================================================================
# FITUR 2: AUTO SIGNUP GENERATOR
# =============================================================================
def feature_auto_signup() -> None:
    """Menjalankan modul pendaftaran akun ber-entropi tinggi multi-negara."""
    console.print("\n[bold cyan]>>> Modul Pendaftaran Akun Otomatis (50 Negara & High Entropy)[/]\n")

    selected_country = Prompt.ask(
        "[bold green]?[/] Pilih kode negara (contoh: ID, US, JP, DE) atau 'RANDOM' untuk campuran 50 negara",
        default="RANDOM",
    ).strip().upper()

    count = IntPrompt.ask(
        "[bold green]?[/] Berapa jumlah akun baru yang ingin didaftarkan?",
        default=1,
    )

    ua_mode = Prompt.ask(
        "[bold green]?[/] Mode User-Agent yang digunakan [okhttp / dalvik / webview]",
        choices=["okhttp", "dalvik", "webview"],
        default="okhttp",
    )

    console.print(f"\n[yellow]Memulai pendaftaran {count} akun target ({selected_country})...[/]\n")

    proxies = load_proxies_from_file("proxies.txt")
    runner = RegistrationRunner(accounts_file="akun.txt", proxies=proxies)
    success_count = 0

    with console.status("[bold cyan]Memproses pendaftaran akun ke API Quarterfull...[/]"):
        for i in range(1, count + 1):
            res = runner.register_account(country_code=selected_country, ua_mode=ua_mode)
            if res.get("status") == "success":
                success_count += 1
                acc = res["account"]
                console.print(f"  [bold green][OK][/] Akun #{i}: [bold white]{acc.get('email')}[/] | Negara: [cyan]{acc.get('country')}[/] | User ID: [yellow]{acc.get('user_id')}[/]")
            else:
                err_text = str(res.get("error", ""))
                # Jika terdeteksi 400 No IPs in selected country, coba lagi menggunakan proxy negara lain
                if "No IPs" in err_text or "400" in err_text:
                    console.print(f"  [yellow][RETRY][/] Akun #{i}: 400 No IPs in selected country. Mencoba lagi menggunakan proxy negara alternatif (US)...")
                    retry_res = runner.register_account(country_code="US", ua_mode=ua_mode)
                    if retry_res.get("status") == "success":
                        success_count += 1
                        acc = retry_res["account"]
                        console.print(f"  [bold green][OK][/] Akun #{i} (Retry): [bold white]{acc.get('email')}[/] | Negara: [cyan]{acc.get('country')}[/] | User ID: [yellow]{acc.get('user_id')}[/]")
                        continue
                console.print(f"  [bold red][GAGAL][/] Akun #{i}: {res.get('error')}")

            # Jeda pacing natural untuk menjaga IP agar tidak diblokir rate limit
            if i < count:
                time.sleep(0.5 if proxies else 2.0)

    console.print(f"\n[bold green][OK] Berhasil mendaftarkan {success_count}/{count} akun dan disimpan ke 'akun.txt'![/]")
    wait_for_enter()


# =============================================================================
# FITUR 3: MANAJEMEN & VALIDASI AKUN
# =============================================================================
def feature_account_manager() -> None:
    """Melihat daftar akun yang tersimpan dan memvalidasi keaktifan tokennya."""
    console.print("\n[bold cyan]>>> Modul Manajemen & Validasi Akun[/]\n")
    accounts = load_accounts_from_file("akun.txt")

    if not accounts:
        console.print("[yellow]Belum ada akun tersimpan di 'akun.txt'. Gunakan Menu [2] untuk membuat akun baru.[/]")
        wait_for_enter()
        return

    acc_table = Table(title=f"[bold green]Daftar Akun Terdaftar ({len(accounts)} Akun)[/]", border_style="cyan")
    acc_table.add_column("No", style="dim", width=4)
    acc_table.add_column("User ID", style="yellow")
    acc_table.add_column("Nama / Nickname", style="bold yellow")
    acc_table.add_column("Email", style="bold white")
    acc_table.add_column("Negara", style="cyan")
    acc_table.add_column("User Agent", style="dim")
    acc_table.add_column("Created At", style="dim")

    for idx, acc in enumerate(accounts, start=1):
        created = acc.get("created_at", "-")[:16].replace("T", " ")
        nickname = acc.get("nickname") or f"[dim]({clean_name_from_email(acc.get('email', ''))})[/]"
        acc_table.add_row(
            str(idx),
            str(acc.get("user_id", "-")),
            nickname,
            acc.get("email", "-"),
            acc.get("country", "-"),
            acc.get("user_agent", "-")[:18] + "...",
            created,
        )

    console.print(acc_table)
    console.print()

    # Opsi sinkronisasi nama ke server
    sync_names = Confirm.ask(
        "[bold green]?[/] Sinkronkan & Ubah Nama Pengguna (Nickname) semua akun di server API sekarang?",
        default=False,
    )
    if sync_names:
        run_sync_nicknames_cli()
        wait_for_enter()
        return

    # Opsi uji token
    check_tokens = Confirm.ask("[bold green]?[/] Uji keaktifan semua Bearer Token via API sekarang?", default=False)
    if check_tokens:
        console.print("\n[dim]Menguji keaktifan token ke endpoint resmi backend...[/]\n")
        with httpx.Client(http2=True, base_url="https://api.quarterfull.io", timeout=15.0) as client:
            for idx, acc in enumerate(accounts, start=1):
                headers = {
                    "authorization": f"Bearer {acc.get('access_token', '')}",
                    "user-agent": acc.get("user_agent", "okhttp/4.12.0"),
                    "x-device-id": acc.get("device_id", ""),
                }
                try:
                    resp = client.get("/api/reading/progress", headers=headers)
                    if resp.status_code != 401:
                        status_str = "[bold green]AKTIF (Valid)[/]"
                    else:
                        status_str = "[bold red]KADALUARSA (Expired)[/]"
                except Exception as exc:
                    status_str = f"[yellow]Error: {exc}[/]"

                console.print(f"  [{idx:02d}] {acc.get('email')} -> {status_str}")

    wait_for_enter()


# =============================================================================
# FITUR 4: JELAJAH & CARI NOVEL
# =============================================================================
def feature_search_novels() -> None:
    """Mencari novel berdasarkan kata kunci dan mengambil Novel ID secara instan."""
    console.print("\n[bold cyan]>>> Modul Pencarian & Katalog Novel[/]\n")

    query = Prompt.ask("[bold green]?[/] Masukkan kata kunci pencarian judul/sinopsis (contoh: cinta, dokter, boss, serigala)").strip()
    if not query:
        console.print("[yellow]Kata kunci kosong. Kembali ke menu.[/]")
        wait_for_enter()
        return

    with console.status(f"[bold cyan]Mencari novel dengan kata kunci '{query}'...[/]"):
        try:
            with httpx.Client(http2=True, base_url="https://api.quarterfull.io", timeout=20.0) as client:
                resp = client.get(f"/api/v1/search?q={query}&limit=10", headers={"user-agent": "okhttp/4.12.0"})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            console.print(f"[bold red]Gagal menghubungi server pencarian:[/] {exc}")
            wait_for_enter()
            return

    items = data.get("internal", [])
    if not items:
        console.print(f"[yellow]Tidak ada novel yang ditemukan untuk kata kunci '{query}'.[/]")
        wait_for_enter()
        return

    result_table = Table(title=f"[bold green]Hasil Pencarian: '{query}' ({len(items)} Novel)[/]", border_style="yellow")
    result_table.add_column("No", style="dim", width=4)
    result_table.add_column("Novel ID", style="bold cyan")
    result_table.add_column("Judul Novel", style="bold white")
    result_table.add_column("Status", style="dim")

    for idx, item in enumerate(items[:10], start=1):
        novel_id = item.get("hash_id", "")
        title = item.get("title", "")
        status = "Tersedia"
        result_table.add_row(str(idx), novel_id, title, status)

    console.print(result_table)
    console.print()

    # Opsi langsung tindak lanjut dari hasil pencarian
    action_choice = Prompt.ask(
        "[bold green]?[/] Tindakan pada salah satu novel di atas? [1: Auto Reader | 2: Auto Like | 3: Auto Bookmark | 4: Follow Penulis | 0: Lewati]",
        choices=["0", "1", "2", "3", "4"],
        default="0",
    )
    if action_choice != "0":
        choice_idx = IntPrompt.ask(f"[bold green]?[/] Pilih nomor novel [1 - {len(items[:10])}]", default=1)
        chosen_item = items[max(0, min(choice_idx - 1, len(items) - 1))]
        chosen_id = chosen_item.get("hash_id")
        console.print(f"[green]Memilih: [bold yellow]{chosen_item.get('title')}[/] ({chosen_id})[/]")

        if action_choice == "1":
            feature_auto_reader(preset_novel_id=chosen_id)
            return
        elif action_choice == "2":
            feature_auto_like(preset_novel_id=chosen_id)
            return
        elif action_choice == "3":
            feature_auto_bookmark(preset_novel_id=chosen_id)
            return
        elif action_choice == "4":
            feature_auto_followers(preset_author_id=chosen_id)
            return

    wait_for_enter()


# =============================================================================
# FITUR 5: DIAGNOSTIK SESI TAMU
# =============================================================================
def feature_guest_diagnostics() -> None:
    """Melakukan uji coba koneksi HTTP/2, pembuatan sesi tamu, dan client fingerprint."""
    console.print("\n[bold cyan]>>> Modul Diagnostik Sesi Tamu & Fingerprint HTTP/2[/]\n")

    with console.status("[bold cyan]Menghubungi server Toodat via HTTP/2 dan membuat sesi tamu baru...[/]"):
        try:
            with ToodatGuestClient() as client:
                session_data = client.init_session()
                headers = client.get_headers()
                cookies = client.cookies

                diag_table = Table(title="[bold green]Hasil Diagnostik Sesi Tamu Resmi[/]", border_style="green")
                diag_table.add_column("Parameter Sesi", style="cyan")
                diag_table.add_column("Nilai yang Diperoleh", style="bold white")

                diag_table.add_row("Protokol Jaringan", "HTTP/2 (h2) [OK]")
                diag_table.add_row("Status Sesi", "200 OK (Berhasil Dibuat)")
                diag_table.add_row("Device ID (UUID v4)", client.device_id)
                diag_table.add_row("Guest ID", str(client.guest_id))
                diag_table.add_row("Guest Token (HMAC Sig)", f"{client.guest_token[:36]}... ({len(client.guest_token or '')} Karakter)")
                diag_table.add_row("Cookie qf_guest_reader", f"{cookies.get('qf_guest_reader', '')[:36]}...")
                diag_table.add_row("Masa Berlaku Token", f"{session_data.get('expires_in')} detik (1 Tahun)")

                console.print(diag_table)
        except Exception as exc:
            console.print(f"[bold red]Uji sesi tamu gagal:[/] {exc}")

    wait_for_enter()


# =============================================================================
# FITUR 6: PENGATURAN PROXY
# =============================================================================
def feature_proxy_settings() -> None:
    """Memeriksa file proxies.txt serta menguji konektivitas proxy."""
    console.print("\n[bold cyan]>>> Modul Pengaturan & Verifikasi Proxy (Bright Data / ISP)[/]\n")
    test_proxy_cli()
    wait_for_enter()


# =============================================================================
# FITUR 7: AUTO LIKE NOVEL
# =============================================================================
def feature_auto_like(preset_novel_id: Optional[str] = None) -> None:
    """Menjalankan modul Auto Like Novel via akun terdaftar."""
    try:
        run_auto_like_cli(preset_novel_id=preset_novel_id)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Sesi Auto Like dibatalkan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi kesalahan pada Auto Like:[/] {exc}")
    wait_for_enter()


# =============================================================================
# FITUR 8: AUTO BOOKMARK NOVEL
# =============================================================================
def feature_auto_bookmark(preset_novel_id: Optional[str] = None) -> None:
    """Menjalankan modul Auto Bookmark / Simpan ke Rak Buku."""
    try:
        run_auto_bookmark_cli(preset_novel_id=preset_novel_id)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Sesi Auto Bookmark dibatalkan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi kesalahan pada Auto Bookmark:[/] {exc}")
    wait_for_enter()


# =============================================================================
# FITUR 9: AUTO FOLLOWERS AKUN
# =============================================================================
def feature_auto_followers(preset_author_id: Optional[str] = None) -> None:
    """Menjalankan modul Auto Followers untuk akun penulis / kreator."""
    try:
        run_auto_followers_cli(preset_author_id=preset_author_id)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Sesi Auto Followers dibatalkan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi kesalahan pada Auto Followers:[/] {exc}")
    wait_for_enter()


# =============================================================================
# FITUR 10: UBAH & SINKRONISASI NAMA PENGGUNA (NICKNAME)
# =============================================================================
def feature_sync_nicknames() -> None:
    """Menjalankan modul pembaruan nama pengguna agar sesuai dengan nama asli akun.txt."""
    try:
        run_sync_nicknames_cli()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Sesi pembaruan nama dibatalkan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi kesalahan pada pembaruan nama:[/] {exc}")
    wait_for_enter()


# =============================================================================
# FITUR 11: FULL AUTO BOT (BACA + LIKE + BOOKMARK + FOLLOW + AUTO SKIP)
# =============================================================================
def feature_full_auto(preset_novel_id: Optional[str] = None) -> None:
    """Menjalankan modul Full Auto Bot (All-in-One Novel Automation)."""
    console.print("\n[bold cyan]>>> Membuka Modul Full Auto Bot...[/]\n")
    try:
        asyncio.run(run_full_auto_cli(preset_target=preset_novel_id))
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Sesi Full Auto dibatalkan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi kesalahan pada Full Auto:[/] {exc}")
    wait_for_enter()


# =============================================================================
# MENU UTAMA (MAIN EVENT LOOP)
# =============================================================================
def main_menu() -> None:
    """Loop utama menu interaktif bot."""
    while True:
        console.clear()
        render_header()
        render_dashboard_stats()

        menu_table = Table(title="[bold yellow]PILIHAN MENU UTAMA[/]", border_style="bright_blue", show_edge=True)
        menu_table.add_column("No", style="bold cyan", width=6, justify="center")
        menu_table.add_column("Fitur & Deskripsi", style="bold white")

        menu_table.add_row("[1]", "Auto Readers Simulator [dim](Simulasi Baca Member Royalti & Guest Heartbeat)[/]")
        menu_table.add_row("[2]", "Auto Signup Generator [dim](Daftar Akun Baru 50 Negara & High Entropy Profile)[/]")
        menu_table.add_row("[3]", "Manajemen & Validasi Akun [dim](Lihat Akun Terdaftar & Tes Keaktifan Token)[/]")
        menu_table.add_row("[4]", "Pencarian & Katalog Novel [dim](Cari Novel ID & Judul di Database Resmi)[/]")
        menu_table.add_row("[5]", "Diagnostik Sesi Tamu [dim](Uji Coba HTTP/2, Guest Token & Device Fingerprint)[/]")
        menu_table.add_row("[6]", "Pengaturan Proxy [dim](Kelola Daftar Proxy di proxies.txt)[/]")
        menu_table.add_row("[7]", "Auto Like Novel [bold red](Mass Like Novel Target via Akun Terdaftar)[/]")
        menu_table.add_row("[8]", "Auto Bookmark Novel [bold yellow](Simpan ke Rak Buku / Library)[/]")
        menu_table.add_row("[9]", "Auto Followers Akun [bold green](Mass Follow Akun Penulis / Kreator)[/]")
        menu_table.add_row("[10]", "Ubah Nama Pengguna Akun [bold cyan](Sinkronkan Nickname Sesuai akun.txt)[/]")
        menu_table.add_row("[11]", "★ FULL AUTO BOT ★ [bold magenta](Baca Novel + Like + Simpan + Follow + Auto Skip)[/]")
        menu_table.add_row("[0]", "[bold red]Keluar / Exit[/]")

        console.print(menu_table)
        console.print()

        try:
            choice = Prompt.ask(
                "[bold green]?[/] Pilih menu [0-11]",
                choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"],
                default="11",
            )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Keluar dari aplikasi.[/]")
            break

        if choice == "1":
            feature_auto_reader()
        elif choice == "2":
            feature_auto_signup()
        elif choice == "3":
            feature_account_manager()
        elif choice == "4":
            feature_search_novels()
        elif choice == "5":
            feature_guest_diagnostics()
        elif choice == "6":
            feature_proxy_settings()
        elif choice == "7":
            feature_auto_like()
        elif choice == "8":
            feature_auto_bookmark()
        elif choice == "9":
            feature_auto_followers()
        elif choice == "10":
            feature_sync_nicknames()
        elif choice == "11":
            feature_full_auto()
        elif choice == "0":
            console.print("\n[bold yellow]Terima kasih telah menggunakan Toodat Bot Suite. Sampai jumpa![/]\n")
            break


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\n[yellow]Aplikasi ditutup oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi error fatal:[/] {exc}")
