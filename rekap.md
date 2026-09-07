# Dokumentasi & Rekapitulasi Lengkap Traffic API Toodat / Quarterfull

Dokumen ini menyajikan hasil analisis menyeluruh dari berkas sniffing HTTP/2 dan HAR (`api.quarterfull.io_2026_09_07_05_36_27.har`) pada aplikasi Android **Toodat / Quarterfull**, mencakup arsitektur sistem, struktur verifikasi & token, rekapitulasi seluruh 34 endpoint, serta alur kerja aplikasi secara mendetail.

---

## 1. Profil & Arsitektur Aplikasi

* **Identitas Aplikasi**: **Toodat / Quarterfull** (Platform Pembaca Webnovel Digital)
* **Package Name**: `com.toodat.android`
* **Versi Client**: `3.0.52` (Variant: `prod`, OS: Android 15, Model Emulasi: `SM-F936U`)
* **HTTP Client Engine**: `OkHttp/4.12.0` berjalan di atas protokol **HTTP/2 (h2)**
* **Backend API Engine**: `Uvicorn` (FastAPI / Asynchronous Python Framework)
* **Base Domain API**: `https://api.quarterfull.io`
* **Content Delivery & Storage**:
  * Novel Cover CDN: AWS S3 Bucket `https://toodat-kr.s3.ap-northeast-2.amazonaws.com/covers/...`
  * Author Media CDN: `https://edge.mofic.io/author-profile/...`
* **Third-Party Services & SDKs**:
  * **AppsFlyer** (`v6.17`): Pelacakan atribusi instalasi dan sumber akuisisi pengguna (`meta_attribution`).
  * **OneSignal** (`v5.8.1`): Manajemen push notification berbasis token perangkat.
  * **Firebase Cloud Messaging (FCM) & FIS**: Push messaging & Firebase Installations API.
  * **Facebook SDK** (`v16.0 / 18.3.0`): Pelacakan event in-app analytics & campaign.

---

## 2. Standar Header & Client Fingerprint

Aplikasi menyertakan set header berikut pada setiap panggilan HTTP request:

```http
host: api.quarterfull.io
user-agent: okhttp/4.12.0
accept-encoding: gzip
x-platform: android
x-app-variant: prod
x-app-version: 3.0.52
x-timezone: Asia/Jakarta
x-local-date: YYYY-MM-DD
x-user-country: ID
x-user-raw-country: ID
accept-language: id
```

---

## 3. Analisis Kredensial, Autentikasi & Identitas Pengguna

Aplikasi memiliki dua tingkatan akses: **Sesi Tamu (Guest Mode)** dan **Sesi Member (Registered User)**.

```
                    ┌──────────────────────────────────────────────┐
                    │            POST /api/guest-reading/session   │
                    └──────────────────────┬───────────────────────┘
                                           │ (Mengembalikan token bertanda tangan)
                                           ▼
             ┌────────────────────────────────────────────────────────────┐
             │ Guest Token: [UUID v4] . [Version 1] . [HMAC-SHA256 Sig]   │
             │ Disimpan di Cookie 'qf_guest_reader' & 'x-guest-token'     │
             └─────────────────────────────┬──────────────────────────────┘
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
        ┌───────────────────────────────┐     ┌───────────────────────────────┐
        │        Membaca Gratis         │     │  Membuka Bab Berbayar / Like  │
        │ - PUT /api/guest-reading/...  │     │ - Memicu Registrasi / Login   │
        └───────────────────────────────┘     └───────────────┬───────────────┘
                                                              ▼
                                              ┌───────────────────────────────┐
                                              │ POST /api/auth/signup & login │
                                              └───────────────┬───────────────┘
                                                              │
                                                              ▼
                                              ┌───────────────────────────────┐
                                              │   JWT RS256 Bearer Token      │
                                              │ - Access Token (Aktif 8 Jam)  │
                                              │ - Refresh Token (Aktif 30 Hri)│
                                              └───────────────────────────────┘
```

### A. Sesi Tamu (Guest Token & Cookie `qf_guest_reader`)

