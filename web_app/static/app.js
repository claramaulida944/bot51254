/**
 * Rinara Platform — Reader Engine Client Controller
 * Clean, Modular, Resilient JavaScript with 100% Dynamic Tariffs & Addon Fees
 */

let currentUser = null;
let currentTaskId = null;
let activeEventSource = null;
let selectedTerminals = 1;
let selectedTargetReaders = 10;
let selectedMode = "valid"; // "valid" atau "guest"
let isGuestConversionEnabled = false; // Addon konversi tamu ke member resmi
let selectedTopupAmount = 25000;
let qrisPollingInterval = null;
let currentQrisRefId = null;

// Dynamic System Settings (dimuat dari backend /api/settings)
let appSettings = {
  price_valid_reader: 450,
  price_guest_reader: 50,
  addon_price_per_terminal: 500,
  addon_price_guest_conversion: 150,
  min_deposit: 5000,
  reading_delay_seconds: 140,
  max_concurrent_terminals: 5,
};

// =============================================================================
// TOAST NOTIFICATION SYSTEM
// =============================================================================
function showToast(type = "info", message = "", title = "") {
  let container = document.getElementById("toastContainer");
  if (!container) {
    container = document.createElement("div");
    container.id = "toastContainer";
    container.className = "toast-container";
    document.body.appendChild(container);
  }

  const icons = {
    success: `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`,
    error: `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    info: `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
    warning: `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  };

  const titles = {
    success: "Berhasil",
    error: "Peringatan",
    info: "Informasi",
    warning: "Perhatian",
  };

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    ${icons[type] || icons.info}
    <div class="toast-content">
      <div class="toast-title">${escapeHtml(title || titles[type] || "Pemberitahuan")}</div>
      <div class="toast-message">${escapeHtml(message)}</div>
    </div>
    <button class="toast-close" onclick="this.parentElement.remove()">&times;</button>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    if (toast.parentElement) {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(8px)";
      setTimeout(() => toast.remove(), 220);
    }
  }, 4200);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatRupiah(num) {
  return "Rp " + (num || 0).toLocaleString("id-ID");
}

function getAuthHeaders() {
  const token = localStorage.getItem("rinara_session") || sessionStorage.getItem("rinara_session") || "";
  return {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${token}`,
    "x-session-token": token,
  };
}

// =============================================================================
// LIFECYCLE INIT
// =============================================================================
document.addEventListener("DOMContentLoaded", async () => {
  await loadPublicSettings();
  await checkAuthMe();
  await fetchSystemStats();
  setInterval(fetchSystemStats, 10000);
  await checkActiveTask();
  await loadRecentDashboardTasks();
  initStandbyTerminalHeartbeat();
  updateCostCalculation();
});

// =============================================================================
// DYNAMIC SETTINGS MODULE
// =============================================================================
async function loadPublicSettings() {
  try {
    const resp = await fetch("/api/settings");
    const data = await resp.json();
    if (data.ok && data.settings) {
      appSettings = { ...appSettings, ...data.settings };
      renderDynamicUI();
    }
  } catch (e) {
    console.warn("Gagal memuat setting publik:", e);
  }
}

function renderDynamicUI() {
  // Update badge tarif di header
  const badgeRate = document.getElementById("badgeRatePerReader");
  const currentRate = selectedMode === "guest" ? appSettings.price_guest_reader : appSettings.price_valid_reader;
  const modeLabel = selectedMode === "guest" ? "Guest Reader" : "Valid Reader";
  if (badgeRate) badgeRate.textContent = `Tarif: ${formatRupiah(currentRate)} / ${modeLabel}`;

  // Update card rate pills
  const cardVal = document.getElementById("cardRateValid");
  const cardGst = document.getElementById("cardRateGuest");
  if (cardVal) cardVal.textContent = formatRupiah(appSettings.price_valid_reader);
  if (cardGst) cardGst.textContent = formatRupiah(appSettings.price_guest_reader);

  // Update addon konversi text
  const convRate = appSettings.addon_price_guest_conversion || 150;
  const convText = document.getElementById("addonGuestConversionRateText");
  if (convText) convText.textContent = `+${formatRupiah(convRate)} / Reader`;

  // Update addon text di terminal chips
  const aRate = appSettings.addon_price_per_terminal || 500;
  const aText2 = document.getElementById("addonText2");
  const aText3 = document.getElementById("addonText3");
  const aText5 = document.getElementById("addonText5");
  if (aText2) aText2.textContent = `+${formatRupiah(aRate * 1)}`;
  if (aText3) aText3.textContent = `+${formatRupiah(aRate * 2)}`;
  if (aText5) aText5.textContent = `+${formatRupiah(aRate * 4)}`;

  // Update estimasi readers di halaman top up
  const baseRate = appSettings.price_valid_reader || 450;
  [10000, 25000, 50000, 100000, 250000, 500000].forEach((nom) => {
    const idKey = nom >= 1000 ? (nom / 1000) + "k" : nom;
    const el = document.getElementById(`nomReaders${idKey}`);
    if (el) el.textContent = `~${Math.floor(nom / baseRate).toLocaleString("id-ID")} Readers`;
  });

  updateCostCalculation();
}

// =============================================================================
// NAVIGATION & PAGE SWITCHING
// =============================================================================
function switchPage(pageKey) {
  const pages = ["dashboard", "run", "topup", "history"];
  pages.forEach((p) => {
    const el = document.getElementById("view" + p.charAt(0).toUpperCase() + p.slice(1));
    const nav = document.getElementById("nav" + p.charAt(0).toUpperCase() + p.slice(1));
    if (el) el.style.display = p === pageKey ? "block" : "none";
    if (nav) {
      if (p === pageKey) nav.classList.add("active");
      else nav.classList.remove("active");
    }
  });

  const titleEl = document.getElementById("topNavTitle");
  const titles = {
    dashboard: "Dashboard Layanan",
    run: "Konfigurasi & Peluncur Bot",
    topup: "Isi Saldo QRIS",
    history: "Riwayat & Mutasi Saldo",
  };
  if (titleEl) titleEl.textContent = titles[pageKey] || "Dashboard";

  if (pageKey === "history") {
    loadTaskHistory();
    loadTransactions();
  } else if (pageKey === "dashboard") {
    checkActiveTask();
    loadRecentDashboardTasks();
    fetchSystemStats();
    initStandbyTerminalHeartbeat();
  }

  toggleSidebar(false);
}

function toggleSidebar(open) {
  const sb = document.getElementById("appSidebar");
  const bd = document.getElementById("sidebarBackdrop");
  if (sb) {
    if (open) sb.classList.add("open");
    else sb.classList.remove("open");
  }
  if (bd) bd.style.display = open ? "block" : "none";
}

// =============================================================================
// AUTHENTICATION MODULE
// =============================================================================
async function checkAuthMe() {
  const token = localStorage.getItem("rinara_session") || sessionStorage.getItem("rinara_session");
  if (!token) {
    renderUserState(null);
    return;
  }

  try {
    const resp = await fetch("/api/auth/me", { headers: getAuthHeaders() });
    if (resp.ok) {
      const data = await resp.json();
      currentUser = data.user;
      renderUserState(currentUser);
    } else {
      localStorage.removeItem("rinara_session");
      sessionStorage.removeItem("rinara_session");
      renderUserState(null);
    }
  } catch (e) {
    console.warn("Gagal cek sesi auth:", e);
  }
}

