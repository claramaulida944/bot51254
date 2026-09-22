"""
Pekerja Pembaca Organik (Stealth Reader Worker).
Mengatur alur membaca satu akun:
1. Warm-up (Eksplorasi Beranda).
2. Membaca novel kamuflase untuk diversifikasi riwayat akun.
3. Membaca novel target dengan durasi WPM dinamis & pengiriman telemetri bertahap.
4. Menerapkan drop-off & jeda kelelahan manusia normal.
"""

import asyncio
import logging
import random
from typing import Any, Callable, Dict, List, Optional

from .client import StealthApiClient
from .camouflage import CamouflageEngine
from .timing import ReadingSimulator

logger = logging.getLogger("StealthWorker")


class StealthWorker:
    """Pekerja yang menjalankan sesi membaca layaknya pengguna manusia nyata."""

    def __init__(
        self,
        worker_id: int,
        client: StealthApiClient,
        target_novel_id: str,
        status_cb: Optional[Callable[[str], None]] = None,
    ):
        self.worker_id = worker_id
        self.client = client
        self.target_novel_id = target_novel_id
        self.status_cb = status_cb or (lambda msg: None)
        self.timing = ReadingSimulator()
        self.camouflage = CamouflageEngine(target_novel_id)

    def log(self, text: str):
        self.status_cb(f"[W-{self.worker_id:02d}] {text}")

    async def run_stealth_session(self, max_chapters_target: int = 5) -> int:
        """
        Menjalankan 1 sesi membaca lengkap untuk akun ini:
        - Mengembalikan total bab novel target yang berhasil dibaca.
        """
        self.log("[cyan]Memulai sesi pembaca organik...[/]")
        target_read_count = 0
        consecutive_target = 0

        # 1. Warm-up: Eksplorasi katalog beranda (simulasi 3 - 7 detik)
        self.log("[dim]Menjelajahi Beranda & Rekomendasi (Warm-up)...[/]")
        await self.camouflage.fetch_catalog_novels(await self.client.get_client())
        await asyncio.sleep(random.uniform(3.0, 7.0))

        # 2. Ambil bab novel target
        target_chapters = await self.client.get_novel_chapters(self.target_novel_id)
        if not target_chapters:
            self.log("[red]Gagal memuat bab novel target.[/]")
            return 0

        target_idx = 0

        while target_read_count < max_chapters_target and target_idx < len(target_chapters):
            # Tentukan apakah bab ini membaca novel kamuflase atau novel target
            decision = self.camouflage.pick_next_action(consecutive_target)

            if decision == "camouflage":
                # Baca 1 bab novel kamuflase dari katalog
                camou_novel = await self.camouflage.get_random_camouflage_novel(await self.client.get_client())
                c_id = camou_novel.get("hash_id")
                c_title = camou_novel.get("title", "Popular Novel")

                self.log(f"[dim]Membaca Kamuflase: '{c_title[:20]}' (Mencegah Deteksi 1 Target)...[/]")
                c_chapters = await self.client.get_novel_chapters(c_id)
                if c_chapters:
                    first_c = c_chapters[0]
                    c_ch_id = first_c.get("hash_id") or first_c.get("id")
                    c_detail = await self.client.get_chapter_detail(c_id, c_ch_id)
                    if c_detail:
                        c_text = c_detail.get("content", "")
                        dur, pace = self.timing.calculate_reading_duration(c_text)
                        # Baca kamuflase (bisa lebih cepat / skimmer)
                        dur_c = min(dur, 25.0)
                        await asyncio.sleep(dur_c)
                        await self.client.send_reading_telemetry(c_id, c_ch_id, 1, dur_c)
                consecutive_target = 0
                await asyncio.sleep(random.uniform(2.0, 5.0))
                continue

            # Baca Novel Target
            ch = target_chapters[target_idx]
            ch_id = ch.get("hash_id") or ch.get("id")
            ch_num = ch.get("chapter_num", target_idx + 1)
            ch_title = ch.get("title", f"Bab {ch_num}")

            self.log(f"[yellow]Membuka Bab {ch_num}: '{ch_title[:25]}'[/]")
            ch_detail = await self.client.get_chapter_detail(self.target_novel_id, ch_id)
            if not ch_detail:
                self.log(f"[red]Gagal memuat isi bab {ch_num}.[/]")
                target_idx += 1
                continue

            content = ch_detail.get("content", "")
            duration, pace_pct = self.timing.calculate_reading_duration(content)
            pace_str = f"({'+' if pace_pct > 0 else ''}{pace_pct}% vs sblm)" if pace_pct != 0 else ""

            self.log(f"[green]Membaca Bab {ch_num} (WPM: {len(content.split())} kata, durasi: {int(duration)}s {pace_str})...[/]")

            # Kirim sinyal mulai membaca
            logical_sess = f"rls_{self.worker_id}"

            # Simulasi membaca bertahap dengan mid-chapter ticks (25%, 50%, 75%, 100%)
            def on_progress(pct: int, el: float):
                if pct < 100:
                    self.log(f"[dim]  Bab {ch_num} progres: {pct}% ({int(el)}s)[/]")
                    # Kirim heartbeat mid-progress
                    asyncio.create_task(
                        self.client.send_reading_telemetry(
                            self.target_novel_id, ch_id, ch_num, el, depth_percent=pct, completed=False
                        )
                    )

            await self.timing.simulate_human_reading(duration, on_progress=on_progress)

            # Kirim telemetri final (Post-view payout resmi)
            await self.client.send_reading_telemetry(
                self.target_novel_id, ch_id, ch_num, duration, depth_percent=100, completed=True
            )

            target_read_count += 1
            consecutive_target += 1
            target_idx += 1
            self.log(f"[bold green]Selesai Bab {ch_num}! Total dibaca: {target_read_count}[/]")

            # Cek kelelahan membaca alami (Fatigue / Drop-off)
            take_break, break_sec = self.timing.should_take_fatigue_break()
            if take_break:
                self.log(f"[dim]Simulasi istirahat sejenak ({int(break_sec)}s) layaknya pembaca nyata...[/]")
                await asyncio.sleep(break_sec)
            else:
                # Jeda normal buka bab berikutnya (3 - 8 detik)
                await asyncio.sleep(random.uniform(3.0, 8.0))

            # Simulasi kemungkinan drop-off alami (15% kemungkinan berhenti per bab)
            if target_read_count >= 3 and random.random() < 0.15:
                self.log(f"[yellow]Simulasi pengguna menutup aplikasi setelah Bab {ch_num} (Natural Drop-off).[/]")
                break

        self.log(f"[bold cyan]Selesai sesi! Total {target_read_count} bab novel target terselesaikan secara organik.[/]")
        return target_read_count


