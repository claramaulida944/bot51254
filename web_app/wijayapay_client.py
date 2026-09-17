"""
Klien Integrasi Resmi WijayaPay Payment Gateway untuk RinaraDev Automation.
Menangani pembuatan transaksi QRIS/VA/Retail, pengecekan status real-time,
dan verifikasi webhook callback bersignature MD5.
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("WijayaPayClient")

# =============================================================================
# KREDENSIAL MERCHANT WIJAYAPAY
# =============================================================================
DEFAULT_CODE_MERCHANT = "WP6aa5ec5543584"
DEFAULT_API_KEY = "7sabi5kjuoae1aefm1lbtaqdbey72i"
GATEWAY_BASE_URL = "https://gateway.wijayapay.com"


class WijayaPayClient:
    """Klien API Gateway WijayaPay."""

    def __init__(
        self,
        code_merchant: str = DEFAULT_CODE_MERCHANT,
        api_key: str = DEFAULT_API_KEY,
        base_url: str = GATEWAY_BASE_URL,
        timeout: float = 20.0,
    ):
        self.code_merchant = code_merchant
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate_signature(self, ref_id: str) -> str:
        """
        Menghasilkan X-Signature sesuai standar dokumentasi WijayaPay:
        md5(code_merchant + api_key + ref_id) tanpa spasi.
        """
        raw = f"{self.code_merchant}{self.api_key}{ref_id}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def verify_signature(self, ref_id: str, incoming_signature: str) -> bool:
        """Memvalidasi X-Callback-Signature dari webhook WijayaPay."""
        if not incoming_signature:
            return False
        expected = self.generate_signature(ref_id)
        return incoming_signature.strip().lower() == expected.lower()

    def verify_callback_signature(self, ref_id: str, incoming_signature: str) -> bool:
        """Alias untuk verify_signature."""
        return self.verify_signature(ref_id, incoming_signature)

    def get_payment_methods(self) -> List[Dict[str, Any]]:
        """
        [Endpoint 1] Mengambil daftar metode pembayaran aktif.
        GET /api/get-payment?code_merchant=...&api_key=...
        """
        url = f"{self.base_url}/api/get-payment"
        params = {
            "code_merchant": self.code_merchant,
            "api_key": self.api_key,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    url,
                    params=params,
                    headers={"User-Agent": "RinaraDev/3.0 (WijayaPay Integration)"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", [])
                logger.error(f"[WijayaPay] Gagal ambil channels: HTTP {resp.status_code} - {resp.text}")
                return []
        except Exception as exc:
            logger.error(f"[WijayaPay] Exception get_payment_methods: {exc}")
            return []

    def create_transaction(
        self,
        ref_id: str,
        nominal: int,
        payment_code: str = "QRIS",
        callback_url: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        [Endpoint 2] Membuat transaksi pembayaran baru (QRIS / VA / Retail).
        POST /api/transaction/create
        """
        url = f"{self.base_url}/api/transaction/create"
        signature = self.generate_signature(ref_id)

        form_data = {
            "code_merchant": self.code_merchant,
            "api_key": self.api_key,
            "ref_id": ref_id,
            "code_payment": payment_code,
            "nominal": int(nominal),
        }
        if callback_url:
            form_data["callback_url"] = callback_url

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "RinaraDev/3.0 (WijayaPay Integration)",
            "X-Signature": signature,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, data=form_data, headers=headers)
                try:
                    res_json = resp.json()
                except Exception:
                    res_json = {"raw": resp.text}

                if resp.status_code in (200, 201) and res_json.get("success"):
                    return {
                        "ok": True,
                        "data": res_json.get("data", {}),
                        "status_code": resp.status_code,
                    }
                else:
                    err_msg = res_json.get("message") or res_json.get("error") or resp.text
                    logger.warning(f"[WijayaPay] Gagal create transaction: {err_msg}")
                    return {
                        "ok": False,
                        "error": str(err_msg),
                        "status_code": resp.status_code,
                        "raw": res_json,
                    }
        except Exception as exc:
            logger.error(f"[WijayaPay] Exception create_transaction: {exc}")
            return {"ok": False, "error": str(exc)}

    def check_status(self, ref_id: str) -> Dict[str, Any]:
        """
        [Endpoint 3] Memeriksa status pembayaran transaksi.
        GET /api/get-status?code_merchant=...&api_key=...&ref_id=...
        """
        url = f"{self.base_url}/api/get-status"
        params = {
            "code_merchant": self.code_merchant,
            "api_key": self.api_key,
            "ref_id": ref_id,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    url,
                    params=params,
                    headers={"User-Agent": "RinaraDev/3.0 (WijayaPay Integration)"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    status_raw = str(data.get("data", {}).get("status_pembayaran", "")).lower()
                    return {
                        "ok": True,
                        "status": status_raw,  # "pending", "paid", "expired"
                        "is_paid": status_raw == "paid",
                        "data": data.get("data", {}),
                    }
                return {"ok": False, "status": "unknown", "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as exc:
            logger.error(f"[WijayaPay] Exception check_status: {exc}")
            return {"ok": False, "status": "unknown", "error": str(exc)}


# Instance default klien WijayaPay
default_wijayapay_client = WijayaPayClient()
