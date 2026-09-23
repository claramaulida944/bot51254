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
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from .config import PROXIES_FILE, FALLBACK_PROXIES_FILE

logger = logging.getLogger("StealthProxy")

# Pemetaan seluruh negara resmi Quarterfull terverifikasi (/api/v1/service-countries)
COUNTRY_METADATA: Dict[str, Dict[str, str]] = {
    "ID": {"name": "Indonesia", "timezone": "Asia/Jakarta", "lang": "id", "service_country": "ID", "raw_country": "ID"},
    "US": {"name": "United States", "timezone": "America/New_York", "lang": "en-US,en;q=0.9", "service_country": "EN", "raw_country": "US"},
    "KR": {"name": "South Korea", "timezone": "Asia/Seoul", "lang": "ko-KR,ko;q=0.9", "service_country": "KR", "raw_country": "KR"},
    "JP": {"name": "Japan", "timezone": "Asia/Tokyo", "lang": "ja-JP,ja;q=0.9", "service_country": "JP", "raw_country": "JP"},
    "GB": {"name": "United Kingdom", "timezone": "Europe/London", "lang": "en-GB,en;q=0.9", "service_country": "UK", "raw_country": "GB"},
    "UK": {"name": "United Kingdom", "timezone": "Europe/London", "lang": "en-GB,en;q=0.9", "service_country": "UK", "raw_country": "GB"},
    "EN": {"name": "English / Global", "timezone": "America/New_York", "lang": "en-US,en;q=0.9", "service_country": "EN", "raw_country": "US"},
    "AU": {"name": "Australia", "timezone": "Australia/Sydney", "lang": "en-AU,en;q=0.9", "service_country": "EN", "raw_country": "AU"},
    "CA": {"name": "Canada", "timezone": "America/Toronto", "lang": "en-CA,en;q=0.9", "service_country": "EN", "raw_country": "CA"},
    "SG": {"name": "Singapore", "timezone": "Asia/Singapore", "lang": "en-SG,en;q=0.9", "service_country": "SG", "raw_country": "SG"},
    "MY": {"name": "Malaysia", "timezone": "Asia/Kuala_Lumpur", "lang": "ms-MY,ms;q=0.9,en;q=0.8", "service_country": "MY", "raw_country": "MY"},
    "PH": {"name": "Philippines", "timezone": "Asia/Manila", "lang": "fil-PH,fil;q=0.9,en;q=0.8", "service_country": "PH", "raw_country": "PH"},
    "TH": {"name": "Thailand", "timezone": "Asia/Bangkok", "lang": "th-TH,th;q=0.9", "service_country": "TH", "raw_country": "TH"},
    "VN": {"name": "Vietnam", "timezone": "Asia/Ho_Chi_Minh", "lang": "vi-VN,vi;q=0.9", "service_country": "VN", "raw_country": "VN"},
    "IN": {"name": "India", "timezone": "Asia/Kolkata", "lang": "en-IN,en;q=0.9,hi;q=0.8", "service_country": "IN", "raw_country": "IN"},
    "DE": {"name": "Germany", "timezone": "Europe/Berlin", "lang": "de-DE,de;q=0.9", "service_country": "DE", "raw_country": "DE"},
    "FR": {"name": "France", "timezone": "Europe/Paris", "lang": "fr-FR,fr;q=0.9", "service_country": "FR", "raw_country": "FR"},
    "ES": {"name": "Spain", "timezone": "Europe/Madrid", "lang": "es-ES,es;q=0.9", "service_country": "ES", "raw_country": "ES"},
    "IT": {"name": "Italy", "timezone": "Europe/Rome", "lang": "it-IT,it;q=0.9", "service_country": "IT", "raw_country": "IT"},
    "NL": {"name": "Netherlands", "timezone": "Europe/Amsterdam", "lang": "nl-NL,nl;q=0.9", "service_country": "NL", "raw_country": "NL"},
    "SE": {"name": "Sweden", "timezone": "Europe/Stockholm", "lang": "sv-SE,sv;q=0.9", "service_country": "SE", "raw_country": "SE"},
    "NO": {"name": "Norway", "timezone": "Europe/Oslo", "lang": "no-NO,no;q=0.9", "service_country": "NO", "raw_country": "NO"},
    "DK": {"name": "Denmark", "timezone": "Europe/Copenhagen", "lang": "da-DK,da;q=0.9", "service_country": "DK", "raw_country": "DK"},
    "FI": {"name": "Finland", "timezone": "Europe/Helsinki", "lang": "fi-FI,fi;q=0.9", "service_country": "FI", "raw_country": "FI"},
    "PL": {"name": "Poland", "timezone": "Europe/Warsaw", "lang": "pl-PL,pl;q=0.9", "service_country": "PL", "raw_country": "PL"},
    "CZ": {"name": "Czech Republic", "timezone": "Europe/Prague", "lang": "cs-CZ,cs;q=0.9", "service_country": "CZ", "raw_country": "CZ"},
    "TR": {"name": "Turkey", "timezone": "Europe/Istanbul", "lang": "tr-TR,tr;q=0.9", "service_country": "TR", "raw_country": "TR"},
    "BR": {"name": "Brazil", "timezone": "America/Sao_Paulo", "lang": "pt-BR,pt;q=0.9", "service_country": "BR", "raw_country": "BR"},
    "AR": {"name": "Argentina", "timezone": "America/Argentina/Buenos_Aires", "lang": "es-AR,es;q=0.9", "service_country": "AR", "raw_country": "AR"},
    "CO": {"name": "Colombia", "timezone": "America/Bogota", "lang": "es-CO,es;q=0.9", "service_country": "CO", "raw_country": "CO"},
    "MX": {"name": "Mexico", "timezone": "America/Mexico_City", "lang": "es-MX,es;q=0.9", "service_country": "MX", "raw_country": "MX"},
    "CL": {"name": "Chile", "timezone": "America/Santiago", "lang": "es-CL,es;q=0.9", "service_country": "CL", "raw_country": "CL"},
    "PE": {"name": "Peru", "timezone": "America/Lima", "lang": "es-PE,es;q=0.9", "service_country": "PE", "raw_country": "PE"},
    "HK": {"name": "Hong Kong", "timezone": "Asia/Hong_Kong", "lang": "zh-HK,zh;q=0.9,en;q=0.8", "service_country": "HK", "raw_country": "HK"},
    "TW": {"name": "Taiwan", "timezone": "Asia/Taipei", "lang": "zh-TW,zh;q=0.9", "service_country": "TW", "raw_country": "TW"},
    "AT": {"name": "Austria", "timezone": "Europe/Vienna", "lang": "de-AT,de;q=0.9", "service_country": "AT", "raw_country": "AT"},
    "PT": {"name": "Portugal", "timezone": "Europe/Lisbon", "lang": "pt-PT,pt;q=0.9", "service_country": "PT", "raw_country": "PT"},
    "HU": {"name": "Hungary", "timezone": "Europe/Budapest", "lang": "hu-HU,hu;q=0.9", "service_country": "HU", "raw_country": "HU"},
    "BE": {"name": "Belgium", "timezone": "Europe/Brussels", "lang": "nl-BE,fr-BE;q=0.9", "service_country": "BE", "raw_country": "BE"},
    "CH": {"name": "Switzerland", "timezone": "Europe/Zurich", "lang": "de-CH,fr-CH;q=0.9", "service_country": "CH", "raw_country": "CH"},
    "NZ": {"name": "New Zealand", "timezone": "Pacific/Auckland", "lang": "en-NZ,en;q=0.9", "service_country": "EN", "raw_country": "NZ"},
    "IE": {"name": "Ireland", "timezone": "Europe/Dublin", "lang": "en-IE,en;q=0.9", "service_country": "EN", "raw_country": "IE"},
    "ZA": {"name": "South Africa", "timezone": "Africa/Johannesburg", "lang": "en-ZA,en;q=0.9", "service_country": "EN", "raw_country": "ZA"},
    "AE": {"name": "United Arab Emirates", "timezone": "Asia/Dubai", "lang": "ar-AE,en;q=0.9", "service_country": "AE", "raw_country": "AE"},
    "SA": {"name": "Saudi Arabia", "timezone": "Asia/Riyadh", "lang": "ar-SA,en;q=0.9", "service_country": "SA", "raw_country": "SA"},
    "EG": {"name": "Egypt", "timezone": "Africa/Cairo", "lang": "ar-EG,en;q=0.9", "service_country": "EG", "raw_country": "EG"},
    "IL": {"name": "Israel", "timezone": "Asia/Jerusalem", "lang": "he-IL,en;q=0.9", "service_country": "IL", "raw_country": "IL"},
    "GR": {"name": "Greece", "timezone": "Europe/Athens", "lang": "el-GR,el;q=0.9", "service_country": "GR", "raw_country": "GR"},
    "RO": {"name": "Romania", "timezone": "Europe/Bucharest", "lang": "ro-RO,ro;q=0.9", "service_country": "RO", "raw_country": "RO"},
    "UA": {"name": "Ukraine", "timezone": "Europe/Kyiv", "lang": "uk-UA,uk;q=0.9", "service_country": "UA", "raw_country": "UA"},
}