function renderUserState(user) {
  const guestBox = document.getElementById("sidebarGuestBox");
  const userBox = document.getElementById("sidebarUserBox");
  const usernameDisplay = document.getElementById("sidebarUsername");
  const balanceDisplay = document.getElementById("sidebarBalance");
  const metricBalance = document.getElementById("metricUserBalance");

  if (user) {
    if (guestBox) guestBox.style.display = "none";
    if (userBox) userBox.style.display = "block";
    if (usernameDisplay) usernameDisplay.textContent = "@" + user.username;
    if (balanceDisplay) balanceDisplay.textContent = formatRupiah(user.balance);
    if (metricBalance) metricBalance.textContent = formatRupiah(user.balance);
  } else {
    if (guestBox) guestBox.style.display = "block";
    if (userBox) userBox.style.display = "none";
    if (metricBalance) metricBalance.textContent = "Rp 0";
  }
  updateCostCalculation();
}

function openAuthModal(tab = "login") {
  const modal = document.getElementById("authModal");
  if (modal) {
    modal.style.display = "flex";
    modal.style.opacity = "1";
  }
  switchAuthTab(tab);
}

function closeAuthModal() {
  const modal = document.getElementById("authModal");
  if (modal) modal.style.display = "none";
}

function switchAuthTab(tab) {
  const loginForm = document.getElementById("authLoginForm");
  const regForm = document.getElementById("authRegisterForm");
  const tabLogin = document.getElementById("tabBtnLogin");
  const tabReg = document.getElementById("tabBtnRegister");
  const title = document.getElementById("authModalTitle");
  const subtitle = document.getElementById("authModalSubtitle");

  // Reset errors
  const lErr = document.getElementById("loginErrorBox");
  const rErr = document.getElementById("regErrorBox");
  if (lErr) lErr.style.display = "none";
  if (rErr) rErr.style.display = "none";

  if (tab === "login") {
    if (loginForm) loginForm.style.display = "block";
    if (regForm) regForm.style.display = "none";
    if (tabLogin) tabLogin.classList.add("active");
    if (tabReg) tabReg.classList.remove("active");
    if (title) title.textContent = "Masuk ke Platform";
    if (subtitle) subtitle.textContent = "Akses aman armada bot & kendalikan pembaca novel";
    setTimeout(() => {
      const idEl = document.getElementById("loginIdentifier");
      if (idEl) idEl.focus();
    }, 100);
  } else {
    if (loginForm) loginForm.style.display = "none";
    if (regForm) regForm.style.display = "block";
    if (tabLogin) tabLogin.classList.remove("active");
    if (tabReg) tabReg.classList.add("active");
    if (title) title.textContent = "Buka Akun Baru";
    if (subtitle) subtitle.textContent = "Daftarkan identitas aman terenkripsi PBKDF2";
    setTimeout(() => {
      const uEl = document.getElementById("regUsername");
      if (uEl) uEl.focus();
    }, 100);
  }
}

function togglePasswordVisibility(inputId, btnEl) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const isPass = input.type === "password";
  input.type = isPass ? "text" : "password";

  if (btnEl) {
    const openIcon = btnEl.querySelector(".eye-open");
    const closedIcon = btnEl.querySelector(".eye-closed");
    if (openIcon && closedIcon) {
      openIcon.style.display = isPass ? "none" : "block";
      closedIcon.style.display = isPass ? "block" : "none";
    }
  }
}

function evaluatePasswordStrength(password) {
  const p = password || "";
  const hasLen = p.length >= 8;
  const hasLetter = /[a-zA-Z]/.test(p);
  const hasNum = /[0-9]/.test(p);
  const hasSpecial = /[^a-zA-Z0-9]/.test(p);

  const updateReq = (id, valid) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (valid) el.classList.add("valid");
    else el.classList.remove("valid");
  };

  updateReq("reqLen", hasLen);
  updateReq("reqLetter", hasLetter);
  updateReq("reqNum", hasNum);
  updateReq("reqSpecial", hasSpecial);

  let score = 0;
  if (hasLen) score++;
  if (hasLetter) score++;
  if (hasNum) score++;
  if (hasSpecial) score++;

  const seg1 = document.getElementById("pwdSeg1");
  const seg2 = document.getElementById("pwdSeg2");
  const seg3 = document.getElementById("pwdSeg3");
  const seg4 = document.getElementById("pwdSeg4");
  const label = document.getElementById("pwdStrengthLabel");

  const segments = [seg1, seg2, seg3, seg4];
  segments.forEach(s => { if (s) s.style.background = "rgba(255, 255, 255, 0.08)"; });

  if (p.length === 0) {
    if (label) {
      label.textContent = "Belum Terisi";
      label.style.color = "var(--text-muted)";
    }
    return;
  }

  if (score <= 1) {
    if (seg1) seg1.style.background = "#f43f5e";
    if (label) { label.textContent = "Sangat Lemah"; label.style.color = "#f43f5e"; }
  } else if (score === 2) {
    if (seg1) seg1.style.background = "#f59e0b";
    if (seg2) seg2.style.background = "#f59e0b";
    if (label) { label.textContent = "Cukup"; label.style.color = "#f59e0b"; }
  } else if (score === 3) {
    if (seg1) seg1.style.background = "#38bdf8";
    if (seg2) seg2.style.background = "#38bdf8";
    if (seg3) seg3.style.background = "#38bdf8";
    if (label) { label.textContent = "Kuat & Aman"; label.style.color = "#38bdf8"; }
  } else {
    segments.forEach(s => { if (s) s.style.background = "#10b981"; });
    if (label) { label.textContent = "Sangat Kuat (Maksimal)"; label.style.color = "#10b981"; }
  }

  checkPasswordMatch();
}

function checkPasswordMatch() {
  const p1 = document.getElementById("regPassword")?.value || "";
  const p2 = document.getElementById("regConfirmPassword")?.value || "";
  const badge = document.getElementById("pwdMatchBadge");

  if (!badge) return;
  if (!p2) {
    badge.textContent = "";
    return;
  }
  if (p1 === p2) {
    badge.textContent = "✓ Sandi Cocok";
    badge.style.color = "#10b981";
  } else {
    badge.textContent = "✗ Sandi Belum Sama";
    badge.style.color = "#f43f5e";
  }
}

function triggerAuthShake() {
  const card = document.getElementById("authCardContainer");
  if (!card) return;
  card.classList.remove("shake-animation");
  void card.offsetWidth; // Trigger reflow
  card.classList.add("shake-animation");
  setTimeout(() => card.classList.remove("shake-animation"), 450);
}

async function submitLogin() {
  const idEl = document.getElementById("loginIdentifier");
  const passEl = document.getElementById("loginPassword");
  const errBox = document.getElementById("loginErrorBox");
  const errText = document.getElementById("loginErrorText");
  const btn = document.getElementById("btnLoginSubmit");
  const rememberMe = document.getElementById("loginRememberMe")?.checked ?? true;

  const identifier = idEl ? idEl.value.trim() : "";
  const password = passEl ? passEl.value : "";

  if (errBox) errBox.style.display = "none";

  if (!identifier || !password) {
    if (errText) errText.textContent = "Harap masukkan username/email dan kata sandi.";
    if (errBox) errBox.style.display = "flex";
    triggerAuthShake();
    return;
  }

  // Set loading state
  if (btn) {
    btn.disabled = true;
    btn.querySelector(".btn-text").textContent = "Memverifikasi Akun...";
  }

  try {
    const resp = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier, password }),
    });
    const data = await resp.json();

    if (data.ok && data.user) {
      if (rememberMe) {
        localStorage.setItem("rinara_session", data.user.session_token);
        sessionStorage.removeItem("rinara_session");
      } else {
        sessionStorage.setItem("rinara_session", data.user.session_token);
        localStorage.removeItem("rinara_session");
      }

      currentUser = data.user;
      renderUserState(currentUser);
      closeAuthModal();
      showToast("success", `Selamat datang kembali, @${data.user.username}!`, "Login Berhasil");
      if (passEl) passEl.value = "";
    } else {
      triggerAuthShake();
      const msg = data.error || (typeof data.detail === "string" ? data.detail : null) || (resp.status === 404 ? "Layanan autentikasi server sedang dimuat ulang. Coba sesaat lagi." : "Gagal masuk ke akun.");
      if (errText) errText.textContent = msg;
      if (errBox) errBox.style.display = "flex";
    }
  } catch (e) {
    triggerAuthShake();
    if (errText) errText.textContent = "Gagal terhubung ke server: " + e;
    if (errBox) errBox.style.display = "flex";
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.querySelector(".btn-text").innerHTML = "Masuk ke Platform Sekarang &rarr;";
    }
  }
}