1. **Pembuatan Sesi**:
   * Token tamu **dibuat oleh server**, bukan digenerate secara lokal oleh client.
   * Client mengirim `POST /api/guest-reading/session` dengan payload kosong.
   * Server merespons dengan JSON dan menyetel Cookie:
     ```http
     Set-Cookie: qf_guest_reader=0320941e-aaf7-4907-b1e9-fa70836030ff.1.d0fd913d93c4bb1d73b214b21fbe8ce1074f5ea40a4a706db48b30218cb05ed1; HttpOnly; Max-Age=31536000; Path=/; SameSite=lax; Secure
     ```
2. **Struktur String Token**:
   $$\underbrace{\text{0320941e-aaf7-4907-b1e9-fa70836030ff}}_{\text{UUID v4 (Guest ID)}} \mathbf{.} \underbrace{\text{1}}_{\text{Version}} \mathbf{.} \underbrace{\text{d0fd913d93c4bb1d73b214b21fbe8ce1074f5ea40a4a706db48b30218cb05ed1}}_{\text{HMAC-SHA256 Signature (64 Hex)}}$$
   * **HMAC Signature**: Dihitung oleh backend untuk memverifikasi keaslian `guest_id` dan mencegah pemalsuan identitas tanpa izin server.
   * **Masa Aktif**: 31.536.000 detik (1 tahun).
3. **Penggunaan**:
   * Dikirim melalui header `x-guest-token: <token>` dan `cookie: qf_guest_reader=<token>`.

### B. Sesi Pengguna Terdaftar (Bearer Token & Refresh Token)

Diterbitkan via endpoint `POST /api/auth/signup` atau `POST /api/auth/login`.

1. **Access Token (`Authorization: Bearer <JWT>`)**:
   * **Format**: RS256 JWT (Asymmetric RSA Signature).
   * **Masa Berlaku**: 28.800 detik (**8 Jam**).
   * **Isi Payload**:
     ```json
     {
       "sub": "1178588",
       "exp": 1788781435,
       "iat": 1788752635,
       "type": "access",
       "jti": "e2089637-eb7d-4072-845b-6e4436f06b87",
       "phone_verified": false,
       "market_policy_v1": true,
       "account_created_at": "2026-09-07T03:43:55.007453+00:00"
     }
     ```
2. **Refresh Token**:
   * **Format**: RS256 JWT.
   * **Masa Berlaku**: 2.592.000 detik (**30 Hari**).
   * **Isi Payload**:
     ```json
     {
       "sub": "1178588",
       "exp": 1791344635,
       "iat": 1788752635,
       "type": "refresh",
       "jti": "c742e6c2-19bc-40f0-8a9d-8820ab988a1d"
     }
     ```

### C. Identifier yang Dibuat di Sisi Client (Client-Side Generators)

1. **`x-device-id`**:
   * Format UUID v4 standar (cth: `d24063e7-a5ff-4831-92b2-4e28c0123498`).
   * Dibuat saat aplikasi pertama kali dijalankan dan disimpan di Android `SharedPreferences`.
2. **Pola Session ID & Telemetri**:
   * Format konsisten: `{prefix}_{timestamp_base36}_{random_string_base36}`.
   * Rumus:
     ```javascript
     const sessionId = prefix + "_" + Date.now().toString(36) + "_" + Math.random().toString(36).substring(2, 12);
     ```
   * **`reading_session_id`**: `grs_mtqe0smj_7qif5752fi` (`grs` = Guest Reading Session, `mtqe0smj` = Epoch 1788733970155).
   * **`attribution_session_id`**: `attr_mtqdxsq4_baes21ud` (`attr` = Attribution).
   * **`entry_event_id`**: `entry_mtqphcmg_73t2d602`.
3. **`x-guest-event-id`**:
   * Format: `guest-open:{chapter_hash_id}:{timestamp_ms}:{random_string}`.
   * Contoh: `guest-open:vJ4openkLPpa7Az1:1788733995671:vrqg482jg4d`.
