# Comprehensive Security Audit & Analysis: Toodat / Quarterfull Web & Mobile Platform

> **System Security Evaluation Document & Remediation Roadmap**  
> Compiled based on vulnerability assessments and findings discovered during the development and testing of the automation bot suite (`auto_signup.py`, `auto_reader.py`, `interaction_manager.py`, `full_auto_runner.py`).

---

## Executive Summary

The **Toodat / Quarterfull** platform and API (`api.quarterfull.io`) feature a modern architecture based on FastAPI/Uvicorn running over HTTP/2 with RS256 JWT encryption. However, from an application security (AppSec) and offensive penetration testing perspective, the backend suffers from critical business logic flaws, excessive trust in client-reported state (*client trust vulnerability*), absence of hardware/OS attestation (*device integrity*), and a lack of adaptive anti-bot controls at the network layer (*WAF / TLS fingerprinting*).

These vulnerabilities allowed the developed bot suite to seamlessly:
1. **Mass-register millions of fictitious accounts** without email verification, SMS verification, or CAPTCHA challenges.
2. **Fabricate reading telemetry (*active reading time*) and claim publisher payouts (*post-view logs*)**, exposing the platform to severe financial risk and monetization fraud.
3. **Bypass rewarded ad-gated chapters** without ever streaming or watching advertisement videos (by spoofing client-side ad event callbacks).
4. **Manipulate social engagement metrics (Auto Like, Bookmark, Follow)** on a massive scale (*Sybil Attack*), disrupting editorial and recommendation algorithms ("For You", "Rising", "Rankings").
5. **Scrape raw novel manuscripts (plaintext)** at high volume without DRM or dynamic steganographic watermarking.

---

## 1. Vulnerability Matrix

| ID | Category | Vulnerability Finding | Impact & Threat Vector | Severity Score (CVSS) |
| :--- | :--- | :--- | :--- | :---: |
| **SEC-01** | Authentication & Lifecycle | `skip_email_verification: true` honored by production backend | Unrestricted Mass Account Creation (Sybil Attack) | **CRITICAL (9.5)** |
| **SEC-02** | Business Logic & Monetization | Client-controlled reading telemetry & royalty logs (`/logs/post-view`) | Direct Financial Royalty Fraud & Metric Falsification | **CRITICAL (9.3)** |
| **SEC-03** | Ad Fraud / Paywall Bypass | Ad-watch confirmation (`ad-events`) lacks Server-Side Verification (SSV) | Unlocked premium/ad content without ad revenue | **HIGH (8.8)** |
| **SEC-04** | Anti-Bot & Fingerprinting | No CAPTCHA & No Hardware Attestation (Play Integrity / DeviceCheck) | Headless script automation & residential proxy spoofing | **HIGH (8.5)** |
| **SEC-05** | Content Protection & DRM | Chapters delivered in plaintext with no encryption or watermarking | Large-scale content scraping & unauthorized novel distribution | **HIGH (8.0)** |
| **SEC-06** | Social Metric Integrity | Like, Bookmark, and Follow actions lack account reputation/aging checks | Artificial chart manipulation & algorithmic distortion | **MEDIUM (7.2)** |
| **SEC-07** | Network & WAF Defense | Rate limiting enforced solely on single IP (bypassed via proxy rotation) | Distributed credential stuffing & automated scraping | **MEDIUM (6.8)** |
| **SEC-08** | Session & Token Policy | Long-lived Access Tokens (8 Hours) & Guest Session Lifespan (1 Year) | Token replay risk & delayed revocation capabilities | **LOW-MEDIUM (5.5)** |

---

## 2. In-Depth Vulnerability Analysis

### 2.1. Critical: Unrestricted `skip_email_verification` Parameter (SEC-01)
* **Endpoint**: `POST /api/auth/signup`
* **Exploitation Method**:
  During account registration (`auto_signup.py`), the client sends:
  ```json
  {
    "email": "random_user@example.com",
    "password": "Password123!",
    "password_confirm": "Password123!",
    "skip_email_verification": true
  }
  ```
  The production server responds with HTTP **201 Created** along with an active RS256 `access_token` and `refresh_token`, bypassing any email OTP or activation link requirement.
* **Root Cause**:
  An internal development or automated integration testing flag was left enabled and directly bindable from unauthenticated public client payloads in production.
* **Impact**:
  Adversaries can mass-generate disposable bot accounts using randomly synthesized or non-existent email domains at zero cost.

---