def pick_weighted_country(allowed_pool: Optional[List[str]] = None) -> str:
    """
    Memilih kode negara dengan proporsi prioritas:
    - 30% US (Amerika Serikat)
    - 20% KR (Korea Selatan)
    - 10% ID (Indonesia)
    - 40% Sisanya acak merata dari seluruh negara resmi Quarterfull lainnya (GB, JP, DE, CA, AU, dll).
    """
    all_codes = list(COUNTRY_METADATA.keys())
    # Kecualikan alias UK & EN dari pick agar tidak duplikat dengan GB & US
    valid_codes = [c for c in all_codes if c not in ("UK", "EN")]
    
    if allowed_pool:
        pool = [c for c in allowed_pool if c in COUNTRY_METADATA and c not in ("UK", "EN")]
        if not pool:
            pool = valid_codes
    else:
        pool = valid_codes

    target_weights = {
        "US": 30.0,
        "KR": 20.0,
        "ID": 10.0,
    }

    present_targets = [c for c in target_weights if c in pool]
    sum_targets = sum(target_weights[c] for c in present_targets)
    remaining_pool = [c for c in pool if c not in target_weights]

    if not remaining_pool:
        weights = [target_weights.get(c, 1.0) for c in pool]
    else:
        rem_weight = max(0.01, (100.0 - sum_targets) / len(remaining_pool))
        weights = [target_weights.get(c, rem_weight) for c in pool]

    return random.choices(pool, weights=weights, k=1)[0]



