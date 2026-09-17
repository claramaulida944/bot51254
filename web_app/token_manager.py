"""
Modul Manajemen Saldo & Akses Token (Token Manager)
Mendukung sistem kredit token dengan pemotongan per-item sukses:
- 1 Akun Valid Readers = Rp 500
- 10 Akun Guest = Rp 500 (Rp 50 / akun guest)
- Pemotongan real-time saat log sukses tercatat.
- Direct CTA ke WhatsApp Owner (wa.me/6287734343023) untuk pembelian & topup.
"""

import json
import os
import secrets
import string
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OWNER_WHATSAPP = "6287734343023"
RATE_VALID_READER = 500  # Rp 500 per 1 akun valid reader sukses
RATE_GUEST_READER = 50   # Rp 50 per 1 akun guest reader (10 guest = Rp 500)
RATE_ACCOUNT_GENERATOR = 50 # Rp 50 per akun yang di-generate pengguna
RATE_LIKE = 100          # Opsional: Rp 100 per like sukses
RATE_BOOKMARK = 100      # Opsional: Rp 100 per bookmark sukses
RATE_FOLLOW = 100        # Opsional: Rp 100 per follow sukses

DEFAULT_PACKAGES = [
    {
        "id": "pkg_10k",
        "name": "Paket Saldo 10K",
        "price": 10000,
        "features": [
            "20 sesi akun member resmi atau",
            "200 sesi pembaca tamu",
            "Otomatis suka & simpan novel",
            "Saldo utuh tanpa masa kadaluarsa",
        ],
        "popular": False,
    },
    {
        "id": "pkg_20k",
        "name": "Paket Saldo 20K",
        "price": 20000,
        "features": [
            "40 sesi akun member resmi atau",
            "400 sesi pembaca tamu",
            "Full engagement (baca + suka + ikuti)",
            "Dukungan multi bab (hingga 30 bab)",
            "Saldo utuh tanpa masa kadaluarsa",
        ],
        "popular": True,
    },
    {
        "id": "pkg_50k",
        "name": "Paket Saldo 50K",
        "price": 50000,
        "features": [
            "100 sesi akun member resmi atau",
            "1.000 sesi pembaca tamu",
            "Dukungan untuk beberapa novel target",
            "Prioritas pemrosesan antrean sesi",
            "Saldo utuh tanpa masa kadaluarsa",
        ],
        "popular": False,
    },
]

TOKEN_PRESETS = [
    {"label": "Paket Starter", "amount": 10000, "description": "Saldo Rp 10.000 (~20 Akun Valid / 200 Guest)"},
    {"label": "Paket Medium", "amount": 20000, "description": "Saldo Rp 20.000 (~40 Akun Valid / 400 Guest)"},
    {"label": "Paket Pro", "amount": 50000, "description": "Saldo Rp 50.000 (~100 Akun Valid / 1000 Guest)"},
]

DATA_FILE = Path(__file__).parent / "tokens.json"


