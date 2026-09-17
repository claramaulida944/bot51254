# 🚀 Panduan Lengkap Setup Bot di Windows Server (Dari Nol sampai Terhubung ke Domain)

Panduan ini disusun secara langkah-demi-langkah untuk menjalankan bot automasi **RinaraDev** di **Windows Server (2019 / 2022 / 2025 / Windows 11)**, mengatur autostart 24 jam nonstop, hingga menghubungkannya ke domain Anda dengan HTTPS (SSL) aktif.

---

## 📋 DAFTAR ISI
1. [Langkah 1: Akses RDP ke Windows Server](#langkah-1-akses-rdp-ke-windows-server)
2. [Langkah 2: Install Software Prasyarat (Git & Python)](#langkah-2-install-software-prasyarat-git--python)
3. [Langkah 3: Clone Repositori & Install Dependensi](#langkah-3-clone-repositori--install-dependensi)
4. [Langkah 4: Menghubungkan ke Domain Pribadi](#langkah-4-menghubungkan-ke-domain-pribadi)
   * [Metode A: Cloudflare Tunnel (Sangat Direkomendasikan — Paling Mudah & Otomatis SSL)](#metode-a-menggunakan-cloudflare-tunnel-sangat-direkomendasikan)
   * [Metode B: Direct IP + DNS A Record Tradisional](#metode-b-menggunakan-dns-a-record--caddy-reverse-proxy)
5. [Langkah 5: Menjalankan Bot Otomatis 24 Jam Nonstop (Auto-Start Service)](#langkah-5-menjalankan-bot-24-jam-nonstop-autostart)
6. [Langkah 6: Cara Update Kode dari GitHub di Masa Depan](#langkah-6-cara-update-kode-di-kemudian-hari)

---

## Langkah 1: Akses RDP ke Windows Server

1. Buka aplikasi **Remote Desktop Connection** di komputer / laptop Anda (tekan `Windows + R`, ketik `mstsc`, lalu Enter).
2. Masukkan **IP Address VPS** Anda.
3. Masukkan Username: `Administrator` (atau user yang diberikan penyedia VPS).
4. Masukkan **Password VPS** Anda, lalu klik **Connect**.
5. Anda sekarang berada di dalam tampilan desktop Windows Server.

---

## Langkah 2: Install Software Prasyarat (Git & Python)

Di dalam desktop Windows Server, buka **PowerShell (Run as Administrator)**.

### 1. Install Git for Windows
Ketik perintah berikut di PowerShell atau download manual:
```powershell
winget install --id Git.Git -e --source winget
```
*(Atau download manual file instaler dari: [https://git-scm.com/download/win](https://git-scm.com/download/win))*.

### 2. Install Python (Versi 3.11 atau 3.12)
Ketik perintah berikut di PowerShell:
```powershell
winget install --id Python.Python.3.11 -e --source winget
```
*(Atau download manual dari [https://www.python.org/downloads/](https://www.python.org/downloads/))*.

> ⚠️ **SANGAT PENTING SAAT INSTALL MANUAL PYTHON:**
> Pada halaman pertama installer Python, **WAJIB CENTANG** kotak:
> **☑ Add python.exe to PATH**
> Lalu pilih **Customize installation** -> centang **pip** dan **Install for all users**.

### 3. Verifikasi Instalasi
Tutup jendela PowerShell lama, lalu buka jendela PowerShell baru dan ketik:
```powershell
git --version
python --version
pip --version
```
Jika semuanya mengeluarkan output versi (misal `Python 3.11.x`), instalasi berhasil!

---

## Langkah 3: Clone Repositori & Install Dependensi

1. Di PowerShell Windows Server, buat folder proyek (misal di drive `C:\`):
   ```powershell
   cd C:\
   git clone https://github.com/claramaulida944/bot51254.git
   cd C:\bot51254
   ```

2. Install seluruh modul Python yang dibutuhkan:
   ```powershell
   pip install -r requirements.txt
   ```
   *(Web app juga akan otomatis melengkapi dependensi `fastapi`, `uvicorn`, dan `rich` saat pertama kali dijalankan).*

3. **Uji Coba Menjalankan Web Bot Pertama Kali:**
   ```powershell
   py web_app/run_web.py
   ```
   * Jika muncul banner terminal berwarna biru dengan status `[+] Web URL: http://127.0.0.1:8000`, maka bot sudah sukses berjalan!
   * Buka browser di dalam VPS (Edge/Chrome), ketik `http://127.0.0.1:8000` untuk melihat web klien dan `http://127.0.0.1:8000/admin` untuk portal admin.
   * Tekan `Ctrl + C` di terminal untuk mematikan sementara sementara kita menghubungkan domain.

---

## Langkah 4: Menghubungkan ke Domain Pribadi

Ada 2 metode untuk menghubungkan bot ke domain Anda. **Metode A (Cloudflare Tunnel) adalah yang paling mudah, aman, dan tanpa biaya SSL.**

---

### METODE A: Menggunakan Cloudflare Tunnel (Sangat Direkomendasikan)
* **Kelebihan:** 
  * ❌ Tidak perlu setting port forwarding / firewall Windows.
  * ❌ Tidak perlu beli/setting sertifikat SSL manual (otomatis dapat gembok hijau HTTPS).
  * 🛡️ IP VPS Anda terlindungi dari serangan DDoS karena disembunyikan oleh Cloudflare.
  * ⚡ Gratis 100%.

#### Cara Setup Cloudflare Tunnel:
1. Pastikan domain Anda sudah terhubung ke **Cloudflare** (Name Server domain sudah diarahkan ke Cloudflare).
2. Buka dashboard Cloudflare: [https://dash.cloudflare.com/](https://dash.cloudflare.com/)
3. Pada menu samping kiri, klik **Zero Trust** -> lalu pilih **Networks** -> **Tunnels**.
4. Klik tombol **Add a tunnel** (atau **Create a tunnel**).
5. Beri nama tunnel, misal: `bot-rinaradev`, lalu klik **Save tunnel**.
6. Pada bagian **Choose your environment**, pilih **Windows**.
7. Cloudflare akan memberikan 1 baris perintah PowerShell untuk menginstall agen Cloudflared. Contoh:
   ```powershell
   winget install --id Cloudflare.cloudflared
   cloudflared.exe service install eyJhIjoiY... (token panjang Anda)
   ```
8. Buka **PowerShell (Run as Administrator)** di Windows Server Anda, lalu **Paste** perintah tersebut dan tekan **Enter**.
9. Status tunnel di dashboard Cloudflare akan berubah menjadi **Active (Healthy)** berwarna hijau. Klik **Next**.
10. Pada tab **Public Hostname**:
    * **Subdomain:** misal `bot` (jika ingin diakses lewat `bot.domainanda.com`) atau kosongkan jika ingin domain utama.
    * **Domain:** Pilih nama domain Anda dari dropdown (contoh: `domainanda.com`).
    * **Type:** Pilih **`HTTP`**
    * **URL:** Ketik **`localhost:8000`**
11. Klik **Save hostname**.

🎉 **Selesai!** Domain Anda sekarang sudah langsung terhubung dengan HTTPS: `https://bot.domainanda.com`!

---

### METODE B: Menggunakan DNS A Record + Caddy Reverse Proxy

Jika Anda tidak memakai Cloudflare Tunnel dan ingin menghubungkan langsung via IP VPS:

1. **Buka Port di Windows Firewall:**
   Jalankan perintah ini di PowerShell Administrator untuk membuka port web:
   ```powershell
   New-NetFirewallRule -DisplayName "Web Bot HTTP" -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow
   New-NetFirewallRule -DisplayName "Web Bot HTTPS" -Direction Inbound -LocalPort 443 -Protocol TCP -Action Allow
   New-NetFirewallRule -DisplayName "Web Bot App" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
   ```

2. **Arahkan DNS A Record di Penyedia Domain (Cloudflare / Namecheap / Domainesia dll):**
   * Tipe: `A`
   * Name: `bot` (atau `@` untuk root domain)
   * Target / Value: `IP_PUBLIK_VPS_ANDA`
   * TTL: `Auto`

3. **Gunakan Caddy Server (Web Server Otomatis SSL Paling Praktis di Windows):**
   * Download Caddy Windows: [https://caddyserver.com/download](https://caddyserver.com/download)
   * Ekstrak file `caddy.exe` ke folder `C:\bot51254\`.
   * Buat file teks bernama `Caddyfile` (tanpa ekstensi `.txt`) di folder `C:\bot51254\`:
     ```text
     bot.domainanda.com {
         reverse_proxy localhost:8000
     }
     ```
   * Jalankan Caddy di command prompt:
     ```cmd
     caddy run
     ```
   * Caddy akan otomatis membuat sertifikat SSL Let's Encrypt resmi dan mengarahkan domain Anda ke port 8000.

---

## Langkah 5: Menjalankan Bot 24 Jam Nonstop (Auto-Start)

Agar bot tetap menyala di background meskipun Anda logout dari Remote Desktop (RDP) atau jika VPS di-restart:

### Opsi 1: Menggunakan Windows Task Scheduler (Paling Praktis, Bawaan Windows)

1. Buat file batch launcher bernama `start_bot.bat` di dalam folder `C:\bot51254\`:
   ```bat
   @echo off
   cd /d C:\bot51254
   :loop
   echo Starting RinaraDev Bot Web Server...
   py web_app/run_web.py
   echo Server terhenti, merestart dalam 5 detik...
   timeout /t 5
   goto loop
   ```

2. Buka **Task Scheduler** di Windows Server (`Windows + R` -> ketik `taskschd.msc`).
3. Di panel kanan, klik **Create Task...** (bukan Create Basic Task).
4. **Tab General:**
   * Name: `RinaraDevBotService`
   * Centang: **Run whether user is logged on or not**
   * Centang: **Run with highest privileges**
5. **Tab Triggers:**
   * Klik **New...**
   * Begin the task: Pilih **At startup**
   * Klik **OK**.
6. **Tab Actions:**
   * Klik **New...**
   * Action: **Start a program**
   * Program/script: `C:\bot51254\start_bot.bat`
   * Start in: `C:\bot51254\`
   * Klik **OK**.
7. **Tab Settings:**
   * Centang: **If the task fails, restart every: 1 minute**
   * Hapus centang pada: *Stop the task if it runs longer than 3 days*.
8. Klik **OK** dan masukkan password administrator Windows Anda.

Sekarang, bot akan langsung otomatis menyala saat Windows Server booting dan akan terus berjalan di background tanpa perlu membuka jendela CMD!

---

## Langkah 6: Cara Update Kode di Kemudian Hari

Jika ada fitur baru atau perbaikan kode yang Anda push ke GitHub:

1. Buka PowerShell di Windows Server:
   ```powershell
   cd C:\bot51254
   git pull origin main
   ```
2. Restart bot:
   * Jika menggunakan Task Scheduler, cukup buka Task Scheduler -> klik kanan pada task `RinaraDevBotService` -> pilih **Restart**.
   * Atau bunuh proses Python lama:
     ```powershell
     Stop-Process -Name python -Force
     ```
     Task Scheduler akan otomatis menyalakan ulang versi kodenya yang terbaru dalam hitungan detik.

---

## 🔒 Informasi Keamanan Penting
* **Master PIN Admin Default:** `51254` (dapat diubah di `server.py`).
* **Database Token:** Tersimpan secara lokal di `web_app/tokens.json`.
* **Database Transaksi:** Tersimpan di `web_app/payments.json`.
* Jangan lupa untuk membuat backup berkala terhadap file `tokens.json` jika sudah banyak token klien yang beredar.