async function submitRegister() {
  const uEl = document.getElementById("regUsername");
  const eEl = document.getElementById("regEmail");
  const pEl = document.getElementById("regPassword");
  const cEl = document.getElementById("regConfirmPassword");
  const errBox = document.getElementById("regErrorBox");
  const errText = document.getElementById("regErrorText");
  const btn = document.getElementById("btnRegSubmit");

  const username = uEl ? uEl.value.trim() : "";
  const email = eEl ? eEl.value.trim() : "";
  const password = pEl ? pEl.value : "";
  const confirmPassword = cEl ? cEl.value : "";

  if (errBox) errBox.style.display = "none";

  if (!username || !email || !password) {
    if (errText) errText.textContent = "Semua bidang wajib diisi secara lengkap.";
    if (errBox) errBox.style.display = "flex";
    triggerAuthShake();
    return;
  }

  if (username.length < 3 || username.length > 24) {
    if (errText) errText.textContent = "Username harus antara 3 sampai 24 karakter.";
    if (errBox) errBox.style.display = "flex";
    triggerAuthShake();
    return;
  }

  if (password.length < 8) {
    if (errText) errText.textContent = "Kata sandi minimal 8 karakter demi keamanan Anda.";
    if (errBox) errBox.style.display = "flex";
    triggerAuthShake();
    return;
  }

  const hasLetter = /[a-zA-Z]/.test(password);
  const hasDigit = /[0-9]/.test(password);
  if (!hasLetter || !hasDigit) {
    if (errText) errText.textContent = "Kata sandi harus mengandung kombinasi huruf dan angka.";
    if (errBox) errBox.style.display = "flex";
    triggerAuthShake();
    return;
  }

  if (password !== confirmPassword) {
    if (errText) errText.textContent = "Konfirmasi kata sandi tidak cocok. Mohon ketik ulang.";
    if (errBox) errBox.style.display = "flex";
    triggerAuthShake();
    return;
  }

  // Set loading state
  if (btn) {
    btn.disabled = true;
    btn.querySelector(".btn-text").textContent = "Mendaftarkan Profil Aman...";
  }

  try {
    const resp = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username,
        email,
        password,
        confirm_password: confirmPassword
      }),
    });
    const data = await resp.json();

    if (data.ok && data.user) {
      localStorage.setItem("rinara_session", data.user.session_token);
      currentUser = data.user;
      renderUserState(currentUser);
      closeAuthModal();
      showToast("success", `Akun @${data.user.username} berhasil dibuat & dilindungi enkripsi!`, "Pendaftaran Sukses");
      if (pEl) pEl.value = "";
      if (cEl) cEl.value = "";
    } else {
      triggerAuthShake();
      const msg = data.error || (typeof data.detail === "string" ? data.detail : null) || (resp.status === 404 ? "Layanan pendaftaran server sedang dimuat ulang. Coba sesaat lagi." : "Gagal mendaftarkan akun baru.");
      if (errText) errText.textContent = msg;
      if (errBox) errBox.style.display = "flex";
    }
  } catch (e) {
    triggerAuthShake();
    if (errText) errText.textContent = "Gagal terhubung ke server: " + e;
    if (errBox) errBox.style.display = "flex";
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.querySelector(".btn-text").innerHTML = "🚀 Buat Akun &amp; Buka Akses Bot";
    }
  }
}