import sys
_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

try:
    from proxy_manager import HypeProxyClient
except ImportError:
    HypeProxyClient = None

class ProxyItem:
    def __init__(self, raw_url: str):
        self.raw_url = raw_url.strip()
        self.parsed = urlparse(self.raw_url)
        self.fail_count = 0
        self.last_failed = 0.0
        self.cooldown_until = 0.0
        # Deteksi ID HypeProxy (user4 -> 4)
        m_uid = re.search(r"user(\d+)", self.raw_url, re.IGNORECASE)
        self.hypeproxy_id = int(m_uid.group(1)) if m_uid else None

    @property
    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until

    def mark_failed(self, reason: Any = None):
        self.fail_count += 1
        self.last_failed = time.time()
        # Cooldown bertahap (15s s/d 120s)
        backoff = min(15.0 * (2 ** min(self.fail_count - 1, 3)), 120.0)
        self.cooldown_until = time.time() + backoff

        # Picu rotasi IP instan di HypeProxy jika slot terdaftar
        if HypeProxyClient and self.hypeproxy_id is not None:
            def _rotate():
                try:
                    HypeProxyClient.rotate_proxy(self.hypeproxy_id)
                except Exception:
                    pass
            threading.Thread(target=_rotate, daemon=True).start()

    def mark_success(self):
        self.fail_count = max(0, self.fail_count - 1)
        self.cooldown_until = 0.0


class StealthProxyManager:
    """Manajer Proxy yang mengedepankan isolasi IP & mencegah clustering request."""

    def __init__(self, proxies_file: Optional[Path] = None, enabled: bool = True):
        self.proxies_file = proxies_file or (PROXIES_FILE if PROXIES_FILE.exists() else FALLBACK_PROXIES_FILE)
        self.enabled = enabled
        self.lock = threading.Lock()
        self.proxies: List[ProxyItem] = []
        if self.enabled:
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
        return self.enabled and len(self.proxies) > 0

    def pop_proxy(self, country_code: Optional[str] = None) -> Optional[str]:
        """Mengambil proxy yang siap pakai secara round-robin atau acak tertimbang."""
        if not self.enabled:
            return None
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


default_proxy_manager = StealthProxyManager(enabled=True)
