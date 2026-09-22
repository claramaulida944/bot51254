"""
Modul Pengelola Proxy Bersih (Stealth Proxy Manager)
Mendukung pembacaan pool proxy HypeProxy / Bright Data, isolasi negara,
serta pelacakan status kesehatan proxy agar request tidak menumpuk di IP yang sama.
"""

import logging
import random
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from .config import PROXIES_FILE, FALLBACK_PROXIES_FILE

logger = logging.getLogger("StealthProxy")

# Pemetaan negara dan zona waktu resmi
COUNTRY_METADATA: Dict[str, Dict[str, str]] = {
    "ID": {"name": "Indonesia", "timezone": "Asia/Jakarta", "lang": "id", "service_country": "ID", "raw_country": "ID"},
    "US": {"name": "United States", "timezone": "America/New_York", "lang": "en-US,en;q=0.9", "service_country": "EN", "raw_country": "US"},
    "KR": {"name": "South Korea", "timezone": "Asia/Seoul", "lang": "ko-KR,ko;q=0.9", "service_country": "KR", "raw_country": "KR"},
    "JP": {"name": "Japan", "timezone": "Asia/Tokyo", "lang": "ja-JP,ja;q=0.9", "service_country": "JP", "raw_country": "JP"},
    "GB": {"name": "United Kingdom", "timezone": "Europe/London", "lang": "en-GB,en;q=0.9", "service_country": "UK", "raw_country": "GB"},
    "CA": {"name": "Canada", "timezone": "America/Toronto", "lang": "en-CA,en;q=0.9", "service_country": "EN", "raw_country": "CA"},
    "AU": {"name": "Australia", "timezone": "Australia/Sydney", "lang": "en-AU,en;q=0.9", "service_country": "EN", "raw_country": "AU"},
    "DE": {"name": "Germany", "timezone": "Europe/Berlin", "lang": "de-DE,de;q=0.9", "service_country": "DE", "raw_country": "DE"},
    "FR": {"name": "France", "timezone": "Europe/Paris", "lang": "fr-FR,fr;q=0.9", "service_country": "FR", "raw_country": "FR"},
    "ES": {"name": "Spain", "timezone": "Europe/Madrid", "lang": "es-ES,es;q=0.9", "service_country": "ES", "raw_country": "ES"},
    "IT": {"name": "Italy", "timezone": "Europe/Rome", "lang": "it-IT,it;q=0.9", "service_country": "IT", "raw_country": "IT"},
    "NL": {"name": "Netherlands", "timezone": "Europe/Amsterdam", "lang": "nl-NL,nl;q=0.9", "service_country": "NL", "raw_country": "NL"},
    "BR": {"name": "Brazil", "timezone": "America/Sao_Paulo", "lang": "pt-BR,pt;q=0.9", "service_country": "BR", "raw_country": "BR"},
    "MX": {"name": "Mexico", "timezone": "America/Mexico_City", "lang": "es-MX,es;q=0.9", "service_country": "MX", "raw_country": "MX"},
    "SG": {"name": "Singapore", "timezone": "Asia/Singapore", "lang": "en-SG,en;q=0.9", "service_country": "SG", "raw_country": "SG"},
    "MY": {"name": "Malaysia", "timezone": "Asia/Kuala_Lumpur", "lang": "ms-MY,ms;q=0.9,en;q=0.8", "service_country": "MY", "raw_country": "MY"},
    "PH": {"name": "Philippines", "timezone": "Asia/Manila", "lang": "fil-PH,fil;q=0.9,en;q=0.8", "service_country": "PH", "raw_country": "PH"},
    "TH": {"name": "Thailand", "timezone": "Asia/Bangkok", "lang": "th-TH,th;q=0.9", "service_country": "TH", "raw_country": "TH"},
    "VN": {"name": "Vietnam", "timezone": "Asia/Ho_Chi_Minh", "lang": "vi-VN,vi;q=0.9", "service_country": "VN", "raw_country": "VN"},
}


class ProxyItem:
    def __init__(self, raw_url: str):
        self.raw_url = raw_url.strip()
        self.parsed = urlparse(self.raw_url)
        self.fail_count = 0
        self.last_failed = 0.0
        self.cooldown_until = 0.0

    @property
    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until

    def mark_failed(self, reason: Any = None):
        self.fail_count += 1
        self.last_failed = time.time()
        # Cooldown bertahap (15s s/d 120s)
        backoff = min(15.0 * (2 ** min(self.fail_count - 1, 3)), 120.0)
        self.cooldown_until = time.time() + backoff

    def mark_success(self):
        self.fail_count = max(0, self.fail_count - 1)
        self.cooldown_until = 0.0


class StealthProxyManager:
    """Manajer Proxy yang mengedepankan isolasi IP & mencegah clustering request."""

    def __init__(self, proxies_file: Optional[Path] = None):
        self.proxies_file = proxies_file or (PROXIES_FILE if PROXIES_FILE.exists() else FALLBACK_PROXIES_FILE)
        self.lock = threading.Lock()
        self.proxies: List[ProxyItem] = []
        self._load_proxies()

    def _load_proxies(self):
        if not self.proxies_file.exists():
            return
        items = []
        with open(self.proxies_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "://" in line:
                    items.append(ProxyItem(line))
        self.proxies = items
        logger.info("Dimuat %d proxy dari %s", len(self.proxies), self.proxies_file)

    @property
    def has_proxies(self) -> bool:
        return len(self.proxies) > 0

    def pop_proxy(self, country_code: Optional[str] = None) -> Optional[str]:
        """Mengambil proxy yang siap pakai secara round-robin atau acak tertimbang."""
        with self.lock:
            if not self.proxies:
                self._load_proxies()
                if not self.proxies:
                    return None

            available = [p for p in self.proxies if p.is_available]
            if not available:
                # Jika semua cooldown, ambil yang cooldown-nya paling dekat berakhir
                candidate = min(self.proxies, key=lambda p: p.cooldown_until)
            else:
                candidate = random.choice(available)

            return candidate.raw_url

    def mark_used(self, proxy_url: str):
        with self.lock:
            for p in self.proxies:
                if p.raw_url == proxy_url:
                    p.mark_success()
                    break

    def mark_failed(self, proxy_url: str, reason: Any = None):
        with self.lock:
            for p in self.proxies:
                if p.raw_url == proxy_url:
                    p.mark_failed(reason)
                    break


default_proxy_manager = StealthProxyManager()