4. **`listen_ticket` (Audio TTS Authorization)**:
   * Format Base64URL JSON + HMAC Token yang disertakan pada bab novel:
     ```json
     {
       "c": 2035693,
       "e": "original",
       "exp": 1788741195,
       "r": null,
       "ru": null,
       "u": "g"
     }
     ```
     `u: "g"` menandakan akses tamu (*guest*).

---

## 4. Rekapitulasi Seluruh 34 Endpoint API

Berikut adalah daftar lengkap 34 endpoint yang terdeteksi dari hasil sniffing, dikelompokkan berdasarkan fungsinya:

### Modul 1: Otentikasi, Akun & Konfigurasi Global
| No | Method | Endpoint | Fungsi | Kebutuhan Auth |
| :---: | :--- | :--- | :--- | :--- |
| 1 | `POST` | `/api/guest-reading/session` | Inisiasi / perpanjangan sesi tamu | Anonymous / Guest Token |
| 2 | `POST` | `/api/auth/signup` | Registrasi user baru | Device ID |
| 3 | `POST` | `/api/auth/login` | Login user dengan email/password | Device ID |
| 4 | `POST` | `/api/auth/signup-attribution` | Sinkronisasi data atribusi AppsFlyer | Device ID |
| 5 | `GET` | `/api/auth/app-version` | Pengecekan minimum & latest version aplikasi | Public |
| 6 | `GET` | `/api/auth/flags` | Mengambil feature flags / Remote Config | Public |
| 7 | `GET` | `/api/auth/public-flags` | Feature flags publik berdasarkan Anonymous ID | Public |
| 8 | `GET` | `/api/v1/service-countries` | Daftar negara dan locale yang didukung | Public |

### Modul 2: Beranda, Rak Buku & Discovery Katalog
| No | Method | Endpoint | Fungsi | Parameter / Payload |
| :---: | :--- | :--- | :--- | :--- |
| 9 | `GET` | `/api/v1/bookstore/for-you-shelf` | Rak rekomendasi personal ("For You") | `?limit=18` |
| 10 | `GET` | `/api/v1/bookstore/sections/new-release-best` | Daftar novel rilisan terbaru terbaik | - |
| 11 | `GET` | `/api/v1/bookstore/sections/deep-reading-best` | Daftar novel yang paling banyak dibaca tuntas | `?limit=50` |
| 12 | `GET` | `/api/v1/bookstore/sections/rising` | Daftar novel trending yang sedang naik | - |
| 13 | `GET` | `/api/v1/bookstore/sections/hidden-gems` | Daftar novel pilihan (Hidden Gems) | - |
| 14 | `GET` | `/api/v1/bookstore/sections/weekly-q-sponsorship-best` | Novel sponsorship terfavorit mingguan | `?limit=50` |
| 15 | `GET` | `/api/v1/bookstore/sections/early-engagement-best` | Novel dengan interaksi awal pembaca tinggi | `?limit=50` |
| 16 | `GET` | `/api/v1/rankings` | Leaderboard peringkat novel | `?period=world&content_type=novel&page=1&size=50` |
| 17 | `GET` | `/api/v1/novels` | Listing katalog novel umum | `?limit=24` |
| 18 | `GET` | `/api/v1/search` | Pencarian novel berdasarkan kata kunci | `?q={keyword}&limit=50` |
| 19 | `GET` | `/api/v1/categories` | Daftar kategori novel utama | - |
| 20 | `GET` | `/api/v1/subcategories` | Daftar subkategori novel | - |