### 2.2. Critical Financial Risk: Client-Reported Dwell Time & Post-View Royalties (SEC-02)
* **Endpoints**:
  * `PUT /api/guest-reading/progress`
  * `POST /api/reading/progress`
  * `POST /api/reading/sessions/heartbeat`
  * `POST /api/reading/v2/logs/post-view`
* **Exploitation Method**:
  1. The backend relies on client-supplied values for `active_reading_seconds`, `progress`, and `completed: true`.
  2. The bot in `auto_reader.py` fabricates arbitrary human dwell times:
     ```json
     {
       "active_reading_seconds": 240,
       "scroll_percent": 1.0,
       "completed": true
     }
     ```
  3. Immediately afterward (or after a brief simulated interval), the bot invokes the member royalty endpoint `POST /api/reading/v2/logs/post-view`:
     ```json
     {
       "source_event_id": "member:1178588:2024428:2026-09",
       "callback_contract": "telemetry_v2",
       "attribution": { ... }
     }
     ```
     The server acknowledges with **200 OK**, recording a legitimate view payout for the author and inflating platform engagement figures.
* **Root Cause**:
  **Inverted Trust Model**: The server trusts client telemetry to measure elapsed time. The server must measure duration independently by computing $\Delta t$ between server-side timestamps.
* **Impact**:
  * Direct financial drainage through fraudulent royalty payouts organized by rogue creators or bot operators.
  * Artificially boosted placement on leaderboard feeds such as "Deep Reading Best" and "Rising".

---

### 2.3. Ad Monetization Fraud: Unverified Ad-Event Submissions (SEC-03)
* **Endpoint**: `POST /api/guest-reading/ad-events`
* **Exploitation Method**:
  In the official Android app, rewarded video ads are triggered before locked chapters. Once completed, the mobile client calls `POST /api/guest-reading/ad-events` with `status: "completed"`.
  The bot issues this HTTP call directly without ever initializing an ad SDK or streaming video chunks. The server unlocks chapter access immediately upon receipt.
* **Root Cause**:
  Absence of **Server-Side Verification (SSV)** callbacks between third-party ad networks (Google AdMob, Unity Ads, AppLovin) and the Quarterfull backend.
* **Impact**:
  Full access to monetized content without generating advertising revenue, resulting in compute and bandwidth expenses with zero financial return.

---

### 2.4. Lack of Hardware Attestation & Network Fingerprinting (SEC-04 & SEC-07)
* **Endpoints**: Global API endpoints (`x-device-id`, `user-agent`)
* **Exploitation Method**:
  1. Header `x-device-id` accepts arbitrary UUID v4 strings generated locally via `uuid.uuid4()`. The bot continuously spoofs new device identities on every invocation.
  2. The header `user-agent: okhttp/4.12.0` is accepted without verifying the underlying TLS fingerprint (JA3/JA4). Python's `httpx` (OpenSSL TLS stack) communicates directly without interception.
  3. Rate limiting is enforced strictly on isolated IP addresses (HTTP 429). When utilizing residential/ISP proxy rotation (e.g., Bright Data integrated into `proxy_manager.py`), rate limiting is neutralized as each HTTP request originates from a different residential egress IP spanning 50 countries.
* **Root Cause**:
  Absence of **Google Play Integrity API** (Android) or **App Attest / DeviceCheck** (iOS), combined with the lack of TLS fingerprint classification at the WAF level.

---

### 2.5. Plaintext Content Transmission & Scraping Exposure (SEC-05)
* **Endpoint**: `GET /api/v1/novels/{novel_id}/chapters/{chapter_id}`
* **Exploitation Method**:
  Novel narrative chapters are returned as raw plaintext strings within JSON (`"content": "CHAPTER 1: ..."`). Formatting elements (bold, italic) are decoupled into character index ranges.
* **Root Cause**:
  Absence of payload-level encryption and steganographic watermarking.
* **Impact**:
  A lightweight Python script can download an author's complete multi-hundred-chapter catalogue within minutes and re-publish it across unauthorized third-party aggregator domains.

---

### 2.6. Unrestricted Social Engagement Manipulation (SEC-06)
* **Endpoints**:
  * `POST /api/v1/novels/{novel_id}/like`
  * `POST /api/v1/novels/{novel_id}/bookmark`
  * `PUT /api/v1/social/profiles/{author_id}/follow`
* **Exploitation Method**:
  Any account holding a valid JWT bearer token can immediately like, bookmark, or follow profiles without satisfying eligibility thresholds:
  * Zero minimum reading history.
  * Zero account aging requirement.
  * No phone or email verification gate.
