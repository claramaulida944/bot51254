"""
Modul Penyamaran & Penjelajah Katalog Organik (Camouflage Engine).
Mematahkan deteksi konsentrasi 1 novel tunggal (Target Concentration)
dan membangun rekam jejak discovery path resmi:
1. Menjelajahi katalog resmi (Rising, New Release, For You).
2. Membaca novel-novel populer lain secara acak sebagai lalu lintas kamuflase.
3. Menjaga rasio pembacaan novel target tetap proporsional (organik).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
import httpx

from .config import BASE_URL, CAMOUFLAGE_NOVEL_RATIO

if TYPE_CHECKING:
    from .client import StealthApiClient

logger = logging.getLogger("StealthCamouflage")


class CamouflageEngine:
    """Mengelola discovery path dan pemilihan novel kamuflase dari katalog resmi."""

    DISCOVERY_ENDPOINTS = [
        "/api/v1/bookstore/sections/rising",
        "/api/v1/bookstore/sections/new-release-best",
        "/api/v1/bookstore/for-you-shelf?limit=18",
        "/api/v1/novels?limit=24",
    ]

    def __init__(self, target_novel_id: str):
        self.target_novel_id = target_novel_id
        self.cached_novels: List[Dict[str, Any]] = []

    async def fetch_catalog_novels(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Mengambil daftar novel populer yang sedang tayang di platform secara langsung."""
        if self.cached_novels:
            return self.cached_novels

        collected = []
        endpoint = random.choice(self.DISCOVERY_ENDPOINTS)
        try:
            resp = await client.get(endpoint)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", []) or data.get("novels", [])
                if isinstance(data, list):
                    items = data

                for item in items:
                    novel_id = item.get("hash_id") or item.get("id")
                    title = item.get("title", "")
                    if novel_id and novel_id != self.target_novel_id:
                        collected.append({
                            "hash_id": novel_id,
                            "title": title,
                        })
        except Exception as exc:
            logger.debug("Gagal mengambil katalog kamuflase (%s): %s", endpoint, exc)

        # Fallback jika gagal mengambil dari endpoint (novel-novel nyata terpopuler)
        if not collected:
            collected = [
                {"hash_id": "VqQK9b6Z99bEvYnG", "title": "Billionaire's Secret"},
                {"hash_id": "kpQJ0dNk7V8eLOvE", "title": "Moonlight Shadow"},
                {"hash_id": "DXMVyb8jKjevAZEJ", "title": "Silent Promises"},
                {"hash_id": "kQWjnegmEDbwZ1p0", "title": "Destined to Reign"},
            ]

        self.cached_novels = collected
        return self.cached_novels

    def pick_next_action(self, consecutive_target_count: int = 0) -> str:
        """
        Menentukan apakah bab berikutnya harus membaca novel target atau novel kamuflase:
        - Memastikan rasio target tidak melebihi batas anomali.
        - Membatasi pembacaan target beruntun.
        """
        if consecutive_target_count >= 3:
            return "camouflage"

        # Kemungkinan membaca kamuflase berdasarkan rasio
        if random.random() < CAMOUFLAGE_NOVEL_RATIO:
            return "camouflage"
        return "target"

    async def get_random_camouflage_novel(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Mengambil satu novel kamuflase acak dari katalog."""
        novels = await self.fetch_catalog_novels(client)
        return random.choice(novels)

    def decide_discovery_books_count(self) -> int:
        """
        Menentukan berapa banyak buku yang dilihat sebelum menemukan novel target:
        - 25% peluang: Direct / Promo link (Langsung ketemu buku target, 0 buku lain dilihat).
        - 45% peluang: Casual browse (Melihat 1 s/d 3 buku di beranda/rekomendasi).
        - 30% peluang: Deep discovery (Menjelajah 4 s/d 10 buku sebelum menemukan target).
        """
        dice = random.random()
        if dice < 0.25:
            return 0  # Langsung ketemu
        elif dice < 0.70:
            return random.randint(1, 3)  # Liat 1 - 3 buku
        else:
            return random.randint(4, 10)  # Liat 4 - 10 buku

    async def simulate_discovery_journey(self, client: Any, log_func, progress_func: Any = None) -> int:
        """
        Mensimulasikan penjelajahan katalog nyata sebelum menemukan novel target:
        - Membuka cover/sinopsis buku lain di rak dengan durasi membaca alami (12-25s).
        - Membaca cuplikan bab pembuka sesekali (35% peluang, durasi 25-50s).
        - Menghasilkan rekam jejak analitik platform yang 100% organik.
        """
        count = self.decide_discovery_books_count()
        if count == 0:
            log_func("[dim]Alur Penemuan: Tautan langsung / Iklan promosi (Langsung membuka novel target).[/]")
            return 0

        raw_client = await client.get_client()
        novels = await self.fetch_catalog_novels(raw_client)
        if not novels:
            return 0

        # Ambil sampel buku unik
        candidates = [n for n in novels if (n.get("hash_id") or n.get("id")) != self.target_novel_id]
        if not candidates:
            candidates = novels

        sample_books = random.sample(candidates, min(count, len(candidates)))
        log_func(f"[cyan]Alur Penemuan: Menjelajahi katalog ({count} buku lain dilihat sebelum novel target)...[/]")

        for i, book in enumerate(sample_books, start=1):
            b_id = book.get("hash_id") or book.get("id")
            b_title = book.get("title", "Popular Novel")

            # 1. Buka dan baca sinopsis buku lain (12 - 25 detik)
            syn_dur = random.uniform(12.0, 25.0)
            if progress_func:
                progress_func(f"[dim]Sinopsis: {b_title[:16]}...[/]")
            log_func(f"[dim]  [{i}/{count}] Membaca sinopsis buku: '{b_title[:22]}' ({int(syn_dur)}s)...[/]")
            await client.get_novel_detail(b_id)
            await asyncio.sleep(syn_dur)

            # 2. Peluang 35% mengintip cuplikan Bab 1 (25 - 50 detik)
            if random.random() < 0.35:
                chaps = await client.get_novel_chapters(b_id)
                if chaps:
                    c_id = chaps[0].get("hash_id") or chaps[0].get("id")
                    await client.get_chapter_detail(b_id, c_id)
                    skim_dur = random.uniform(25.0, 50.0)
                    if progress_func:
                        progress_func(f"[dim]Skim Bab 1 ({int(skim_dur)}s)...[/]")
                    log_func(f"[dim]      Mengintip cuplikan bab ({int(skim_dur)}s)...[/]")
                    await asyncio.sleep(skim_dur)
                    await client.send_guest_progress(b_id, c_id, skim_dur, scroll_percent=0.6, completed=False)

            # Jeda alami sebelum memilih buku berikutnya (3 - 6 detik)
            await asyncio.sleep(random.uniform(3.0, 6.0))

        log_func(f"[bold green]✓ Berhasil menemukan novel target setelah melihat {count} buku lain![/]")
        return count

    async def simulate_reader_boredom_sidetrack(
        self,
        client: Any,
        log_func,
        progress_func: Any = None,
        is_guest: bool = True,
    ) -> bool:
        """
        Mensimulasikan perilaku manusia yang merasa jenuh/bosen saat membaca:
        1. Menutup sementara novel target.
        2. Menjelajah 1 s/d 3 cerita lain di katalog / rekomendasi.
        3. Membaca sinopsis atau cuplikan bab pembuka cerita lain.
        4. Kembali membuka dan melanjutkan novel target.
        """
        raw_client = await client.get_client()
        novels = await self.fetch_catalog_novels(raw_client)
        if not novels:
            return False

        candidates = [n for n in novels if (n.get("hash_id") or n.get("id")) != self.target_novel_id]
        if not candidates:
            candidates = novels

        sidetrack_count = random.randint(1, 3)
        sample_books = random.sample(candidates, min(sidetrack_count, len(candidates)))

        log_func(
            f"[yellow]Simulasi Pembaca Bosen: Rehat sejenak dari novel target & menengok {len(sample_books)} cerita lain di katalog...[/]"
        )
        if progress_func:
            progress_func("[yellow]Rehat: Baca cerita lain...[/]")

        for i, book in enumerate(sample_books, start=1):
            b_id = book.get("hash_id") or book.get("id")
            b_title = book.get("title", "Popular Novel")

            # 1. Buka dan baca sinopsis cerita lain (10 - 20 detik)
            syn_dur = random.uniform(10.0, 20.0)
            if progress_func:
                progress_func(f"[dim]Bosen: Sinopsis '{b_title[:14]}'..[/]")
            log_func(f"[dim]  [Cerita Lain {i}/{len(sample_books)}] Menengok sinopsis '{b_title[:22]}' ({int(syn_dur)}s)...[/]")
            await client.get_novel_detail(b_id)
            await asyncio.sleep(syn_dur)

            # 2. Peluang 50% membaca 1 bab cuplikan cerita lain (20 - 40 detik)
            if random.random() < 0.50:
                chaps = await client.get_novel_chapters(b_id)
                if chaps:
                    c_id = chaps[0].get("hash_id") or chaps[0].get("id")
                    await client.get_chapter_detail(b_id, c_id)
                    skim_dur = random.uniform(20.0, 40.0)
                    if progress_func:
                        progress_func(f"[dim]Skim Bab 1: '{b_title[:12]}'..[/]")
                    log_func(f"[dim]      Membaca cuplikan Bab 1 '{b_title[:20]}' ({int(skim_dur)}s)...[/]")
                    await asyncio.sleep(skim_dur)
                    if is_guest:
                        await client.send_guest_progress(b_id, c_id, skim_dur, scroll_percent=0.7, completed=False)
                    else:
                        await client.send_reading_telemetry(b_id, c_id, 1, skim_dur, depth_percent=70, completed=False)

            await asyncio.sleep(random.uniform(2.5, 5.0))

        log_func(f"[bold green]✓ Rasa penasaran kembali: Pembaca membuka kembali novel target dan melanjutkan bab berikutnya![/]")
        if progress_func:
            progress_func("[green]Kembali ke novel target...[/]")
        await asyncio.sleep(random.uniform(3.0, 6.0))
        return True

    async def simulate_read_other_reader_remake(
        self,
        client: StealthApiClient,
        log_func: Callable[[str], None],
        progress_func: Optional[Callable[[str], None]] = None,
        is_guest: bool = False,
    ) -> bool:
        """
        Simulasi 5% pembaca masuk ke cerita yang di-remake oleh pembaca lain:
        1. Membuka daftar cabang seri remake novel target
        2. Memilih salah satu cerita remake karya pembaca lain
        3. Membuka dan membaca episode remake tersebut layaknya pembaca organik
        4. Kembali ke novel utama
        """
        try:
            roots = await client.get_remix_roots(self.target_novel_id)
            if not roots:
                log_func("[dim]Mengecek tab cerita Remake pembaca lain (belum ada cabang aktif)...[/]")
                if progress_func:
                    progress_func("[dim]Cek Tab Remake (0 Cabang)...[/]")
                await asyncio.sleep(random.uniform(3.0, 6.0))
                return False

            chosen = random.choice(roots)
            series_id = chosen.get("series_id")
            if not series_id:
                return False

            series_title = chosen.get("title") or chosen.get("series_title") or chosen.get("name") or "Remake Cerita Pembaca"
            await client.get_remix_episode(series_id, 1)

            read_dur = random.uniform(14.0, 26.0)
            if progress_func:
                progress_func(f"[cyan]Baca Remake: '{series_title[:14]}'..[/]")

            log_func(
                f"[bold cyan]📖 Pembaca masuk membaca cerita yang di-remake pembaca lain: "
                f"'{series_title[:30]}' ({int(read_dur)}s)...[/]"
            )
            await asyncio.sleep(read_dur)

            log_func(f"[dim]Selesai membaca remake pembaca lain, kembali ke alur novel target.[/]")
            if progress_func:
                progress_func("[green]Kembali ke novel utama...[/]")
            await asyncio.sleep(random.uniform(2.0, 4.0))
            return True
        except Exception:
            return False