class StealthGuestWorker:
    """Pekerja yang mereplikasi pembaca tamu (Guest Reader) yang baru instal aplikasi."""

    def __init__(
        self,
        guest_index: int,
        client: StealthApiClient,
        target_novel_id: str,
        status_cb: Optional[Callable[[str], None]] = None,
    ):
        self.guest_index = guest_index
        self.client = client
        self.target_novel_id = target_novel_id
        self.status_cb = status_cb or (lambda msg: None)
        self.timing = ReadingSimulator()
        self.camouflage = CamouflageEngine(target_novel_id)

    def log(self, text: str):
        self.status_cb(f"[Guest-{self.guest_index:02d}] {text}")

    async def run_guest_session(self, max_chapters: int = 3) -> int:
        """
        Menjalankan 1 siklus pembaca tamu organik:
        1. Inisiasi sesi tamu resmi (Cold Start -> /api/guest-reading/session).
        2. Eksplorasi katalog & rekomendasi (Warm-up).
        3. Membaca bab novel target dengan durasi WPM dinamis.
        4. Mengirimkan heartbeat progres PUT /api/guest-reading/progress.
        """
        self.log("[cyan]Menginisiasi sesi tamu resmi (Cold Start Android)...[/]")
        ok, msg = await self.client.init_guest_session()
        if not ok:
            self.log(f"[red]Gagal inisiasi tamu: {msg}[/]")
            return 0

        self.log("[green]✓ Sesi tamu aktif & Cookie qf_guest_reader disematkan.[/]")

        # 1. Warm-up di beranda (2 - 5 detik)
        self.log("[dim]Membuka Beranda & Jelajah Katalog (Warm-up)...[/]")
        await self.camouflage.fetch_catalog_novels(await self.client.get_client())
        await asyncio.sleep(random.uniform(2.0, 5.0))

        # 2. Buka daftar bab
        chapters = await self.client.get_novel_chapters(self.target_novel_id)
        if not chapters:
            self.log("[red]Gagal mengambil bab novel target.[/]")
            return 0

        read_count = 0
        limit = min(max_chapters, len(chapters))

        for idx in range(limit):
            ch = chapters[idx]
            ch_id = ch.get("hash_id") or ch.get("id")
            ch_num = ch.get("chapter_num", idx + 1)
            ch_title = ch.get("title", f"Bab {ch_num}")

            self.log(f"[yellow]Membuka Bab {ch_num}: '{ch_title[:25]}'[/]")
            detail = await self.client.get_chapter_detail(self.target_novel_id, ch_id)
            if not detail:
                self.log(f"[red]Gagal memuat teks bab {ch_num}.[/]")
                continue

            content = detail.get("content", "")
            duration, pace_pct = self.timing.calculate_reading_duration(content)
            pace_str = f"({'+' if pace_pct > 0 else ''}{pace_pct}% vs sblm)" if pace_pct != 0 else ""

            self.log(f"[green]Membaca Bab {ch_num} (WPM: {len(content.split())} kata, durasi: {int(duration)}s {pace_str})...[/]")

            # Kirim heartbeat bertahap PUT /api/guest-reading/progress
            def on_guest_progress(pct: int, el: float):
                if pct < 100:
                    self.log(f"[dim]  Bab {ch_num} progres: {pct}% ({int(el)}s)[/]")
                    asyncio.create_task(
                        self.client.send_guest_progress(
                            self.target_novel_id, ch_id, el, scroll_percent=(pct / 100.0), completed=False
                        )
                    )

            await self.timing.simulate_human_reading(duration, on_progress=on_guest_progress)

            # Kirim penyelesaian bab resmi
            await self.client.send_guest_progress(
                self.target_novel_id, ch_id, duration, scroll_percent=1.0, completed=True
            )

            read_count += 1
            self.log(f"[bold green]✓ Selesai Bab {ch_num} sebagai Tamu![/]")

            # Jeda antar bab (3 - 7 detik)
            if idx < limit - 1:
                await asyncio.sleep(random.uniform(3.0, 7.0))

            # Drop-off alami pembaca tamu (sebagian tamu tidak membaca tuntas seluruh bab gratis)
            if read_count >= 2 and random.random() < 0.30:
                self.log(f"[yellow]Simulasi tamu menutup aplikasi setelah Bab {ch_num} (Natural Drop-off).[/]")
                break

        self.log(f"[bold cyan]Selesai sesi tamu! Total {read_count} bab terselesaikan dengan aman.[/]")
        return read_count