* **Impact**:
  The bot module `interaction_manager.py` can inject thousands of artificial likes, bookshelf saves, and author followers in minutes, manipulating platform recommendation feeds.

---

### 2.7. Excessive Token Lifespans (SEC-08)
* **Scope**: JWT Access Tokens & Guest Session Cookies
* **Analysis**:
  * **Access Token**: Active for **8 Hours** (28,800 seconds). Modern security standards recommend 15–30 minutes for stateless access tokens. Intercepted tokens offer a wide exploitation window.
  * **Guest Token**: Active for **1 Year** (31,536,000 seconds). The HMAC-SHA256 signed guest tokens never rotate, allowing persistent, long-term scraping sessions without renewal.

---

## 3. Comprehensive Remediation & Hardening Plan

```
                                PROPOSED DEFENSE ARCHITECTURE
 ┌──────────────────────┐
 │ Android / iOS Client │
 └──────────┬───────────┘
            │ 1. Encrypted Payload + Play Integrity Token + Cloudflare Turnstile
            ▼
 ┌───────────────────────────────────────────────────────────┐
 │ WAF & API Gateway (Cloudflare Enterprise / Envoy)         │
 │ - JA4 / TLS Fingerprint Validation (Drop Python/curl)     │
 │ - Distributed Sliding-Window Rate Limiting (Redis)        │
 │ - GeoIP Mismatch Filtering                                │
 └──────────────────────────┬────────────────────────────────┘
                            │ 2. Passed Network Layer Inspection
                            ▼
 ┌───────────────────────────────────────────────────────────┐
 │ FastAPI Backend Cluster                                   │
 │ ├── Middleware: Device Attestation Validator              │
 │ ├── Auth Service: Strict Email OTP / Deprecate skip_verify│
 │ ├── Reading Service: Server-Side Elapsed Time Engine      │
 │ ├── Ad Service: AdMob Server-Side Verification (SSV)      │
 │ └── Social Guard: Sybil Shield & Trust Score Evaluator    │
 └───────────────────────────────────────────────────────────┘
```

---

### Step 1: Remove `skip_email_verification` in Production (Priority: P0 - Urgent)
1. **Implementation**:
   * Completely purge `skip_email_verification` from the public Pydantic request schema.
   * Mark new registrations with `is_active: false` until the user supplies a valid 6-digit OTP dispatched to their email address.
   * If automated testing requires bypasses, isolate the logic behind a dedicated testing environment or an internal header with a cryptographic secret (e.g., `X-Internal-Test-Secret`) stored securely in server environment variables—**never** packaged into mobile binaries.

```python
# Secure Pydantic Registration Schema Example
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    birth_date: date
    gender: str
    # skip_email_verification is strictly removed from public contracts
```

---

### Step 2: Implement Hardware & Binary Attestation (Priority: P0 - Urgent)
1. **Android**:
   Integrate the **Google Play Integrity API**.
   * Prior to invoking sensitive endpoints (`/api/auth/signup`, `/api/auth/login`, `/api/reading/v2/logs/post-view`), the Android client requests an integrity token from Google Play Services.
   * The token is passed via header `X-Play-Integrity-Token`.
   * The backend validates the token against Google's API, verifying:
     * App Licensing: `LICENSED` & `RECOGNIZED_VERSION` (unmodified binary).
     * Device Verdict: `MEETS_DEVICE_INTEGRITY` or `MEETS_STRONG_INTEGRITY` (not an emulator or rooted bot environment).
2. **iOS**:
   Integrate **DeviceCheck** and **DCAppAttestService** to guarantee requests originate from genuine Apple devices.

---

### Step 3: Transition Reading Telemetry to a Server-Side Time Engine (Priority: P0 - Urgent)
Never rely on client-reported dwell durations or progress percentages.

**Secure Verification Workflow**:
1. **Session Start**:
   When a user opens a chapter, the client calls `POST /api/reading/session/start` with `{novel_id, chapter_id}`.
   The server records a persistent timestamp in Redis: `session_start_time = now()`.
2. **Session Completion & Payout**:
   When the client reports chapter completion via `POST /api/reading/session/finish`:
   * The server calculates true elapsed time:
     $$\text{elapsed\_time} = \text{now}() - \text{session\_start\_time}$$
   * The server validates reading speed against the chapter length ($N$ characters). If reading speed is humanly impossible (e.g., $> 80$ characters/second, or a 4,000-word chapter completed in under 10 seconds), **reject the view log** and flag the session for anomaly monitoring.
   * Cap royalty-qualifying views to a maximum of 1 payout per novel per unique user/IP within any 24-hour window.

