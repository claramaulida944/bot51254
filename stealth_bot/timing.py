"""
Modul Simulasi Waktu Membaca Manusia (Human Timing & Behavior Simulator).
Menghilangkan pola robotik:
1. Menghitung durasi baca bab dinamis berbasis jumlah kata nyata (Words Per Minute / WPM).
2. Menghasilkan variasi kecepatan baca alami (pace change) antar bab agar tidak 0%.
3. Simulasi mid-chapter progress ticks (25%, 50%, 75%, 100%).
4. Simulasi kelelahan membaca (Reading Fatigue) & jeda istirahat realistis (Drop-off).
"""

import asyncio
import math
import random
import re
import time
from typing import Callable, List, Optional, Tuple

from .config import MIN_WPM, MAX_WPM, WPM_JITTER_PCT


class ReadingSimulator:
    """Simulator membaca manusia yang proporsional dan non-linier."""

    def __init__(self, min_wpm: int = MIN_WPM, max_wpm: int = MAX_WPM):
        self.min_wpm = min_wpm
        self.max_wpm = max_wpm
        self.last_reading_time_sec: Optional[float] = None
        self.chapters_read_count: int = 0

    def estimate_effective_words(self, chapter_text: str) -> int:
        """
        Menghitung estimasi kata secara cerdas lintas bahasa:
        - Bahasa Latin & Korea: memiliki spasi antar kata.
        - Bahasa Jepang (Kanji/Hiragana/Katakana) & Mandarin: tidak memakai spasi.
          Rata-rata kecepatan baca Jepang adalah 400 - 600 karakter/menit (setara 200 WPM, ~2.5 char/kata).
        """
        if not chapter_text:
            return 800

        cjk_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]', chapter_text))
        if cjk_chars > 80:
            # Teks Jepang / CJK
            equivalent_words = int(cjk_chars / 2.5)
            latin_words = len(re.findall(r'[a-zA-Z0-9]+', chapter_text))
            return max(equivalent_words + latin_words, 700)

        # Latin / Korea
        words = len(chapter_text.split())
        return max(words, 600)

    def calculate_reading_duration(self, chapter_text: str) -> Tuple[float, float]:
        """
        Menghitung durasi membaca realistis:
        - Menghitung jumlah kata nyata/ekuivalen dari teks bab.
        - Menerapkan WPM acak dalam rentang normal manusia (175 - 250 WPM).
        - Menambahkan variasi jitter mikro.
        - Memastikan batas aman anti-fraud (minimal 2.5 menit / 150 detik per bab penuh).
        - Mengembalikan: (durasi_detik, pace_change_pct)
        """
        words = self.estimate_effective_words(chapter_text)

        # Pilih base WPM acak per bab (manusia membaca tidak pernah berkecepatan konstan)
        base_wpm = random.uniform(self.min_wpm, self.max_wpm)
        
        # Tambah variansi jitter mikro (+/- 15% s/d 25%)
        jitter = random.uniform(-WPM_JITTER_PCT, WPM_JITTER_PCT)
        effective_wpm = base_wpm * (1.0 + jitter)

        # Durasi dasar (menit -> detik)
        duration_sec = (words / effective_wpm) * 60.0

        # Tambahkan jeda awal buka bab & jeda akhir bab (3 - 6 detik)
        prep_delay = random.uniform(3.0, 6.0)
        # Batas aman: Minimal 140s (2.3 menit) agar lolos anti-fraud, maksimal 360s (6 menit) agar efisien
        calc_dur = duration_sec + prep_delay
        total_duration = max(min(calc_dur, 360.0), 140.0)

        # Hitung perubahan kecepatan dibanding bab sebelumnya (pace_change_pct)
        pace_change_pct = 0.0
        if self.last_reading_time_sec is not None and self.last_reading_time_sec > 0:
            pace_change_pct = round(((total_duration - self.last_reading_time_sec) / self.last_reading_time_sec) * 100, 1)

        self.last_reading_time_sec = total_duration
        self.chapters_read_count += 1

        return total_duration, pace_change_pct

    async def simulate_human_reading(
        self,
        total_duration: float,
        on_progress: Optional[Callable[[int, float], None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """
        Mensimulasikan proses membaca secara bertahap:
        - Membagi total durasi ke dalam 4 kuadran progres: 25%, 50%, 75%, 100%.
        - Memanggil callback `on_progress(depth_percent, elapsed_seconds)` di setiap titik.
        - Memungkinkan jeda mikro acak selama proses berlangsung.
        """
        milestones = [25, 50, 75, 100]
        step_duration = total_duration / len(milestones)
        elapsed = 0.0

        for pct in milestones:
            if cancel_event and cancel_event.is_set():
                return False

            # Tambahkan sedikit variasi acak pada jeda per kuadran
            chunk_time = step_duration * random.uniform(0.9, 1.1)
            
            # Tidur secara asynchronous
            await asyncio.sleep(chunk_time)
            elapsed += chunk_time

            if on_progress:
                try:
                    on_progress(pct, round(elapsed, 1))
                except Exception:
                    pass

        return True

    def should_take_fatigue_break(self) -> Tuple[bool, float]:
        """
        Mengevaluasi kelelahan membaca alami:
        - Setiap 3-5 bab, manusia biasanya beristirahat atau terdistraksi.
        - Mengembalikan: (apakah_istirahat, durasi_istirahat_detik)
        """
        if self.chapters_read_count >= random.randint(3, 5):
            self.chapters_read_count = 0
            # Istirahat 30 detik s/d 90 detik
            break_time = random.uniform(30.0, 90.0)
            return True, break_time
        return False, 0.0
