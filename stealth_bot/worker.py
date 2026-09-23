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
from datetime import datetime
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
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        account_data: Optional[Dict[str, Any]] = None,
        save_account_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.worker_id = worker_id
        self.client = client
        self.target_novel_id = target_novel_id
        self.status_cb = status_cb or (lambda msg: None)
        self.progress_cb = progress_cb
        self.account_data = account_data
        self.save_account_cb = save_account_cb
        if self.account_data and not self.client.account_data:
            self.client.account_data = self.account_data
        if self.save_account_cb and not self.client.save_account_cb:
            self.client.save_account_cb = self.save_account_cb
        self.timing = ReadingSimulator()
        self.camouflage = CamouflageEngine(target_novel_id)

    def log(self, text: str):
        self.status_cb(f"[W-{self.worker_id:02d}] {text}")

    def update_progress(self, current: int, total: int, desc: str):
        if self.progress_cb:
            try:
                self.progress_cb(current, total, desc)
            except Exception:
                pass

    async def run_stealth_session(
        self,
        max_chapters_target: int = 5,
        valid_reader_mode: bool = False,
        binge_to_end_chance: float = 0.30,
    ) -> int:
        """
        Menjalankan 1 sesi membaca lengkap untuk akun ini:
        - valid_reader_mode: Mode pembaca valid. Membaca tuntas Bab 1-20 tanpa drop-off prematur,
          dengan probabilitas binge maraton membaca tuntas sampai seluruh bab tamat.
        - Mengembalikan total bab novel target yang berhasil dibaca.
        """
        read_all_mode = (max_chapters_target <= 0) and not valid_reader_mode
        if read_all_mode:
            self.log("[cyan]Mode Aktif: [bold green]BACA SEMUA BAB NOVEL SAMPAI TAMAT[/] (Tanpa early drop-off).[/]")
            max_chapters_target = 999999
        elif valid_reader_mode:
            self.log("[bold cyan]Mode Aktif: [bold green]PEMBACA VALID (VALID READER)[/] - Target Tuntas Bab 1-20 / Tamat.[/]")

        target_read_count = 0
        consecutive_target = 0

        # 0. Pembaruan Otomatis Token & Validasi Sesi Aktif
        if self.client.access_token or (self.account_data and self.account_data.get("email")):
            session_ok = await self.client.ensure_active_session()
            if not session_ok:
                self.log("[red]Gagal memperbarui sesi/token akun (login ulang gagal).[/]")
                return 0

        # Auto-claim benefit Q harian (1x per hari)
        try:
            await self.client.claim_daily_q(log_func=self.log)
        except Exception:
            pass

        # Pengecekan Batas Waktu Baca Harian (1 - 3 Jam per hari per akun)
        today = datetime.now().strftime("%Y-%m-%d")
        if self.account_data is not None:
            stats = self.account_data.get("daily_read_stats")
            if not stats or stats.get("date") != today:
                max_seconds = random.randint(3600, 10800)  # 1 - 3 jam secara realistis
                stats = {
                    "date": today,
                    "seconds_read": 0,
                    "max_seconds_allowed": max_seconds,
                }
                self.account_data["daily_read_stats"] = stats
                if self.save_account_cb:
                    self.save_account_cb(self.account_data)

            seconds_read = stats.get("seconds_read", 0)
            max_seconds = stats.get("max_seconds_allowed", 7200)
            if seconds_read >= max_seconds:
                hours_done = seconds_read / 3600.0
                hours_max = max_seconds / 3600.0
                self.log(
                    f"[yellow]Akun telah mencapai batas kuota baca harian ({hours_done:.1f}/{hours_max:.1f} jam hari ini {today}). "
                    f"Melewati (skip) akun ini untuk besok.[/]"
                )
                return 0

        # 1. Warm-up: Scroll Beranda & Rekomendasi (8 - 18 detik)
        home_browse_sec = random.uniform(8.0, 18.0)
        self.update_progress(0, max_chapters_target, f"[dim]Scroll Beranda ({int(home_browse_sec)}s)...[/]")
        self.log(f"[dim]Menjelajahi Beranda & Rekomendasi Populer ({int(home_browse_sec)}s)...[/]")
        await asyncio.sleep(home_browse_sec)

        def disc_prog(desc: str):
            self.update_progress(0, max_chapters_target, desc)

        try:
            await self.camouflage.simulate_discovery_journey(self.client, self.log, progress_func=disc_prog)
        except Exception:
            pass

        # 2. Ambil metadata & bab novel target
        novel_detail = await self.client.get_novel_detail(self.target_novel_id)
        author_info = (novel_detail.get("author") or novel_detail.get("creator") or {}) if novel_detail else {}
        author_id = author_info.get("hash_id") or author_info.get("id") or (novel_detail.get("author_id") if novel_detail else None)

        target_chapters = await self.client.get_novel_chapters(self.target_novel_id)
        if not target_chapters:
            self.log("[red]Gagal memuat bab novel target.[/]")
            return 0

        # Membaca sinopsis novel target secara realistis sebelum masuk Bab 1 (15 - 30 detik)
        synopsis_dur = random.uniform(15.0, 30.0)
        self.update_progress(0, max_chapters_target, f"[cyan]Baca Sinopsis Target ({int(synopsis_dur)}s)...[/]")
        self.log(f"[cyan]Membaca Sinopsis Novel Target & Memeriksa Daftar Bab ({int(synopsis_dur)}s)...[/]")
        await asyncio.sleep(synopsis_dur)

        total_avail_ch = len(target_chapters)
        if valid_reader_mode:
            if total_avail_ch <= 20:
                max_chapters_target = total_avail_ch
                self.log(f"[bold green]✓ Novel memiliki {total_avail_ch} bab (<=20). Pembaca valid membaca tuntas semua bab sampai TAMAT.[/]")
            else:
                # Cek kemungkinan pembaca sangat suka dan maraton sampai selesai semua bab
                if random.random() < binge_to_end_chance:
                    max_chapters_target = total_avail_ch
                    self.log(f"[bold magenta]★ Pembaca Valid ini SANGAT KECANDUAN CERITA! Lanjut maraton membaca tuntas seluruh {total_avail_ch} bab sampai TAMAT![/]")
                else:
                    max_chapters_target = 20
                    self.log(f"[bold green]✓ Pembaca Valid membaca tuntas Bab 1 s/d Bab 20 (Target Validitas Penuh).[/]")
        elif read_all_mode:
            max_chapters_target = total_avail_ch
            self.log(f"[green]Total {total_avail_ch} bab ditemukan. Membaca bab 1 sampai {total_avail_ch}...[/]")

        target_idx = 0
        engaged_socially = False
        remade_story = False
        visited_remake = False

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
                        if self.account_data is not None:
                            c_stats = self.account_data.setdefault("daily_read_stats", {})
                            c_stats["seconds_read"] = c_stats.get("seconds_read", 0) + dur_c
                            if self.save_account_cb:
                                self.save_account_cb(self.account_data)
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

            eff_words = self.timing.estimate_effective_words(content)
            self.log(f"[green]Membaca Bab {ch_num} (Est: {eff_words} kata, durasi: {int(duration)}s {pace_str})...[/]")

            # Kirim sinyal mulai membaca
            logical_sess = f"rls_{self.worker_id}"

            # Simulasi membaca bertahap dengan mid-chapter ticks (25%, 50%, 75%, 100%)
            def on_progress(pct: int, el: float):
                if pct < 100:
                    self.update_progress(target_read_count, max_chapters_target, f"[green]Bab {ch_num} ({pct}%)[/]")
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
            self.update_progress(target_read_count, max_chapters_target, f"[bold green]Selesai Bab {ch_num}[/]")
            self.log(f"[bold green]Selesai Bab {ch_num}! Total dibaca: {target_read_count}/{max_chapters_target}[/]")

            # Akumulasi durasi membaca harian akun & cek batas 1-3 jam
            if self.account_data is not None:
                t_stats = self.account_data.setdefault("daily_read_stats", {})
                t_stats["seconds_read"] = t_stats.get("seconds_read", 0) + duration
                if self.save_account_cb:
                    self.save_account_cb(self.account_data)

                # Jika sudah mencapai kuota harian akun (1 - 3 jam)
                max_sec = t_stats.get("max_seconds_allowed", 7200)
                if t_stats["seconds_read"] >= max_sec:
                    h_done = t_stats["seconds_read"] / 3600.0
                    h_max = max_sec / 3600.0
                    self.log(
                        f"[yellow]Akun telah mencapai batas maksimal waktu membaca harian ({h_done:.1f}/{h_max:.1f} jam). "
                        f"Mengakhiri sesi membaca hari ini secara organik dan lanjut besok.[/]"
                    )
                    break

            # Peluang pembaca menyukai cerita: Like, Simpan Rak (Bookmark), Follow Penulis, atau ketiganya
            if not engaged_socially and target_read_count >= 1:
                if random.random() < 0.65 or target_read_count >= max_chapters_target:
                    await self.client.maybe_engage_socially(self.target_novel_id, author_id, log_func=self.log)
                    engaged_socially = True

            # 1-3% kemungkinan pembaca login meremake cerita (jika tersedia di platform)
            if not remade_story and self.client.access_token and target_read_count >= 1:
                if random.random() < random.uniform(0.01, 0.03):
                    remake_info = await self.client.create_reader_remix(
                        self.target_novel_id, ch_id, log_func=self.log
                    )
                    if remake_info:
                        remade_story = True

            # 5% kemungkinan pembaca masuk ke cerita yang di-remake pembaca lain
            if not visited_remake and random.random() < 0.05:
                def on_w_remake_prog(desc: str):
                    self.update_progress(target_read_count, max_chapters_target, desc)
                did_visit = await self.camouflage.simulate_read_other_reader_remake(
                    self.client, self.log, progress_func=on_w_remake_prog, is_guest=False
                )
                if did_visit:
                    visited_remake = True

            # Cek kelelahan membaca alami (Fatigue / Drop-off)
            take_break, break_sec = self.timing.should_take_fatigue_break()
            if take_break:
                self.log(f"[dim]Simulasi istirahat sejenak ({int(break_sec)}s) layaknya pembaca nyata...[/]")
                await asyncio.sleep(break_sec)
            elif target_idx < len(target_chapters) and random.random() < 0.22:
                # Cek kemungkinan pembaca bosen & beralih membaca cerita lain sejenak (22% peluang)
                def on_w_sidetrack_prog(desc: str):
                    self.update_progress(target_read_count, max_chapters_target, desc)
                await self.camouflage.simulate_reader_boredom_sidetrack(
                    self.client, self.log, progress_func=on_w_sidetrack_prog, is_guest=False
                )
            else:
                # Jeda normal buka bab berikutnya (6 - 14 detik)
                pause_s = random.uniform(6.0, 14.0)
                self.log(f"[dim]Jeda santai sebelum bab berikutnya ({int(pause_s)}s)...[/]")
                await asyncio.sleep(pause_s)

            # Simulasi kemungkinan drop-off alami (Hanya jika BUKAN mode baca semua bab dan BUKAN pembaca valid)
            if not read_all_mode and not valid_reader_mode and target_read_count >= 3 and random.random() < 0.15:
                self.log(f"[yellow]Simulasi pengguna menutup aplikasi setelah Bab {ch_num} (Natural Drop-off).[/]")
                break

        if not engaged_socially and target_read_count > 0:
            await self.client.maybe_engage_socially(self.target_novel_id, author_id, log_func=self.log)

        if not remade_story and self.client.access_token and target_read_count > 0:
            if random.random() < random.uniform(0.01, 0.03):
                last_ch = target_chapters[-1] if target_chapters else {}
                last_ch_id = last_ch.get("hash_id") or last_ch.get("id", "")
                remake_info = await self.client.create_reader_remix(
                    self.target_novel_id, last_ch_id, log_func=self.log
                )
                if remake_info:
                    remade_story = True

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
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
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
        self.progress_cb = progress_cb
        self.timing = ReadingSimulator()
        self.camouflage = CamouflageEngine(target_novel_id)
        self.auto_convert = auto_convert
        self.verifier = verifier
        self.save_account_cb = save_account_cb
        self.existing_emails = existing_emails if existing_emails is not None else set()
        self.existing_base_users = existing_base_users if existing_base_users is not None else set()

    def log(self, text: str):
        self.status_cb(f"[Guest-{self.guest_index:02d}] {text}")

    def update_progress(self, current: int, total: int, desc: str):
        if self.progress_cb:
            try:
                self.progress_cb(current, total, desc)
            except Exception:
                pass

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

        # 1. Warm-up di beranda & Alur Penemuan Alami (Rate: 0 s/d 10 buku lain dilihat)
        home_browse_sec = random.uniform(10.0, 22.0)
        self.update_progress(0, 15, f"[dim]Scroll Beranda ({int(home_browse_sec)}s)...[/]")
        self.log(f"[dim]Membuka Beranda & Menjelajahi Rekomendasi ({int(home_browse_sec)}s)...[/]")
        await asyncio.sleep(home_browse_sec)

        def guest_disc_prog(desc: str):
            self.update_progress(0, 15, desc)

        try:
            await self.camouflage.simulate_discovery_journey(self.client, self.log, progress_func=guest_disc_prog)
        except Exception as exc:
            self.log(f"[dim]Discovery warm-up terselesaikan.[/]")

        # 2. Membuka novel target (Melihat detail sinopsis & daftar bab)
        novel_detail = await self.client.get_novel_detail(self.target_novel_id)
        author_info = (novel_detail.get("author") or novel_detail.get("creator") or {}) if novel_detail else {}
        author_id = author_info.get("hash_id") or author_info.get("id") or (novel_detail.get("author_id") if novel_detail else None)
        engaged_socially = False
        remade_story = False
        visited_remake = False

        chapters = await self.client.get_novel_chapters(self.target_novel_id)
        if not chapters:
            self.log("[red]Gagal mengambil bab novel target.[/]")
            return 0

        # Membaca sinopsis novel target secara realistis sebelum masuk Bab 1 (15 - 32 detik)
        target_syn_dur = random.uniform(15.0, 32.0)
        self.update_progress(0, len(chapters), f"[cyan]Baca Sinopsis Target ({int(target_syn_dur)}s)...[/]")
        self.log(f"[cyan]Membaca Sinopsis Novel Target & Mengecek Daftar Bab ({int(target_syn_dur)}s)...[/]")
        await asyncio.sleep(target_syn_dur)

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

            eff_words = self.timing.estimate_effective_words(content)
            self.log(f"[green]Membaca Bab {ch_num} (Est: {eff_words} kata, durasi: {int(duration)}s {pace_str})...[/]")

            # Kirim heartbeat bertahap PUT /api/guest-reading/progress
            def on_guest_progress(pct: int, el: float):
                if pct < 100:
                    self.update_progress(read_count, len(chapters), f"[cyan]Tamu Bab {ch_num} ({pct}%)[/]")
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
            self.update_progress(read_count, len(chapters), f"[bold green]Selesai Bab {ch_num}[/]")
            self.log(f"[bold green]✓ Selesai Bab {ch_num} sebagai Tamu![/]")

            # 5% kemungkinan pembaca masuk ke cerita yang di-remake pembaca lain
            if not visited_remake and random.random() < 0.05:
                def on_g_remake_prog(desc: str):
                    self.update_progress(read_count, len(chapters), desc)
                did_visit = await self.camouflage.simulate_read_other_reader_remake(
                    self.client, self.log, progress_func=on_g_remake_prog, is_guest=True
                )
                if did_visit:
                    visited_remake = True

            # Jeda antar bab alami & peluang pembaca bosen menengok cerita lain
            if idx < limit - 1:
                # 25% peluang pembaca bosen & beralih membaca 1-3 cerita lain di katalog sejenak
                if random.random() < 0.25:
                    def on_sidetrack_prog(desc: str):
                        self.update_progress(read_count, len(chapters), desc)
                    await self.camouflage.simulate_reader_boredom_sidetrack(
                        self.client, self.log, progress_func=on_sidetrack_prog, is_guest=True
                    )
                else:
                    pause_s = random.uniform(6.0, 15.0)
                    self.log(f"[dim]Jeda santai sebelum bab berikutnya ({int(pause_s)}s)...[/]")
                    await asyncio.sleep(pause_s)

        # 4. FASE KONVERSI ALAMI KE MEMBER (JIKA DIAKTIFKAN)
        if self.auto_convert and self.verifier:
            self.log(f"[bold cyan]Menghadapi gerbang pendaftaran akun (Konversi Alami Tamu -> Member)...[/]")
            self.update_progress(read_count, len(chapters), "[yellow]Daftar & OTP 120s...[/]")
            
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
                        today = datetime.now().strftime("%Y-%m-%d")
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
                            "liked_novels": [],
                            "bookmarked_novels": [],
                            "followed_authors": [],
                            "daily_read_stats": {
                                "date": today,
                                "seconds_read": 0,
                                "max_seconds_allowed": random.randint(3600, 10800),
                            },
                        }
                        if self.save_account_cb:
                            self.save_account_cb(acc_data)
                        self.client.account_data = acc_data
                        self.client.save_account_cb = self.save_account_cb

                        # Klaim Q harian pertama kali
                        try:
                            await self.client.claim_daily_q(log_func=self.log)
                        except Exception:
                            pass

                        self.log(f"[bold green]✓ KONVERSI SUKSES: Tamu resmi menjadi Member Terdaftar ([bold green]VERIFIED[/])![/]")
                        self.update_progress(read_count, len(chapters), "[bold green]Member Verified ✓[/]")

                        # 5. FASE MEMBER: Lanjut membaca bab-bab berikutnya
                        start_idx = read_count
                        if member_chapters_after <= 0:
                            end_idx = len(chapters)  # Baca semua bab sisa sampai tamat!
                        else:
                            end_idx = min(start_idx + member_chapters_after, len(chapters))

                        if start_idx < end_idx:
                            self.log(f"[cyan]Melanjutkan membaca {end_idx - start_idx} bab berikutnya (Bab {start_idx + 1} s/d {end_idx}) sebagai Member Terdaftar...[/]")
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
                                eff_words = self.timing.estimate_effective_words(m_content)
                                self.log(f"[green]Membaca Bab {m_ch_num} (Est: {eff_words} kata, durasi: {int(m_dur)}s)...[/]")

                                def on_m_progress(pct: int, el: float):
                                    if pct < 100:
                                        self.update_progress(read_count, len(chapters), f"[green]Member Bab {m_ch_num} ({pct}%)[/]")
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
                                self.update_progress(read_count, len(chapters), f"[bold green]Selesai Bab {m_ch_num}[/]")
                                self.log(f"[bold green]✓ Selesai Bab {m_ch_num} sebagai Member Resmi Terdaftar![/]")

                                # Akumulasi durasi membaca harian akun & cek batas 1-3 jam
                                if self.client.account_data is not None:
                                    gm_stats = self.client.account_data.setdefault("daily_read_stats", {})
                                    gm_stats["seconds_read"] = gm_stats.get("seconds_read", 0) + m_dur
                                    if self.save_account_cb:
                                        self.save_account_cb(self.client.account_data)

                                    max_gm_sec = gm_stats.get("max_seconds_allowed", 7200)
                                    if gm_stats["seconds_read"] >= max_gm_sec:
                                        gm_h_done = gm_stats["seconds_read"] / 3600.0
                                        gm_h_max = max_gm_sec / 3600.0
                                        self.log(
                                            f"[yellow]Akun telah mencapai batas maksimal waktu membaca harian ({gm_h_done:.1f}/{gm_h_max:.1f} jam). "
                                            f"Mengakhiri sesi membaca hari ini secara organik dan lanjut besok.[/]"
                                        )
                                        break

                                # Peluang pembaca menyukai cerita: Like, Simpan Rak (Bookmark), Follow Penulis
                                if not engaged_socially and read_count >= (limit + 1):
                                    if random.random() < 0.65 or m_idx >= end_idx - 1:
                                        await self.client.maybe_engage_socially(self.target_novel_id, author_id, log_func=self.log)
                                        engaged_socially = True

                                # 1-3% kemungkinan tamu yang mendaftar meremake cerita (jika tersedia)
                                if not remade_story and self.client.access_token:
                                    if random.random() < random.uniform(0.01, 0.03):
                                        remake_info = await self.client.create_reader_remix(
                                            self.target_novel_id, m_ch_id, log_func=self.log
                                        )
                                        if remake_info:
                                            remade_story = True

                                # 5% kemungkinan pembaca masuk ke cerita yang di-remake pembaca lain
                                if not visited_remake and random.random() < 0.05:
                                    def on_m_remake_prog(desc: str):
                                        self.update_progress(read_count, len(chapters), desc)
                                    did_visit = await self.camouflage.simulate_read_other_reader_remake(
                                        self.client, self.log, progress_func=on_m_remake_prog, is_guest=False
                                    )
                                    if did_visit:
                                        visited_remake = True

                                # Jeda antar bab alami & peluang pembaca bosen menengok cerita lain
                                if m_idx < end_idx - 1:
                                    # 20% peluang pembaca bosen & beralih membaca 1-3 cerita lain di katalog sejenak
                                    if random.random() < 0.20:
                                        def on_m_sidetrack_prog(desc: str):
                                            self.update_progress(read_count, len(chapters), desc)
                                        await self.camouflage.simulate_reader_boredom_sidetrack(
                                            self.client, self.log, progress_func=on_m_sidetrack_prog, is_guest=False
                                        )
                                    else:
                                        pause_s = random.uniform(6.0, 15.0)
                                        self.log(f"[dim]Jeda santai sebelum bab berikutnya ({int(pause_s)}s)...[/]")
                                        await asyncio.sleep(pause_s)
                    else:
                        self.log(f"[bold red]✗ Akun gagal diverifikasi, tidak disimpan ke file.[/]")
                else:
                    self.log(f"[red]Pendaftaran gagal: {signup_msg}[/]")
            else:
                self.log("[yellow]Gagal mendapatkan email wajar unik untuk konversi tamu.[/]")

        if self.auto_convert and not engaged_socially and self.client.access_token and read_count > 0:
            await self.client.maybe_engage_socially(self.target_novel_id, author_id, log_func=self.log)

        if not remade_story and self.client.access_token and read_count > 0:
            if random.random() < random.uniform(0.01, 0.03):
                last_ch = chapters[-1] if chapters else {}
                last_ch_id = last_ch.get("hash_id") or last_ch.get("id", "")
                remake_info = await self.client.create_reader_remix(
                    self.target_novel_id, last_ch_id, log_func=self.log
                )
                if remake_info:
                    remade_story = True

        self.update_progress(read_count, len(chapters), f"[bold green]Tuntas ({read_count} Bab)[/]")
        self.log(f"[bold cyan]Selesai sesi! Total {read_count} bab terselesaikan secara alami.[/]")
        return read_count