---

### Step 4: Enforce Ad Network Server-Side Verification (SSV) (Priority: P1 - High)
1. Do not unlock ad-gated chapters based on direct HTTP calls from the mobile client.
2. Configure **AdMob Server-Side Verification (SSV)**:
   * When loading a rewarded video in the mobile SDK, inject custom metadata: `user_id` and `chapter_id`.
   * Upon ad completion, Google AdMob servers dispatch an authenticated webhook directly to your backend (`https://api.quarterfull.io/api/webhooks/admob-ssv`) signed with ECDSA cryptography.
   * The Quarterfull backend validates Google's public key signature and grants chapter access only upon verified receipt of the webhook.

---

### Step 5: Distributed Rate Limiting & WAF TLS Fingerprinting (Priority: P1 - High)
1. **Cloudflare Bot Management / JA4 Fingerprinting**:
   * Inspect the **JA4 TLS Fingerprint** at the gateway level. Python HTTP engines (`httpx`, `requests`, `urllib3`) utilize distinct cipher suites and TLS extension orders compared to native Android OkHttp. Cloudflare WAF rules should reject requests claiming `okhttp/...` user-agents that do not present native Android TLS fingerprints.
   * Implement **Cloudflare Turnstile** (invisible CAPTCHA) on web authentication flows.
2. **Distributed Sliding-Window Limiting (Redis)**:
   * Registration throttling: Max 3 accounts created per `/24` subnet per hour.
   * Guest session throttling: Max 10 new sessions per IP per hour.

---

### Step 6: Sybil Shield for Social Engagement (Priority: P2 - Medium)
Enforce **Account Trust Score Requirements** before enabling social actions:
* **Reading Prerequisite**: An account must accumulate at least 3 completed chapters or 10 minutes of active reading history before unlocking Like, Bookmark, or Follow permissions.
* **Identity Verification**: Restrict mass social interactions to accounts with verified email addresses or confirmed phone numbers.
* **Velocity Limits**: Restrict social actions to a maximum of 10 events per account per 5-minute interval.

---

### Step 7: Content Watermarking & Anti-Scraping Defenses (Priority: P2 - Medium)
1. **Dynamic Steganographic Watermarking**:
   When rendering chapter text via `GET /chapters/{id}`, dynamically embed invisible Unicode zero-width characters (*Zero-Width Space* `\u200B`, *Zero-Width Non-Joiner* `\u200C`) encoding the user's ID or session token within the manuscript whitespace. If content leaks to unauthorized mirror sites, the originating account can be identified and terminated immediately.
2. **Payload Obfuscation / Dynamic Encryption**:
   Encrypt chapter text payloads using AES-256-GCM on the server with an ephemeral session key negotiated during handshake, decrypting only within the native C++/NDK layer on Android.

---

### Step 8: Token Expiration Policy Optimization (Priority: P2 - Medium)
1. Reduce JWT Access Token lifespan from **8 Hours** to **15–30 Minutes**.
2. Implement **Refresh Token Rotation (RTR)**: Invalidate old refresh tokens immediately upon exchanging for a new pair. If an expired refresh token is reused, revoke the entire token lineage to protect against session hijacking.
3. Lower Guest Session validity from **1 Year** to a maximum of **7 Days**.

---

## 4. Remediation Action Checklist

- [ ] **P0 (Critical)**: Purge `skip_email_verification` from production and mandate email OTP verification.
- [ ] **P0 (Critical)**: Shift dwell time calculation and post-view royalty logging to server-side timestamp delta verification.
- [ ] **P0 (Critical)**: Deploy Google AdMob Server-Side Verification (SSV) webhooks for ad-gated chapter access.
- [ ] **P1 (High)**: Integrate Google Play Integrity API (Android) and App Attest (iOS) to reject unauthorized headless calls.
- [ ] **P1 (High)**: Configure Cloudflare WAF JA4/TLS fingerprint inspection to block simulated OkHttp clients.
- [ ] **P1 (High)**: Integrate Cloudflare Turnstile on public authentication entry points.
- [ ] **P2 (Medium)**: Establish account trust thresholds (minimum reading time) for social interactions.
- [ ] **P2 (Medium)**: Shorten JWT Access Token duration to 30 minutes and deploy Refresh Token Rotation (RTR).
- [ ] **P2 (Medium)**: Implement zero-width steganographic watermarking across chapter text payloads.
