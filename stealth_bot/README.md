# Stealth Bot V2: Panduan & Arsitektur Anti-Fraud Bypass

Direktori mandiri ini berisi sistem bot generasi baru yang dirancang khusus untuk mematahkan seluruh parameter deteksi sistem keamanan Quarterfull / Toodat:

---

## Lapisan Proteksi Anti-Deteksi (Matrix Perbandingan)

| Parameter Deteksi Server | Perilaku Bot Lama (Terjaring) | Arsitektur `stealth_bot` (Lolos) |
| :--- | :--- | :--- |
| **Registration Clustering** | 200+ akun dibuat serentak dalam rentang menit | **Poisson Spaced Signup** (Jeda acak 60–180s per akun, jam aktif lokal). |
| **Target Concentration** | 173 akun langsung membuka 1 novel yang sama | **Camouflage Engine** (65% membaca novel trending lain, 35% target). |
| **Robotic Timing (`signal_reading_pace`)** | Jeda antar bab statis & seragam (pace change = 0%) | **Dynamic WPM Simulator** (Durasi dihitung dari jumlah kata nyata $\pm 20\%$). |
| **Discovery Funnel (`signal_discovery_path`)** | Langsung menembak chapter tanpa riwayat navigasi | **Warm-up Emulation** (Jelajah Beranda -> Section Rising -> Buka Bab). |
| **Mid-Chapter Progress Ticks** | Hanya kirim post-view log instan di akhir | **Incremental Heartbeat** (Mengirim detak progres 25%, 50%, 75%, 100%). |
| **Atribusi AppsFlyer Hilang** | Tidak pernah memanggil atribusi | **Otomatis sync via `POST /api/auth/signup-attribution`**. |
| **Versi Aplikasi Kedaluwarsa** | Mengirim `x-app-version: 3.0.52` | **Tersinkronisasi resmi ke `3.0.55`**. |
| **State Hydration** | Akun kosong tanpa saldo | **Memuat dompet `/api/q/account` dan kategori `/api/v1/categories`**. |

---

## Struktur Berkas

```
stealth_bot/
├── config.py         # Konfigurasi target, WPM, rasio kamuflase, dan header resmi
├── proxy.py          # Pengelola pool proxy HypeProxy/BrightData dengan isolasi negara
├── profile.py        # Generator identitas ber-entropi tinggi & fingerprint Android
├── timing.py         # WPM dynamic engine, fatigue break, dan progres kuadran (25/50/75/100%)
├── camouflage.py     # Penjelajah katalog resmi (Rising/Bestseller) untuk lalu lintas kamuflase
├── client.py         # Klien HTTP native dengan siklus pre-check -> signup -> appsflyer -> hydration
├── worker.py         # Pekerja pembaca organik (Warm-up -> Kamuflase -> Target Novel)
├── email_verifier.py # Integrasi temp.tf untuk verifikasi email otomatis (Gmail Dot Trick/Outlook)
├── scheduler.py      # Penjadwal registrasi terdistribusi (anti-clustering & verifikasi email)
├── main.py           # Antarmuka CLI interaktif berbasis Rich UI
└── akun_stealth.txt  # Penyimpanan akun terisolasi berkualitas tinggi
```

---

## Cara Menjalankan

Masuk ke folder `stealth_bot` dan jalankan:

```bash
cd stealth_bot
py main.py   # Windows
# atau: python3 main.py (Linux/VPS)
```

### Menu Utama:
1. **`[1] Jalankan Member Stealth Reader`**:
   Menjalankan akun terdaftar dengan penyamaran katalog, durasi membaca WPM dinamis, dan telemetri pembayaran royalti resmi.
2. **`[2] Jalankan Guest Stealth Reader`**:
   Menjalankan sesi membaca tamu (*Guest Mode*) tanpa memerlukan akun. Bebas 100% dari resiko *Registration Clustering*, dilengkapi cold-start resmi Android dan pengiriman heartbeat `PUT /api/guest-reading/progress`.
3. **`[3] Registrasi Akun Halus (Spaced / Anti-Clustering Signup + Verified Email)`**:
   Mendaftarkan akun baru secara bertahap dengan jeda acak, sinkronisasi atribusi AppsFlyer, dan opsi **Verifikasi Email Otomatis** menggunakan kolam akun Gmail asli (*Gmail Dot Trick*) dari `temp.tf`.
4. **`[4] Inspeksi Akun`**:
   Melihat daftar akun yang tersimpan di `akun_stealth.txt` beserta status verifikasi emailnya.