class TokenManager:
    """Manajer database token saldo dengan persistensi file JSON thread-safe."""

    _lock = threading.RLock()

    @classmethod
    def _load_data(cls) -> Dict[str, Any]:
        with cls._lock:
            if not DATA_FILE.exists():
                initial = {
                    "config": {
                        "rates": {
                            "valid_reader": RATE_VALID_READER,
                            "guest_reader": RATE_GUEST_READER,
                            "like": RATE_LIKE,
                            "bookmark": RATE_BOOKMARK,
                            "follow": RATE_FOLLOW,
                        },
                        "packages": DEFAULT_PACKAGES,
                        "owner_wa": OWNER_WHATSAPP,
                    },
                    "tokens": {},
                }
                cls._save_data(initial)
                return initial
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {"config": {}, "tokens": {}}

    @classmethod
    def get_pricing_config(cls) -> Dict[str, Any]:
        """Mengambil konfigurasi tarif per sesi dan paket harga saat ini secara thread-safe."""
        with cls._lock:
            db = cls._load_data()
            cfg = db.get("config", {})
            rates = cfg.get("rates", {})
            packages = cfg.get("packages") or DEFAULT_PACKAGES
            owner_wa = cfg.get("owner_wa", OWNER_WHATSAPP)
            return {
                "rates": {
                    "valid_reader": int(rates.get("valid_reader", RATE_VALID_READER)),
                    "guest_reader": int(rates.get("guest_reader", RATE_GUEST_READER)),
                    "account_generator": int(rates.get("account_generator", RATE_ACCOUNT_GENERATOR)),
                    "like": int(rates.get("like", RATE_LIKE)),
                    "bookmark": int(rates.get("bookmark", RATE_BOOKMARK)),
                    "follow": int(rates.get("follow", RATE_FOLLOW)),
                },
                "packages": packages,
                "owner_wa": str(owner_wa),
            }

    @classmethod
    def update_pricing_config(
        cls,
        rates: Optional[Dict[str, Any]] = None,
        packages: Optional[List[Dict[str, Any]]] = None,
        owner_wa: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Memperbarui konfigurasi tarif dan paket harga secara dinamis oleh Admin."""
        with cls._lock:
            db = cls._load_data()
            cfg = db.setdefault("config", {})
            if rates is not None:
                current_rates = cfg.setdefault("rates", {})
                for k, v in rates.items():
                    try:
                        current_rates[k] = max(1, int(v))
                    except (ValueError, TypeError):
                        pass
            if packages is not None:
                cleaned_pkgs = []
                for p in packages:
                    if isinstance(p, dict) and "price" in p:
                        try:
                            feat_raw = p.get("features", [])
                            if isinstance(feat_raw, list):
                                feat_list = [str(f).strip() for f in feat_raw if str(f).strip()]
                            else:
                                feat_list = [s.strip() for s in str(feat_raw).replace("\r", "").split("\n") if s.strip()]
                            is_pop = bool(p.get("popular", p.get("is_featured", False)))
                            cleaned_pkgs.append({
                                "id": str(p.get("id") or f"pkg_{p.get('price')}"),
                                "name": str(p.get("name", f"Paket {p.get('price')}")).strip(),
                                "price": int(p.get("price")),
                                "features": feat_list,
                                "popular": is_pop,
                                "is_featured": is_pop,
                            })
                        except (ValueError, TypeError):
                            pass
                if cleaned_pkgs:
                    cfg["packages"] = cleaned_pkgs
            if owner_wa is not None:
                cfg["owner_wa"] = str(owner_wa).strip()

            cls._save_data(db)
            return cls.get_pricing_config()

    @classmethod
    def get_rate(cls, item_type: str) -> int:
        """Mengambil tarif aktif saat ini berdasarkan jenis aksi bot."""
        pricing = cls.get_pricing_config()
        rates = pricing.get("rates", {})
        if item_type in ("valid_reader", "member_reader", "member_read"):
            return rates.get("valid_reader", RATE_VALID_READER)
        elif item_type in ("guest_reader", "guest_read"):
            return rates.get("guest_reader", RATE_GUEST_READER)
        return rates.get(item_type, 50)

    @classmethod
    def _save_data(cls, data: Dict[str, Any]) -> None:
        with cls._lock:
            temp_file = DATA_FILE.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_file.replace(DATA_FILE)

    @classmethod
    def generate_token_code(cls) -> str:
        """Membuat format token unik: TK-XXXX-YYYY-ZZZZ"""
        chars = string.ascii_uppercase + string.digits
        p1 = "".join(secrets.choice(chars) for _ in range(4))
        p2 = "".join(secrets.choice(chars) for _ in range(4))
        p3 = "".join(secrets.choice(chars) for _ in range(4))
        return f"TK-{p1}-{p2}-{p3}"

    @classmethod
    def create_token(
        cls,
        initial_balance: int = 10000,
        label: str = "Client Token",
        expiry_days: Optional[int] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Membuat token akses baru dengan saldo Rupiah."""
        token_code = cls.generate_token_code()
        now_iso = datetime.now(timezone.utc).isoformat()
        exp_iso = None
        if expiry_days and expiry_days > 0:
            exp_ts = time.time() + (expiry_days * 86400)
            exp_iso = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()

        token_data = {
            "token": token_code,
            "label": label.strip() or "Client Token",
            "initial_balance": initial_balance,
            "current_balance": initial_balance,
            "total_spent": 0,
            "created_at": now_iso,
            "expires_at": exp_iso,
            "status": "active",  # active, suspended, revoked
            "notes": notes,
            "history": [
                {
                    "time": now_iso,
                    "action": "CREATE",
                    "amount": initial_balance,
                    "balance_after": initial_balance,
                    "note": f"Inisialisasi token saldo Rp {initial_balance:,}",
                }
            ],
            "stats": {
                "valid_readers_count": 0,
                "guest_readers_count": 0,
                "likes_count": 0,
                "bookmarks_count": 0,
                "follows_count": 0,
            },
        }

        with cls._lock:
            db = cls._load_data()
            db.setdefault("tokens", {})[token_code] = token_data
            cls._save_data(db)

        return token_data

    @classmethod
    def get_token(cls, token_code: str) -> Optional[Dict[str, Any]]:
        """Mengambil data token berdasarkan token_code."""
        if not token_code:
            return None
        clean_code = token_code.strip().upper()
        db = cls._load_data()
        return db.get("tokens", {}).get(clean_code)

    @classmethod
    def list_tokens(cls) -> List[Dict[str, Any]]:
        """Mendapatkan daftar seluruh token yang pernah dibuat (diurutkan dari yang terbaru)."""
        db = cls._load_data()
        tokens_map = db.get("tokens", {})
        token_list = list(tokens_map.values())
        token_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return token_list

    @classmethod
    def verify_token(cls, token_code: str, min_required_balance: int = 50) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Memverifikasi apakah token valid, aktif, belum kedaluwarsa, dan memiliki saldo minimum.
        Mengembalikan (is_valid, message, token_data).
        """
        token = cls.get_token(token_code)
        if not token:
            return False, "Token tidak ditemukan atau salah. Silakan periksa kembali token Anda.", None

        if token.get("status") == "revoked":
            return False, "Token ini telah dicabut / dinonaktifkan oleh Owner.", token

        if token.get("status") == "suspended":
            return False, "Token ini sedang ditangguhkan. Silakan hubungi Owner.", token

        # Periksa tanggal kedaluwarsa jika disetel
        exp_iso = token.get("expires_at")
        if exp_iso:
            try:
                exp_dt = datetime.fromisoformat(exp_iso)
                if datetime.now(timezone.utc) > exp_dt:
                    return False, f"Token telah kedaluwarsa pada {exp_iso[:10]}. Silakan top up ke Owner.", token
            except Exception:
                pass

        curr_bal = token.get("current_balance", 0)
        if curr_bal < min_required_balance:
            msg = (
                f"Saldo token tidak mencukupi (Sisa: Rp {curr_bal:,}). "
                f"Minimal dibutuhkan Rp {min_required_balance:,}. Silakan hubungi Owner via WhatsApp untuk top up."
            )
            return False, msg, token

        return True, "Token valid & aktif.", token

    @classmethod
    def deduct_balance(
        cls,
        token_code: str,
        item_type: str = "valid_reader",
        quantity: int = 1,
        note: str = "",
    ) -> Tuple[bool, int, str]:
        """
        Memotong saldo token secara real-time saat log sukses.
        item_type: 'valid_reader' (Rp 500), 'guest_reader' (Rp 50), 'like' (Rp 100), etc.
        Mengembalikan (success, sisa_saldo, pesan).
        """
        with cls._lock:
            db = cls._load_data()
            clean_code = token_code.strip().upper()
            tokens_map = db.get("tokens", {})
            token = tokens_map.get(clean_code)
            if not token:
                return False, 0, "Token tidak valid"

            rate_per_unit = cls.get_rate(item_type)
            total_deduct = rate_per_unit * quantity

            curr_bal = token.get("current_balance", 0)
            if curr_bal < total_deduct:
                return False, curr_bal, f"Saldo tidak mencukupi (Dibutuhkan: Rp {total_deduct:,}, Sisa: Rp {curr_bal:,})"

            token["current_balance"] = curr_bal - total_deduct
            token["total_spent"] = token.get("total_spent", 0) + total_deduct

            # Update stats
            stat_key = f"{item_type}s_count"
            if stat_key in token.setdefault("stats", {}):
                token["stats"][stat_key] += quantity

            now_iso = datetime.now(timezone.utc).isoformat()
            token.setdefault("history", []).append({
                "time": now_iso,
                "action": "DEDUCT",
                "item_type": item_type,
                "quantity": quantity,
                "amount": total_deduct,
                "balance_after": token["current_balance"],
                "note": note or f"Pemotongan sukses {quantity}x {item_type} (-Rp {total_deduct:,})",
            })

            # Batasi riwayat maksimal 200 entri terakhir
            if len(token["history"]) > 200:
                token["history"] = token["history"][-200:]

            cls._save_data(db)
            return True, token["current_balance"], f"Berhasil memotong Rp {total_deduct:,}. Sisa saldo: Rp {token['current_balance']:,}"

    @classmethod
    def topup_balance(cls, token_code: str, amount: int, note: str = "Top up saldo via Owner") -> Tuple[bool, int, str]:
        """Menambah saldo token (Top Up) oleh Owner."""
        with cls._lock:
            db = cls._load_data()
            clean_code = token_code.strip().upper()
            token = db.get("tokens", {}).get(clean_code)
            if not token:
                return False, 0, "Token tidak ditemukan"

            token["current_balance"] = token.get("current_balance", 0) + amount
            now_iso = datetime.now(timezone.utc).isoformat()
            token.setdefault("history", []).append({
                "time": now_iso,
                "action": "TOPUP",
                "amount": amount,
                "balance_after": token["current_balance"],
                "note": note,
            })
            cls._save_data(db)
            return True, token["current_balance"], f"Berhasil top up Rp {amount:,}. Saldo baru: Rp {token['current_balance']:,}"

    @classmethod
    def update_token_status(cls, token_code: str, status: str) -> bool:
        """Mengubah status token (active / suspended / revoked)."""
        clean_code = token_code.strip().upper()
        if status not in ("active", "suspended", "revoked"):
            return False
        with cls._lock:
            db = cls._load_data()
            token = db.get("tokens", {}).get(clean_code)
            if not token:
                return False
            token["status"] = status
            cls._save_data(db)
            return True

    # =========================================================================
    # TRANSAKSI PAYMENT GATEWAY WIJAYAPAY
    # =========================================================================
    PAYMENTS_FILE = Path(__file__).parent / "payments.json"

    @classmethod
    def _load_payments(cls) -> Dict[str, Any]:
        with cls._lock:
            if not cls.PAYMENTS_FILE.exists():
                init = {"payments": {}}
                cls._save_payments(init)
                return init
            try:
                with open(cls.PAYMENTS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {"payments": {}}

    @classmethod
    def _save_payments(cls, data: Dict[str, Any]) -> None:
        with cls._lock:
            temp = cls.PAYMENTS_FILE.with_suffix(".tmp")
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp.replace(cls.PAYMENTS_FILE)

    @classmethod
    def record_payment(
        cls,
        ref_id: str,
        order_type: str,
        nominal: int,
        payment_code: str,
        gateway_data: Dict[str, Any],
        token_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mencatat order transaksi baru dari WijayaPay."""
        with cls._lock:
            db = cls._load_payments()
            record = {
                "ref_id": ref_id,
                "order_type": order_type,  # "new_token" or "topup"
                "nominal": int(nominal),
                "payment_code": payment_code,
                "target_token": (token_code or "").strip().upper(),
                "generated_token": None,
                "status": "pending",
                "trx_reference": gateway_data.get("trx_reference") or gateway_data.get("trx_id") or gateway_data.get("reference"),
                "total_bayar": gateway_data.get("total_bayar") or nominal,
                "qr_link": gateway_data.get("qr_link") or gateway_data.get("qr_url") or gateway_data.get("qr_image"),
                "qr_string": gateway_data.get("qr_string") or gateway_data.get("qr_content"),
                "nomor_va": gateway_data.get("nomor_va") or gateway_data.get("va_number") or gateway_data.get("nomor_pembayaran") or gateway_data.get("pay_code"),
                "expired": gateway_data.get("expired") or gateway_data.get("expired_time") or gateway_data.get("expired_at"),
                "tutorial": gateway_data.get("tutorial_pembayaran") or gateway_data.get("tutorial"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "paid_at": None,
            }
            db.setdefault("payments", {})[ref_id] = record
            cls._save_payments(db)
            return record

    @classmethod
    def get_payment(cls, ref_id: str) -> Optional[Dict[str, Any]]:
        """Mengambil data transaksi pembayaran berdasarkan ref_id."""
        with cls._lock:
            db = cls._load_payments()
            return db.get("payments", {}).get(ref_id)

    @classmethod
    def list_payments(cls, limit: int = 50) -> List[Dict[str, Any]]:
        """Menampilkan riwayat transaksi pembayaran terbaru."""
        with cls._lock:
            db = cls._load_payments()
            items = list(db.get("payments", {}).values())
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return items[:limit]

    @classmethod
    def update_payment_status(cls, ref_id: str, status: str) -> bool:
        """Mengubah status pembayaran secara manual/gateway (misal expired)."""
        with cls._lock:
            db = cls._load_payments()
            record = db.get("payments", {}).get(ref_id)
            if not record:
                return False
            record["status"] = status
            cls._save_payments(db)
            return True

    @classmethod
    def fulfill_payment(cls, ref_id: str, trx_reference: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Memproses pemenuhan pesanan saat pembayaran telah terkonfirmasi lunas (PAID).
        Idempotent: Aman dipanggil berulang kali dari webhook & polling.
        """
        with cls._lock:
            db = cls._load_payments()
            record = db.get("payments", {}).get(ref_id)
            if not record:
                return False, None, "Transaksi tidak ditemukan"

            # Jika sudah pernah diproses lunas sebelumnya, kembalikan record langsung
            if record.get("status") == "paid":
                return True, record, "Transaksi sudah diproses sebelumnya (Idempotent)"

            nominal = record.get("nominal", 0)
            order_type = record.get("order_type", "new_token")
            now_iso = datetime.now(timezone.utc).isoformat()

            if trx_reference:
                record["trx_reference"] = trx_reference

            # 1. Kasus: Pembelian Token Baru
            if order_type == "new_token":
                pkg_name = "Token Akses"
                try:
                    cfg = cls.get_pricing_config()
                    for p in cfg.get("packages", []):
                        if int(p.get("price", 0)) == int(nominal):
                            pkg_name = str(p.get("name", "Token Akses")).strip()
                            break
                except Exception:
                    pass

                token_obj = cls.create_token(
                    initial_balance=nominal,
                    label=f"{pkg_name} ({ref_id})",
                    notes=f"Dibeli via WijayaPay ({record.get('payment_code')}) Ref: {ref_id}",
                )
                record["generated_token"] = token_obj.get("token")
                record["status"] = "paid"
                record["paid_at"] = now_iso
                msg = f"Token baru berhasil diterbitkan: {token_obj.get('token')} (Saldo: Rp {nominal:,})"

            # 2. Kasus: Top-up Saldo Token yang Ada
            elif order_type == "topup":
                target_token = record.get("target_token")
                if not target_token:
                    return False, record, "Token target untuk topup tidak ditemukan di data transaksi"

                ok, new_bal, err = cls.topup_balance(
                    token_code=target_token,
                    amount=nominal,
                    note=f"Topup Otomatis WijayaPay ({record.get('payment_code')}) Ref: {ref_id}",
                )
                if not ok:
                    return False, record, f"Gagal topup saldo: {err}"

                record["status"] = "paid"
                record["paid_at"] = now_iso
                msg = f"Saldo token {target_token} berhasil ditambah Rp {nominal:,}. Saldo baru: Rp {new_bal:,}"

            else:
                record["status"] = "paid"
                record["paid_at"] = now_iso
                msg = "Status pembayaran berhasil diperbarui"

            cls._save_payments(db)
            return True, record, msg

    @classmethod
    def get_whatsapp_url(cls, token_code: Optional[str] = None, nominal: int = 20000) -> str:
        """Membuat link WhatsApp langsung dengan prefilled message untuk pembelian/topup."""
        phone = OWNER_WHATSAPP
        if token_code:
            text = f"Halo Admin, saya mau top up saldo token RinaraDev Automation: *{token_code}* senilai Rp {nominal:,}."
        else:
            text = f"Halo Admin, saya ingin membeli token akses baru RinaraDev Automation paket Rp {nominal:,}."
        import urllib.parse
        encoded = urllib.parse.quote(text)
        return f"https://wa.me/{phone}?text={encoded}"