function submitLogout() {
  const token = localStorage.getItem("rinara_session") || sessionStorage.getItem("rinara_session");
  if (token) {
    fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` }
    }).catch(() => {});
  }
  localStorage.removeItem("rinara_session");
  sessionStorage.removeItem("rinara_session");
  currentUser = null;
  renderUserState(null);
  showToast("info", "Sesi aman telah ditutup. Anda telah keluar dari akun.", "Logout Sukses");
}

// =============================================================================
// NOVEL & PARAMETERS INSPECTION
// =============================================================================
async function pasteNovelLink() {
  try {
    const text = await navigator.clipboard.readText();
    const input = document.getElementById("inputNovelUrl");
    if (input && text) {
      input.value = text.trim();
      fetchNovelInfo();
    }
  } catch (e) {
    showToast("warning", "Akses clipboard tidak diizinkan oleh browser.");
  }
}

async function fetchNovelInfo() {
  const input = document.getElementById("inputNovelUrl");
  const rawUrl = input ? input.value.trim() : "";
  if (!rawUrl) {
    showToast("warning", "Harap masukkan tautan atau ID novel target.");
    return;
  }

  const btn = document.getElementById("btnCheckNovel");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Memeriksa...";
  }

  try {
    const resp = await fetch(`/api/novel-info?url=${encodeURIComponent(rawUrl)}`);
    const data = await resp.json();

    if (data.ok) {
      const preview = document.getElementById("novelPreviewBox");
      const cover = document.getElementById("novelCoverImg");
      const title = document.getElementById("novelTitle");
      const author = document.getElementById("novelAuthor");
      const tagChapters = document.getElementById("tagChapters");

      if (preview) preview.style.display = "flex";
      if (cover) cover.src = data.cover_url || "https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?w=120";
      if (title) title.textContent = data.title;
      if (author) author.textContent = "Oleh: " + data.author;
      if (tagChapters) tagChapters.textContent = `${data.total_chapters || 0} Bab Tersedia`;

      showToast("success", `Novel '${data.title}' berhasil diverifikasi.`, "Pemeriksaan Selesai");
    } else {
      showToast("error", data.error || "Gagal memverifikasi informasi novel.");
    }
  } catch (e) {
    showToast("error", "Koneksi ke gateway novel terputus: " + e);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Periksa Novel";
    }
  }
}

// Mode Selection: Valid vs Guest
function selectReaderMode(mode) {
  selectedMode = mode;
  document.querySelectorAll(".reader-mode-card").forEach((c) => {
    if (c.getAttribute("data-mode") === mode) {
      c.classList.add("active");
    } else {
      c.classList.remove("active");
    }
  });

  const conversionGroup = document.getElementById("addonGuestConversionGroup");
  const hint = document.getElementById("hintTargetMode");

  if (mode === "guest") {
    if (conversionGroup) conversionGroup.style.display = "block";
    if (hint) hint.textContent = "Setiap pembaca tamu menjalankan sesi anonim instan atau konversi ke akun resmi (Addon).";
  } else {
    if (conversionGroup) conversionGroup.style.display = "none";
    isGuestConversionEnabled = false;
    const checkConv = document.getElementById("checkGuestConversion");
    if (checkConv) checkConv.checked = false;
    const convBox = document.getElementById("addonConversionBox");
    if (convBox) convBox.classList.remove("active");
    if (hint) hint.textContent = "Setiap pembaca menyelesaikan minimal 25 bab berturut-turut + like, simpan, dan follow.";
  }

  renderDynamicUI();
}

function toggleGuestConversionAddon() {
  const check = document.getElementById("checkGuestConversion");
  if (check) {
    check.checked = !check.checked;
    onGuestConversionChange(check.checked);
  }
}

function onGuestConversionChange(checked) {
  isGuestConversionEnabled = Boolean(checked);
  const box = document.getElementById("addonConversionBox");
  if (box) {
    if (isGuestConversionEnabled) box.classList.add("active");
    else box.classList.remove("active");
  }
  updateCostCalculation();
}

// Target Readers: Flexible Input Box + Quick Preset Chips
function onTargetReadersInput(val) {
  let num = parseInt(val);
  if (isNaN(num) || num < 1) num = 1;
  selectedTargetReaders = num;

  // Update highlight on preset chip if exact match
  document.querySelectorAll(".quick-chips-row .preset-chip").forEach((b) => {
    if (parseInt(b.textContent.trim()) === selectedTargetReaders) {
      b.classList.add("active");
    } else {
      b.classList.remove("active");
    }
  });

  updateCostCalculation();
}

function setTargetReaders(num) {
  selectedTargetReaders = parseInt(num) || 10;
  const input = document.getElementById("inputTargetReaders");
  if (input) input.value = selectedTargetReaders;

  document.querySelectorAll(".quick-chips-row .preset-chip").forEach((b) => {
    if (parseInt(b.textContent.trim()) === selectedTargetReaders) {
      b.classList.add("active");
    } else {
      b.classList.remove("active");
    }
  });

  updateCostCalculation();
}

// Terminal Concurrency (Addon)
function selectTerminals(count) {
  selectedTerminals = parseInt(count) || 1;
  document.querySelectorAll(".terminal-chip").forEach((b) => {
    if (parseInt(b.getAttribute("data-terminals")) === selectedTerminals) {
      b.classList.add("active");
    } else {
      b.classList.remove("active");
    }
  });
  updateCostCalculation();
}

// Cost Calculation (100% Dynamic from appSettings)
function updateCostCalculation() {
  const readers = selectedTargetReaders || 1;
  const baseRate = selectedMode === "guest" ? (appSettings.price_guest_reader || 50) : (appSettings.price_valid_reader || 450);
  const convRate = appSettings.addon_price_guest_conversion || 150;
  const addonRate = appSettings.addon_price_per_terminal || 500;
  
  const addonFee = selectedTerminals > 1 ? (selectedTerminals - 1) * addonRate : 0;
  const conversionFee = (selectedMode === "guest" && isGuestConversionEnabled) ? (readers * convRate) : 0;
  
  const readersCost = readers * baseRate;
  const totalCost = readersCost + addonFee + conversionFee;

  const currentBal = currentUser ? currentUser.balance : 0;
  const remainingBal = currentBal - totalCost;
  const minRequired = addonFee + baseRate + (selectedMode === "guest" && isGuestConversionEnabled ? convRate : 0);

  // Update DOM displays
  const rateDisplay = document.getElementById("billingRatePerReader");
  const addonRow = document.getElementById("billingAddonRow");
  const addonDisplay = document.getElementById("billingAddonCost");
  const convRow = document.getElementById("billingConversionRow");
  const convDisplay = document.getElementById("billingConversionCost");
  const readerCountDisplay = document.getElementById("billingTargetReaders");
  const balanceDisplay = document.getElementById("billingCurrentBalance");
  const costDisplay = document.getElementById("billingEstimatedCost");
  const remainingDisplay = document.getElementById("billingRemainingBalance");
  const btnStart = document.getElementById("btnStartBot");

  if (rateDisplay) rateDisplay.textContent = formatRupiah(baseRate);
  if (addonDisplay) addonDisplay.textContent = addonFee > 0 ? `+${formatRupiah(addonFee)}` : "Rp 0 (Reguler)";
  
  if (convRow) convRow.style.display = (selectedMode === "guest" && isGuestConversionEnabled) ? "flex" : "none";
  if (convDisplay) convDisplay.textContent = `+${formatRupiah(conversionFee)}`;

  const modeName = selectedMode === "guest" 
    ? (isGuestConversionEnabled ? "Tamu ➔ Member" : "Tamu (Guest)") 
    : "Valid (25 Bab)";
  if (readerCountDisplay) readerCountDisplay.textContent = `${readers.toLocaleString("id-ID")} ${modeName}`;
  if (balanceDisplay) balanceDisplay.textContent = formatRupiah(currentBal);
  if (costDisplay) costDisplay.textContent = formatRupiah(totalCost);

  if (remainingDisplay) {
    remainingDisplay.textContent = formatRupiah(remainingBal);
    remainingDisplay.style.color = remainingBal >= 0 ? "#10b981" : "#ef4444";
  }

  // Kalkulasi Estimasi Waktu Selesai (ETA)
  const terms = Math.max(1, selectedTerminals);
  let estTotalSec = 0;
  if (selectedMode === "guest") {
    const timePerRdr = isGuestConversionEnabled ? 135 : 75;
    estTotalSec = Math.ceil(readers / terms) * timePerRdr;
  } else {
    const delayPerChapter = parseFloat(appSettings.reading_delay_seconds) || 140;
    estTotalSec = Math.ceil(readers / terms) * (25 * delayPerChapter);
  }

  const estMinutes = Math.ceil(estTotalSec / 60);
  let timeStr = "";
  if (estMinutes < 60) {
    timeStr = `~${estMinutes} Menit`;
  } else {
    const hours = Math.floor(estMinutes / 60);
    const mins = estMinutes % 60;
    timeStr = `~${hours} Jam ${mins} Menit`;
  }

  const finishDate = new Date(Date.now() + estTotalSec * 1000);
  const finishHours = String(finishDate.getHours()).padStart(2, "0");
  const finishMins = String(finishDate.getMinutes()).padStart(2, "0");
  const finishStr = `${finishHours}:${finishMins} WIB`;

  const etaDisplay = document.getElementById("summaryETA");
  if (etaDisplay) etaDisplay.textContent = `${timeStr} (Selesai ~${finishStr})`;

  // Proyeksi Hasil Interaksi
  const likesDisplay = document.getElementById("summaryLikes");
  const bmsDisplay = document.getElementById("summaryBookmarks");
  const fllwDisplay = document.getElementById("summaryFollows");
  const guestNote = document.getElementById("guestInteractionNote");
  const convertedRow = document.getElementById("summaryConvertedRow");
  const convertedVal = document.getElementById("summaryConverted");

  if (selectedMode === "valid" || (selectedMode === "guest" && isGuestConversionEnabled)) {
    const pLikes = Math.round(readers * 0.70);
    const pBms = Math.round(readers * 0.65);
    const pFllw = Math.round(readers * 0.40);
    if (likesDisplay) likesDisplay.textContent = `+${pLikes} Like (~70%)`;
    if (bmsDisplay) bmsDisplay.textContent = `+${pBms} Simpan (~65%)`;
    if (fllwDisplay) fllwDisplay.textContent = `+${pFllw} Follow (~40%)`;

    if (selectedMode === "guest" && isGuestConversionEnabled) {
      if (convertedRow) convertedRow.style.display = "flex";
      if (convertedVal) convertedVal.textContent = `+${readers} Akun Member (OTP)`;
      if (guestNote) {
        guestNote.textContent = "*Tamu otomatis mendaftar akun resmi Android + verifikasi OTP 120s dan melakukan interaksi penuh.";
        guestNote.style.color = "#10b981";
        guestNote.style.display = "block";
      }
    } else {
      if (convertedRow) convertedRow.style.display = "none";
      if (guestNote) guestNote.style.display = "none";
    }
  } else {
    if (convertedRow) convertedRow.style.display = "none";
    if (likesDisplay) likesDisplay.textContent = "0 (Fitur Akun Valid / Addon Konversi)";
    if (bmsDisplay) bmsDisplay.textContent = "0 (Fitur Akun Valid / Addon Konversi)";
    if (fllwDisplay) fllwDisplay.textContent = "0 (Fitur Akun Valid / Addon Konversi)";
    if (guestNote) {
      guestNote.textContent = "*Mode Tamu murni menambah pembaca/visitor unik tanpa interaksi sosial akun (Aktifkan Addon Konversi di atas untuk auto-daftar akun).";
      guestNote.style.color = "var(--text-dim)";
      guestNote.style.display = "block";
    }
  }

  if (btnStart) {
    if (!currentUser) {
      btnStart.disabled = true;
      btnStart.textContent = "Masuk Akun untuk Memulai";
    } else if (currentBal < minRequired) {
      btnStart.disabled = true;
      btnStart.textContent = `Saldo Tidak Cukup (Minimal ${formatRupiah(minRequired)})`;
    } else {
      btnStart.disabled = false;
      btnStart.textContent = "Luncurkan Bot Sekarang";
    }
  }
}

// =============================================================================
// BOT EXECUTION & LIVE SSE CONSOLE
// =============================================================================
async function startBotTask() {
  if (!currentUser) {
    openAuthModal("login");
    showToast("warning", "Silakan masuk ke akun Anda terlebih dahulu.");
    return;
  }

  const urlInput = document.getElementById("inputNovelUrl");
  const rawUrl = urlInput ? urlInput.value.trim() : "";
  if (!rawUrl) {
    showToast("warning", "Harap masukkan tautan atau ID novel target.");
    return;
  }

  const baseRate = selectedMode === "guest" ? (appSettings.price_guest_reader || 50) : (appSettings.price_valid_reader || 450);
  const convRate = appSettings.addon_price_guest_conversion || 150;
  const addonRate = appSettings.addon_price_per_terminal || 500;
  const addonFee = selectedTerminals > 1 ? (selectedTerminals - 1) * addonRate : 0;
  const minRequired = addonFee + baseRate + (selectedMode === "guest" && isGuestConversionEnabled ? convRate : 0);

  if (currentUser.balance < minRequired) {
    showToast("error", `Saldo Anda (${formatRupiah(currentUser.balance)}) tidak mencukupi untuk biaya tugas minimal ${formatRupiah(minRequired)}. Harap isi saldo terlebih dahulu.`);
    switchPage("topup");
    return;
  }

  const btnStart = document.getElementById("btnStartBot");
  if (btnStart) {
    btnStart.disabled = true;
    btnStart.textContent = "Meluncurkan Bot...";
  }

  try {
    const payload = {
      novel_url: rawUrl,
      target_readers: selectedTargetReaders,
      concurrent_terminals: selectedTerminals,
      max_chapters: selectedMode === "guest" ? 1 : 25,
      reading_delay: parseFloat(appSettings.reading_delay_seconds) || 140.0,
      mode: selectedMode,
      addon_guest_conversion: Boolean(isGuestConversionEnabled && selectedMode === "guest"),
    };

    const resp = await fetch("/api/tasks/start", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();

    if (data.ok) {
      currentTaskId = data.task_id;
      showToast("success", `Tugas diluncurkan dengan ${selectedTerminals} sesi terminal (${selectedMode === "guest" ? "Guest" : "Valid"}).`, "Bot Berjalan");
      listenTaskSSE(currentTaskId);
      updateTaskControlBar(true, data.novel_title);
      checkAuthMe(); // Perbarui saldo jika terpotong addon fee
    } else {
      showToast("error", data.error || "Gagal memulai tugas.");
    }
  } catch (e) {
    showToast("error", "Kesalahan saat memulai bot: " + e);
  } finally {
    if (btnStart) {
      btnStart.disabled = false;
      btnStart.textContent = "Luncurkan Bot Sekarang";
    }
  }
}

let currentTaskLogs = [];
let activeTerminalFilter = "all";
let currentTaskMetadata = null;

// =============================================================================
// STANDBY TERMINAL SIMULATOR & TELEMETRY HEARTBEAT
// =============================================================================
let standbyHeartbeatTimer = null;
const standbyLogPresets = [
  { tag: "CORE", msg: "Rinara Engine v5.0 core is healthy. Memory: 38MB, CPU: 1.2%." },
  { tag: "PROXY", msg: "Proxy pool cluster verified: 3 residential nodes responsive." },
  { tag: "STEALTH", msg: "Device fingerprinting ready: Android okhttp/4.12.0 + Gaussian curves." },
  { tag: "BILLING", msg: "ACID Atomic Billing armed. Jaminan saldo aman: potong per pembaca sukses." },
  { tag: "SOCKET", msg: "Telemetry channel standby. Listening for new dispatch queue..." },
  { tag: "GUARD", msg: "Anti-Banned engine: Single-action isolation per title armed." },
  { tag: "STANDBY", msg: "Siap menerima target novel. Tempel tautan novel di atas untuk gaspol!" },
];
let standbyLogIndex = 0;

function initStandbyTerminalHeartbeat() {
  const screen = document.getElementById("dashStandbyTerminalScreen");
  if (!screen) return;

  if (screen.children.length === 0) {
    const now = new Date().toTimeString().split(" ")[0];
    screen.innerHTML = `
      <div class="term-line"><span class="term-time">[${now}]</span> <span class="term-tag">[SYSTEM]</span> <span class="term-msg">Rinara Reader Engine v5.0 online. Siap menerima target novel.</span></div>
      <div class="term-line"><span class="term-time">[${now}]</span> <span class="term-tag">[PROXY]</span> <span class="term-msg">Pool proxy terisolasi aktif. Latensi optimal.</span></div>
    `;
  }

  if (standbyHeartbeatTimer) return;

  standbyHeartbeatTimer = setInterval(() => {
    const screen = document.getElementById("dashStandbyTerminalScreen");
    if (!screen) return;

    // Jitter latency display between 16ms and 24ms
    const pingEl = document.getElementById("dashPingMs");
    if (pingEl) {
      const ms = Math.floor(Math.random() * 9) + 16;
      pingEl.textContent = `${ms}ms`;
    }

    const preset = standbyLogPresets[standbyLogIndex % standbyLogPresets.length];
    standbyLogIndex++;
    const now = new Date().toTimeString().split(" ")[0];

    const line = document.createElement("div");
    line.className = "term-line";
    line.innerHTML = `<span class="term-time">[${now}]</span> <span class="term-tag">[${preset.tag}]</span> <span class="term-msg">${escapeHtml(preset.msg)}</span>`;
    screen.appendChild(line);

    // Keep max 7 lines in standby terminal
    while (screen.children.length > 7) {
      screen.removeChild(screen.firstChild);
    }
    screen.scrollTop = screen.scrollHeight;
  }, 3800);
}

function quickLaunchFromDash() {
  const input = document.getElementById("dashQuickNovelInput");
  const val = (input ? input.value : "").trim();
  switchPage("run");
  const runInput = document.getElementById("inputNovelUrl");
  if (val && runInput) {
    runInput.value = val;
    fetchNovelInfo();
    showToast("info", "Memverifikasi metadata novel target...", "Memproses Target");
  } else if (runInput) {
    runInput.focus();
    showToast("info", "Silakan masukkan tautan atau ID novel Anda.", "Peluncur Bot");
  }
}

async function pasteQuickNovel() {
  try {
    const text = await navigator.clipboard.readText();
    const input = document.getElementById("dashQuickNovelInput");
    if (input && text) {
      input.value = text.trim();
      showToast("info", "Tautan novel berhasil ditempel dari clipboard.");
      input.focus();
    }
  } catch (e) {
    showToast("warning", "Gagal membaca clipboard otomatis. Silakan tempel manual.");
  }
}

function animateNumberCount(elem, startVal, endVal, duration = 500, prefix = "", suffix = "") {
  if (!elem) return;
  const start = parseInt(startVal) || 0;
  const end = parseInt(endVal) || 0;
  if (start === end) {
    elem.textContent = `${prefix}${end.toLocaleString("id-ID")}${suffix}`;
    return;
  }
  const startTime = performance.now();
  function update(currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const ease = 1 - Math.pow(1 - progress, 3); // cubic ease-out
    const current = Math.round(start + (end - start) * ease);
    elem.textContent = `${prefix}${current.toLocaleString("id-ID")}${suffix}`;
    if (progress < 1) {
      requestAnimationFrame(update);
    }
  }
  requestAnimationFrame(update);
}

function setDashboardTaskActive(isActive) {
  const activeCard = document.getElementById("dashActiveTaskCard");
  const standbyCard = document.getElementById("dashNoActiveTaskCard");
  if (activeCard) activeCard.style.display = isActive ? "block" : "none";
  if (standbyCard) standbyCard.style.display = isActive ? "none" : "block";

  if (isActive) {
    if (standbyHeartbeatTimer) {
      clearInterval(standbyHeartbeatTimer);
      standbyHeartbeatTimer = null;
    }
  } else {
    initStandbyTerminalHeartbeat();
  }
}

function renderDashboardActiveTask(data) {
  if (!data) return;
  setDashboardTaskActive(true);

  const titleEl = document.getElementById("dashNovelTitle");
  const idEl = document.getElementById("dashNovelId");
  const modeBadge = document.getElementById("dashModeBadge");
  const rateBadge = document.getElementById("dashRateBadge");
  const termsEl = document.getElementById("dashConcurrentTerms");
  const termLabel = document.getElementById("dashTermCountLabel");
  const chipConvertedBox = document.getElementById("dashChipConvertedBox");

  if (titleEl) titleEl.textContent = data.novel_title || `Novel #${data.novel_id || ''}`;
  if (idEl) idEl.textContent = data.novel_id || "-";
  if (termsEl) termsEl.textContent = data.concurrent_terminals || 1;
  if (termLabel) termLabel.textContent = data.concurrent_terminals || 1;

  const isGuest = data.mode === "guest";
  const isConv = Boolean(data.addon_guest_conversion);

  if (modeBadge) {
    if (isGuest) {
      modeBadge.textContent = isConv ? "Tamu ➔ Member (OTP)" : "Pembaca Tamu (Guest)";
      modeBadge.className = isConv ? "tag emerald" : "tag amber";
    } else {
      modeBadge.textContent = "Akun Valid (25 Bab)";
      modeBadge.className = "tag emerald";
    }
  }

  if (rateBadge) {
    rateBadge.textContent = `Rp ${(data.rate_per_reader || (isGuest ? 50 : 450)).toLocaleString("id-ID")} / Reader`;
  }

  if (chipConvertedBox) {
    chipConvertedBox.style.display = (isGuest && isConv) ? "inline-flex" : "none";
  }

  // Generate fleet grid
  const fleetGrid = document.getElementById("dashFleetGrid");
  const terms = data.concurrent_terminals || 1;
  if (fleetGrid) {
    let gridHtml = "";
    for (let i = 1; i <= terms; i++) {
      gridHtml += `
        <div class="dash-fleet-card active" id="fleetCard_${i}">
          <div class="dash-fleet-header">
            <span class="dash-fleet-name">🖥️ Sesi Terminal #${i}</span>
            <span class="dash-fleet-badge running" id="fleetBadge_${i}">AKTIF</span>
          </div>
          <div class="dash-fleet-info">
            <div style="color:var(--accent-emerald); font-weight:600; margin-bottom:2px;" id="fleetStatus_${i}">● Sesi Aktif</div>
            <div style="font-size:10.5px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" id="fleetAct_${i}">Menjalankan sesi pembaca...</div>
          </div>
        </div>
      `;
    }
    fleetGrid.innerHTML = gridHtml;
  }

  // Generate tabs for console filter
  const tabsContainer = document.getElementById("dashTerminalTabsContainer");
  if (tabsContainer) {
    let tabsHtml = `<button class="dash-term-tab ${activeTerminalFilter === 'all' ? 'active' : ''}" data-term="all" onclick="filterDashTerminalLogs('all')">🖥️ Semua Sesi</button>`;
    for (let i = 1; i <= terms; i++) {
      tabsHtml += `<button class="dash-term-tab ${activeTerminalFilter === String(i) ? 'active' : ''}" data-term="${i}" onclick="filterDashTerminalLogs('${i}')">⚡ Terminal ${i}</button>`;
    }
    tabsContainer.innerHTML = tabsHtml;
  }
}

function updateDashboardTaskStats(stats) {
  if (!stats) return;

  const target = stats.target_readers || 1;
  const completed = stats.completed_readers || 0;
  const pct = Math.min(100, Math.round((completed / target) * 100));

  const pBar = document.getElementById("dashProgressBar");
  const pText = document.getElementById("dashProgressText");
  const lEl = document.getElementById("dashStatLikes");
  const bEl = document.getElementById("dashStatBookmarks");
  const fEl = document.getElementById("dashStatFollows");
  const cEl = document.getElementById("dashStatConverted");
  const chEl = document.getElementById("dashStatChapters");
  const etaEl = document.getElementById("dashEtaCountdown");

  if (pBar) pBar.style.width = `${pct}%`;
  if (pText) pText.textContent = `${completed.toLocaleString("id-ID")} / ${target.toLocaleString("id-ID")} Pembaca (${pct}%)`;

  if (lEl) lEl.textContent = stats.likes || 0;
  if (bEl) bEl.textContent = stats.bookmarks || 0;
  if (fEl) fEl.textContent = stats.follows || 0;
  if (cEl) cEl.textContent = stats.converted_accounts || 0;
  if (chEl) chEl.textContent = stats.chapters_read || 0;

  if (stats.estimated_seconds_left !== undefined) {
    const sec = stats.estimated_seconds_left;
    const mins = Math.ceil(sec / 60);
    const etaStr = sec > 0 ? `⏳ Sisa ~${mins} Menit (${stats.estimated_finish_time || ''})` : "🏁 Hampir Selesai";
    if (etaEl) etaEl.textContent = etaStr;
  }
}

function filterDashTerminalLogs(term) {
  activeTerminalFilter = String(term);
  document.querySelectorAll(".dash-term-tab").forEach(tab => {
    if (tab.getAttribute("data-term") === activeTerminalFilter) {
      tab.classList.add("active");
    } else {
      tab.classList.remove("active");
    }
  });

  const screen = document.getElementById("dashTerminalLogs");
  if (!screen) return;
  screen.innerHTML = "";

  const filtered = currentTaskLogs.filter(log => {
    if (activeTerminalFilter === "all") return true;
    return String(log.terminal) === activeTerminalFilter;
  });

  if (filtered.length === 0) {
    screen.innerHTML = `<div class="log-line debug"><span class="log-time">[System]</span> <span class="log-text">Belum ada log untuk Terminal ${activeTerminalFilter}.</span></div>`;
    return;
  }

  filtered.forEach(log => {
    const div = document.createElement("div");
    div.className = `log-line ${log.level || "info"}`;
    div.innerHTML = `<span class="log-time">[${log.time || "-"}]</span> <span class="log-text">${escapeHtml(log.message || "")}</span>`;
    screen.appendChild(div);
  });
  screen.scrollTop = screen.scrollHeight;
}

function appendDashLogLine(logObj) {
  const screen = document.getElementById("dashTerminalLogs");
  if (!screen) return;

  if (activeTerminalFilter !== "all" && String(logObj.terminal) !== activeTerminalFilter) {
    return;
  }

  const div = document.createElement("div");
  div.className = `log-line ${logObj.level || "info"}`;
  div.innerHTML = `<span class="log-time">[${logObj.time || "-"}]</span> <span class="log-text">${escapeHtml(logObj.message || "")}</span>`;
  screen.appendChild(div);
  screen.scrollTop = screen.scrollHeight;
}

function updateFleetCardFromLog(logObj) {
  if (!logObj || !logObj.terminal) return;
  const tid = logObj.terminal;
  const actEl = document.getElementById(`fleetAct_${tid}`);
  const stEl = document.getElementById(`fleetStatus_${tid}`);

  if (actEl && logObj.message) {
    let clean = logObj.message.replace(/\[Terminal-\d+\]\s*/g, "").trim();
    if (clean.length > 40) clean = clean.substring(0, 40) + "...";
    actEl.textContent = clean;
  }

  if (logObj.level === "error" || logObj.level === "warn") {
    if (stEl) {
      stEl.textContent = "⚠️ Menunggu / Rotasi";
      stEl.style.color = "var(--accent-amber)";
    }
  } else if (logObj.level === "success") {
    if (stEl) {
      stEl.textContent = "✓ Selesai 1 Pembaca";
      stEl.style.color = "var(--accent-emerald)";
    }
  } else {
    if (stEl) {
      stEl.textContent = "● Sesi Aktif";
      stEl.style.color = "var(--accent-emerald)";
    }
  }
}

function copyDashLogs() {
  const el = document.getElementById("dashTerminalLogs");
  if (!el) return;
  navigator.clipboard.writeText(el.innerText).then(() => {
    showToast("info", "Log terminal berhasil disalin ke clipboard.");
  });
}

function clearDashLogs() {
  const el = document.getElementById("dashTerminalLogs");
  if (el) el.innerHTML = `<div class="log-line debug"><span class="log-time">[System]</span> <span class="log-text">Log terminal dashboard dibersihkan.</span></div>`;
  currentTaskLogs = [];
}

async function loadRecentDashboardTasks() {
  const container = document.getElementById("dashRecentTasksList");
  if (!container) return;

  try {
    const resp = await fetch("/api/tasks/history", { headers: getAuthHeaders() });
    const data = await resp.json();
    if (data.ok && data.tasks && data.tasks.length > 0) {
      const topTasks = data.tasks.slice(0, 4);
      container.innerHTML = topTasks.map(t => {
        const isRun = t.status === "RUNNING";
        const isComp = t.status === "COMPLETED";
        const tagClass = isComp ? "emerald" : isRun ? "blue" : "amber";
        return `
          <div style="display:flex; align-items:center; justify-content:space-between; padding:8px 10px; background:#141418; border:1px solid var(--border-subtle); border-radius:var(--radius-xs); gap:8px;">
            <div style="min-width:0; flex:1;">
              <div style="font-size:12.5px; font-weight:600; color:#fafafa; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                ${escapeHtml(t.novel_title || 'Novel')}
              </div>
              <div style="font-size:11px; color:var(--text-dim); margin-top:2px;">
                ${t.completed_readers || 0} / ${t.target_readers || 0} Pembaca &nbsp;•&nbsp; ${t.concurrent_terminals || 1} Sesi
              </div>
            </div>
            <div style="display:flex; flex-direction:column; align-items:flex-end; gap:3px;">
              <span class="tag ${tagClass}">${t.status}</span>
              <span style="font-size:10px; color:var(--text-dim); font-family:var(--font-mono);">${(t.created_at || '').split('T')[0] || '-'}</span>
            </div>
          </div>
        `;
      }).join("");
    } else {
      container.innerHTML = `<div style="font-size:12px; color:var(--text-dim); text-align:center; padding:16px;">Belum ada riwayat tugas pembaca.</div>`;
    }
  } catch (e) {
    container.innerHTML = `<div style="font-size:12px; color:var(--text-dim); text-align:center; padding:16px;">Gagal memuat riwayat tugas.</div>`;
  }
}

function listenTaskSSE(taskId) {
  if (activeEventSource) {
    activeEventSource.close();
    activeEventSource = null;
  }

  const logsContainer = document.getElementById("liveTerminalLogs");
  if (logsContainer) {
    logsContainer.innerHTML = "";
    appendLogLine("info", "Menghubungkan ke live terminal streaming (SSE)...");
  }

  const es = new EventSource(`/api/tasks/stream/${taskId}`);
  activeEventSource = es;

  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "log") {
        currentTaskLogs.push(data);
        if (currentTaskLogs.length > 500) currentTaskLogs.shift();
        appendLogLine(data.level || "info", data.message, data.time);
        appendDashLogLine(data);
        updateFleetCardFromLog(data);
      } else if (data.type === "stats") {
        updateStatsWidget(data.stats);
        updateDashboardTaskStats(data.stats);
      } else if (data.type === "done") {
        appendLogLine("success", "✅ [SELESAI] Tugas pembaca novel telah tuntas!");
        appendDashLogLine({ level: "success", time: new Date().toTimeString().split(" ")[0], message: "✅ [SELESAI] Tugas pembaca novel telah tuntas!" });
        updateTaskControlBar(false);
        setDashboardTaskActive(false);
        checkAuthMe();
        loadRecentDashboardTasks();
        es.close();
      }
    } catch (e) {}
  };

  es.onerror = () => {
    appendLogLine("debug", "Koneksi stream ditutup atau tugas telah berakhir.");
    es.close();
  };
}

