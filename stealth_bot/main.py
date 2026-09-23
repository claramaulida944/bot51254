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
import random
import sys
from pathlib import Path

# Pastikan modul internal terbaca
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table, Column
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TaskID,
)

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
from stealth_bot.proxy import default_proxy_manager, pick_weighted_country, COUNTRY_METADATA
from stealth_bot.scheduler import StealthScheduler, extract_base_username
from stealth_bot.worker import StealthWorker, StealthGuestWorker
from stealth_bot.email_verifier import TempTfVerifier

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


async def run_valid_reading_flow(scheduler: StealthScheduler):
    accounts = scheduler.load_accounts()
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun_stealth.txt'![/]")
        console.print("[yellow]Silakan buat akun baru terlebih dahulu via Menu Registrasi.[/]")
        Prompt.ask("\nTekan Enter untuk kembali")
        return

    console.print("\n[bold cyan]>>> Konfigurasi Pembaca Valid (Valid Reader: Bab 1-20 / Tamat)[/]\n")
    console.print(
        "[dim]Karakteristik Pembaca Valid:\n"
        " • Akun login membaca Bab 1 s/d Bab 20 secara tuntas tanpa drop-off prematur.\n"
        " • Jika novel memiliki >20 bab, pembaca berpeluang maraton membaca semua bab sampai tamat.\n"
        " • Telemetri progres kuadran (25%, 50%, 75%, 100%), WPM dinamis, dan apresiasi sosial (Like/Rak/Follow).[/]\n"
    )

    target_novel = Prompt.ask("Masukkan Novel ID Target", default=DEFAULT_TARGET_NOVEL_ID)
    worker_count = IntPrompt.ask(f"Jumlah Akun Pembaca Valid (Maks {len(accounts)})", default=min(3, len(accounts)))
    worker_count = max(1, min(worker_count, len(accounts)))
    concurrency = IntPrompt.ask("Jumlah Worker Bersamaan / Paralel (1-5 disarankan)", default=min(3, worker_count))
    concurrency = max(1, min(concurrency, worker_count))

    binge_input = Prompt.ask("Peluang Maraton Tamat jika bab > 20 (Persentase %)", default="30").strip().replace("%", "")
    try:
        binge_chance = max(0.0, min(100.0, float(binge_input))) / 100.0
    except ValueError:
        binge_chance = 0.30

    with console.status("[bold cyan]Memuat informasi novel target...[/]"):
        temp_client = StealthApiClient(proxy_manager=default_proxy_manager)
        try:
            ch_list = await temp_client.get_novel_chapters(target_novel)
            total_chs = len(ch_list) if ch_list else 20
        except Exception:
            total_chs = 20
        finally:
            await temp_client.close()

    if total_chs <= 20:
        expected_chapters = total_chs
        console.print(f"\n[bold green]Novel memiliki {total_chs} bab (<= 20). Semua {worker_count} akun akan membaca tuntas sampai TAMAT![/]\n")
    else:
        expected_chapters = total_chs
        console.print(f"\n[bold green]Novel memiliki {total_chs} bab. Akun membaca minimal 20 bab tuntas ({int(binge_chance*100)}% peluang maraton sampai tamat {total_chs} bab)...\n")

    selected_accs = accounts[:worker_count]
    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.fields[role]}[/]", justify="left", table_column=Column(width=13, no_wrap=True)),
        TextColumn("{task.description}", justify="left", table_column=Column(width=34, no_wrap=True)),
        BarColumn(bar_width=16),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=4,
    ) as progress:
        overall_task = progress.add_task(
            description="[dim]Memproses antrean reader valid...[/]",
            total=worker_count,
            role="[bold yellow]★ TOTAL[/]",
        )

        num_slots = min(concurrency, worker_count)
        slot_tasks = []
        for s_idx in range(1, num_slots + 1):
            tid = progress.add_task(
                description="[dim]Menunggu antrean...[/]",
                total=expected_chapters,
                role=f"Slot-{s_idx:02d}",
            )
            slot_tasks.append((s_idx, tid))

        async def run_single_valid_member(idx: int, acc: dict, slot_idx: int, task_id: TaskID):
            email = acc.get("email", f"User_{idx}")
            country = acc.get("country", "ID")

            progress.reset(task_id, total=expected_chapters)
            progress.update(
                task_id,
                role=f"Slot-{slot_idx:02d}",
                description=f"[cyan]W-{idx:02d} Valid ({country})...[/]",
                completed=0,
            )

            profile = AccountProfile(
                email=acc.get("email", ""),
                password=acc.get("password", ""),
                first_name=acc.get("nickname", "User").split()[0],
                last_name="Reader",
                nickname=acc.get("nickname", "Reader"),
                birth_date=acc.get("created_at", "2000-01-01"),
                gender="prefer_not_to_say",
                country=country,
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
                account_data=acc,
                save_account_cb=scheduler.update_account,
            )

            def prog_cb(completed: int, total: int, desc: str):
                progress.update(
                    task_id,
                    completed=completed,
                    total=total or expected_chapters,
                    description=desc[:34],
                )

            def stat_cb(msg: str):
                raw = msg.split("]", 1)[-1].strip() if "]" in msg else msg
                if "Selesai sesi" in raw:
                    progress.console.print(f"[bold green]✓ [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "KECANDUAN" in raw or "TAMAT" in raw:
                    progress.console.print(f"[bold magenta]★ [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "Gagal" in raw or "Terhenti" in raw:
                    progress.console.print(f"[yellow]! [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "meremake cerita" in raw:
                    progress.console.print(f"[bold yellow]⚡ [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "di-remake pembaca lain" in raw:
                    progress.console.print(f"[bold cyan]📖 [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "menyukai novel ini" in raw or "SANGAT SUKA" in raw or "SANGAT MENYUKAI" in raw:
                    progress.console.print(f"[bold magenta]♥ [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                else:
                    progress.update(task_id, description=raw[:34])

            worker = StealthWorker(
                worker_id=idx,
                client=client,
                target_novel_id=target_novel,
                status_cb=stat_cb,
                progress_cb=prog_cb,
                account_data=acc,
                save_account_cb=scheduler.update_account,
            )

            read_ch = 0
            try:
                read_ch = await worker.run_stealth_session(
                    valid_reader_mode=True,
                    binge_to_end_chance=binge_chance,
                )
                if read_ch >= total_chs:
                    progress.update(task_id, completed=read_ch, description="[bold green]Valid (TAMAT) [OK][/]")
                elif read_ch >= min(20, total_chs):
                    progress.update(task_id, completed=read_ch, description="[bold green]Valid (20 Bab) [OK][/]")
                else:
                    progress.update(task_id, completed=read_ch, description=f"[yellow]Parsial ({read_ch} Bab)[/]")
            except Exception as exc:
                progress.console.print(f"[yellow]! [Slot-{slot_idx:02d}][W-{idx:02d}] Terhenti: {exc}[/]")
                progress.update(task_id, description="[red]Terhenti[/]")
            finally:
                await client.close()
                progress.advance(overall_task, 1)

            if read_ch >= total_chs:
                status_label = "[bold green]Valid (TAMAT)[/]"
            elif read_ch >= min(20, total_chs):
                status_label = "[green]Valid (20 Bab)[/]"
            elif read_ch > 0:
                status_label = f"[yellow]Parsial ({read_ch} Bab)[/]"
            else:
                status_label = "[red]Gagal (0 Bab)[/]"

            results.append({
                "no": len(results) + 1,
                "type": "Valid Reader",
                "ident": email,
                "country": country,
                "chapters_read": read_ch,
                "status": status_label,
            })

        member_queue: asyncio.Queue = asyncio.Queue()
        for idx, acc in enumerate(selected_accs, start=1):
            member_queue.put_nowait((idx, acc))

        async def slot_worker(slot_idx: int, task_id: TaskID):
            if slot_idx > 1:
                await asyncio.sleep(random.uniform(1.2, 3.0) * (slot_idx - 1))

            while not member_queue.empty():
                try:
                    idx, acc = member_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                await run_single_valid_member(idx, acc, slot_idx, task_id)

                if not member_queue.empty():
                    progress.update(task_id, description="[dim]Jeda antarsesi...[/]")
                    await asyncio.sleep(random.uniform(2.0, 5.0))

            progress.update(task_id, description="[bold green]Semua Selesai [OK][/]")

        workers = [slot_worker(s_idx, tid) for s_idx, tid in slot_tasks]
        await asyncio.gather(*workers)

    # Cetak Tabel Laporan Rapi di Akhir
    report_table = Table(
        title=f"\n[bold green]Laporan Sesi Pembaca Valid ({target_novel})[/]",
        border_style="cyan",
    )
    report_table.add_column("No", style="dim", width=4)
    report_table.add_column("Tipe", style="bold", width=14)
    report_table.add_column("Identitas / Akun", style="bold white")
    report_table.add_column("Negara", style="cyan", width=8)
    report_table.add_column("Bab Dibaca", justify="center", width=14)
    report_table.add_column("Status Validitas", style="bold")

    valid_count = 0
    for r in results:
        report_table.add_row(
            str(r["no"]),
            r["type"],
            r["ident"],
            r["country"],
            f"{r['chapters_read']}/{expected_chapters}",
            r["status"],
        )
        if "Valid" in r["status"]:
            valid_count += 1

    console.print(report_table)
    console.print(f"\n[bold green]Semua {worker_count} sesi pembaca valid telah selesai! ({valid_count}/{worker_count} Akun Berstatus Valid)[/]")
    Prompt.ask("\nTekan Enter untuk kembali")


async def run_organic_reading_flow(scheduler: StealthScheduler):
    accounts = scheduler.load_accounts()
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun_stealth.txt'![/]")
        console.print("[yellow]Silakan buat akun baru terlebih dahulu via Menu [3].[/]")
        Prompt.ask("\nTekan Enter untuk kembali")
        return

    console.print("\n[bold cyan]>>> Konfigurasi Sesi Pembaca Organik (Member Reader)[/]\n")
    target_novel = Prompt.ask("Masukkan Novel ID Target", default=DEFAULT_TARGET_NOVEL_ID)
    worker_count = IntPrompt.ask(f"Jumlah Akun yang Dijalankan (Maks {len(accounts)})", default=min(3, len(accounts)))
    worker_count = max(1, min(worker_count, len(accounts)))
    concurrency = IntPrompt.ask("Jumlah Worker Bersamaan / Paralel (1-5 disarankan)", default=min(3, worker_count))
    concurrency = max(1, min(concurrency, worker_count))

    ch_input = Prompt.ask("Maksimal Bab Target per Akun ([bold green]0[/] atau [bold green]'ALL'[/] untuk BACA SEMUA BAB SAMPAI TAMAT)", default="0")
    if ch_input.strip().upper() in ("0", "ALL", "SEMUA", ""):
        chapters_per_account = 0
        console.print("[dim cyan]Mode Terpilih: Baca semua bab novel sampai tamat (tanpa drop-off awal).[/]")
    else:
        try:
            chapters_per_account = int(ch_input)
        except ValueError:
            chapters_per_account = 0

    with console.status("[bold cyan]Memuat informasi novel target...[/]"):
        temp_client = StealthApiClient(proxy_manager=default_proxy_manager)
        try:
            ch_list = await temp_client.get_novel_chapters(target_novel)
            total_chs = len(ch_list) if ch_list else 15
        except Exception:
            total_chs = 15
        finally:
            await temp_client.close()

    expected_chapters = total_chs if chapters_per_account <= 0 else min(chapters_per_account, total_chs)

    console.print(f"\n[bold green]Memulai {worker_count} pekerja pembaca organik untuk novel target '{target_novel}' ({expected_chapters} bab target)...[/]\n")

    selected_accs = accounts[:worker_count]
    total_batches = (worker_count + concurrency - 1) // concurrency
    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.fields[role]}[/]", justify="left", table_column=Column(width=13, no_wrap=True)),
        TextColumn("{task.description}", justify="left", table_column=Column(width=34, no_wrap=True)),
        BarColumn(bar_width=16),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=4,
    ) as progress:
        overall_task = progress.add_task(
            description="[dim]Memproses antrean reader...[/]",
            total=worker_count,
            role="[bold yellow]★ TOTAL[/]",
        )

        num_slots = min(concurrency, worker_count)
        slot_tasks = []
        for s_idx in range(1, num_slots + 1):
            tid = progress.add_task(
                description="[dim]Menunggu antrean...[/]",
                total=expected_chapters,
                role=f"Slot-{s_idx:02d}",
            )
            slot_tasks.append((s_idx, tid))

        async def run_single_member(idx: int, acc: dict, slot_idx: int, task_id: TaskID):
            email = acc.get("email", f"User_{idx}")
            country = acc.get("country", "ID")

            progress.reset(task_id, total=expected_chapters)
            progress.update(
                task_id,
                role=f"Slot-{slot_idx:02d}",
                description=f"[cyan]W-{idx:02d} Masuk ({country})...[/]",
                completed=0,
            )

            profile = AccountProfile(
                email=acc.get("email", ""),
                password=acc.get("password", ""),
                first_name=acc.get("nickname", "User").split()[0],
                last_name="Reader",
                nickname=acc.get("nickname", "Reader"),
                birth_date=acc.get("created_at", "2000-01-01"),
                gender="prefer_not_to_say",
                country=country,
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
                account_data=acc,
                save_account_cb=scheduler.update_account,
            )

            def prog_cb(completed: int, total: int, desc: str):
                progress.update(
                    task_id,
                    completed=completed,
                    total=total or expected_chapters,
                    description=desc[:34],
                )

            def stat_cb(msg: str):
                raw = msg.split("]", 1)[-1].strip() if "]" in msg else msg
                if "Selesai sesi" in raw:
                    progress.console.print(f"[bold green]✓ [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "Gagal" in raw or "Terhenti" in raw:
                    progress.console.print(f"[yellow]! [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "meremake cerita" in raw:
                    progress.console.print(f"[bold yellow]⚡ [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "di-remake pembaca lain" in raw:
                    progress.console.print(f"[bold cyan]📖 [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                elif "menyukai novel ini" in raw or "SANGAT SUKA" in raw or "SANGAT MENYUKAI" in raw:
                    progress.console.print(f"[bold magenta]♥ [Slot-{slot_idx:02d}][W-{idx:02d}] {raw}[/]")
                else:
                    progress.update(task_id, description=raw[:34])

            worker = StealthWorker(
                worker_id=idx,
                client=client,
                target_novel_id=target_novel,
                status_cb=stat_cb,
                progress_cb=prog_cb,
                account_data=acc,
                save_account_cb=scheduler.update_account,
            )

            read_ch = 0
            is_ok = False
            try:
                read_ch = await worker.run_stealth_session(max_chapters_target=chapters_per_account)
                is_ok = (read_ch > 0)
                if is_ok:
                    progress.update(task_id, completed=read_ch, description="[bold green]Selesai [OK][/]")
                else:
                    progress.update(task_id, completed=0, description="[yellow]Gagal (0 Bab)[/]")
            except Exception as exc:
                progress.console.print(f"[yellow]! [Slot-{slot_idx:02d}][W-{idx:02d}] Terhenti: {exc}[/]")
                progress.update(task_id, description="[red]Terhenti[/]")
            finally:
                await client.close()
                progress.advance(overall_task, 1)

            results.append({
                "no": len(results) + 1,
                "type": "Member",
                "ident": email,
                "country": country,
                "chapters_read": read_ch,
                "status": "[green]Sukses[/]" if is_ok else "[red]Gagal[/]",
            })

        # Antrean Dinamis: Setiap slot yang selesai langsung mengambil sesi berikutnya tanpa menunggu slot lain
        member_queue: asyncio.Queue = asyncio.Queue()
        for idx, acc in enumerate(selected_accs, start=1):
            member_queue.put_nowait((idx, acc))

        async def slot_worker(slot_idx: int, task_id: TaskID):
            if slot_idx > 1:
                await asyncio.sleep(random.uniform(1.2, 3.0) * (slot_idx - 1))

            while not member_queue.empty():
                try:
                    idx, acc = member_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                await run_single_member(idx, acc, slot_idx, task_id)

                if not member_queue.empty():
                    progress.update(task_id, description="[dim]Jeda antarsesi...[/]")
                    await asyncio.sleep(random.uniform(2.0, 5.0))

            progress.update(task_id, description="[bold green]Semua Selesai [OK][/]")

        workers = [slot_worker(s_idx, tid) for s_idx, tid in slot_tasks]
        await asyncio.gather(*workers)

    # Cetak Tabel Laporan Rapi di Akhir
    report_table = Table(
        title=f"\n[bold green]Laporan Sesi Stealth Auto Reader ({target_novel})[/]",
        border_style="cyan",
    )
    report_table.add_column("No", style="dim", width=4)
    report_table.add_column("Tipe", style="bold", width=14)
    report_table.add_column("Identitas / Akun", style="bold white")
    report_table.add_column("Negara", style="cyan", width=8)
    report_table.add_column("Bab Dibaca", justify="center", width=12)
    report_table.add_column("Status", style="bold")

    for r in results:
        report_table.add_row(
            str(r["no"]),
            r["type"],
            r["ident"],
            r["country"],
            f"{r['chapters_read']}/{expected_chapters}",
            r["status"],
        )

    console.print(report_table)
    console.print(f"\n[bold green]Semua {worker_count} sesi pembaca organik telah selesai secara alami dan rapi![/]")
    Prompt.ask("\nTekan Enter untuk kembali")


async def run_guest_reading_flow(scheduler: StealthScheduler):
    console.print("\n[bold cyan]>>> Sesi Pembaca Tamu Organik (Multi-Worker Guest Mode)[/]\n")
    console.print("[dim]Keunggulan: Bebas 100% dari 'Registration Clustering' & pola bot serentak.[/]")
    console.print("[dim]Didukung konversi organik: Tamu membaca bab gratis -> Lanjut mendaftar & verifikasi OTP resmi.[/]\n")

    target_novel = Prompt.ask("Masukkan Novel ID Target", default=DEFAULT_TARGET_NOVEL_ID)
    guest_count = IntPrompt.ask("Total Sesi Tamu yang ingin dijalankan", default=10)
    concurrency = IntPrompt.ask("Jumlah Worker Bersamaan / Paralel (5-10 disarankan)", default=5)
    chapters_per_guest = IntPrompt.ask("Jumlah Bab Dibaca sebagai Tamu (Maks 4 bab gratis sebelum gembok login)", default=4)

    auto_convert = Confirm.ask("Aktifkan Konversi Alami (Tamu lanjut Daftar Akun & Verifikasi OTP)?", default=True)
    member_chapters_after = 0
    if auto_convert:
        after_input = Prompt.ask("Jumlah Bab Dibaca Lanjutan Setelah Jadi Member ([bold green]0[/] atau [bold green]'ALL'[/] untuk BACA SEMUA BAB SISA)", default="0")
        if after_input.strip().upper() in ("0", "ALL", "SEMUA", ""):
            member_chapters_after = 0
            convert_label = "+ Konversi Akun (BACA SEMUA BAB SISA SAMPAI TAMAT)"
        else:
            try:
                member_chapters_after = int(after_input)
                convert_label = f"+ Konversi Akun ({member_chapters_after} Bab Member)"
            except ValueError:
                member_chapters_after = 0
                convert_label = "+ Konversi Akun (BACA SEMUA BAB SISA SAMPAI TAMAT)"
    else:
        convert_label = "(Tamu Murni)"

    country_input = Prompt.ask("Negara Tamu (50 Negara Resmi, default 'RANDOM': 30% US, 20% KR, 10% ID, 40% Global)", default="RANDOM").upper().strip()

    with console.status("[bold cyan]Memuat informasi novel target...[/]"):
        temp_client = StealthApiClient(proxy_manager=default_proxy_manager)
        try:
            ch_list = await temp_client.get_novel_chapters(target_novel)
            total_chs = len(ch_list) if ch_list else 15
        except Exception:
            total_chs = 15
        finally:
            await temp_client.close()

    if auto_convert and member_chapters_after == 0:
        expected_chapters = total_chs
    elif auto_convert and member_chapters_after > 0:
        expected_chapters = min(chapters_per_guest + member_chapters_after, total_chs)
    else:
        expected_chapters = min(chapters_per_guest, total_chs)

    console.print(
        f"\n[bold green]Memulai {guest_count} sesi tamu {convert_label} ({concurrency} worker paralel, negara: {country_input}) "
        f"untuk novel '{target_novel}' ({expected_chapters} bab target)...[/]\n"
    )

    verifier = TempTfVerifier() if auto_convert else None
    saved_accs = scheduler.load_accounts()
    existing_emails = {str(a.get("email", "")).strip().lower() for a in saved_accs if a.get("email")}
    existing_base_users = {extract_base_username(e) for e in existing_emails if e}

    total_batches = (guest_count + concurrency - 1) // concurrency
    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.fields[role]}[/]", justify="left", table_column=Column(width=13, no_wrap=True)),
        TextColumn("{task.description}", justify="left", table_column=Column(width=34, no_wrap=True)),
        BarColumn(bar_width=16),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=4,
    ) as progress:
        overall_task = progress.add_task(
            description="[dim]Memproses antrean reader...[/]",
            total=guest_count,
            role="[bold yellow]★ TOTAL[/]",
        )

        num_slots = min(concurrency, guest_count)
        slot_tasks = []
        for s_idx in range(1, num_slots + 1):
            tid = progress.add_task(
                description="[dim]Menunggu antrean...[/]",
                total=expected_chapters,
                role=f"Slot-{s_idx:02d}",
            )
            slot_tasks.append((s_idx, tid))

        async def run_single_guest(idx: int, slot_idx: int, task_id: TaskID):
            if country_input in ("RANDOM", "ALL", "AUTO", ""):
                c_code = pick_weighted_country()
            else:
                c_code = country_input if country_input in COUNTRY_METADATA else "US"

            progress.reset(task_id, total=expected_chapters)
            progress.update(
                task_id,
                role=f"Slot-{slot_idx:02d}",
                description=f"[cyan]G-{idx:02d} Masuk ({c_code})...[/]",
                completed=0,
            )

            profile = ProfileGenerator.generate_profile(country_code=c_code)
            proxy = default_proxy_manager.pop_proxy(c_code)
            client = StealthApiClient(
                profile=profile,
                proxy_manager=default_proxy_manager,
                current_proxy=proxy,
            )

            def prog_cb(completed: int, total: int, desc: str):
                progress.update(
                    task_id,
                    completed=completed,
                    total=total or expected_chapters,
                    description=desc[:34],
                )

            def stat_cb(msg: str):
                raw = msg.split("]", 1)[-1].strip() if "]" in msg else msg
                # Milestone penting dicetak bersih di atas progress bar
                if "EMAIL TERVERIFIKASI" in raw or "KONVERSI SUKSES" in raw:
                    progress.console.print(f"[bold green]✓ [Slot-{slot_idx:02d}][Guest-{idx:02d}] {raw}[/]")
                elif "Timeout" in raw or "ditolak" in raw:
                    progress.console.print(f"[yellow]! [Slot-{slot_idx:02d}][Guest-{idx:02d}] {raw}[/]")
                elif "meremake cerita" in raw:
                    progress.console.print(f"[bold yellow]⚡ [Slot-{slot_idx:02d}][Guest-{idx:02d}] {raw}[/]")
                elif "di-remake pembaca lain" in raw:
                    progress.console.print(f"[bold cyan]📖 [Slot-{slot_idx:02d}][Guest-{idx:02d}] {raw}[/]")
                elif "menyukai novel ini" in raw or "SANGAT SUKA" in raw or "SANGAT MENYUKAI" in raw:
                    progress.console.print(f"[bold magenta]♥ [Slot-{slot_idx:02d}][Guest-{idx:02d}] {raw}[/]")
                else:
                    progress.update(task_id, description=raw[:34])

            worker = StealthGuestWorker(
                guest_index=idx,
                client=client,
                target_novel_id=target_novel,
                status_cb=stat_cb,
                progress_cb=prog_cb,
                auto_convert=auto_convert,
                verifier=verifier,
                save_account_cb=scheduler.save_account,
                existing_emails=existing_emails,
                existing_base_users=existing_base_users,
            )

            read_ch = 0
            is_ok = False
            try:
                read_ch = await worker.run_guest_session(
                    max_chapters=chapters_per_guest,
                    member_chapters_after=member_chapters_after,
                )
                is_ok = (read_ch > 0)
                if is_ok:
                    progress.update(task_id, completed=read_ch, description="[bold green]Selesai [OK][/]")
                else:
                    progress.update(task_id, completed=0, description="[yellow]Gagal (0 Bab)[/]")
            except Exception as exc:
                progress.console.print(f"[yellow]! [Slot-{slot_idx:02d}][Guest-{idx:02d}] Terhenti: {exc}[/]")
                progress.update(task_id, description="[red]Terhenti[/]")
            finally:
                await client.close()
                progress.advance(overall_task, 1)

            is_conv = bool(worker.client.access_token and worker.client.profile.email)
            ident_str = worker.client.profile.email if is_conv else f"Guest-{worker.client.profile.device_id[:8]}"
            results.append({
                "no": len(results) + 1,
                "type": "Guest+Member" if is_conv else "Guest",
                "ident": ident_str,
                "country": c_code,
                "chapters_read": read_ch,
                "status": "[green]Sukses[/]" if is_ok else "[red]Gagal[/]",
            })

        # Antrean Dinamis: Setiap slot yang selesai langsung mengambil antrean tamu berikutnya tanpa menunggu slot lain!
        guest_queue: asyncio.Queue = asyncio.Queue()
        for idx in range(1, guest_count + 1):
            guest_queue.put_nowait(idx)

        async def slot_worker(slot_idx: int, task_id: TaskID):
            if slot_idx > 1:
                await asyncio.sleep(random.uniform(1.2, 3.0) * (slot_idx - 1))

            while not guest_queue.empty():
                try:
                    idx = guest_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                await run_single_guest(idx, slot_idx, task_id)

                if not guest_queue.empty():
                    progress.update(task_id, description="[dim]Jeda antarsesi...[/]")
                    await asyncio.sleep(random.uniform(2.0, 5.0))

            progress.update(task_id, description="[bold green]Semua Selesai [OK][/]")

        workers = [slot_worker(s_idx, tid) for s_idx, tid in slot_tasks]
        await asyncio.gather(*workers)

    # Cetak Tabel Laporan Rapi di Akhir
    report_table = Table(
        title=f"\n[bold green]Laporan Sesi Stealth Auto Reader ({target_novel})[/]",
        border_style="cyan",
    )
    report_table.add_column("No", style="dim", width=4)
    report_table.add_column("Tipe", style="bold", width=14)
    report_table.add_column("Identitas / Akun", style="bold white")
    report_table.add_column("Negara", style="cyan", width=8)
    report_table.add_column("Bab Dibaca", justify="center", width=12)
    report_table.add_column("Status", style="bold")

    for r in results:
        report_table.add_row(
            str(r["no"]),
            r["type"],
            r["ident"],
            r["country"],
            f"{r['chapters_read']}/{expected_chapters}",
            r["status"],
        )

    console.print(report_table)
    console.print(f"\n[bold green]Semua {guest_count} sesi pembaca tamu telah selesai secara alami dan rapi![/]")
    Prompt.ask("\nTekan Enter untuk kembali")


async def run_spaced_signup_flow(scheduler: StealthScheduler):
    console.print("\n[bold cyan]>>> Registrasi Akun Halus (Full Auto Random Stealth)[/]\n")
    console.print("[dim]Fitur Otomatis Penuh: 50 Negara Resmi (Bobot: 30% US, 20% KR, 10% ID, 40% Global), Provider Email Wajar (Outlook/Hotmail/Gmail), dan Verifikasi OTP 120s.[/]\n")

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


async def run_sync_nicknames_flow(scheduler: StealthScheduler):
    """Menu: Ubah Nickname Akun (Single / Bulk Random)."""
    accounts = scheduler.load_accounts()
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun_stealth.txt'![/]")
        Prompt.ask("\nTekan Enter untuk kembali")
        return

    console.print(f"\n[bold cyan]>>> Ubah Nickname Akun ({len(accounts)} Akun Tersedia)[/]\n")
    console.print(
        "[dim]Pilihan Mode:\n"
        " • [bold]BULK[/] = Generate nickname acak realistis untuk SEMUA akun.\n"
        " • [bold]SINGLE[/] = Pilih 1 akun dan ketik nickname custom.[/]\n"
    )

    mode = Prompt.ask("Mode", choices=["bulk", "single"], default="bulk")

    if mode == "single":
        # Tampilkan daftar akun
        for i, acc in enumerate(accounts[:30], start=1):
            console.print(f"  [{i}] {acc.get('email', '-')}  (Nick: {acc.get('nickname', '-')})")
        idx_str = Prompt.ask("Pilih nomor akun", default="1")
        try:
            idx = max(1, min(int(idx_str), len(accounts)))
        except ValueError:
            idx = 1
        acc = accounts[idx - 1]
        new_nick = Prompt.ask("Masukkan Nickname Baru", default=ProfileGenerator.generate_nickname(acc.get('country', 'ID')))

        with console.status(f"[bold cyan]Mengubah nickname akun {acc.get('email')}...[/]"):
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
                account_data=acc,
                save_account_cb=scheduler.update_account,
            )
            ok, result = await client.update_nickname(new_nick)
            await client.close()

        if ok:
            console.print(f"[bold green]✓ Nickname berhasil diubah menjadi:[/] [bold yellow]{result}[/]")
        else:
            console.print(f"[red]Gagal mengubah nickname: {result}[/]")

    else:
        # Bulk: generate nickname acak untuk semua akun
        console.print(f"\n[bold cyan]Mengubah nickname {len(accounts)} akun secara massal (Bulk Random)...[/]\n")
        success = 0
        fail = 0
        for i, acc in enumerate(accounts, start=1):
            new_nick = ProfileGenerator.generate_nickname(acc.get('country', 'ID'))
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
                account_data=acc,
                save_account_cb=scheduler.update_account,
            )
            ok, result = await client.update_nickname(new_nick)
            await client.close()

            if ok:
                console.print(f"  [green]✓[/] [{i}/{len(accounts)}] {acc.get('email', '-')} → [bold yellow]{result}[/]")
                success += 1
            else:
                console.print(f"  [red]✗[/] [{i}/{len(accounts)}] {acc.get('email', '-')} → Gagal: {result}")
                fail += 1

            # Jeda antar akun agar tidak rate-limited
            if i < len(accounts):
                await asyncio.sleep(random.uniform(1.0, 2.5))

        console.print(f"\n[bold green]Selesai! {success} berhasil, {fail} gagal.[/]")

    Prompt.ask("\nTekan Enter untuk kembali")


async def run_bulk_claim_q_flow(scheduler: StealthScheduler):
    """Menu: Klaim Q Harian Otomatis untuk Seluruh Akun."""
    accounts = scheduler.load_accounts()
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun_stealth.txt'![/]")
        Prompt.ask("\nTekan Enter untuk kembali")
        return

    console.print(f"\n[bold cyan]>>> Klaim Q Harian Otomatis ({len(accounts)} Akun)[/]\n")
    console.print("[dim]Setiap akun diklaim 1x per hari. Akun yang sudah klaim hari ini akan di-skip.[/]\n")

    success = 0
    skipped = 0
    fail = 0

    for i, acc in enumerate(accounts, start=1):
        email = acc.get("email", f"Akun-{i}")
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
            account_data=acc,
            save_account_cb=scheduler.update_account,
        )

        def log_q(msg: str):
            console.print(f"  [{i}/{len(accounts)}] {email}: {msg}")

        try:
            result = await client.claim_daily_q(log_func=log_q)
            if result.get("status") == "already_claimed":
                skipped += 1
            elif result.get("status") == "error":
                fail += 1
            else:
                success += 1
        except Exception as exc:
            console.print(f"  [red]✗[/] [{i}/{len(accounts)}] {email}: Error: {exc}")
            fail += 1
        finally:
            await client.close()

        if i < len(accounts):
            await asyncio.sleep(random.uniform(1.0, 2.0))

    console.print(f"\n[bold green]Selesai! Berhasil: {success}, Skip (sudah klaim): {skipped}, Gagal: {fail}[/]")
    Prompt.ask("\nTekan Enter untuk kembali")


async def async_main():
    scheduler = StealthScheduler()

    while True:
        print_banner()
        show_accounts_summary(scheduler)

        console.print(
            "\n[bold white]Pilihan Menu Utama:[/]\n"
            " [bold green][1][/] Jalankan Pembaca Valid (Valid Reader - Tuntas Bab 1-20 / Tamat)\n"
            " [bold green][2][/] Jalankan Member Stealth Reader (Custom Bab / Standar)\n"
            " [bold green][3][/] Jalankan Guest Stealth Reader (Mode Tamu Organik - Tanpa Akun)\n"
            " [bold green][4][/] Registrasi Akun Halus (Spaced / Anti-Clustering Signup)\n"
            " [bold green][5][/] Inspeksi Akun di 'akun_stealth.txt'\n"
            " [bold green][6][/] Toggle Mode Proxy (Aktif / Direct Koneksi)\n"
            " [bold green][7][/] Ubah Nickname Akun (Single / Bulk Random)\n"
            " [bold green][8][/] Klaim Q Harian Otomatis (Seluruh Akun)\n"
            " [bold red][0][/] Keluar ke Terminal\n"
        )

        choice = Prompt.ask("Pilih Menu", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"], default="1")

        if choice == "1":
            await run_valid_reading_flow(scheduler)
        elif choice == "2":
            await run_organic_reading_flow(scheduler)
        elif choice == "3":
            await run_guest_reading_flow(scheduler)
        elif choice == "4":
            await run_spaced_signup_flow(scheduler)
        elif choice == "5":
            run_inspect_accounts(scheduler)
        elif choice == "6":
            default_proxy_manager.enabled = not default_proxy_manager.enabled
            status = "[bold green]DIAKTIFKAN[/]" if default_proxy_manager.enabled else "[bold yellow]DINONAKTIFKAN (Direct Connection)[/]"
            console.print(f"\n[bold cyan]Status Proxy:[/] {status}")
            Prompt.ask("\nTekan Enter untuk kembali")
        elif choice == "7":
            await run_sync_nicknames_flow(scheduler)
        elif choice == "8":
            await run_bulk_claim_q_flow(scheduler)
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