### Modul 3: Konten Novel, Bab & Penulis
| No | Method | Endpoint | Fungsi | Keterangan |
| :---: | :--- | :--- | :--- | :--- |
| 21 | `GET` | `/api/v1/novels/{novel_id}` | Metadata novel (sinopsis, cover, author, statistik) | `{novel_id}` cth: `Py7LDdwpEQ8e1YKX` |
| 22 | `POST` | `/api/v1/novels/{novel_id}` | Rekomendasi novel terkait berbasis genre | Body: `{genre_id, limit, pool}` |
| 23 | `GET` | `/api/v1/novels/{novel_id}/chapters` | Daftar bab / daftar isi novel | `?order=desc&include_read_progress=true` |
| 24 | `GET` | `/api/v1/novels/{novel_id}/chapters/{chapter_id}` | **Isi teks lengkap narasi bab & token TTS** | `?rewarded_reader=true` |
| 25 | `GET` | `/api/v1/author-profiles/public/{author_id}/profile` | Profil publik penulis novel | `{author_id}` cth: `Yxk8mep482eMyJNj` |
| 26 | `GET` | `/api/v1/novels/by-author/{author_id}` | Daftar novel lain karya penulis yang sama | `?limit=12&exclude_hash_id={id}` |
| 27 | `GET` | `/api/v1/bookstore/sections/hidden-gems/detail-recommendations` | Rekomendasi lanjutan di halaman detail novel | `?exclude_hash_id={id}&limit=8` |
| 28 | `GET` | `/api/v2/comment-threads/novel/{novel_id}/visible-count` | Jumlah komentar pada novel | - |

### Modul 4: Progres Membaca, Telemetri & Analitik
| No | Method | Endpoint | Fungsi | Keterangan |
| :---: | :--- | :--- | :--- | :--- |
| 29 | `PUT` | `/api/guest-reading/progress` | **Heartbeat progres membaca tamu (Active Reading)** | Mengirim detik aktif baca & rasio scroll |
| 30 | `GET` | `/api/reading/progress` | Mengambil status progres baca member | Butuh `Authorization: Bearer` |
| 31 | `POST` | `/api/reading/v2/logs/post-view` | **Log resmi view bab (Member Payout / Royalti)** | Butuh `Authorization: Bearer` |
| 32 | `POST` | `/api/guest-reading/ad-events` | Melaporkan status tayangan iklan unlock bab | Status: `skipped`, `completed` |
| 33 | `POST` | `/api/v1/novels/{novel_id}/like` | Menyukai novel (Like) | Butuh `Authorization: Bearer` |
| 34 | `POST` | `/api/v1/analytics/events` | Pengiriman event analitik internal aplikasi (batch) | Body: array of analytics events |

---

## 5. Contoh Struktur Request & Response Kunci

### A. Inisialisasi Sesi Tamu (`POST /api/guest-reading/session`)
* **Request Header**:
  ```http
  POST /api/guest-reading/session HTTP/2
  host: api.quarterfull.io
  accept: application/json
  x-platform: android
  content-length: 0
  ```
* **Response Body (200 OK)**:
  ```json
  {
    "guest_id": "0320941e-aaf7-4907-b1e9-fa70836030ff",
    "guest_token": "0320941e-aaf7-4907-b1e9-fa70836030ff.1.d0fd913d93c4bb1d73b214b21fbe8ce1074f5ea40a4a706db48b30218cb05ed1",
    "created": true,
    "expires_in": 31536000
  }
  ```

---

### B. Registrasi Pengguna Baru (`POST /api/auth/signup`)
* **Request Body**:
  ```json
  {
    "email": "user.test@example.com",
    "password": "Password123!",
    "password_confirm": "Password123!",
    "birth_date": "2001-04-10",
    "gender": "prefer_not_to_say",
    "is_agree_terms": true,
    "meta_attribution": {
      "source_site": "appsflyer"
    },
    "skip_email_verification": true,
    "signup_market_country": "ID",
    "signup_market_source": "auto"
  }
  ```
* **Response Body (201 Created)**:
  ```json
  {
    "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
    "user": {
      "id": 1178588,
      "login_id": "user.test_14d6dcc0",
      "email": "user.test@example.com",
      "nickname": "deep-owl-24",
      "gender": "prefer_not_to_say",
      "register_method": "DIRECT",
      "locale": "id",
      "country": "ID",
      "benefit_country": "ID",
      "created_at": "2026-09-07T03:43:55.007453"
    }
  }
  ```

---

### C. Mengambil Teks Lengkap Bab Novel (`GET /api/v1/novels/{novel_id}/chapters/{chapter_id}`)
* **Request Header**:
  ```http
  GET /api/v1/novels/Py7LDdwpEQ8e1YKX/chapters/vJ4openkLPpa7Az1?rewarded_reader=true HTTP/2
  x-guest-token: 0320941e-aaf7-4907-b1e9-fa70836030ff.1.d0fd913d93c4bb1d73b214b21fbe8ce1074f5ea40a4a706db48b30218cb05ed1
  x-guest-event-id: guest-open:vJ4openkLPpa7Az1:1788733995671:vrqg482jg4d
  x-frontend-capability: member_payout_telemetry.v1
  ```
