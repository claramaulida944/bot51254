# 🐧 Panduan Lengkap Setup Bot di VPS Ubuntu (20.04 / 22.04 / 24.04 LTS)

Panduan langkah-demi-langkah dari nol untuk memasang bot automasi **RinaraDev**, menjalankannya 24 jam nonstop di latar belakang (*background service* dengan auto-restart), hingga menghubungkannya ke domain dengan HTTPS (SSL gratis).

---

## 📋 DAFTAR ISI
1. [Langkah 1: Login SSH ke VPS Ubuntu](#langkah-1-login-ssh-ke-vps-ubuntu)
2. [Langkah 2: Update Sistem & Install Software Prasyarat](#langkah-2-update-sistem--install-software-prasyarat)
3. [Langkah 3: Clone / Upload Repositori Proyek](#langkah-3-clone--upload-repositori-proyek)
4. [Langkah 4: Buat Virtual Environment & Install Dependensi](#langkah-4-buat-virtual-environment--install-dependensi)
5. [Langkah 5: Uji Coba Jalankan Server](#langkah-5-uji-coba-jalankan-server)
6. [Langkah 6: Pasang Systemd Service (Auto-Start 24 Jam Nonstop)](#langkah-6-pasang-systemd-service-auto-start-24-jam-nonstop)
7. [Langkah 7: Menghubungkan ke Domain & Pasang SSL (HTTPS)](#langkah-7-menghubungkan-ke-domain--pasang-ssl-https)
   - [Opsi A: Cloudflare Tunnel (Paling Mudah, Tanpa Buka Port, Otomatis SSL)](#opsi-a-cloudflare-tunnel-sangat-direkomendasikan)
   - [Opsi B: Nginx + Certbot Let's Encrypt (Tradisional)](#opsi-b-nginx--certbot-lets-encrypt-tradisional)
8. [Langkah 8: Perintah Berguna untuk Manajemen Bot](#langkah-8-perintah-berguna-untuk-manajemen-bot)

---

## Langkah 1: Login SSH ke VPS Ubuntu

Buka **Terminal** (di Mac/Linux) atau **PowerShell / Git Bash / PuTTY** (di Windows), lalu ketik:

```bash
ssh root@IP_VPS_ANDA
```
*Contoh:* `ssh root@103.187.145.22`

Masukkan password VPS Anda saat diminta. Jika berhasil, Anda akan melihat tampilan prompt terminal Ubuntu: `root@vps:~#`.

---

## Langkah 2: Update Sistem & Install Software Prasyarat

Jalankan perintah berikut untuk memperbarui paket sistem dan menginstal Python 3, Pip, Virtualenv, dan Git:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl ufw
```

Verifikasi instalasi:
```bash
python3 --version
git --version
```
*(Pastikan versi Python 3.10, 3.11, atau 3.12).*

---

## Langkah 3: Clone / Upload Repositori Proyek

Jika Anda login sebagai user `ubuntu` (atau user biasa lainnya), **cukup gunakan direktori home Anda sendiri (`~`)**, tidak perlu masuk ke `/root`:

```bash
# Pastikan berada di home folder Anda (/home/ubuntu)
cd ~

# Clone repositori Anda ke folder 'bot'
git clone URL_REPOSITORY_ANDA bot
cd bot
```

> 💡 **Catatan:** Jika Anda ingin masuk sebagai user `root`, ketik `sudo su` terlebih dahulu. Namun, menjalankan aplikasi di `/home/ubuntu/bot` sebagai user `ubuntu` jauh lebih aman dan direkomendasikan.

---

## Langkah 4: Buat Virtual Environment & Install Dependensi

Sangat disarankan menggunakan virtual environment agar paket Python rapi dan tidak bentrok dengan sistem OS:

```bash
# 1. Buat virtual environment bernama 'venv'
python3 -m venv venv

# 2. Aktifkan virtual environment
source venv/bin/activate

# 3. Upgrade pip
pip install --upgrade pip

# 4. Install seluruh dependensi
pip install -r requirements.txt
```

Jika `requirements.txt` belum lengkap di repo Anda, jalankan perintah manual ini:
```bash
pip install fastapi "uvicorn[standard]" pydantic jinja2 python-multipart "httpx[http2]" rich faker
```

---

## Langkah 5: Uji Coba Jalankan Server

Jalankan server sementara untuk memastikan semuanya berjalan lancar tanpa error:

```bash
uvicorn server:app --app-dir web_app --host 0.0.0.0 --port 8000
```

Buka browser di laptop/HP Anda dan akses:
`http://IP_VPS_ANDA:8000`

Jika dashboard RinaraDev terbuka, artinya bot web berjalan sempurna!
Tekan `Ctrl + C` di terminal untuk mematikan uji coba sementara.

---

## Langkah 6: Pasang Systemd Service (Auto-Start 24 Jam Nonstop)

Agar aplikasi otomatis menyala saat VPS reboot dan terus berjalan di background meskipun terminal SSH ditutup, gunakan **systemd**:

### 1. Buat file konfigurasi service:
```bash
sudo nano /etc/systemd/system/rinaradev.service
```

### 2. Tempelkan (Paste) konfigurasi berikut:
```ini
[Unit]
Description=RinaraDev Automation Web Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/bot
ExecStart=/home/ubuntu/bot/venv/bin/uvicorn server:app --app-dir web_app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
*(Catatan: Jika Anda menjalankan sebagai user `root`, ganti `ubuntu` dengan `root` dan `/home/ubuntu/bot` dengan `/root/bot`)*.
*Simpan file dengan menekan `Ctrl + O`, lalu `Enter`, kemudian keluar dengan `Ctrl + X`.*

### 3. Aktifkan dan Jalankan Service:
```bash
# Reload systemd
sudo systemctl daemon-reload

# Aktifkan auto-start saat reboot
sudo systemctl enable rinaradev

# Nyalakan service sekarang
sudo systemctl start rinaradev

# Cek status service
sudo systemctl status rinaradev
```

Jika statusnya berwarna hijau bertuliskan **`active (running)`**, aplikasi sudah online 24 jam nonstop!

---

## Langkah 7: Menghubungkan ke Domain & Pasang SSL (HTTPS)

Pilih salah satu dari 2 opsi berikut:

### Opsi A: Cloudflare Tunnel (Sangat Direkomendasikan)
*Keuntungan: Tidak perlu buka port 80/443 di firewall, IP VPS Anda tersembunyi aman dari DDoS, dan SSL HTTPS otomatis aktif.*

1. Pasang `cloudflared` di Ubuntu:
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   ```
2. Buka dashboard [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) -> **Networks** -> **Tunnels**.
3. Klik **Create a Tunnel**, beri nama (misal: `rinaradev-bot`).
4. Pilih environment **Debian 64-bit**, salin dan jalankan perintah token yang disediakan Cloudflare di terminal VPS Anda.
5. Di tab **Public Hostname**:
   - Subdomain: misal `bot` (domain: `domainanda.com`).
   - Service Type: `HTTP`
   - URL: `127.0.0.1:8000`
6. Klik **Save hostname**. Selesai! Web bot langsung aktif di `https://bot.domainanda.com` dengan SSL gembok hijau resmi!

---

### Opsi B: Nginx + Certbot Let's Encrypt (Tradisional)

Jika menggunakan DNS A Record langsung ke IP VPS:

1. **Install Nginx & Certbot:**
   ```bash
   sudo apt install -y nginx certbot python3-certbot-nginx
   ```

2. **Buat file konfigurasi Nginx:**
   ```bash
   sudo nano /etc/nginx/sites-available/bot.conf
   ```

   Isi dengan:
   ```nginx
   server {
       listen 80;
       server_name bot.domainanda.com;

       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;

           # Dukungan Server-Sent Events (SSE) log terminal real-time
           proxy_http_version 1.1;
           proxy_set_header Connection "";
           proxy_buffering off;
           proxy_cache off;
           proxy_read_timeout 86400s;
       }
   }
   ```

3. **Aktifkan konfigurasi & Restart Nginx:**
   ```bash
   sudo ln -s /etc/nginx/sites-available/bot.conf /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

4. **Pasang SSL Otomatis dengan Certbot:**
   ```bash
   sudo certbot --nginx -d bot.domainanda.com
   ```
   Ikuti petunjuk di layar (masukkan email dan setujui ToS). Certbot akan otomatis mengaktifkan HTTPS.

5. **Buka Firewall (UFW):**
   ```bash
   sudo ufw allow OpenSSH
   sudo ufw allow 'Nginx Full'
   sudo ufw --force enable
   ```

---

## Langkah 8: Perintah Berguna untuk Manajemen Bot

- **Cek log aplikasi real-time:**
  ```bash
  sudo journalctl -u rinaradev -f
  ```
- **Restart bot (misal setelah edit file):**
  ```bash
  sudo systemctl restart rinaradev
  ```
- **Stop bot:**
  ```bash
  sudo systemctl stop rinaradev
  ```
- **Update kode dari GitHub di masa depan:**
  ```bash
  cd /root/bot
  git pull
  source venv/bin/activate
  pip install -r requirements.txt
  sudo systemctl restart rinaradev
  ```
