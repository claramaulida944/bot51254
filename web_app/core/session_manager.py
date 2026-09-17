"""
Modul Manajemen Sesi Tamu (Guest Reader) API Toodat / Quarterfull.

Modul ini bertanggung jawab menangani:
- Koneksi jaringan HTTP/2 via httpx
- Standard header fingerprint aplikasi mobile Toodat
- Generator ID unik sisi klien (Device ID, Base36 Session ID, Guest Event ID)
- Inisialisasi dan persistensi sesi tamu (Guest Token & Cookie)
"""

import logging
import secrets
import string
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback jika zoneinfo tidak tersedia di lingkungan tertentu
    ZoneInfo = None  # type: ignore

import httpx

# Konfigurasi logger default
logger = logging.getLogger("ToodatGuestClient")


class IdentifierGenerator:
    """Menyediakan generator identitas unik sisi klien yang kompatibel dengan aplikasi Toodat."""

    BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

    @staticmethod
    def generate_device_id() -> str:
        """Menghasilkan Device ID berbasis UUID v4 standar."""
        return str(uuid.uuid4())

    @classmethod
    def base36_encode(cls, number: int) -> str:
        """Mengonversi bilangan bulat positif ke representasi string base36."""
        if number < 0:
            raise ValueError("Hanya mendukung bilangan bulat non-negatif")
        if number == 0:
            return "0"

        result = []
        alphabet = cls.BASE36_ALPHABET
        while number > 0:
            number, rem = divmod(number, 36)
            result.append(alphabet[rem])
        return "".join(reversed(result))

    @classmethod
    def generate_random_base36(cls, length: int = 10) -> str:
        """Menghasilkan string acak base36 dengan panjang yang ditentukan."""
        return "".join(secrets.choice(cls.BASE36_ALPHABET) for _ in range(length))

    @classmethod
    def generate_session_id(
        cls,
        prefix: str = "grs",
        timestamp_ms: Optional[int] = None,
        random_len: int = 10,
    ) -> str:
        """
        Menghasilkan Session ID dengan pola:
        {prefix}_{timestamp_base36}_{random_base36_10_char}

        Contoh prefix:
        - 'grs': Guest Reading Session (cth: grs_mtqe0smj_7qif5752fi)
        - 'attr': Attribution Session (cth: attr_mtqdxsq4_baes21ud)
        - 'entry': Entry Event (cth: entry_mtqphcmg_73t2d602)
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)

        ts_b36 = cls.base36_encode(timestamp_ms)
        rand_b36 = cls.generate_random_base36(random_len)
        return f"{prefix}_{ts_b36}_{rand_b36}"

    @classmethod
    def generate_guest_event_id(
        cls,
        chapter_hash_id: str,
        timestamp_ms: Optional[int] = None,
        random_len: int = 11,
    ) -> str:
        """
        Menghasilkan Guest Event ID dengan format:
        guest-open:{chapter_hash_id}:{timestamp_ms}:{random_string_base36}

        Contoh:
        guest-open:vJ4openkLPpa7Az1:1788733995671:vrqg482jg4d
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)

        rand_b36 = cls.generate_random_base36(random_len)
        return f"guest-open:{chapter_hash_id}:{timestamp_ms}:{rand_b36}"