* **Response Body (200 OK - Ringkasan)**:
  ```json
  {
    "hash_id": "vJ4openkLPpa7Az1",
    "title": "SOROT LAMPU DAN TANGGUNG JAWAB",
    "chapter_num": 5,
    "content": "BAB 5: SOROT LAMPU DAN TANGGUNG JAWAB\n\nUdara di koridor rumah sakit pagi ini terasa sangat aneh dan tidak biasa...",
    "inline_formatting": {
      "runs": [
        { "start": 0, "end": 37, "bold": true, "italic": false }
      ],
      "content_hash": "d8e5472a43fcce0882597b0cc68aa8aaadaab6a6750bfa3e6ac48d2a2cf265f4"
    },
    "is_premium": false,
    "is_published": true,
    "reading_access": {
      "is_unlimited": true,
      "interstitial_ad_required": false,
      "resource": "guest_web_reading"
    },
    "prev_chapter_hash_id": "m0wMvbmjKP3dYAlO",
    "next_chapter_hash_id": "ABWJxbolMPLdgwOL",
    "tts": {
      "availability": "ready",
      "listen_ticket": "eyJjIjoyMDM1NjkzLCJlIjoib3JpZ2luYWwiLCJleHAiOjE3ODg3NDExOTUsInIiOm51bGwsInJ1IjpudWxsLCJ1IjoiZyJ9...",
      "voices": [
        { "voice_id": "qf-id-f-01", "gender": "female", "display_key": "tts_female" },
        { "voice_id": "qf-id-m-02", "gender": "male", "display_key": "tts_male" }
      ]
    }
  }
  ```

---

### D. Heartbeat & Progres Membaca (`PUT /api/guest-reading/progress`)
* **Request Body**:
  ```json
  {
    "chapter_hash_id": "vJ4openkLPpa7Az1",
    "progress": 0.9794,
    "active_reading_seconds": 24.68,
    "completed": true,
    "reading_session_id": "grs_mtqe0smj_7qif5752fi",
    "session_active_reading_seconds": 24.68,
    "session_ended": true,
    "session_end_reason": "chapter_change",
    "platform": "android",
    "entry_source": "chapter_route",
    "attribution_session_id": "attr_mtqdxsq4_baes21ud",
    "chapter_number": 5,
    "content_type": "novel",
    "content_character_count": 19541,
    "read_mode": "scroll"
  }
  ```
* **Response Body (200 OK)**:
  ```json
  {
    "max_progress": 1.0,
    "max_active_reading_seconds": 25.0,
    "completed": true,
    "completed_at": "2026-09-06T22:33:07.448451Z"
  }
  ```

---

### E. Telemetri Pembacaan Member (`POST /api/reading/v2/logs/post-view`)
* **Request Header**:
  ```http
  Authorization: Bearer <access_token>
  ```
* **Request Body**:
  ```json
  {
    "novel_hash_id": "Py7LDdwpEQ8e1YKX",
    "post_hash_id": "wNJAPdRJxGzdGyOX",
    "post_title": "ASRAMA DAN ANGAN-ANGAN",
    "novel_title": "Anthophila-VI",
    "attribution": {
      "attribution_session_id": "attr_mtqdxsq4_baes21ud",
      "work_id": "Py7LDdwpEQ8e1YKX",
      "first_touch": {
        "discovery_method": "direct",
        "source_screen": "chapter_route",
        "touched_at": "2026-09-07T03:53:38.344Z",
        "entry_event_id": "entry_mtqphcmg_73t2d602"
      },
      "last_touch": {
        "discovery_method": "direct",
        "source_screen": "chapter_route",
        "touched_at": "2026-09-07T03:53:38.344Z",
        "entry_event_id": "entry_mtqphcmg_73t2d602"
      }
    },
    "callback_contract": "telemetry_v2",
    "source_event_id": "member:1178588:2024428:2026-09"
  }
  ```