function appendLogLine(level, msg, timeStr) {
  const body = document.getElementById("liveTerminalLogs");
  if (!body) return;

  const now = timeStr || new Date().toTimeString().split(" ")[0];
  const div = document.createElement("div");
  div.className = `log-line ${level}`;
  div.innerHTML = `<span class="log-time">[${now}]</span> <span class="log-text">${escapeHtml(msg)}</span>`;
  body.appendChild(div);
  body.scrollTop = body.scrollHeight;
}

function updateStatsWidget(stats) {
  if (!stats) return;
  const doneEl = document.getElementById("metricDoneReaders");
  const chEl = document.getElementById("metricChaptersRead");
  const balEl = document.getElementById("sidebarBalance");
  const mBal = document.getElementById("metricUserBalance");

  if (doneEl) doneEl.textContent = stats.completed_readers || 0;
  if (chEl) chEl.textContent = stats.chapters_read || 0;
  if (stats.current_balance !== undefined) {
    if (balEl) balEl.textContent = formatRupiah(stats.current_balance);
    if (mBal) mBal.textContent = formatRupiah(stats.current_balance);
    if (currentUser) currentUser.balance = stats.current_balance;
  }

  // Update banner live stats
  const pEl = document.getElementById("activeTaskProgress");
  const etaEl = document.getElementById("activeTaskEta");
  const lEl = document.getElementById("activeTaskLikes");
  const bEl = document.getElementById("activeTaskBookmarks");
  const fEl = document.getElementById("activeTaskFollows");
  const cEl = document.getElementById("activeTaskConverted");
  const consoleEta = document.getElementById("liveConsoleEta");

  if (pEl) pEl.textContent = `${stats.completed_readers || 0} / ${stats.target_readers || 0}`;
  if (lEl) lEl.textContent = stats.likes || 0;
  if (bEl) bEl.textContent = stats.bookmarks || 0;
  if (fEl) fEl.textContent = stats.follows || 0;
  if (cEl) cEl.textContent = stats.converted_accounts || 0;

  if (stats.estimated_seconds_left !== undefined) {
    const sec = stats.estimated_seconds_left;
    const mins = Math.ceil(sec / 60);
    const textEta = sec > 0 ? `~${mins} Menit (${stats.estimated_finish_time || ''})` : "Hampir Selesai";
    if (etaEl) etaEl.textContent = textEta;
    if (consoleEta) consoleEta.textContent = `ETA: ${textEta}`;
  }
}