class ToodatGuestClient:
    """
    Klien HTTP/2 untuk simulasi interaksi pengguna tamu (guest reader)
    pada API Toodat / Quarterfull.
    """

    BASE_URL: str = "https://api.quarterfull.io"
    USER_AGENT: str = "okhttp/4.12.0"
    PLATFORM: str = "android"
    APP_VARIANT: str = "prod"
    APP_VERSION: str = "3.0.52"
    TIMEZONE: str = "Asia/Jakarta"
    COUNTRY: str = "ID"
    LANGUAGE: str = "id"

    def __init__(
        self,
        device_id: Optional[str] = None,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ) -> None:
        """
        Inisialisasi klien dengan Device ID dan konfigurasi HTTP/2.

        :param device_id: UUID v4 device ID klien (akan di-generate jika None).
        :param timeout: Durasi timeout koneksi dalam detik.
        :param verify_ssl: Verifikasi sertifikat SSL server.
        """
        self.device_id: str = device_id or IdentifierGenerator.generate_device_id()
        self.guest_id: Optional[str] = None
        self.guest_token: Optional[str] = None

        # Siapkan base headers default
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": self.USER_AGENT,
            "accept-encoding": "gzip",
            "x-platform": self.PLATFORM,
            "x-app-variant": self.APP_VARIANT,
            "x-app-version": self.APP_VERSION,
            "x-timezone": self.TIMEZONE,
            "x-local-date": self._get_current_local_date(),
            "x-user-country": self.COUNTRY,
            "x-user-raw-country": self.COUNTRY,
            "accept-language": self.LANGUAGE,
            "x-device-id": self.device_id,
            "accept": "application/json",
        }

        # Inisialisasi httpx.Client dengan protokol HTTP/2
        self._client: httpx.Client = httpx.Client(
            base_url=self.BASE_URL,
            http2=True,
            headers=headers,
            timeout=httpx.Timeout(timeout),
            verify=verify_ssl,
        )

        logger.info(
            "ToodatGuestClient berhasil diinisialisasi [Device ID: %s, HTTP/2: True]",
            self.device_id,
        )

    def _get_current_local_date(self) -> str:
        """Mengembalikan tanggal lokal saat ini dalam format YYYY-MM-DD sesuai zona waktu Asia/Jakarta."""
        try:
            if ZoneInfo is not None:
                now = datetime.now(ZoneInfo(self.TIMEZONE))
            else:
                now = datetime.now()
            return now.strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def _refresh_dynamic_headers(self) -> None:
        """Memperbarui nilai header yang dinamis seperti x-local-date."""
        self._client.headers["x-local-date"] = self._get_current_local_date()

    def init_session(self) -> Dict[str, Any]:
        """
        Menginisiasi sesi tamu dengan memanggil POST /api/guest-reading/session.
        
        Mengekstrak guest_token dan guest_id dari respons, lalu menyimpannya
        ke dalam header 'x-guest-token' dan cookie 'qf_guest_reader'
        pada instance client untuk seluruh request berikutnya.

        :return: Respons dictionary dari server yang berisi data sesi tamu.
        :raises httpx.HTTPError: Jika terjadi kegagalan jaringan atau kode status non-2xx.
        """
        endpoint = "/api/guest-reading/session"
        self._refresh_dynamic_headers()

        logger.info("Mengirim inisialisasi sesi tamu ke %s...", endpoint)

        try:
            # Mengirim POST dengan payload kosong (content-length: 0) sesuai perilaku aplikasi resmi
            response = self._client.post(endpoint, content=b"")
            response.raise_for_status()

            data: Dict[str, Any] = response.json()

            self.guest_id = data.get("guest_id")
            self.guest_token = data.get("guest_token")

            if not self.guest_token:
                raise ValueError("Respons server tidak menyertakan 'guest_token'")

            # Simpan guest_token ke state header dan cookie client
            self._client.headers["x-guest-token"] = self.guest_token
            # Server telah menyetel cookie qf_guest_reader melalui respons Set-Cookie,
            # pastikan nilai tetap tersinkron tanpa konflik domain
            self._client.cookies.set("qf_guest_reader", self.guest_token, domain="api.quarterfull.io", path="/")

            logger.info(
                "Sesi tamu berhasil dibuat! Guest ID: %s, Token Sig: %s...",
                self.guest_id,
                self.guest_token[:24] if self.guest_token else "None",
            )
            return data

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Server mengembalikan status HTTP error %d: %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise
        except httpx.RequestError as exc:
            logger.error("Terjadi kegagalan koneksi saat menghubungi server: %s", exc)
            raise
        except Exception as exc:
            logger.error("Terjadi kesalahan tak terduga saat inisialisasi sesi: %s", exc)
            raise

    def get_headers(self) -> Dict[str, str]:
        """Mengembalikan kamus seluruh header aktif saat ini pada klien HTTP."""
        return dict(self._client.headers)

    @property
    def cookies(self) -> Dict[str, str]:
        """Mengembalikan kamus seluruh cookie aktif saat ini pada klien HTTP."""
        cookie_dict: Dict[str, str] = {}
        for c in self._client.cookies.jar:
            cookie_dict[c.name] = c.value
        return cookie_dict

    def close(self) -> None:
        """Menutup koneksi client HTTP."""
        self._client.close()
        logger.debug("Koneksi ToodatGuestClient telah ditutup.")

    def __enter__(self) -> "ToodatGuestClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


if __name__ == "__main__":
    # Format logging agar mudah dibaca di terminal/konsol
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    print("=" * 70)
    print("DEMO INISIALISASI MODUL SESI TAMU (TOODAT / QUARTERFULL)")
    print("=" * 70)

    # 1. Demonstrasi Client-Side Generator
    print("\n--- 1. Uji Coba Identifier Generator ---")
    demo_device_id = IdentifierGenerator.generate_device_id()
    reading_session_id = IdentifierGenerator.generate_session_id("grs")
    attribution_session_id = IdentifierGenerator.generate_session_id("attr")
    entry_event_id = IdentifierGenerator.generate_session_id("entry")
    sample_chapter_hash = "vJ4openkLPpa7Az1"
    guest_event_id = IdentifierGenerator.generate_guest_event_id(sample_chapter_hash)

    print(f"Device ID (UUID v4)   : {demo_device_id}")
    print(f"Reading Session ID    : {reading_session_id}")
    print(f"Attribution Session ID: {attribution_session_id}")
    print(f"Entry Event ID        : {entry_event_id}")
    print(f"Guest Event ID        : {guest_event_id}")

    # 2. Demonstrasi ToodatGuestClient & Inisialisasi Sesi
    print("\n--- 2. Inisialisasi Client & Panggilan API Session ---")
    with ToodatGuestClient(device_id=demo_device_id) as client:
        print("Header sebelum inisialisasi sesi:")
        for k, v in client.get_headers().items():
            print(f"  {k}: {v}")

        print("\nMemanggil endpoint POST /api/guest-reading/session...")
        session_data = client.init_session()

        print("\n--- 3. Hasil Sesi Tamu yang Diperoleh ---")
        print(f"Status Sukses    : {session_data.get('created')}")
        print(f"Guest ID         : {client.guest_id}")
        print(f"Guest Token      : {client.guest_token}")
        print(f"Expires In (sec) : {session_data.get('expires_in')}")

        print("\nCookie Aktif:")
        for c_name, c_val in client.cookies.items():
            print(f"  {c_name} = {c_val}")

        print("\nHeader Aktif Terkini (memuat x-guest-token):")
        for k, v in client.get_headers().items():
            if k.lower() in ("x-guest-token", "x-device-id", "user-agent", "x-platform"):
                print(f"  {k}: {v}")

    print("\n" + "=" * 70)
    print("UJI COBA SELESAI DENGAN SUKSES")
    print("=" * 70)