---

## 6. Alur Cara Kerja Aplikasi (Lifecycle Walkthrough)

```
 [Aplikasi Dibuka]
        │
        ├──> GET /api/auth/app-version (Cek update aplikasi)
        ├──> GET /api/auth/flags & public-flags (Cek fitur/A-B Testing)
        └──> POST /api/guest-reading/session (Dapatkan guest_token & cookie)
        │
 [Masuk Beranda]
        │
        ├──> GET /api/v1/bookstore/for-you-shelf
        ├──> GET /api/v1/bookstore/sections/new-release-best
        ├──> GET /api/v1/bookstore/sections/deep-reading-best
        └──> GET /api/v1/rankings (Peringkat novel)
        │
 [Pilih Novel & Buka Detail]
        │
        ├──> GET /api/v1/novels/{novel_id} (Sinopsis, cover, info author)
        ├──> GET /api/v1/novels/{novel_id}/chapters (Daftar bab)
        └──> POST /api/v1/novels/{novel_id} (Rekomendasi novel serupa)
        │
 [Buka Bab & Membaca Konten]
        │
        ├──> GET /api/v1/novels/{novel_id}/chapters/{chapter_id} (Teks narasi bab)
        ├──> POST /api/guest-reading/ad-events (Cek apakah kena interstitial ads)
        └──> PUT /api/guest-reading/progress (Kirim detik baca & scroll berkala)
        │
 [Interaksi Lanjutan / Bayar / Like]
        │
        └──> Memicu login/signup (POST /api/auth/login)
             └──> Dapatkan JWT Bearer Token
             └──> Panggilan berikutnya menyertakan `Authorization: Bearer <JWT>`
```

1. **Inisialisasi & Verifikasi Versi**:
   Saat aplikasi diluncurkan, aplikasi memastikan API kompatibel via `/api/auth/app-version` dan mengambil konfigurasi server via `/api/auth/flags`. Sesi tamu dibuat secara otomatis tanpa memerlukan interaksi pengguna.
2. **Browsing & Discovery**:
   Halaman beranda disusun modular berdasarkan section. Setiap section memuat daftar novel dengan cover yang disimpan di S3 AWS.
3. **Penyajian Konten**:
   Teks novel dikirim secara utuh (plain text) pada field `content` di endpoint chapter. Format tampilan (bold, italic) dikirim terpisah melalui array indeks `inline_formatting.runs`.
4. **Validasi Anti-Abuse Membaca**:
   Server tidak langsung menandai bab selesai dibaca. Aplikasi wajib mengirimkan `PUT /api/guest-reading/progress` secara berkala dengan parameter `active_reading_seconds` yang bertambah secara realistis sesuai jumlah karakter (`content_character_count`).
5. **Transisi Tamu ke Member**:
   Pendaftaran akun mendukung parameter `skip_email_verification: true`, sehingga pengguna langsung aktif dan menerima access token JWT 8 jam tanpa jeda verifikasi email.

---

## 7. Rekomendasi Teknis untuk Pembuatan Scraper / Bot

1. **Payload & Transport**: Semua request adalah JSON murni di atas HTTPS HTTP/2 tanpa enkripsi ganda (tidak menggunakan AES/RSA pada body request).
2. **Autentikasi Guest Sangat Mudah Direplikasi**: Tidak perlu reverse engineering signature HMAC; cukup tembak `POST /api/guest-reading/session` sekali, simpan string `guest_token`, lalu gunakan untuk seluruh request crawling bab.
3. **Simulasi Waktu Baca untuk Bot Auto-Read**: Jika membuat bot farming koin/poin baca, buat delay waktu baca bertahap pada payload `active_reading_seconds` (contoh: bertambah 5–10 detik tiap panggilan) agar status bab berhasil diubah menjadi `completed: true`.

---

## 8. Integrasi Proxy Cerdas & Dynamic Geo-Targeting (Bright Data SuperProxy)