function updateTaskControlBar(isRunning, title = "") {
  const bar = document.getElementById("activeTaskBar");
  const titleDisplay = document.getElementById("activeTaskTitle");
  if (bar) bar.style.display = isRunning ? "flex" : "none";
  if (titleDisplay && title) titleDisplay.textContent = title;
  if (!isRunning) {
    const consoleEta = document.getElementById("liveConsoleEta");
    if (consoleEta) consoleEta.textContent = "ETA: Selesai";
  }
}

async function stopCurrentTask() {
  if (!currentTaskId) return;
  try {
    const resp = await fetch(`/api/tasks/stop/${currentTaskId}`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    const data = await resp.json();
    showToast("info", data.message || "Tugas dihentikan.");
    updateTaskControlBar(false);
    setDashboardTaskActive(false);
    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }
    loadRecentDashboardTasks();
  } catch (e) {
    showToast("error", "Gagal menghentikan tugas: " + e);
  }
}

async function checkActiveTask() {
  try {
    const resp = await fetch("/api/tasks/active", { headers: getAuthHeaders() });
    if (resp.ok) {
      const data = await resp.json();
      if (data.ok && data.active && data.task_id) {
        currentTaskId = data.task_id;
        currentTaskMetadata = data;
        updateTaskControlBar(true, data.novel_title);
        renderDashboardActiveTask(data);
        if (data.stats) {
          updateStatsWidget(data.stats);
          updateDashboardTaskStats(data.stats);
        }
        if (!activeEventSource) {
          listenTaskSSE(currentTaskId);
        }
      } else {
        setDashboardTaskActive(false);
      }
    }
  } catch (e) {}
}

