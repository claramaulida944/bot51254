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
from .profile import ProfileGenerator
from .email_verifier import TempTfVerifier
from .scheduler import extract_base_username, format_natural_email

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
    """Pekerja yang mereplikasi pembaca tamu (Guest Reader) yang baru instal aplikasi,
    dengan kemampuan konversi organik ke Member Terdaftar saat menghadapi gerbang login."""

    def __init__(
        self,
        guest_index: int,
        client: StealthApiClient,
        target_novel_id: str,
        status_cb: Optional[Callable[[str], None]] = None,
        auto_convert: bool = False,
        verifier: Optional[TempTfVerifier] = None,
        save_account_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        existing_emails: Optional[set] = None,
        existing_base_users: Optional[set] = None,
    ):
        self.guest_index = guest_index
        self.client = client
        self.target_novel_id = target_novel_id
        self.status_cb = status_cb or (lambda msg: None)
        self.timing = ReadingSimulator()
        self.camouflage = CamouflageEngine(target_novel_id)
        self.auto_convert = auto_convert
        self.verifier = verifier
        self.save_account_cb = save_account_cb
        self.existing_emails = existing_emails if existing_emails is not None else set()
        self.existing_base_users = existing_base_users if existing_base_users is not None else set()

    def log(self, text: str):
        self.status_cb(f"[Guest-{self.guest_index:02d}] {text}")

    async def run_guest_session(
        self,
        max_chapters: int = 3,
        member_chapters_after: int = 2,
    ) -> int:
        """
        Menjalankan 1 siklus pembaca tamu organik:
        1. Inisiasi sesi tamu resmi (Cold Start -> /api/guest-reading/session).
        2. Eksplorasi katalog & rekomendasi (Warm-up).
        3. Membaca bab novel target secara gratis (Guest Phase).
        4. Jika mode konversi aktif: Mendaftar akun resmi & verifikasi OTP 120s langsung
           dari sesi, device_id, dan proxy yang sama persis (Guest-to-Member Conversion).
        5. Melanjutkan membaca bab-bab berikutnya sebagai Member Resmi dengan telemetri royalty.
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

        # 3. FASE TAMU: Membaca bab-bab gratis
        for idx in range(limit):
            ch = chapters[idx]
            ch_id = ch.get("hash_id") or ch.get("id")
            ch_num = ch.get("chapter_num", idx + 1)
            ch_title = ch.get("title", f"Bab {ch_num}")

            self.log(f"[yellow]Membuka Bab {ch_num}: '{ch_title[:25]}' sebagai Tamu[/]")
            detail = await self.client.get_chapter_detail(self.target_novel_id, ch_id)
            if not detail:
                self.log(f"[yellow]Bab {ch_num} terhalang/terkunci untuk Tamu.[/]")
                break

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

        # 4. FASE KONVERSI ALAMI KE MEMBER (JIKA DIAKTIFKAN)
        if self.auto_convert and self.verifier:
            self.log(f"[bold cyan]Menghadapi gerbang pendaftaran akun (Konversi Alami Tamu -> Member)...[/]")
            
            # Dapatkan email wajar & belum pernah dipakai
            temp_email = None
            for _ in range(15):
                prov = random.choice(["outlook", "hotmail", "gmail"])
                cand = await self.verifier.get_email(provider=prov, use_dot=(prov == "gmail"), use_plus=(prov != "gmail"))
                if cand:
                    cand = format_natural_email(cand)
                    base_u = extract_base_username(cand)
                    if base_u not in self.existing_base_users and cand.lower() not in self.existing_emails:
                        temp_email = cand.lower()
                        self.existing_emails.add(temp_email)
                        self.existing_base_users.add(base_u)
                        break
                await asyncio.sleep(0.5)

            if temp_email:
                self.client.profile.email = temp_email
                self.client.profile.password = ProfileGenerator.generate_password()
                self.log(f"[cyan]Mendaftarkan akun resmi dari sesi tamu:[/] [green]{temp_email}[/]...")

                signup_ok, signup_msg = await self.client.perform_organic_signup()
                if signup_ok:
                    is_verified = False
                    self.log("[cyan]Memicu pengiriman kode OTP verifikasi...[/]")
                    send_ok, send_msg = await self.client.send_email_verification()
                    if send_ok:
                        otp_code = await self.verifier.poll_for_otp(
                            temp_email, timeout_sec=120, interval_sec=4, log_callback=self.log
                        )
                        if otp_code:
                            v_ok, v_msg = await self.client.verify_email_code(otp_code)
                            if v_ok:
                                is_verified = True
                                self.log(f"[bold green]✓ EMAIL TERVERIFIKASI RESMI:[/] {temp_email} (Kode: [yellow]{otp_code}[/])")
                            else:
                                self.log(f"[yellow]Verifikasi OTP ditolak server: {v_msg}[/]")
                        else:
                            self.log("[yellow]Timeout: OTP tidak diterima dalam 120s.[/]")

                    # Simpan akun HANYA jika terverifikasi
                    if is_verified:
                        if self.save_account_cb:
                            acc_data = {
                                "email": self.client.profile.email,
                                "password": self.client.profile.password,
                                "nickname": self.client.profile.nickname,
                                "access_token": self.client.access_token,
                                "refresh_token": self.client.refresh_token,
                                "country": self.client.profile.country,
                                "device_id": self.client.profile.device_id,
                                "anonymous_id": self.client.profile.anonymous_id,
                                "user_agent": self.client.profile.user_agent,
                                "created_at": self.client.profile.birth_date,
                                "is_email_verified": True,
                            }
                            self.save_account_cb(acc_data)
                        self.log(f"[bold green]✓ KONVERSI SUKSES: Tamu resmi menjadi Member Terdaftar ([bold green]VERIFIED[/])![/]")

                        # 5. FASE MEMBER: Lanjut membaca bab-bab berikutnya
                        start_idx = read_count
                        end_idx = min(start_idx + member_chapters_after, len(chapters))
                        if start_idx < end_idx:
                            self.log(f"[cyan]Melanjutkan membaca {end_idx - start_idx} bab berikutnya sebagai Member Terdaftar...[/]")
                            for m_idx in range(start_idx, end_idx):
                                m_ch = chapters[m_idx]
                                m_ch_id = m_ch.get("hash_id") or m_ch.get("id")
                                m_ch_num = m_ch.get("chapter_num", m_idx + 1)
                                m_ch_title = m_ch.get("title", f"Bab {m_ch_num}")

                                self.log(f"[yellow]Membuka Bab {m_ch_num}: '{m_ch_title[:25]}' sebagai Member[/]")
                                m_detail = await self.client.get_chapter_detail(self.target_novel_id, m_ch_id)
                                if not m_detail:
                                    self.log(f"[red]Gagal memuat bab {m_ch_num}.[/]")
                                    continue

                                m_content = m_detail.get("content", "")
                                m_dur, m_pace = self.timing.calculate_reading_duration(m_content)
                                self.log(f"[green]Membaca Bab {m_ch_num} (WPM: {len(m_content.split())} kata, durasi: {int(m_dur)}s)...[/]")

                                def on_m_progress(pct: int, el: float):
                                    if pct < 100:
                                        self.log(f"[dim]  Bab {m_ch_num} progres: {pct}% ({int(el)}s)[/]")
                                        asyncio.create_task(
                                            self.client.send_reading_telemetry(
                                                self.target_novel_id, m_ch_id, m_ch_num, el, depth_percent=pct, completed=False
                                            )
                                        )

                                await self.timing.simulate_human_reading(m_dur, on_progress=on_m_progress)

                                await self.client.send_reading_telemetry(
                                    self.target_novel_id, m_ch_id, m_ch_num, m_dur, depth_percent=100, completed=True
                                )
                                read_count += 1
                                self.log(f"[bold green]✓ Selesai Bab {m_ch_num} sebagai Member Resmi Terdaftar![/]")

                                if m_idx < end_idx - 1:
                                    await asyncio.sleep(random.uniform(3.0, 7.0))
                    else:
                        self.log(f"[bold red]✗ Akun gagal diverifikasi, tidak disimpan ke file.[/]")
                else:
                    self.log(f"[red]Pendaftaran gagal: {signup_msg}[/]")
            else:
                self.log("[yellow]Gagal mendapatkan email wajar unik untuk konversi tamu.[/]")

        self.log(f"[bold cyan]Selesai sesi! Total {read_count} bab terselesaikan secara alami.[/]")
        return read_count
