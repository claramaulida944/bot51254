"""
Konfigurasi Global & Parameter Anti-Deteksi Stealth Bot V2.
Tersinkronisasi dengan metrik resmi Android Quarterfull / Toodat.
"""

from pathlib import Path

# Direktori dasar
BASE_DIR = Path(__file__).resolve().parent
ACCOUNTS_FILE = BASE_DIR / "akun_stealth.txt"
PROXIES_FILE = BASE_DIR / "proxies.txt"
FALLBACK_PROXIES_FILE = BASE_DIR.parent / "proxies.txt"

# Konfigurasi Target API Resmi
BASE_URL = "https://api.quarterfull.io"
APP_VERSION = "3.0.55"  # Versi resmi yang divalidasi oleh endpoint /api/auth/app-version
APP_VARIANT = "prod"
PLATFORM = "android"

# Novel Target Default (Bisa diganti via UI CLI)
DEFAULT_TARGET_NOVEL_ID = "Py7LDdwpEQ8e1YKX"  # "TERMS OF SURRENDER"

# Parameter Anti-Fraud: Kecepatan Baca Manusia (Words Per Minute)
MIN_WPM = 175  # Kecepatan santai
MAX_WPM = 250  # Kecepatan membaca cepat
WPM_JITTER_PCT = 0.20  # Variasi mikro acak (+/- 20%) antar bab

# Parameter Anti-Fraud: Diversifikasi & Kamuflase
CAMOUFLAGE_NOVEL_RATIO = 0.65  # 65% membaca novel populer lain di katalog, 35% novel target
MAX_CONSECUTIVE_TARGET_CHAPTERS = 4  # Maksimal bab novel target sebelum jeda/fatigue

# Parameter Anti-Fraud: Throttle Registrasi Akun
MIN_SIGNUP_INTERVAL_SEC = 60.0   # Jeda minimum antar registrasi (bukan serentak)
MAX_SIGNUP_INTERVAL_SEC = 180.0  # Jeda maksimum antar registrasi

# Header Capabilities Resmi Android
FRONTEND_CAPABILITY = "member_payout_telemetry.v1"
MISSION_CAPABILITIES = "global-reading-v1,global-comment-v1,exploration-v1,recommend"
BOOKSTORE_GENRE_GATE = "resolved"