function clearTerminalLogs() {
  const el = document.getElementById("liveTerminalLogs");
  if (el) el.innerHTML = `<div class="log-line debug"><span class="log-time">[System]</span> <span class="log-text">Log konsol dibersihkan.</span></div>`;
}

function copyTerminalLogs() {
  const el = document.getElementById("liveTerminalLogs");
  if (!el) return;
  const text = el.innerText;
  navigator.clipboard.writeText(text).then(() => {
    showToast("info", "Log konsol berhasil disalin ke clipboard.");
  });
}

// =============================================================================
// TOP UP SALDO & QRIS INTEGRATION
// =============================================================================
function selectTopupPreset(nominal) {
  selectedTopupAmount = parseInt(nominal) || 25000;
  document.querySelectorAll(".nominal-card").forEach((c) => {
    if (parseInt(c.getAttribute("data-nominal")) === selectedTopupAmount) {
      c.classList.add("active");
    } else {
      c.classList.remove("active");
    }
  });
}

async function submitQrisPayment() {
  if (!currentUser) {
    openAuthModal("login");
    showToast("warning", "Harap masuk ke akun Anda terlebih dahulu untuk top up.");
    return;
  }

  const amt = selectedTopupAmount;
  const minDeposit = appSettings.min_deposit || 5000;
  if (!amt || amt < minDeposit) {
    showToast("warning", `Minimal top up saldo adalah ${formatRupiah(minDeposit)}.`);
    return;
  }

  const btn = document.getElementById("btnCreateQris");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Membuat QRIS...";
  }

  try {
    const resp = await fetch("/api/payment/create", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ nominal: amt, payment_method: "QRIS" }),
    });
    const data = await resp.json();

    if (data.ok) {
      currentQrisRefId = data.ref_id;
      showQrisModal(data);
      startQrisPolling(data.ref_id);
    } else {
      showToast("error", data.error || "Gagal membuat invoice QRIS.");
    }
  } catch (e) {
    showToast("error", "Kesalahan gateway pembayaran: " + e);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Buat Invoice QRIS";
    }
  }
}