Sistem bot dilengkapi modul `proxy_manager.py` yang dirancang khusus untuk menangani rotasi proxy residensial / ISP dari **Bright Data** (`brd.superproxy.io:44445`):

1. **Format Proxy**:
   ```
   http://brd-customer-hl_1fd5f466-zone-isp_proxy1-country-<cc>:n0qesnfsc1j8@brd.superproxy.io:44445
   ```
2. **Dynamic Geo-Targeting**:
   * Saat bot mendaftarkan akun di negara tertentu (misalnya `ID`, `US`, `JP`, `GB`), parameter username `-country-<cc>` secara otomatis dimodifikasi secara dinamis saat runtime.
   * Akun Indonesia didaftarkan melalui IP residensial Jakarta/Indonesia, akun US melalui US, dsb. Hal ini mencegah mismatch antara header geolokasi (`x-user-country`) dan IP publik penyerang.
3. **HTTP/2 vs HTTP/1.1 Forward Tunnel Safeguard**:
   * Menghubungkan client HTTP/2 langsung melalui forward HTTP proxy CONNECT tunnel seringkali menyebabkan socket timeout/hang di pustaka Python `httpx`.
   * Bot secara cerdas menggunakan protokol **HTTP/1.1** saat melalui proxy tunnel, dan mempertahankan protokol **HTTP/2** saat koneksi langsung (direct connection).
4. **Health Check & Diagnostics**:
   * Diagnostik terintegrasi dengan endpoint `https://geo.brdtest.com/mygeo.json` dan verifikasi respon target `https://api.quarterfull.io/api/auth/app-version` langsung dari menu `[6]` di `main.py`.

---

## 9. Sistem Deteksi & Auto-Skip Interaksi Sosial (Anti-Toggle Unintention)

Pada endpoint sosial Toodat / Quarterfull, aksi Like, Bookmark, dan Follow bekerja dengan mekanisme *toggle*:
* Mengirimkan POST ke `/api/v1/novels/{id}/like` saat sudah di-like akan membatalkan like (Unlike).
* Mengirimkan POST ke `/api/v1/novels/{id}/bookmark` saat sudah tersimpan akan menghapus dari rak (Unbookmark).
* Mengirimkan PUT ke `/api/v1/social/profiles/{author_id}/follow` saat sudah follow berisiko unfollow.

Untuk mencegah pembatalan interaksi yang sudah aktif, diimplementasikan **Smart Pre-Check Detection**:
1. **Deteksi Like & Bookmark**:
   * Endpoint `GET /api/v1/novels/{id}` dengan Bearer token mengembalikan field `is_liked` dan `is_saved`.
   * Jika `is_liked == True`, aksi Like langsung **di-SKIP** (`[SKIP] Sudah Like sebelumnya`).
   * Jika `is_saved == True`, aksi Bookmark langsung **di-SKIP** (`[SKIP] Sudah Simpan / Bookmark sebelumnya`).
2. **Deteksi Follow Author**:
   * Endpoint `GET /api/v1/social/profiles/{author_id}/relationship` mengembalikan `is_following`.
   * Jika `is_following == True`, aksi Follow langsung **di-SKIP** (`[SKIP] Sudah Follow sebelumnya`).

---

## 10. Modul Full Auto Bot (All-in-One Novel Automation)

Modul `full_auto_runner.py` menyatukan seluruh lifecycle bot dalam satu alur terpadu:
1. **Target**: Menerima URL web (`https://quarterfull.io/works/<id>`) atau Hash ID novel target.
2. **Otomatisasi Penuh per Akun**:
   * **Tahap 1**: Deteksi & Eksekusi Interaksi Sosial (Auto Like + Auto Simpan/Bookmark + Auto Follow Author) dengan auto-skip akun yang sudah aktif.
   * **Tahap 2**: Simulasi Membaca Bab demi Bab secara natural dengan jeda waktu teratur dan pengiriman telemetri royalti post-view (`POST /api/reading/v2/logs/post-view`).
3. **Eksekusi Paralel (Semaphore Concurrency)**: Berjalan secara cepat dan stabil dengan indikator visual progress bar dan tabel rekapitulasi Rich.