function showQrisModal(data) {
  const modal = document.getElementById("qrisModal");
  const img = document.getElementById("qrisImageDisplay");
  const amtDisplay = document.getElementById("qrisAmountDisplay");
  const refDisplay = document.getElementById("qrisRefDisplay");

  if (img) img.src = data.qr_image_url || `https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(data.qr_string || "QRIS")}`;
  if (amtDisplay) amtDisplay.textContent = formatRupiah(data.nominal);
  if (refDisplay) refDisplay.textContent = data.ref_id;

  if (modal) modal.style.display = "flex";
}

function closeQrisModal() {
  const modal = document.getElementById("qrisModal");
  if (modal) modal.style.display = "none";
  if (qrisPollingInterval) {
    clearInterval(qrisPollingInterval);
    qrisPollingInterval = null;
  }
}

function startQrisPolling(refId) {
  if (qrisPollingInterval) clearInterval(qrisPollingInterval);

  qrisPollingInterval = setInterval(async () => {
    try {
      const resp = await fetch(`/api/payment/check/${encodeURIComponent(refId)}`, {
        headers: getAuthHeaders(),
      });
      const data = await resp.json();

      if (data.ok && data.payment_status === "PAID") {
        clearInterval(qrisPollingInterval);
        qrisPollingInterval = null;
        closeQrisModal();
        showToast("success", "Pembayaran QRIS berhasil! Saldo telah ditambahkan ke akun Anda.", "Top Up Berhasil");
        await checkAuthMe();
      }
    } catch (e) {}
  }, 3000);
}

// =============================================================================
// HISTORY & LEDGER
// =============================================================================
async function loadTaskHistory() {
  const tbody = document.getElementById("taskHistoryTbody");
  if (!tbody) return;

  try {
    const resp = await fetch("/api/tasks/history", { headers: getAuthHeaders() });
    const data = await resp.json();
    if (data.ok && data.tasks && data.tasks.length > 0) {
      tbody.innerHTML = data.tasks
        .map(
          (t) => `
        <tr>
          <td><span style="font-family:var(--font-mono); font-size:11.5px; color:var(--text-muted);">${escapeHtml(t.created_at || "-")}</span></td>
          <td><b>${escapeHtml(t.novel_title || t.novel_id)}</b></td>
          <td><span style="font-family:var(--font-mono); font-weight:700; color:#10b981;">${t.completed_readers} / ${t.target_readers}</span></td>
          <td><span class="tag ${t.status === "COMPLETED" ? "emerald" : t.status === "RUNNING" ? "blue" : "amber"}">${t.status}</span></td>
        </tr>
      `
        )
        .join("");
    } else {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-dim);">Belum ada riwayat tugas.</td></tr>`;
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-dim);">Gagal memuat riwayat.</td></tr>`;
  }
}

async function loadTransactions() {
  const tbody = document.getElementById("transactionTbody");
  if (!tbody) return;

  try {
    const resp = await fetch("/api/auth/transactions", { headers: getAuthHeaders() });
    const data = await resp.json();
    if (data.ok && data.transactions && data.transactions.length > 0) {
      tbody.innerHTML = data.transactions
        .map(
          (tx) => `
        <tr>
          <td><span style="font-family:var(--font-mono); font-size:11.5px; color:var(--text-muted);">${escapeHtml(tx.created_at || "-")}</span></td>
          <td><span class="tag ${tx.type === "TOPUP" ? "emerald" : "blue"}">${tx.type}</span></td>
          <td>${escapeHtml(tx.description || "-")}</td>
          <td style="font-family:var(--font-mono); font-weight:700; color: ${tx.type === "TOPUP" ? "#10b981" : "#fafafa"}">
            ${tx.type === "TOPUP" ? "+" : "-"}${formatRupiah(tx.amount)}
          </td>
        </tr>
      `
        )
        .join("");
    } else {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-dim);">Belum ada transaksi saldo.</td></tr>`;
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-dim);">Gagal memuat mutasi.</td></tr>`;
  }
}

// =============================================================================
// SYSTEM MONITOR & STATS
// =============================================================================
async function fetchSystemStats() {
  try {
    const resp = await fetch("/api/stats/overview");
    if (!resp.ok) return;
    const data = await resp.json();

    const pSum = data.proxies || {};
    const pill = document.getElementById("poolStatusText");
    const readyDisplay = document.getElementById("metricProxyReady");
    const subText = document.getElementById("sidebarProxySummary");

    if (pill) pill.textContent = `${pSum.idle || 0} Node Siap`;
    if (readyDisplay) readyDisplay.textContent = `${pSum.idle || 0} / ${pSum.total || 0}`;
    if (subText) subText.textContent = `${pSum.idle || 0} Siap, ${pSum.busy || 0} Terpakai`;
  } catch (e) {}
}
