// =============================================================================
// RINARADEV AUTOMATION - PRODUCTION FRONTEND CONTROLLER
// Clean, Professional, Resilient State Management
// =============================================================================

let activeToken = localStorage.getItem("rinaradev_token") || localStorage.getItem("toodat_token") || "";
let activeBalance = 0;
let activeMode = "full_auto";
let currentTaskId = null;
let currentEventSource = null;
let adminPin = sessionStorage.getItem("rinaradev_admin_pin") || sessionStorage.getItem("toodat_admin_pin") || "";
let globalPricing = null;
let adminPackagesState = [];

document.addEventListener("DOMContentLoaded", async () => {
  // Load dynamic pricing and packages configuration from server
  await loadPricingConfig();

  // If stored novel URL exists, restore and inspect
  const savedNovel = localStorage.getItem("rinaradev_novel") || localStorage.getItem("toodat_novel");
  const novelInput = document.getElementById("novelUrlInput");
  if (savedNovel && novelInput) {
    novelInput.value = savedNovel;
    inspectNovel();
  }

  // If stored token exists, populate and verify
  if (activeToken) {
    const input = document.getElementById("userTokenInput");
    if (input) {
      input.value = activeToken;
      await verifyUserToken();
    }
  }

  // Auto-inspect if novel URL is pasted or enter key is pressed
  if (novelInput) {
    novelInput.addEventListener("paste", () => {
      setTimeout(inspectNovel, 250);
    });
    novelInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        inspectNovel();
      }
    });
  }

  // Check and reconnect to active running task if user refreshed page
  await checkAndResumeActiveTask();

  // If stored admin PIN exists, unlock owner panel
  if (adminPin) {
    // New sidebar-based admin layout
    const loginScreen = document.getElementById("loginScreen");
    const adminShell = document.getElementById("adminShell");
    if (loginScreen && adminShell) {
      loginScreen.style.display = "none";
      adminShell.style.display = "flex";
      loadAdminStats();
      loadAdminTokens();
      loadAdminAccounts();
      loadAdminPayments();
      loadAdminPricing();
    } else {
      // Legacy single-page admin layout fallback
      const loginCard = document.getElementById("adminLoginCard");
      const dashContent = document.getElementById("adminDashboardContent");
      if (loginCard && dashContent) {
        loginCard.style.display = "none";
        dashContent.style.display = "block";
        loadAdminStats();
        loadAdminTokens();
        loadAdminAccounts();
        loadAdminPayments();
        loadAdminPricing();
      }
    }
  }

  // Initial cost estimate calculation
  calculateEstimatedCost();
});

async function checkAndResumeActiveTask() {
  const savedTaskId = localStorage.getItem("rinaradev_active_task") || localStorage.getItem("toodat_active_task");
  const token = activeToken || (document.getElementById("userTokenInput") ? document.getElementById("userTokenInput").value.trim() : "");
  
  if (!token && !savedTaskId) return;

  try {
    let taskToResume = null;

    // 1. Cek via active token di server
    if (token) {
      const resp = await fetch(`/api/tasks/active?token=${encodeURIComponent(token)}`);
      if (resp.ok) {
        const data = await resp.json();
        if (data.ok && data.active) {
          taskToResume = data;
        }
      }
    }

    // 2. Fallback cek via savedTaskId jika ada
    if (!taskToResume && savedTaskId) {
      const resp = await fetch(`/api/tasks/status/${savedTaskId}`);
      if (resp.ok) {
        const data = await resp.json();
        if (data.ok && data.is_running) {
          taskToResume = data;
        } else {
          localStorage.removeItem("rinaradev_active_task");
          localStorage.removeItem("toodat_active_task");
        }
      }
    }

    if (taskToResume) {
      currentTaskId = taskToResume.task_id;
      localStorage.setItem("rinaradev_active_task", currentTaskId);
      
      const startBtn = document.getElementById("startBotBtn");
      const stopBtn = document.getElementById("stopBotBtn");
      if (startBtn) startBtn.style.display = "none";
      if (stopBtn) stopBtn.style.display = "inline-flex";

      const statusTag = document.getElementById("taskStatusTag");
      if (statusTag) statusTag.textContent = "Status: Berjalan (Tersambung Kembali)";

      if (taskToResume.stats) {
        updateStatsUI(taskToResume.stats);
      }

      appendLog("[System]", `Menyambungkan kembali ke tugas aktif (ID: ${currentTaskId})...`, "info");
      connectSSEStream(currentTaskId);
    }
  } catch (err) {
    console.error("Error checking active task:", err);
  }
}

// =============================================================================
// FORMATTING HELPERS
// =============================================================================
function formatRupiah(num) {
  return "Rp " + (num || 0).toLocaleString("id-ID");
}

// =============================================================================
// WHATSAPP ORDER PACKAGE
// =============================================================================
function orderWhatsAppPackage(amount, packageName) {
  const ownerWa = (globalPricing && globalPricing.owner_whatsapp) ? globalPricing.owner_whatsapp : "6287734343023";
  const text = `Halo Admin, saya ingin memesan ${packageName} (Rp ${amount.toLocaleString('id-ID')}) untuk RinaraDev Automation. Mohon informasi rekening / QRIS pembayaran. Terima kasih.`;
  const waUrl = `https://wa.me/${ownerWa}?text=${encodeURIComponent(text)}`;
  window.open(waUrl, "_blank");
}

// =============================================================================
// 1. TOKEN VERIFICATION & HUD
// =============================================================================
async function verifyUserToken() {
  const inputEl = document.getElementById("userTokenInput");
  const token = inputEl ? inputEl.value.trim().toUpperCase() : "";
  if (!token) {
    alert("Silakan masukkan Token Akses Anda terlebih dahulu.");
    return;
  }

  try {
    const resp = await fetch("/api/verify-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await resp.json();

    const statusBox = document.getElementById("tokenStatusBox");
    const balDisplay = document.getElementById("userBalanceDisplay");
    const capacityDisplay = document.getElementById("userMemberCapacityDisplay");
    const topupBtn = document.getElementById("tokenTopupBtn");
    const navChip = document.getElementById("navBalanceChip");
    const navVal = document.getElementById("navBalanceValue");

    if (data.ok) {
      activeToken = token;
      activeBalance = data.current_balance || 0;
      localStorage.setItem("rinaradev_token", token);

      if (statusBox) statusBox.style.display = "flex";
      if (balDisplay) balDisplay.textContent = formatRupiah(activeBalance);
      if (capacityDisplay) {
        const vrRate = (globalPricing && globalPricing.rates && globalPricing.rates.valid_reader) ? globalPricing.rates.valid_reader : 500;
        const grRate = (globalPricing && globalPricing.rates && globalPricing.rates.guest_reader) ? globalPricing.rates.guest_reader : 50;
        const sessions = Math.floor(activeBalance / vrRate);
        const guestSessions = Math.floor(activeBalance / grRate);
        capacityDisplay.textContent = `${sessions} Sesi Member (${guestSessions} Sesi Tamu)`;
      }

      if (navVal) {
        navVal.textContent = formatRupiah(activeBalance);
      }
      const walletBtn = document.getElementById("sidebarWalletBtn");
      if (walletBtn) {
        walletBtn.setAttribute("data-tooltip", `Saldo: ${formatRupiah(activeBalance)} (Klik Top Up)`);
      }

      const statBal = document.getElementById("statRemainingBal");
      if (statBal) statBal.textContent = formatRupiah(activeBalance);

      const clientGenToken = document.getElementById("clientGenTokenInput");
      if (clientGenToken && !clientGenToken.value) {
        clientGenToken.value = activeToken;
      }
      const clientGenBal = document.getElementById("clientGenBalanceDisplay");
      if (clientGenBal) {
        clientGenBal.textContent = formatRupiah(activeBalance);
      }

      if (topupBtn) {
        const ownerWa = (globalPricing && globalPricing.owner_whatsapp) ? globalPricing.owner_whatsapp : "6287734343023";
        const topupMsg = `Halo Admin, saya ingin top up saldo token ${token} senilai Rp 20.000. Mohon informasi rekening pembayaran.`;
        topupBtn.href = `https://wa.me/${ownerWa}?text=${encodeURIComponent(topupMsg)}`;
      }

      calculateEstimatedCost();
    } else {
      if (statusBox) statusBox.style.display = "none";
      if (navChip) navChip.style.display = "none";
      alert(data.error || "Token lisensi tidak valid atau telah kadaluarsa.");
      if (data.wa_url) {
        if (confirm("Saldo Anda habis atau token belum terdaftar. Ingin hubungi Admin via WhatsApp untuk beli token?")) {
          window.open(data.wa_url, "_blank");
        }
      }
    }
  } catch (err) {
    alert("Gagal memverifikasi token ke server: " + err);
  }
}

// =============================================================================
// 2. NOVEL INSPECTION WITH ANIMATED SKELETON LOADING
// =============================================================================
async function inspectNovel() {
  const inputEl = document.getElementById("novelUrlInput");
  const raw = inputEl ? inputEl.value.trim() : "";
  if (!raw) return;

  const btnInspect = document.getElementById("btnInspectNovel");
  const btnInspectIcon = document.getElementById("btnInspectIcon");
  const btnInspectText = document.getElementById("btnInspectText");
  const loadingBox = document.getElementById("novelLoadingBox");
  const previewBox = document.getElementById("novelPreviewBox");
  const titleDisplay = document.getElementById("novelTitleDisplay");
  const authorDisplay = document.getElementById("novelAuthorDisplay");
  const chDisplay = document.getElementById("novelChaptersDisplay");
  const countryDisplay = document.getElementById("novelCountryDisplay");
  const coverImg = document.getElementById("novelCoverImg");

  // Show loading animation
  if (previewBox) previewBox.style.display = "none";
  if (loadingBox) loadingBox.style.display = "flex";
  if (btnInspect) {
    btnInspect.disabled = true;
    btnInspect.style.opacity = "0.75";
  }
  if (btnInspectIcon) {
    btnInspectIcon.innerHTML = `<span class="btn-spinner"></span>`;
  }
  if (btnInspectText) {
    btnInspectText.textContent = "Mencari...";
  }

  try {
    const resp = await fetch(`/api/novel-info?url=${encodeURIComponent(raw)}`);
    const data = await resp.json();

    if (data.ok) {
      if (previewBox) {
        previewBox.style.display = "flex";
        previewBox.classList.remove("fade-in");
        void previewBox.offsetWidth; // Trigger reflow for smooth animation
        previewBox.classList.add("fade-in");
      }
      if (titleDisplay) titleDisplay.textContent = data.title;
      if (authorDisplay) authorDisplay.textContent = "Penulis: " + (data.author || "-");
      if (chDisplay) chDisplay.textContent = `${data.total_chapters} Bab`;
      if (countryDisplay) countryDisplay.textContent = data.origin_country || "ID";
      const adultBadge = document.getElementById("novelAdultBadge");
      if (adultBadge) {
        adultBadge.style.display = data.is_adult_only ? "inline-block" : "none";
      }
      const lockSel = document.getElementById("countryLockSelect");
      if (lockSel && data.origin_country) {
        for (let i = 0; i < lockSel.options.length; i++) {
          if (lockSel.options[i].value === data.origin_country) {
            lockSel.value = data.origin_country;
            break;
          }
        }
      }
      if (coverImg) {
        coverImg.src = data.cover_url || "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?w=120&q=80";
      }
    } else {
      if (previewBox) previewBox.style.display = "none";
      alert(data.error || "Novel tidak ditemukan. Periksa kembali tautan novel Anda.");
    }
  } catch (err) {
    if (previewBox) previewBox.style.display = "none";
    alert("Gagal melakukan inspeksi novel: " + err);
  } finally {
    // Hide loading skeleton & restore button state
    if (loadingBox) loadingBox.style.display = "none";
    if (btnInspect) {
      btnInspect.disabled = false;
      btnInspect.style.opacity = "";
    }
    if (btnInspectIcon) {
      btnInspectIcon.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`;
    }
    if (btnInspectText) {
      btnInspectText.textContent = "Periksa";
    }
  }
}

// =============================================================================
// 3. MODE & ESTIMATION
// =============================================================================
function selectMode(el, mode) {
  document.querySelectorAll(".mode-option, .mode-box").forEach(c => c.classList.remove("active"));
  el.classList.add("active");
  activeMode = mode;
  calculateEstimatedCost();
}

function getTaskAccountsCount() {
  const inp = document.getElementById("accCountInput");
  if (inp && inp.value !== "") {
    return Math.max(1, parseInt(inp.value) || 1);
  }
  const slider = document.getElementById("accCountSlider");
  return Math.max(1, parseInt(slider ? slider.value : 10) || 1);
}

function getTaskMaxChapters() {
  const inp = document.getElementById("chCountInput");
  if (inp && inp.value !== "") {
    return Math.max(1, parseInt(inp.value) || 1);
  }
  const slider = document.getElementById("chCountSlider");
  return Math.max(1, parseInt(slider ? slider.value : 5) || 1);
}

function updateTaskNumericInputs() {
  const accVal = getTaskAccountsCount();
  const chVal = getTaskMaxChapters();

  const accSlider = document.getElementById("accCountSlider");
  if (accSlider) accSlider.value = accVal;
  const chSlider = document.getElementById("chCountSlider");
  if (chSlider) chSlider.value = chVal;

  syncTaskAccChips(accVal);
  syncTaskChChips(chVal);
  calculateEstimatedCost();
}

function setTaskAccCount(val) {
  const inp = document.getElementById("accCountInput");
  if (inp) inp.value = val;
  const slider = document.getElementById("accCountSlider");
  if (slider) slider.value = val;
  syncTaskAccChips(val);
  calculateEstimatedCost();
}

function setTaskChCount(val) {
  const inp = document.getElementById("chCountInput");
  if (inp) inp.value = val;
  const slider = document.getElementById("chCountSlider");
  if (slider) slider.value = val;
  syncTaskChChips(val);
  calculateEstimatedCost();
}

function syncTaskAccChips(val) {
  const chips = document.querySelectorAll("#taskAccChips .preset-chip");
  chips.forEach(chip => {
    if (parseInt(chip.getAttribute("data-val")) === parseInt(val)) {
      chip.classList.add("active");
    } else {
      chip.classList.remove("active");
    }
  });
}

function syncTaskChChips(val) {
  const chips = document.querySelectorAll("#taskChChips .preset-chip");
  chips.forEach(chip => {
    if (parseInt(chip.getAttribute("data-val")) === parseInt(val)) {
      chip.classList.add("active");
    } else {
      chip.classList.remove("active");
    }
  });
}

// Fallbacks for legacy calls
function updateSliderLabels() {
  updateTaskNumericInputs();
}

function setSliderVal(id, val) {
  if (id === "accCountSlider" || id === "accCountInput") {
    setTaskAccCount(val);
  } else if (id === "chCountSlider" || id === "chCountInput") {
    setTaskChCount(val);
  }
}

async function pasteTokenFromClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      const input = document.getElementById("userTokenInput");
      if (input) {
        input.value = text.trim().toUpperCase();
        verifyUserToken();
      }
    }
  } catch (e) {
    console.log("Clipboard access denied or unavailable", e);
  }
}

async function pasteNovelFromClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      const input = document.getElementById("novelUrlInput");
      if (input) {
        input.value = text.trim();
        inspectNovel();
      }
    }
  } catch (e) {
    console.log("Clipboard access denied or unavailable", e);
  }
}

function calculateEstimatedCost() {
  const accCount = getTaskAccountsCount();
  let estimated = 0;

  const vrRate = (globalPricing && globalPricing.rates && globalPricing.rates.valid_reader) ? globalPricing.rates.valid_reader : 500;
  const grRate = (globalPricing && globalPricing.rates && globalPricing.rates.guest_reader) ? globalPricing.rates.guest_reader : 50;
  const likeRate = (globalPricing && globalPricing.rates && typeof globalPricing.rates.like !== "undefined") ? globalPricing.rates.like : 100;
  let formulaNote = "";

  if (activeMode === "full_auto" || activeMode === "member_read") {
    estimated = accCount * vrRate;
    formulaNote = `${accCount.toLocaleString('id-ID')} Sesi Member × Rp ${vrRate.toLocaleString('id-ID')}`;
  } else if (activeMode === "guest_read") {
    estimated = accCount * grRate;
    formulaNote = `${accCount.toLocaleString('id-ID')} Sesi Tamu × Rp ${grRate.toLocaleString('id-ID')}`;
  } else if (activeMode === "like_only" || activeMode === "bookmark_only" || activeMode === "follow_only") {
    estimated = accCount * likeRate;
    if (likeRate > 0) {
      formulaNote = `${accCount.toLocaleString('id-ID')} Sesi Interaksi × Rp ${likeRate.toLocaleString('id-ID')}`;
    } else {
      formulaNote = "Mode Interaksi Sosial: Bebas biaya saldo";
    }
  }

  const costDisplay = document.getElementById("estimatedCostDisplay");
  if (costDisplay) {
    costDisplay.textContent = formatRupiah(estimated);
  }
  const noteDisplay = document.getElementById("costCalculationNote");
  if (noteDisplay) {
    noteDisplay.textContent = formulaNote || "Dihitung otomatis per-sesi sukses";
  }
}

// =============================================================================
// 4. TASK EXECUTION & SSE LOG STREAM
// =============================================================================
async function startBotTask() {
  const tokenInput = document.getElementById("userTokenInput");
  const novelInput = document.getElementById("novelUrlInput");

  const token = tokenInput ? tokenInput.value.trim() : "";
  const novelUrl = novelInput ? novelInput.value.trim() : "";

  if (!token) {
    alert("Harap masukkan dan verifikasi Token Akses Anda terlebih dahulu.");
    return;
  }
  if (!novelUrl) {
    alert("Harap masukkan tautan atau ID novel target.");
    return;
  }

  const accountsCount = getTaskAccountsCount();
  const maxChapters = getTaskMaxChapters();

  const startBtn = document.getElementById("startBotBtn");
  const stopBtn = document.getElementById("stopBotBtn");
  if (startBtn) startBtn.style.display = "none";
  if (stopBtn) stopBtn.style.display = "inline-flex";

  const statusTag = document.getElementById("taskStatusTag");
  if (statusTag) statusTag.textContent = "Status: Memulai...";

  // Reset UI telemetry & progress
  const statDone1 = document.getElementById("statAccountsDone");
  if (statDone1) statDone1.textContent = `0 / ${accountsCount}`;
  const statDone2 = document.getElementById("statAccountsDone2");
  if (statDone2) statDone2.textContent = `0 / ${accountsCount}`;

  const statCh1 = document.getElementById("statChaptersRead");
  if (statCh1) statCh1.textContent = "0";
  const statCh2 = document.getElementById("statChaptersRead2");
  if (statCh2) statCh2.textContent = "0";

  const statSpent = document.getElementById("statSpent");
  if (statSpent) statSpent.textContent = "Rp 0";
  updateProgressBar(0, accountsCount);

  appendLog("[System]", "Memulai proses inisialisasi tugas...", "info");

  try {
    const countrySelect = document.getElementById("countryLockSelect");
    const countryVal = countrySelect ? countrySelect.value : "RANDOM";

    const resp = await fetch("/api/tasks/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: token,
        novel_url: novelUrl,
        mode: activeMode,
        accounts_count: accountsCount,
        guest_count: accountsCount,
        max_chapters: maxChapters,
        reading_delay: 5.0,
        country: countryVal,
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      appendLog("[Error]", `Gagal menghubungi server (${resp.status}): ${errText.substring(0, 100)}`, "error");
      if (startBtn) startBtn.style.display = "inline-flex";
      if (stopBtn) stopBtn.style.display = "none";
      if (statusTag) statusTag.textContent = "Status: Siap";
      return;
    }

    const data = await resp.json();
    if (!data.ok) {
      appendLog("[Error]", data.error || "Gagal memulai tugas", "error");
      if (startBtn) startBtn.style.display = "inline-flex";
      if (stopBtn) stopBtn.style.display = "none";
      if (statusTag) statusTag.textContent = "Status: Siap";
      if (data.wa_url) {
        if (confirm(`${data.error}\n\nIngin beli atau top up saldo token via WhatsApp Admin?`)) {
          window.open(data.wa_url, "_blank");
        }
      }
      return;
    }

    currentTaskId = data.task_id;
    localStorage.setItem("rinaradev_active_task", currentTaskId);
    localStorage.setItem("rinaradev_novel", novelUrl);

    if (statusTag) statusTag.textContent = "Status: Berjalan";
    appendLog("[System]", `Tugas aktif (ID: ${currentTaskId}). Membuka kanal aktivitas...`, "info");
    connectSSEStream(currentTaskId);

  } catch (err) {
    appendLog("[Error]", "Gagal berkomunikasi dengan server: " + err, "error");
    if (startBtn) startBtn.style.display = "inline-flex";
    if (stopBtn) stopBtn.style.display = "none";
    if (statusTag) statusTag.textContent = "Status: Siap";
  }
}

function connectSSEStream(taskId) {
  if (currentEventSource) {
    currentEventSource.close();
  }

  currentEventSource = new EventSource(`/api/tasks/stream/${taskId}`);

  currentEventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.type === "log") {
        appendLog(`[${data.time}]`, data.message, data.level || "info");
        if (data.balance_exhausted && data.wa_url) {
          if (confirm("Saldo token Anda telah habis. Hubungi WhatsApp Admin untuk melakukan top up saldo?")) {
            window.open(data.wa_url, "_blank");
          }
        }
      } else if (data.type === "stats") {
        updateStatsUI(data.stats);
      } else if (data.type === "done") {
        appendLog("[System]", "Seluruh rangkaian tugas selesai.", "success");
        updateStatsUI(data.stats);
        finishTask();
      }
    } catch (err) {
      console.error("SSE parse error", err);
    }
  };

  currentEventSource.onerror = (e) => {
    // Jangan langsung matikan task saat refresh/reconnect
    console.log("SSE stream status update / reconnecting...", e);
  };
}

async function stopBotTask() {
  if (!currentTaskId) return;
  try {
    await fetch(`/api/tasks/stop/${currentTaskId}`, { method: "POST" });
    appendLog("[System]", "Perintah pembatalan tugas dikirim ke server...", "warning");
  } catch (e) {
    console.error("Stop error", e);
  }
  finishTask();
}

function finishTask() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  localStorage.removeItem("rinaradev_active_task");
  localStorage.removeItem("toodat_active_task");
  currentTaskId = null;

  const startBtn = document.getElementById("startBotBtn");
  const stopBtn = document.getElementById("stopBotBtn");
  if (startBtn) startBtn.style.display = "inline-flex";
  if (stopBtn) stopBtn.style.display = "none";

  const statusTag = document.getElementById("taskStatusTag");
  if (statusTag) statusTag.textContent = "Status: Selesai";

  verifyUserToken();
}

let isAutoScrollEnabled = true;

function toggleAutoScroll() {
  isAutoScrollEnabled = !isAutoScrollEnabled;
  const btn = document.getElementById("btnAutoScroll");
  if (btn) {
    btn.textContent = isAutoScrollEnabled ? "Auto-Scroll: ON" : "Auto-Scroll: OFF";
    btn.style.borderColor = isAutoScrollEnabled ? "var(--primary)" : "var(--border-color)";
    btn.style.color = isAutoScrollEnabled ? "#818cf8" : "var(--text-muted)";
  }
}

function copyLogs() {
  const body = document.getElementById("terminalLogBody2") || document.getElementById("terminalLogBody");
  if (!body) return;
  const text = body.innerText;
  navigator.clipboard.writeText(text).then(() => {
    alert("Log aktivitas berhasil disalin ke Clipboard!");
  }).catch(() => {
    alert("Gagal menyalin log.");
  });
}

function downloadLogs() {
  const body = document.getElementById("terminalLogBody2") || document.getElementById("terminalLogBody");
  if (!body) return;
  const text = body.innerText;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `rinaradev_bot_logs_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "_")}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function appendLog(timePrefix, text, level) {
  const terminals = [document.getElementById("terminalLogBody"), document.getElementById("terminalLogBody2")].filter(Boolean);
  if (terminals.length === 0) return;

  terminals.forEach(terminal => {
    const row = document.createElement("div");
    row.className = "log-row";

    const timeSpan = document.createElement("span");
    timeSpan.className = "log-time";
    timeSpan.textContent = timePrefix;

    const msgSpan = document.createElement("span");
    msgSpan.className = `log-msg-${level}`;
    msgSpan.textContent = text;

    row.appendChild(timeSpan);
    row.appendChild(msgSpan);
    terminal.appendChild(row);
    if (isAutoScrollEnabled) {
      terminal.scrollTop = terminal.scrollHeight;
    }
  });
}

function clearLogs() {
  [document.getElementById("terminalLogBody"), document.getElementById("terminalLogBody2")].filter(Boolean).forEach(terminal => {
    terminal.innerHTML = `
      <div class="log-row">
        <span class="log-time">[System]</span>
        <span class="log-msg-info">Log aktivitas dibersihkan.</span>
      </div>
    `;
  });
}

function updateStatsUI(stats) {
  if (!stats) return;
  const done = stats.accounts_done || 0;
  const total = stats.accounts_total || 0;

  const statAcc = document.getElementById("statAccountsDone");
  if (statAcc) statAcc.textContent = `${done} / ${total}`;
  const statAcc2 = document.getElementById("statAccountsDone2");
  if (statAcc2) statAcc2.textContent = `${done} / ${total}`;

  const statCh = document.getElementById("statChaptersRead");
  if (statCh) statCh.textContent = stats.chapters_read || 0;
  const statCh2 = document.getElementById("statChaptersRead2");
  if (statCh2) statCh2.textContent = stats.chapters_read || 0;

  const statSpent = document.getElementById("statSpent");
  if (statSpent) statSpent.textContent = formatRupiah(stats.total_spent || 0);

  if (stats.current_balance !== undefined) {
    const statRem = document.getElementById("statRemainingBal");
    if (statRem) statRem.textContent = formatRupiah(stats.current_balance);

    const userBal = document.getElementById("userBalanceDisplay");
    if (userBal) userBal.textContent = formatRupiah(stats.current_balance);

    const navVal = document.getElementById("navBalanceValue");
    if (navVal) navVal.textContent = formatRupiah(stats.current_balance);
  }

  updateProgressBar(done, total);
}

function updateProgressBar(done, total) {
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const fill = document.getElementById("progressBarFill");
  const label = document.getElementById("progressPercent");
  if (fill) fill.style.width = `${percent}%`;
  if (label) label.textContent = `${percent}%`;
}

// =============================================================================
// ADMIN / OWNER PANEL CONTROLLER
// =============================================================================
async function adminLogin() {
  const pinInput = document.getElementById("adminPinInput");
  const pin = pinInput ? pinInput.value.trim() : "";
  if (!pin) return;

  try {
    const resp = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin }),
    });
    const data = await resp.json();

    if (data.ok) {
      adminPin = pin;
      sessionStorage.setItem("rinaradev_admin_pin", pin);
      document.getElementById("adminLoginCard").style.display = "none";
      document.getElementById("adminDashboardContent").style.display = "block";
      loadAdminStats();
      loadAdminTokens();
      loadAdminAccounts();
      loadAdminPayments();
      loadAdminPricing();
    } else {
      alert("PIN Master salah.");
    }
  } catch (err) {
    alert("Error: " + err);
  }
}

async function adminCreateToken() {
  const amount = parseInt(document.getElementById("tokenAmountInput").value) || 20000;
  const label = document.getElementById("tokenLabelInput").value.trim() || "Klien Token";
  const expDays = parseInt(document.getElementById("tokenExpiryInput").value) || null;

  try {
    const resp = await fetch("/api/admin/tokens", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Pin": adminPin,
      },
      body: JSON.stringify({
        amount: amount,
        label: label,
        expiry_days: expDays,
      }),
    });
    const data = await resp.json();

    if (data.ok) {
      const tok = data.token;
      document.getElementById("newTokenResultCard").style.display = "block";
      document.getElementById("generatedTokenCode").textContent = tok.token;
      document.getElementById("generatedTokenMeta").textContent = 
        `Saldo: ${formatRupiah(tok.initial_balance)} | Label: ${tok.label}`;

      const waMsg = `Halo, berikut adalah kode Token Akses RinaraDev Automation Anda:\n\n*${tok.token}*\n\nSaldo: ${formatRupiah(tok.initial_balance)}\nStatus: Aktif\nSilakan masukkan token di website untuk memulai. Terima kasih.`;
      document.getElementById("shareWaBtn").href = `https://wa.me/?text=${encodeURIComponent(waMsg)}`;

      loadAdminTokens();
      loadAdminStats();
    } else {
      alert("Gagal membuat token: " + (data.detail || data.error));
    }
  } catch (err) {
    alert("Error: " + err);
  }
}

async function loadAdminStats() {
  try {
    const resp = await fetch("/api/admin/stats", {
      headers: { "X-Admin-Pin": adminPin },
    });
    const data = await resp.json();
    if (data.ok) {
      document.getElementById("adminTotalTokens").textContent = data.total_tokens;
      document.getElementById("adminActiveBalance").textContent = formatRupiah(data.total_active_balance);
      document.getElementById("adminTotalSpent").textContent = formatRupiah(data.total_spent);
      document.getElementById("adminAccountsAvail").textContent = `${data.total_accounts_available} Node`;
      document.getElementById("adminProxiesActive").textContent = `${data.total_proxies_active} Jalur`;
    }
  } catch (e) {
    console.error(e);
  }
}

async function loadAdminTokens() {
  const tbody = document.getElementById("tokenTableBody");
  try {
    const resp = await fetch("/api/admin/tokens", {
      headers: { "X-Admin-Pin": adminPin },
    });
    const data = await resp.json();

    if (data.ok && data.tokens) {
      tbody.innerHTML = "";
      if (data.tokens.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">Belum ada token yang terdaftar.</td></tr>`;
        return;
      }

      data.tokens.forEach(t => {
        const tr = document.createElement("tr");
        const statusBadge = t.status === "active" 
          ? `<span style="color: var(--success); font-weight: 600;">Aktif</span>`
          : (t.status === "suspended" ? `<span style="color: var(--warning); font-weight: 600;">Ditangguhkan</span>` : `<span style="color: var(--danger); font-weight: 600;">Dicabut</span>`);

        tr.innerHTML = `
          <td><b style="font-family: ui-monospace, monospace;">${t.token}</b></td>
          <td>${t.label || "-"}</td>
          <td>${formatRupiah(t.initial_balance)}</td>
          <td><b style="color: var(--success);">${formatRupiah(t.current_balance)}</b></td>
          <td>${formatRupiah(t.total_spent)}</td>
          <td>${statusBadge}</td>
          <td style="font-size: 11px; color: var(--text-muted);">${(t.created_at || "").substring(0, 10)}</td>
          <td>
            <div style="display: flex; gap: 4px;">
              <button class="btn btn-secondary btn-sm" onclick="navigator.clipboard.writeText('${t.token}'); alert('Token disalin.');">Salin</button>
              <button class="btn btn-primary btn-sm" onclick="adminTopupPrompt('${t.token}')">+ Saldo</button>
              <button class="btn btn-danger btn-sm" onclick="adminToggleStatusPrompt('${t.token}', '${t.status === 'active' ? 'suspended' : 'active'}')">${t.status === 'active' ? 'Suspend' : 'Aktif'}</button>
            </div>
          </td>
        `;
        tbody.appendChild(tr);
      });
    }
  } catch (e) {
    console.error(e);
  }
}

async function adminTopupPrompt(tokenCode) {
  const nominalStr = prompt(`Masukkan nominal saldo Top Up untuk ${tokenCode} (contoh: 20000):`, "20000");
  if (!nominalStr) return;
  const amount = parseInt(nominalStr);
  if (!amount || amount <= 0) return;

  try {
    const resp = await fetch(`/api/admin/tokens/${tokenCode}/topup`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Pin": adminPin,
      },
      body: JSON.stringify({ amount }),
    });
    const data = await resp.json();
    if (data.ok) {
      alert(`Berhasil top up ${formatRupiah(amount)} untuk ${tokenCode}.`);
      loadAdminTokens();
      loadAdminStats();
    }
  } catch (e) {
    alert("Error: " + e);
  }
}

async function adminToggleStatusPrompt(tokenCode, newStatus) {
  if (!confirm(`Ubah status token ${tokenCode} menjadi '${newStatus}'?`)) return;

  try {
    const resp = await fetch(`/api/admin/tokens/${tokenCode}/status`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Pin": adminPin,
      },
      body: JSON.stringify({ status: newStatus }),
    });
    const data = await resp.json();
    if (data.ok) {
      loadAdminTokens();
    }
  } catch (e) {
    alert("Error: " + e);
  }
}

function copyGeneratedToken() {
  const code = document.getElementById("generatedTokenCode").textContent.trim();
  if (code && code !== "-") {
    navigator.clipboard.writeText(code);
    alert("Kode token disalin ke clipboard.");
  }
}

// =============================================================================
// ADMIN ACCOUNTS GENERATOR & INVENTORY CONTROLLER
// =============================================================================
let adminAccountsCache = [];

async function adminGenerateAccounts() {
  const countInput = document.getElementById("genAccountCountInput");
  const countrySelect = document.getElementById("genAccountCountrySelect");
  const uaSelect = document.getElementById("genAccountUaSelect");
  const btn = document.getElementById("btnGenerateAccounts");
  const loading = document.getElementById("genAccountLoading");
  const statusText = document.getElementById("genAccountStatusText");
  const resultCard = document.getElementById("genAccountResultCard");
  const resultList = document.getElementById("genResultList");
  const resultSummary = document.getElementById("genResultSummary");
  const resultTime = document.getElementById("genResultTime");

  const count = parseInt(countInput ? countInput.value : 1) || 1;
  const country = countrySelect ? countrySelect.value : "RANDOM";
  const uaMode = uaSelect ? uaSelect.value : "okhttp";

  if (count < 1 || count > 50) {
    alert("Jumlah akun harus antara 1 sampai 50.");
    return;
  }

  // Set loading UI
  if (btn) btn.disabled = true;
  if (loading) loading.style.display = "block";
  if (resultCard) resultCard.style.display = "none";
  if (statusText) statusText.textContent = `Memproses registrasi ${count} akun (${country})...`;

  try {
    const resp = await fetch("/api/admin/accounts/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Pin": adminPin,
      },
      body: JSON.stringify({
        count: count,
        country: country,
        ua_mode: uaMode,
      }),
    });

    const data = await resp.json();

    if (data.ok) {
      if (resultCard && resultList) {
        resultCard.style.display = "block";
        resultSummary.textContent = `Berhasil Mendaftarkan ${data.created_count} dari ${data.requested} Akun`;
        resultTime.textContent = new Date().toLocaleTimeString();

        resultList.innerHTML = "";
        (data.accounts || []).forEach(acc => {
          const item = document.createElement("div");
          item.style.cssText = "padding: 6px 8px; background: #ffffff; border-radius: 4px; border: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center;";
          item.innerHTML = `
            <div>
              <div style="font-weight: 600; color: var(--text-heading);">${acc.email}</div>
              <div style="font-size: 11px; color: var(--text-muted);">ID: ${acc.user_id} &bull; ${acc.country} &bull; ${acc.nickname || "-"}</div>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="navigator.clipboard.writeText('${acc.email} | ${acc.password}'); alert('Email & Password disalin.');">Salin</button>
          `;
          resultList.appendChild(item);
        });

        if (data.errors && data.errors.length > 0) {
          const errDiv = document.createElement("div");
          errDiv.style.cssText = "font-size: 11px; color: var(--danger); margin-top: 4px;";
          errDiv.textContent = `Catatan: ${data.errors.join(", ")}`;
          resultList.appendChild(errDiv);
        }
      }

      loadAdminStats();
      loadAdminAccounts();
    } else {
      alert("Gagal membuat akun: " + (data.detail || data.error || "Terjadi kesalahan pada server."));
    }
  } catch (err) {
    alert("Error: " + err);
  } finally {
    if (btn) btn.disabled = false;
    if (loading) loading.style.display = "none";
  }
}

function switchAdminTab(tabName) {
  const paneTokens = document.getElementById("paneTokens");
  const paneAccounts = document.getElementById("paneAccounts");
  const panePayments = document.getElementById("panePayments");
  const panePricing = document.getElementById("panePricing");
  const tabBtnTokens = document.getElementById("tabBtnTokens");
  const tabBtnAccounts = document.getElementById("tabBtnAccounts");
  const tabBtnPayments = document.getElementById("tabBtnPayments");
  const tabBtnPricing = document.getElementById("tabBtnPricing");

  [paneTokens, paneAccounts, panePayments, panePricing].forEach(p => { if (p) p.style.display = "none"; });
  [tabBtnTokens, tabBtnAccounts, tabBtnPayments, tabBtnPricing].forEach(b => { if (b) b.classList.remove("active"); });

  if (tabName === "tokens") {
    if (paneTokens) paneTokens.style.display = "block";
    if (tabBtnTokens) tabBtnTokens.classList.add("active");
  } else if (tabName === "accounts") {
    if (paneAccounts) paneAccounts.style.display = "block";
    if (tabBtnAccounts) tabBtnAccounts.classList.add("active");
    loadAdminAccounts();
  } else if (tabName === "payments") {
    if (panePayments) panePayments.style.display = "block";
    if (tabBtnPayments) tabBtnPayments.classList.add("active");
    loadAdminPayments();
  } else if (tabName === "pricing") {
    if (panePricing) panePricing.style.display = "block";
    if (tabBtnPricing) tabBtnPricing.classList.add("active");
    loadAdminPricing();
  }
}

async function loadAdminPayments() {
  const tbody = document.getElementById("paymentTableBody");
  const badge = document.getElementById("paymentCountBadge");
  if (!tbody) return;

  try {
    const resp = await fetch("/api/admin/payments", {
      headers: { "X-Admin-Pin": adminPin }
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || "Gagal memuat transaksi");

    const payments = data.payments || [];
    if (badge) badge.textContent = payments.length;

    if (payments.length === 0) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">Belum ada riwayat transaksi pembayaran.</td></tr>`;
      return;
    }

    tbody.innerHTML = payments.map(p => {
      const isPaid = p.status === "paid";
      const statusBadge = isPaid
        ? `<span class="badge badge-active" style="background: #dcfce7; color: #166534; border: 1px solid #bbf7d0;">LUNAS</span>`
        : `<span class="badge badge-suspended" style="background: #fef3c7; color: #92400e; border: 1px solid #fde68a;">PENDING</span>`;

      const typeLabel = p.order_type === "topup"
        ? `<span style="font-weight: 600; color: var(--primary);">Top Up</span>`
        : `<span style="font-weight: 600; color: #16a34a;">Token Baru</span>`;

      const tokenRef = p.generated_token
        ? `<code style="font-weight: 700; color: var(--primary-text);">${p.generated_token}</code>`
        : (p.target_token ? `<code>${p.target_token}</code>` : `-`);

      const createdDate = p.created_at ? new Date(p.created_at).toLocaleString("id-ID") : "-";
      const paidDate = p.paid_at ? new Date(p.paid_at).toLocaleString("id-ID") : "-";

      return `
        <tr>
          <td><strong style="font-family: ui-monospace, monospace; font-size: 11px;">${p.ref_id}</strong></td>
          <td>${typeLabel}</td>
          <td><span class="tag">${p.payment_code || "QRIS"}</span></td>
          <td><strong>Rp ${(p.nominal || 0).toLocaleString("id-ID")}</strong></td>
          <td>${statusBadge}</td>
          <td>${tokenRef}</td>
          <td style="font-size: 11px; color: var(--text-muted);">${createdDate}</td>
          <td style="font-size: 11px; color: var(--text-muted);">${paidDate}</td>
          <td style="font-size: 11px; font-family: ui-monospace, monospace; color: var(--text-muted);">${p.trx_reference || "-"}</td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--danger); padding: 20px;">Gagal memuat: ${err.message}</td></tr>`;
  }
}

async function loadAdminAccounts() {
  const tbody = document.getElementById("accountTableBody");
  const badge = document.getElementById("accountCountBadge");
  const searchInput = document.getElementById("accountSearchInput");
  if (!tbody) return;

  try {
    const resp = await fetch("/api/admin/accounts?limit=250", {
      headers: { "X-Admin-Pin": adminPin },
    });
    const data = await resp.json();

    if (data.ok && data.accounts) {
      adminAccountsCache = data.accounts;
      if (badge) badge.textContent = `${data.total} Akun`;

      // Jika input pencarian terisi otomatis oleh browser password manager (misal admin@...), bersihkan!
      if (searchInput && searchInput.value && searchInput.value.includes("@")) {
        searchInput.value = "";
      }

      const q = searchInput ? searchInput.value.trim().toLowerCase() : "";
      if (q) {
        filterAdminAccounts();
      } else {
        renderAccountsTable(adminAccountsCache);
      }
    }
  } catch (e) {
    console.error(e);
  }
}

function renderAccountsTable(accounts) {
  const tbody = document.getElementById("accountTableBody");
  if (!tbody) return;

  tbody.innerHTML = "";
  if (accounts.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">Tidak ada akun yang sesuai.</td></tr>`;
    return;
  }

  accounts.forEach((acc, idx) => {
    const tr = document.createElement("tr");
    const createdStr = (acc.created_at || "").substring(0, 10);
    const pwd = acc.password || "";
    const masked = pwd.length > 4 ? (pwd.substring(0, 2) + "****" + pwd.substring(pwd.length - 2)) : "****";

    tr.innerHTML = `
      <td style="color: var(--text-muted); font-size: 12px;">${idx + 1}</td>
      <td><b style="font-family: ui-monospace, monospace; font-size: 12px;">${acc.email}</b></td>
      <td style="font-family: ui-monospace, monospace; font-size: 12px;">${acc.user_id || "-"}</td>
      <td><span style="display: inline-block; padding: 2px 6px; background: var(--bg-subtle); border-radius: 4px; font-weight: 600; font-size: 11px;">${acc.country}</span></td>
      <td style="font-size: 12px;">${acc.nickname || "-"}</td>
      <td style="font-family: ui-monospace, monospace; font-size: 11px;">
        <span id="pwd_${idx}">${masked}</span>
        <button class="btn btn-secondary btn-sm" style="padding: 1px 5px; font-size: 10px; margin-left: 4px;" onclick="togglePasswordVisibility(${idx}, '${pwd}')">Lihat</button>
        <button class="btn btn-secondary btn-sm" style="padding: 1px 5px; font-size: 10px;" onclick="navigator.clipboard.writeText('${pwd}'); alert('Password disalin.');">Salin</button>
      </td>
      <td><span style="color: var(--success); font-weight: 600; font-size: 11px;">Token Valid</span></td>
      <td style="font-size: 11px; color: var(--text-muted);">${createdStr || "-"}</td>
    `;
    tbody.appendChild(tr);
  });
}

function togglePasswordVisibility(idx, plainPwd) {
  const el = document.getElementById(`pwd_${idx}`);
  if (!el) return;
  if (el.textContent.includes("*")) {
    el.textContent = plainPwd;
  } else {
    el.textContent = plainPwd.length > 4 ? (plainPwd.substring(0, 2) + "****" + plainPwd.substring(plainPwd.length - 2)) : "****";
  }
}

function filterAdminAccounts() {
  const query = (document.getElementById("accountSearchInput")?.value || "").trim().toLowerCase();
  if (!query) {
    renderAccountsTable(adminAccountsCache);
    return;
  }
  const filtered = adminAccountsCache.filter(acc => {
    return (acc.email || "").toLowerCase().includes(query) ||
           String(acc.user_id || "").includes(query) ||
           (acc.country || "").toLowerCase().includes(query) ||
           (acc.nickname || "").toLowerCase().includes(query);
  });
  renderAccountsTable(filtered);
}

function adminDownloadAccounts() {
  if (!adminPin) {
    alert("PIN Admin dibutuhkan.");
    return;
  }
  window.open(`/api/admin/accounts/download?pin=${encodeURIComponent(adminPin)}`, "_blank");
}

async function syncAdminProxies() {
  const btn = document.getElementById("btnSyncProxies");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="btn-spinner"></span> Sinkron...`;
  }

  try {
    const pin = adminPin || sessionStorage.getItem("rinaradev_admin_pin");
    const resp = await fetch("/api/admin/proxies/sync", {
      method: "POST",
      headers: { "x-admin-pin": pin }
    });
    const data = await resp.json();
    if (data.ok) {
      alert(`✓ ${data.message}`);
      loadAdminStats();
    } else {
      alert("Gagal sinkronisasi proxy: " + (data.detail || data.error));
    }
  } catch (err) {
    alert("Gagal menghubungi server: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml || "⚡ Sync Proxy Baru";
    }
  }
}

// =============================================================================
// WIJAYAPAY PAYMENT GATEWAY & AUTOMATION CLIENT CONTROLLER
// =============================================================================
let currentPayOrderType = "new_token";
let currentPayNominal = 20000;
let currentPayPackageName = "";
let currentPayChannel = "QRIS";
let currentPayRefId = null;
let payPollTimer = null;
let payCountdownTimer = null;
let payPaidTokenResult = null;

function openPaymentModal(orderType = "new_token", targetToken = null, defaultNominal = 20000, defaultPackageName = "") {
  const modal = document.getElementById("paymentModal");
  if (!modal) return;

  // Reset steps
  document.getElementById("paymentStep1").style.display = "block";
  document.getElementById("paymentStep2").style.display = "none";
  document.getElementById("paymentStep3").style.display = "none";
  document.getElementById("paymentCreateError").style.display = "none";

  // Stop any lingering timers
  clearInterval(payPollTimer);
  clearInterval(payCountdownTimer);

  selectPaymentOrderType(orderType);

  if (targetToken) {
    const input = document.getElementById("payTargetTokenInput");
    if (input) input.value = targetToken;
  } else if (activeToken && orderType === "topup") {
    const input = document.getElementById("payTargetTokenInput");
    if (input) input.value = activeToken;
  }

  currentPayPackageName = defaultPackageName || "";
  if (!currentPayPackageName && globalPricing && globalPricing.packages) {
    const found = globalPricing.packages.find(p => p.price === defaultNominal);
    if (found) currentPayPackageName = found.name;
  }

  if (defaultNominal) {
    selectPaymentNominal(defaultNominal, currentPayPackageName);
  }

  modal.style.display = "flex";
}

function closePaymentModal() {
  const modal = document.getElementById("paymentModal");
  if (modal) modal.style.display = "none";
  clearInterval(payPollTimer);
  clearInterval(payCountdownTimer);
}

function selectPaymentOrderType(type) {
  currentPayOrderType = type;
  const btnNew = document.getElementById("payTypeNew");
  const btnTopup = document.getElementById("payTypeTopup");
  const targetGroup = document.getElementById("payTargetTokenGroup");
  const modalTitle = document.getElementById("paymentModalTitle");

  if (type === "new_token") {
    btnNew?.classList.add("active");
    btnTopup?.classList.remove("active");
    if (targetGroup) targetGroup.style.display = "none";
    if (modalTitle) modalTitle.textContent = "Beli Token Akses";
  } else {
    btnTopup?.classList.add("active");
    btnNew?.classList.remove("active");
    if (targetGroup) targetGroup.style.display = "block";
    if (modalTitle) modalTitle.textContent = "Top Up Saldo Token";
    if (activeToken) {
      const input = document.getElementById("payTargetTokenInput");
      if (input && !input.value) input.value = activeToken;
    }
  }
  updateSubmitButtonLabel();
}

function selectPaymentNominal(nominal, packageName = "") {
  currentPayNominal = nominal;
  if (packageName) {
    currentPayPackageName = packageName;
  } else if (globalPricing && globalPricing.packages) {
    const found = globalPricing.packages.find(p => p.price === nominal);
    currentPayPackageName = found ? found.name : `Paket Rp ${nominal.toLocaleString('id-ID')}`;
  } else {
    currentPayPackageName = `Paket Rp ${nominal.toLocaleString('id-ID')}`;
  }

  // De-select all nominal cards dynamically
  const container = document.getElementById("modalNominalGrid");
  if (container) {
    container.querySelectorAll(".nominal-card").forEach(c => c.classList.remove("active"));
    const activeCard = document.getElementById(`nomCard${nominal}`);
    if (activeCard) activeCard.classList.add("active");
  }

  const customInput = document.getElementById("payCustomNominalInput");
  if (customInput) customInput.value = "";
  updateSubmitButtonLabel();
}

function onCustomNominalInput(val) {
  const num = parseInt(val, 10);
  if (!isNaN(num) && num > 0) {
    currentPayNominal = num;
    currentPayPackageName = "Nominal Kustom";
    const container = document.getElementById("modalNominalGrid");
    if (container) {
      container.querySelectorAll(".nominal-card").forEach(c => c.classList.remove("active"));
    }
  }
  updateSubmitButtonLabel();
}

function selectPaymentChannel(channelCode) {
  currentPayChannel = channelCode;
  ["QRIS", "BCAVA", "BRIVA", "BNIVA", "MANDIRIVA"].forEach(code => {
    const card = document.getElementById(`chanCard_${code}`);
    if (card) {
      const checkSpan = card.querySelector(".channel-check");
      if (code === channelCode) {
        card.classList.add("active");
        if (checkSpan) checkSpan.textContent = "✓";
      } else {
        card.classList.remove("active");
        if (checkSpan) checkSpan.textContent = "";
      }
    }
  });
  updateSubmitButtonLabel();
}

function updateSubmitButtonLabel() {
  const btn = document.getElementById("btnSubmitPayment");
  if (!btn) return;
  const channelName = currentPayChannel === "QRIS" ? "QRIS" : currentPayChannel;
  const pkgPrefix = currentPayPackageName ? `${currentPayPackageName} &bull; ` : "";
  btn.innerHTML = `Bayar ${pkgPrefix}Rp ${currentPayNominal.toLocaleString("id-ID")} via ${channelName}`;
}

async function submitPaymentOrder() {
  const btn = document.getElementById("btnSubmitPayment");
  const errBox = document.getElementById("paymentCreateError");
  if (errBox) errBox.style.display = "none";

  let targetToken = null;
  if (currentPayOrderType === "topup") {
    targetToken = (document.getElementById("payTargetTokenInput")?.value || "").trim().toUpperCase();
    if (!targetToken) {
      if (errBox) {
        errBox.textContent = "Silakan masukkan Kode Token yang ingin diisi ulang.";
        errBox.style.display = "block";
      }
      return;
    }
  }

  if (!currentPayNominal || currentPayNominal < 10000) {
    if (errBox) {
      errBox.textContent = "Nominal minimal pembelian adalah Rp 10.000.";
      errBox.style.display = "block";
    }
    return;
  }

  const origHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-spinner"></span> Menghubungkan ke WijayaPay...`;

  try {
    const resp = await fetch("/api/payment/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        order_type: currentPayOrderType,
        nominal: currentPayNominal,
        token_code: targetToken,
        payment_code: currentPayChannel,
      })
    });

    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || "Gagal membuat pesanan pembayaran");
    }

    currentPayRefId = data.ref_id;
    renderCheckoutStep2(data);
  } catch (err) {
    if (errBox) {
      errBox.textContent = err.message || String(err);
      errBox.style.display = "block";
    }
  } finally {
    btn.disabled = false;
    btn.innerHTML = origHtml;
  }
}

function renderCheckoutStep2(orderData) {
  document.getElementById("paymentStep1").style.display = "none";
  document.getElementById("paymentStep2").style.display = "block";
  document.getElementById("paymentStep3").style.display = "none";

  const checkout = orderData.checkout || {};
  const totalBayar = checkout.total_bayar || orderData.nominal;
  document.getElementById("payCheckoutTotal").textContent = `Rp ${totalBayar.toLocaleString("id-ID")}`;

  const qrBox = document.getElementById("payQrBox");
  const vaBox = document.getElementById("payVaBox");
  const qrImg = document.getElementById("payQrImage");
  const qrLoading = document.getElementById("payQrLoading");

  if (orderData.payment_code === "QRIS") {
    if (qrBox) qrBox.style.display = "block";
    if (vaBox) vaBox.style.display = "none";
    if (qrLoading) qrLoading.style.display = "block";
    if (qrImg) {
      qrImg.style.display = "none";
      const qrUrl = checkout.qr_link || (checkout.qr_string ? `https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=${encodeURIComponent(checkout.qr_string)}` : "");
      if (qrUrl) {
        qrImg.onload = () => {
          if (qrLoading) qrLoading.style.display = "none";
          qrImg.style.display = "block";
        };
        qrImg.onerror = () => {
          if (checkout.qr_string) {
            qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=${encodeURIComponent(checkout.qr_string)}`;
          }
        };
        qrImg.src = qrUrl;
      }
    }
  } else {
    // Virtual Account
    if (qrBox) qrBox.style.display = "none";
    if (vaBox) vaBox.style.display = "block";
    const vaNumEl = document.getElementById("payVaNumber");
    if (vaNumEl) vaNumEl.textContent = checkout.nomor_va || "-";
  }

  // Setup countdown
  startPaymentCountdown(checkout.expired);

  // Start polling
  startPaymentPolling(orderData.ref_id);
}

function startPaymentPolling(refId) {
  clearInterval(payPollTimer);
  payPollTimer = setInterval(async () => {
    try {
      const resp = await fetch(`/api/payment/check/${encodeURIComponent(refId)}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.ok && data.is_paid) {
        clearInterval(payPollTimer);
        clearInterval(payCountdownTimer);
        onPaymentSuccess(data);
      } else if (data.status === "expired" || data.status === "failed") {
        clearInterval(payPollTimer);
        clearInterval(payCountdownTimer);
        const statusEl = document.getElementById("payStatusText");
        if (statusEl) statusEl.textContent = "Transaksi kadaluarsa/dibatalkan.";
      }
    } catch (e) {
      console.debug("Polling payment status:", e);
    }
  }, 3000);
}

async function manualCheckPayment() {
  if (!currentPayRefId) return;
  const btn = document.getElementById("btnManualCheck");
  const origText = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Memeriksa...";
  }

  try {
    const resp = await fetch(`/api/payment/check/${encodeURIComponent(currentPayRefId)}`);
    const data = await resp.json();
    if (data.ok && data.is_paid) {
      clearInterval(payPollTimer);
      clearInterval(payCountdownTimer);
      onPaymentSuccess(data);
    } else {
      alert("Status: " + (data.message || "Belum ada pembayaran masuk. Silakan selesaikan pembayaran."));
    }
  } catch (err) {
    alert("Gagal memeriksa status: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText;
    }
  }
}

function onPaymentSuccess(data) {
  document.getElementById("paymentStep1").style.display = "none";
  document.getElementById("paymentStep2").style.display = "none";
  document.getElementById("paymentStep3").style.display = "block";

  payPaidTokenResult = data;

  const isNew = data.order_type === "new_token" || Boolean(data.generated_token);
  const tokenCode = data.generated_token || data.target_token;
  const tokenCodeEl = document.getElementById("paySuccessTokenCode");
  const balanceTextEl = document.getElementById("paySuccessBalanceText");
  const msgEl = document.getElementById("paySuccessMessage");

  if (tokenCodeEl) tokenCodeEl.textContent = tokenCode || "-";

  if (isNew) {
    if (msgEl) msgEl.textContent = `Token baru berhasil dibuat otomatis! Saldo aktif Rp ${(data.nominal || 0).toLocaleString("id-ID")}.`;
    if (balanceTextEl) balanceTextEl.textContent = `Saldo Awal: Rp ${(data.nominal || 0).toLocaleString("id-ID")}`;
  } else {
    if (msgEl) msgEl.textContent = `Saldo token ${tokenCode} berhasil ditambah Rp ${(data.nominal || 0).toLocaleString("id-ID")}!`;
    if (balanceTextEl) balanceTextEl.textContent = `Top up sukses senilai Rp ${(data.nominal || 0).toLocaleString("id-ID")}`;
  }
}

function applyPaidTokenAndClose() {
  if (payPaidTokenResult) {
    const token = payPaidTokenResult.generated_token || payPaidTokenResult.target_token;
    if (token) {
      const input = document.getElementById("userTokenInput");
      if (input) {
        input.value = token;
        verifyUserToken();
      }
    }
  }
  closePaymentModal();
}

function cancelPaymentOrder() {
  clearInterval(payPollTimer);
  clearInterval(payCountdownTimer);
  document.getElementById("paymentStep1").style.display = "block";
  document.getElementById("paymentStep2").style.display = "none";
  document.getElementById("paymentStep3").style.display = "none";
}

function copyVaNumber() {
  const va = document.getElementById("payVaNumber")?.textContent;
  if (va && va !== "-") {
    navigator.clipboard.writeText(va).then(() => {
      alert("Nomor Virtual Account berhasil disalin!");
    });
  }
}

function startPaymentCountdown(expiredTimestamp) {
  clearInterval(payCountdownTimer);
  const countdownEl = document.getElementById("payCountdownText");
  if (!countdownEl) return;

  let seconds = 900; // default 15 minutes
  if (expiredTimestamp) {
    const expTime = new Date(expiredTimestamp).getTime();
    const diffSec = Math.floor((expTime - Date.now()) / 1000);
    if (diffSec > 0) seconds = diffSec;
  }

  const update = () => {
    if (seconds <= 0) {
      countdownEl.textContent = "Waktu Habis";
      clearInterval(payCountdownTimer);
      return;
    }
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    countdownEl.textContent = `Batas Waktu: ${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    seconds--;
  };

  update();
  payCountdownTimer = setInterval(update, 1000);
}

// =============================================================================
// DYNAMIC PRICING & ADMIN TIER CONFIGURATION
// =============================================================================

async function loadPricingConfig() {
  try {
    const resp = await fetch("/api/pricing");
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.ok) {
      globalPricing = data;
      const rates = data.rates || {};
      const vrRate = rates.valid_reader || 500;
      const grRate = rates.guest_reader || 50;

      // 1. Top bar pricing text & WhatsApp link
      const topBarText = document.getElementById("topBarPricingText");
      if (topBarText) {
        topBarText.textContent = `Tarif: Rp ${vrRate.toLocaleString('id-ID')} / sesi akun aktif • Rp ${grRate.toLocaleString('id-ID')} / sesi tamu`;
      }
      const topBarWa = document.getElementById("topBarWaLink");
      if (topBarWa && data.owner_whatsapp) {
        topBarWa.href = `https://wa.me/${data.owner_whatsapp}`;
        topBarWa.textContent = `+${data.owner_whatsapp}`;
      }

      // 2. Mode selector prices
      const vrPriceEl = document.getElementById("modeValidReaderPrice");
      if (vrPriceEl) {
        vrPriceEl.textContent = `Rp ${vrRate.toLocaleString('id-ID')} / sesi`;
      }
      const grPriceEl = document.getElementById("modeGuestReaderPrice");
      if (grPriceEl) {
        grPriceEl.textContent = `Rp ${grRate.toLocaleString('id-ID')} / sesi`;
      }
      const likePriceEl = document.getElementById("modeLikePrice");
      if (likePriceEl) {
        const likeRate = (data.rates && typeof data.rates.like !== "undefined") ? data.rates.like : 100;
        if (likeRate > 0) {
          likePriceEl.textContent = `Rp ${likeRate.toLocaleString('id-ID')} / sesi`;
          likePriceEl.style.color = "";
        } else {
          likePriceEl.textContent = "Tanpa Biaya";
          likePriceEl.style.color = "var(--text-muted)";
        }
      }

      // 3. Re-render public pricing cards on homepage if element exists
      const grid = document.getElementById("publicPricingGrid");
      if (grid && data.packages && data.packages.length > 0) {
        grid.innerHTML = data.packages.map((pkg, idx) => {
          const isFeatured = Boolean(pkg.is_featured || pkg.popular);
          const cardClass = isFeatured ? "tier-card featured" : "tier-card";
          const nameStyle = isFeatured ? 'style="color: var(--primary);"' : '';
          const priceStyle = isFeatured ? 'style="color: var(--primary);"' : '';
          const btnClass = isFeatured ? "btn btn-primary" : "btn btn-secondary";
          const btnLabel = isFeatured ? "⚡ Beli Otomatis (Populer)" : "⚡ Beli Otomatis";
          const featuresList = (pkg.features || []).map(f => `<li>${f}</li>`).join("");
          const pkgNameEsc = (pkg.name || '').replace(/'/g, "\\'");

          return `
            <div class="${cardClass}">
              <div>
                <div class="tier-name" ${nameStyle}>${pkg.name}</div>
                <div class="tier-price" ${priceStyle}>Rp ${(pkg.price || 0).toLocaleString('id-ID')}</div>
                <ul class="tier-features">
                  ${featuresList}
                </ul>
              </div>
              <button class="${btnClass}" style="width: 100%; font-weight: 600;" onclick="openPaymentModal('new_token', null, ${pkg.price}, '${pkgNameEsc}')">
                ${btnLabel}
              </button>
            </div>
          `;
        }).join("");
      }

      // 4. Update modal nominal grid with package names
      const modalNom = document.getElementById("modalNominalGrid");
      if (modalNom && data.packages && data.packages.length > 0) {
        modalNom.innerHTML = data.packages.map((pkg, idx) => {
          const isFeatured = Boolean(pkg.is_featured || pkg.popular);
          const activeClass = isFeatured ? "nominal-card active" : "nominal-card";
          const badge = isFeatured ? `<span class="nom-badge">Populer</span>` : '';
          const pkgNameEsc = (pkg.name || '').replace(/'/g, "\\'");

          return `
            <div class="${activeClass}" id="nomCard${pkg.price}" onclick="selectPaymentNominal(${pkg.price}, '${pkgNameEsc}')">
              ${badge}
              <div class="nom-name">${pkg.name}</div>
              <div class="nom-val">Rp ${(pkg.price || 0).toLocaleString('id-ID')}</div>
              <div class="nom-desc">${pkg.sessions_label || (Math.floor(pkg.price/vrRate) + ' Sesi')}</div>
            </div>
          `;
        }).join("");
      }

      // 5. Update quick token creation buttons in Admin Tab 1
      const presetContainer = document.getElementById("adminPresetButtonsContainer");
      if (presetContainer && data.packages && data.packages.length > 0) {
        const currentAmount = parseInt(document.getElementById("tokenAmountInput")?.value || 0);
        presetContainer.innerHTML = data.packages.map(pkg => {
          const pkgNameEsc = (pkg.name || '').replace(/'/g, "\\'");
          const kLabel = (pkg.price >= 1000) ? (Math.round(pkg.price / 1000) + 'K') : ('Rp ' + pkg.price);
          let shortTitle = pkg.name || kLabel;
          shortTitle = shortTitle.replace(/\s*\(\d+K\)/i, '').replace(/^Paket\s+/i, '');
          const isActive = (pkg.price === currentAmount) ? ' active' : '';
          return `
            <button type="button" class="preset-chip${isActive}" id="presetChip_${pkg.price}" onclick="setPresetPackage(${pkg.price}, '${pkgNameEsc}', this)" title="${pkg.name} - Rp ${(pkg.price || 0).toLocaleString('id-ID')}">
              ${kLabel} <span class="preset-chip-sub">${shortTitle}</span>
            </button>
          `;
        }).join("") + `
          <button type="button" class="preset-chip preset-chip-add" onclick="showAddPresetModal()" title="Tambah Preset Baru">
            + Tambah
          </button>
        `;
      }

      // 6. Update user capacity if token verified
      if (activeBalance > 0) {
        const capacityDisplay = document.getElementById("userMemberCapacityDisplay");
        if (capacityDisplay) {
          const sessions = Math.floor(activeBalance / vrRate);
          const guestSessions = Math.floor(activeBalance / grRate);
          capacityDisplay.textContent = `${sessions} Sesi Member (${guestSessions} Sesi Tamu)`;
        }
      }

      // 7. Recalculate estimated cost with dynamic rates
      calculateEstimatedCost();

      // 8. Update client account generator pricing UI
      const agRate = rates.account_generator || 50;
      const agBadge = document.getElementById("clientGenRateBadge");
      if (agBadge) {
        agBadge.textContent = `Tarif: Rp ${agRate.toLocaleString('id-ID')} / Akun`;
      }
      const agUnitDisplay = document.getElementById("clientGenRateUnitDisplay");
      if (agUnitDisplay) {
        agUnitDisplay.textContent = `Rp ${agRate.toLocaleString('id-ID')}`;
      }
      if (typeof updateAccountGenEstimatedCost === "function") {
        updateAccountGenEstimatedCost();
      }

      // 9. Free Trial banner & pricing card
      const trialCfg = data.free_trial || {};
      const trialEnabled = Boolean(trialCfg.enabled !== false);
      const trialAccounts = parseInt(trialCfg.accounts_count) || 5;
      const trialCooldown = parseInt(trialCfg.cooldown_hours) || 24;
      const trialLike = trialCfg.do_like !== false;
      const trialFollow = trialCfg.do_follow !== false;

      // Banner di dashboard
      const ftBanner = document.getElementById("freeTrialBanner");
      if (ftBanner) ftBanner.style.display = trialEnabled ? "" : "none";

      // Card di pricing page
      const ftPricingCard = document.getElementById("pricingFreeTrialCard");
      if (ftPricingCard) ftPricingCard.style.display = trialEnabled ? "" : "none";

      // Update counter labels
      const accCountEls = ["trialAccountCount", "trialAccountCount2", "trialAccFeature", "ftModalAccCount"];
      accCountEls.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = id === "ftModalAccCount" ? `${trialAccounts} Akun` : trialAccounts;
      });

      // Update cooldown info
      const ftCooldown = document.getElementById("ftCooldownHours");
      if (ftCooldown) ftCooldown.textContent = trialCooldown;

      // Update like/follow feature visibility
      const ftpcLike = document.getElementById("ftpcLikeFeat");
      if (ftpcLike) ftpcLike.style.display = (trialLike || trialFollow) ? "" : "none";
      const ftModalLike = document.getElementById("ftModalLikeBox");
      if (ftModalLike) ftModalLike.style.display = trialLike ? "" : "none";
      const ftModalFollow = document.getElementById("ftModalFollowBox");
      if (ftModalFollow) ftModalFollow.style.display = trialFollow ? "" : "none";

      // Store trial config for modal
      window._trialCfg = { enabled: trialEnabled, accounts_count: trialAccounts, cooldown_hours: trialCooldown, do_like: trialLike, do_follow: trialFollow };
    }
  } catch (e) {
    console.error("Error loading pricing config:", e);
  }
}

async function loadAdminPricing() {
  if (!adminPin) return;
  const alertEl = document.getElementById("adminPricingAlert");
  if (alertEl) alertEl.style.display = "none";

  try {
    const resp = await fetch("/api/admin/pricing", {
      headers: { "X-Admin-Pin": adminPin },
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.detail || data.error || "Gagal memuat tarif");

    const cfg = data.config || {};
    const rates = cfg.rates || {};
    const pkgs = cfg.packages || [];
    const ownerWa = cfg.owner_wa || "6287734343023";
    const trial = cfg.free_trial || {};

    if (document.getElementById("priceValidReaderInput")) {
      document.getElementById("priceValidReaderInput").value = rates.valid_reader || 500;
    }
    if (document.getElementById("priceGuestReaderInput")) {
      document.getElementById("priceGuestReaderInput").value = rates.guest_reader || 50;
    }
    if (document.getElementById("priceLikeInput")) {
      document.getElementById("priceLikeInput").value = rates.like || 100;
    }
    if (document.getElementById("priceAccountGeneratorInput")) {
      document.getElementById("priceAccountGeneratorInput").value = rates.account_generator || 50;
    }
    if (document.getElementById("priceOwnerWaInput")) {
      document.getElementById("priceOwnerWaInput").value = ownerWa;
    }

    // Free trial settings
    const trialEnabled = document.getElementById("trialEnabledToggle");
    if (trialEnabled) trialEnabled.checked = (trial.enabled !== false);
    const trialAccounts = document.getElementById("trialAccountsCountInput");
    if (trialAccounts) trialAccounts.value = trial.accounts_count || 5;
    const trialCooldown = document.getElementById("trialCooldownInput");
    if (trialCooldown) trialCooldown.value = trial.cooldown_hours || 24;
    const trialLike = document.getElementById("trialDoLikeToggle");
    if (trialLike) trialLike.checked = (trial.do_like !== false);
    const trialFollow = document.getElementById("trialDoFollowToggle");
    if (trialFollow) trialFollow.checked = (trial.do_follow !== false);

    // Set state & render dynamic package cards
    adminPackagesState = JSON.parse(JSON.stringify(pkgs));
    renderAdminPackages();
  } catch (err) {
    console.error("Error loadAdminPricing:", err);
  }
}

function renderAdminPackages() {
  const container = document.getElementById("adminPackagesContainer");
  if (!container) return;

  if (!adminPackagesState || adminPackagesState.length === 0) {
    container.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 28px; background: var(--bg-subtle); border-radius: var(--radius-md); border: 1px dashed var(--border-color);">Belum ada paket yang dibuat. Klik tombol <b>"+ Tambah Paket"</b> di atas.</div>`;
    return;
  }

  container.innerHTML = adminPackagesState.map((pkg, idx) => {
    const isPop = Boolean(pkg.popular || pkg.is_featured);
    const cardClass = isPop ? "admin-pkg-card featured" : "admin-pkg-card";
    const featuresStr = Array.isArray(pkg.features) ? pkg.features.join("\n") : (pkg.features || "");
    const pkgName = pkg.name || `Paket Saldo ${Math.round((pkg.price || 10000) / 1000)}K`;

    const pkgIconSvg = isPop
      ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`
      : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>`;

    return `
      <div class="${cardClass}" id="adminPkgCard_${idx}">
        <div class="admin-pkg-header">
          <div class="admin-pkg-title-wrap">
            <div style="width: 32px; height: 32px; border-radius: var(--radius-md); background: ${isPop ? 'var(--primary-subtle)' : 'var(--bg-subtle)'}; color: ${isPop ? 'var(--primary)' : 'var(--text-muted)'}; display: flex; align-items: center; justify-content: center; border: 1px solid ${isPop ? 'var(--primary-border)' : 'var(--border-color)'}; flex-shrink: 0;">
              ${pkgIconSvg}
            </div>
            <div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="admin-pkg-title" id="pkgCardHeader_${idx}">
                  <span class="pkg-header-name">${pkgName}</span>
                </span>
                ${isPop ? `<span class="badge badge-primary" style="font-size: 10px; padding: 2px 7px;">Unggulan</span>` : ''}
              </div>
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <span class="font-mono" style="font-size: 11px; color: var(--text-muted); background: var(--bg-subtle); border: 1px solid var(--border-color); padding: 3px 8px; border-radius: var(--radius-xs);">
              ID: ${pkg.id || `pkg_${idx + 1}`}
            </span>
            <button type="button" class="btn btn-secondary btn-sm" onclick="adminDeletePackageCard(${idx})" style="padding: 4px 10px; font-size: 11px; color: var(--danger); border-color: rgba(220,38,38,0.25); background: rgba(220,38,38,0.04); display: inline-flex; align-items: center; gap: 4px;" title="Hapus paket ini">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              Hapus
            </button>
          </div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 200px; gap: 14px; margin-bottom: 12px;">
          <div>
            <label class="form-label" style="font-size: 11px; color: var(--text-muted); margin-bottom: 5px;">Nama Paket:</label>
            <input type="text" class="form-control" value="${pkgName}" placeholder="Contoh: Paket Starter Novel" oninput="onAdminPkgNameChange(${idx}, this.value)" style="font-weight: 600;">
          </div>
          <div>
            <label class="form-label" style="font-size: 11px; color: var(--text-muted); margin-bottom: 5px;">Harga Saldo (Rp):</label>
            <div class="admin-currency-input-wrap">
              <span class="admin-currency-prefix">Rp</span>
              <input type="number" class="form-control" value="${pkg.price || 10000}" min="1000" step="1000" oninput="onAdminPkgPriceChange(${idx}, this.value)" style="font-weight: 700;">
            </div>
          </div>
        </div>

        <div style="margin-bottom: 14px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
            <label class="form-label" style="font-size: 11px; color: var(--text-muted); margin-bottom: 0;">Deskripsi Sesi / Rincian Fitur:</label>
            <span style="font-size: 10px; color: var(--text-muted);">Gunakan Enter untuk bullet points</span>
          </div>
          <textarea class="form-control admin-features-textarea" rows="3" placeholder="Contoh:&#10;40 sesi akun member resmi&#10;400 sesi pembaca tamu&#10;Otomatis suka &amp; simpan novel" oninput="onAdminPkgFeaturesChange(${idx}, this.value)">${featuresStr}</textarea>
        </div>

        <label class="modern-toggle-label" style="font-size: 12px; margin: 0;">
          <div class="modern-toggle-switch">
            <input type="checkbox" ${isPop ? 'checked' : ''} onchange="onAdminPkgFeaturedToggle(${idx}, this.checked)">
            <span class="modern-toggle-slider"></span>
          </div>
          <span style="color: var(--text-body);">Tandai sebagai <b>Paket Unggulan / Featured</b> (Diberi highlight di halaman kasir &amp; beranda)</span>
        </label>
      </div>
    `;
  }).join("");
}


function onAdminPkgNameChange(idx, val) {
  if (adminPackagesState[idx]) {
    adminPackagesState[idx].name = val.trim() || `Paket ${idx + 1}`;
    const headerName = document.querySelector(`#pkgCardHeader_${idx} .pkg-header-name`);
    if (headerName) headerName.textContent = adminPackagesState[idx].name;
  }
}

function onAdminPkgPriceChange(idx, val) {
  if (adminPackagesState[idx]) {
    adminPackagesState[idx].price = parseInt(val) || 0;
  }
}

function onAdminPkgFeaturesChange(idx, val) {
  if (adminPackagesState[idx]) {
    adminPackagesState[idx].features = val.replace(/\r/g, "").split("\n").map(s => s.trim()).filter(Boolean);
  }
}

function onAdminPkgFeaturedToggle(idx, checked) {
  if (adminPackagesState[idx]) {
    adminPackagesState[idx].popular = checked;
    adminPackagesState[idx].is_featured = checked;
    renderAdminPackages();
  }
}

function adminAddNewPackageCard() {
  if (!Array.isArray(adminPackagesState)) {
    adminPackagesState = [];
  }
  const nextIdx = adminPackagesState.length + 1;
  const defaultPrice = (nextIdx * 10000) || 10000;
  adminPackagesState.push({
    id: `pkg_${defaultPrice}`,
    name: `Paket Saldo ${Math.round(defaultPrice / 1000)}K`,
    price: defaultPrice,
    popular: false,
    is_featured: false,
    features: [
      `${Math.floor(defaultPrice / 300)} sesi akun member resmi`,
      `${Math.floor(defaultPrice / 50)} sesi pembaca tamu`,
      "Otomatis suka & simpan novel",
      "Saldo utuh tanpa masa kadaluarsa"
    ]
  });
  renderAdminPackages();
  const lastCard = document.getElementById(`adminPkgCard_${adminPackagesState.length - 1}`);
  if (lastCard) lastCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function adminDeletePackageCard(idx) {
  if (adminPackagesState.length <= 1) {
    alert("Minimal harus ada 1 paket aktif.");
    return;
  }
  const pkg = adminPackagesState[idx];
  if (confirm(`Apakah Anda yakin ingin menghapus '${pkg.name || `Paket ${idx + 1}`}'?`)) {
    adminPackagesState.splice(idx, 1);
    renderAdminPackages();
  }
}

async function saveAdminPricing() {
  const btn = document.getElementById("btnSavePricing");
  const alertEl = document.getElementById("adminPricingAlert");

  const vrRate = parseInt(document.getElementById("priceValidReaderInput")?.value) || 500;
  const grRate = parseInt(document.getElementById("priceGuestReaderInput")?.value) || 50;
  const likeRate = parseInt(document.getElementById("priceLikeInput")?.value) || 100;
  const agRate = parseInt(document.getElementById("priceAccountGeneratorInput")?.value) || 50;
  const ownerWa = (document.getElementById("priceOwnerWaInput")?.value || "6287734343023").trim();

  // Validate packages
  if (!adminPackagesState || adminPackagesState.length === 0) {
    alert("Minimal harus ada 1 paket saldo yang disimpan.");
    return;
  }

  const payload = {
    rates: {
      valid_reader: vrRate,
      guest_reader: grRate,
      like: likeRate,
      bookmark: likeRate,
      follow: likeRate,
      account_generator: agRate,
    },
    packages: adminPackagesState.map((p, idx) => ({
      id: p.id || `pkg_${p.price || (idx + 1) * 10000}`,
      name: (p.name || `Paket ${idx + 1}`).trim(),
      price: parseInt(p.price) || 10000,
      features: Array.isArray(p.features) ? p.features : [String(p.features || '')],
      popular: Boolean(p.popular || p.is_featured),
      is_featured: Boolean(p.popular || p.is_featured),
    })),
    owner_wa: ownerWa,
    free_trial: {
      enabled: document.getElementById("trialEnabledToggle")?.checked !== false,
      accounts_count: Math.max(1, Math.min(50, parseInt(document.getElementById("trialAccountsCountInput")?.value) || 5)),
      cooldown_hours: Math.max(1, Math.min(720, parseInt(document.getElementById("trialCooldownInput")?.value) || 24)),
      do_like: document.getElementById("trialDoLikeToggle")?.checked !== false,
      do_follow: document.getElementById("trialDoFollowToggle")?.checked !== false,
    },
  };

  if (btn) btn.disabled = true;
  if (alertEl) {
    alertEl.style.display = "block";
    alertEl.style.background = "var(--primary-subtle)";
    alertEl.style.color = "var(--primary-text)";
    alertEl.style.border = "1px solid var(--primary-border)";
    alertEl.textContent = "Menyimpan pengaturan tarif & paket...";
  }

  try {
    const resp = await fetch("/api/admin/pricing", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Pin": adminPin,
      },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.ok) {
      if (alertEl) {
        alertEl.style.background = "#dcfce7";
        alertEl.style.color = "#166534";
        alertEl.style.border = "1px solid #bbf7d0";
        alertEl.textContent = "✅ " + (data.message || "Pengaturan tarif & paket harga berhasil diperbarui!");
      }
      await loadPricingConfig();
      setTimeout(() => { if (alertEl) alertEl.style.display = "none"; }, 4000);
    } else {
      throw new Error(data.detail || data.error || "Gagal menyimpan");
    }
  } catch (err) {
    if (alertEl) {
      alertEl.style.background = "#fef2f2";
      alertEl.style.color = "#991b1b";
      alertEl.style.border = "1px solid #fecaca";
      alertEl.textContent = "❌ " + err.message;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

// =============================================================================
// FREE TRIAL SUITE (Tanpa Token)
// =============================================================================
let _ftEventSource = null;

function openFreeTrialModal() {
  const modal = document.getElementById("freeTrialModal");
  if (!modal) return;
  // Reset to step 1
  const s1 = document.getElementById("ftStep1");
  const s2 = document.getElementById("ftStep2");
  const done = document.getElementById("ftStep2Done");
  if (s1) s1.style.display = "";
  if (s2) s2.style.display = "none";
  if (done) done.style.display = "none";
  const errBox = document.getElementById("ftErrorBox");
  if (errBox) errBox.style.display = "none";
  const logBody = document.getElementById("ftLogBody");
  if (logBody) logBody.innerHTML = "";
  // Pre-fill novel URL from main input
  const mainNovel = document.getElementById("novelUrlInput") || document.getElementById("ftNovelUrlInput");
  const ftInput = document.getElementById("ftNovelUrlInput");
  if (ftInput && mainNovel && mainNovel !== ftInput && mainNovel.value.trim()) {
    ftInput.value = mainNovel.value.trim();
  }
  modal.style.display = "flex";
}

function closeFreeTrialModal() {
  const modal = document.getElementById("freeTrialModal");
  if (modal) modal.style.display = "none";
  if (_ftEventSource) { _ftEventSource.close(); _ftEventSource = null; }
}

async function startFreeTrial() {
  const novelUrl = (document.getElementById("ftNovelUrlInput")?.value || "").trim();
  if (!novelUrl) {
    const errBox = document.getElementById("ftErrorBox");
    if (errBox) { errBox.textContent = "Masukkan URL atau ID novel terlebih dahulu!"; errBox.style.display = "block"; }
    return;
  }

  const btn = document.getElementById("ftStartBtn");
  const errBox = document.getElementById("ftErrorBox");
  if (errBox) errBox.style.display = "none";
  if (btn) btn.disabled = true;

  try {
    const resp = await fetch("/api/free-trial/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ novel_url: novelUrl }),
    });
    const data = await resp.json();

    if (!data.ok) {
      if (errBox) {
        let errMsg = data.error || "Gagal memulai free trial.";
        // Jika cooldown, tambahkan CTA beli
        if (resp.status === 429) {
          errMsg += ` <a href="#" style="color:var(--primary);font-weight:700;" onclick="closeFreeTrialModal();openPaymentModal('new_token');return false;">→ Beli Paket Sekarang</a>`;
        }
        errBox.innerHTML = errMsg;
        errBox.style.display = "block";
      }
      if (btn) btn.disabled = false;
      return;
    }

    // Tampilkan step 2: live log
    const s1 = document.getElementById("ftStep1");
    const s2 = document.getElementById("ftStep2");
    if (s1) s1.style.display = "none";
    if (s2) s2.style.display = "";

    const taskId = data.task_id;
    const logBody = document.getElementById("ftLogBody");
    const statusTag = document.getElementById("ftTrialStatusTag");

    function appendFtLog(msg, cls) {
      if (!logBody) return;
      const row = document.createElement("div");
      row.className = "log-row";
      const ts = new Date().toLocaleTimeString("id-ID");
      row.innerHTML = `<span class="log-time">[${ts}]</span><span class="${cls || 'log-msg-system'}">${msg}</span>`;
      logBody.appendChild(row);
      logBody.scrollTop = logBody.scrollHeight;
    }

    appendFtLog(`Free Trial dimulai dengan ${data.trial_accounts || 5} akun tamu. Task ID: ${taskId}`, "log-msg-system");

    // Connect SSE
    if (_ftEventSource) _ftEventSource.close();
    _ftEventSource = new EventSource(`/api/tasks/stream/${taskId}`);

    _ftEventSource.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === "log") {
          const lvl = ev.level || "info";
          const cls = lvl === "success" ? "log-msg-success" : (lvl === "error" ? "log-msg-error" : (lvl === "warning" ? "log-msg-warning" : "log-msg-info"));
          appendFtLog(ev.message || "", cls);
        } else if (ev.type === "done" || ev.type === "complete") {
          if (statusTag) { statusTag.textContent = "Selesai"; statusTag.style.background = "#dcfce7"; statusTag.style.color = "#166534"; statusTag.style.borderColor = "#bbf7d0"; }
          appendFtLog("Free trial selesai. Silakan periksa pembacaan dan like di akun novel target.", "log-msg-success");
          const doneDiv = document.getElementById("ftStep2Done");
          if (doneDiv) doneDiv.style.display = "";
          _ftEventSource.close(); _ftEventSource = null;
        }
      } catch (_) {}
    };

    _ftEventSource.onerror = () => {
      if (statusTag) { statusTag.textContent = "Terputus"; statusTag.style.background = "#fef2f2"; statusTag.style.color = "#991b1b"; }
      appendFtLog("⚠️ Koneksi stream terputus.", "log-msg-warning");
      _ftEventSource.close(); _ftEventSource = null;
    };

  } catch (err) {
    if (errBox) { errBox.textContent = "Gagal terhubung ke server: " + err.message; errBox.style.display = "block"; }
    if (btn) btn.disabled = false;
  }
}


// =============================================================================
// CLIENT ACCOUNT GENERATOR SUITE
// =============================================================================
let clientGeneratedAccounts = [];


function updateAccountGenEstimatedCost() {
  const countInput = document.getElementById("clientGenCountInput");
  const count = parseInt(countInput ? countInput.value : 5) || 1;
  const rates = (globalPricing && globalPricing.rates) ? globalPricing.rates : {};
  const rate = parseInt(rates.account_generator) || 50;
  const total = count * rate;

  const costDisplay = document.getElementById("clientGenEstimatedCost");
  if (costDisplay) {
    costDisplay.textContent = formatRupiah(total);
  }
  const calcNote = document.getElementById("clientGenCalcNote");
  if (calcNote) {
    calcNote.textContent = `${count} akun × Rp ${rate.toLocaleString('id-ID')} (hanya sukses dipotong)`;
  }
}

function setClientGenCount(val) {
  const input = document.getElementById("clientGenCountInput");
  const slider = document.getElementById("clientGenCountSlider");
  if (input) input.value = val;
  if (slider) slider.value = val;
  syncGenChips(val);
  updateAccountGenEstimatedCost();
}

function syncGenChips(val) {
  const chips = document.querySelectorAll("#clientGenPresetChips .preset-chip");
  chips.forEach(chip => {
    if (parseInt(chip.getAttribute("data-val")) === parseInt(val)) {
      chip.classList.add("active");
    } else {
      chip.classList.remove("active");
    }
  });
}

async function generateClientAccounts() {
  const tokenInput = document.getElementById("clientGenTokenInput");
  const token = (tokenInput ? tokenInput.value.trim() : "") || activeToken;
  if (!token) {
    alert("Silakan masukkan Token Akses terlebih dahulu.");
    if (tokenInput) tokenInput.focus();
    return;
  }

  const countInput = document.getElementById("clientGenCountInput");
  const count = parseInt(countInput ? countInput.value : 5) || 1;
  const countrySelect = document.getElementById("clientGenCountrySelect");
  const country = countrySelect ? countrySelect.value : "RANDOM";
  const uaSelect = document.getElementById("clientGenUaSelect");
  const ua_mode = uaSelect ? uaSelect.value : "okhttp";

  const rates = (globalPricing && globalPricing.rates) ? globalPricing.rates : {};
  const rate = parseInt(rates.account_generator) || 50;
  const estimatedTotal = count * rate;

  if (activeBalance > 0 && activeBalance < rate) {
    alert(`Saldo token Anda (Rp ${activeBalance.toLocaleString('id-ID')}) tidak mencukupi untuk membuat akun (minimal Rp ${rate.toLocaleString('id-ID')}). Silakan top up saldo terlebih dahulu.`);
    openPaymentModal('topup', token);
    return;
  }

  const btn = document.getElementById("btnClientGenStart");
  const alertEl = document.getElementById("clientGenAlert");
  const progressBox = document.getElementById("clientGenProgress");
  const progressText = document.getElementById("clientGenProgressText");

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-block;width:13px;height:13px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:6px;vertical-align:middle;"></span> Memproses Registrasi...`;
  }
  if (alertEl) alertEl.style.display = "none";
  if (progressBox) progressBox.style.display = "block";
  if (progressText) progressText.textContent = `Sedang mendaftarkan ${count} akun WebNovel via bypass proxy (${country})...`;

  try {
    const resp = await fetch("/api/accounts/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, count, country, ua_mode }),
    });

    const data = await resp.json();

    if (!resp.ok || !data.ok) {
      throw new Error(data.error || "Gagal membuat akun.");
    }

    // Update active balance and UI
    if (typeof data.remaining_balance === "number") {
      activeBalance = data.remaining_balance;
      const balDisplay = document.getElementById("userBalanceDisplay");
      if (balDisplay) balDisplay.textContent = formatRupiah(activeBalance);
      const navVal = document.getElementById("navBalanceValue");
      if (navVal) navVal.textContent = formatRupiah(activeBalance);
      const statBal = document.getElementById("statRemainingBal");
      if (statBal) statBal.textContent = formatRupiah(activeBalance);
      const clientBal = document.getElementById("clientGenBalanceDisplay");
      if (clientBal) clientBal.textContent = formatRupiah(activeBalance);
      const walletBtn = document.getElementById("sidebarWalletBtn");
      if (walletBtn) walletBtn.setAttribute("data-tooltip", `Saldo: ${formatRupiah(activeBalance)} (Klik Top Up)`);
    }

    // Append newly created accounts to state
    if (data.accounts && data.accounts.length > 0) {
      data.accounts.forEach(acc => {
        clientGeneratedAccounts.unshift({
          ...acc,
          timestamp: new Date().toLocaleTimeString('id-ID'),
          country: country
        });
      });
      renderClientGeneratedAccounts();
    }

    if (alertEl) {
      alertEl.style.display = "block";
      const created = data.created_count || 0;
      const billed = data.total_cost || 0;
      if (created === count) {
        alertEl.className = "alert alert-success";
        alertEl.style.background = "var(--primary-subtle)";
        alertEl.style.color = "var(--primary)";
        alertEl.style.border = "1px solid var(--primary-border)";
        alertEl.innerHTML = `<b>Berhasil:</b> ${created} akun WebNovel berhasil digenerate &amp; siap digunakan. Terpotong: <b>${formatRupiah(billed)}</b> (Rp ${data.rate_per_account}/akun). Sisa saldo: <b>${formatRupiah(data.remaining_balance)}</b>.`;
      } else if (created > 0) {
        alertEl.className = "alert alert-warning";
        alertEl.style.background = "var(--warning-subtle)";
        alertEl.style.color = "var(--warning)";
        alertEl.style.border = "1px solid var(--warning-border)";
        alertEl.innerHTML = `<b>Selesai Sebagian:</b> ${created} dari ${count} akun berhasil dibuat. Saldo hanya dipotong untuk akun sukses: <b>${formatRupiah(billed)}</b>. Sisa saldo: <b>${formatRupiah(data.remaining_balance)}</b>.`;
      } else {
        alertEl.className = "alert alert-danger";
        alertEl.style.background = "var(--danger-subtle)";
        alertEl.style.color = "var(--danger)";
        alertEl.style.border = "1px solid var(--danger-border)";
        alertEl.innerHTML = `<b>Gagal:</b> Tidak ada akun yang berhasil dibuat. Saldo Anda tidak dipotong (Rp 0). Periksa koneksi proxy atau coba negara lain.`;
      }
    }

  } catch (err) {
    if (alertEl) {
      alertEl.style.display = "block";
      alertEl.className = "alert alert-danger";
      alertEl.style.background = "var(--danger-subtle)";
      alertEl.style.color = "var(--danger)";
      alertEl.style.border = "1px solid var(--danger-border)";
      alertEl.innerHTML = `<b>Terjadi Kesalahan:</b> ${err.message}`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg> Generate Akun Sekarang`;
    }
    if (progressBox) progressBox.style.display = "none";
  }
}

function renderClientGeneratedAccounts() {
  const container = document.getElementById("clientGenAccountsList");
  const countBadge = document.getElementById("clientGenTotalBadge");
  const rawTextarea = document.getElementById("clientGenRawTextarea");
  if (!container) return;

  if (countBadge) countBadge.textContent = `${clientGeneratedAccounts.length} Akun`;

  if (clientGeneratedAccounts.length === 0) {
    container.innerHTML = `
      <div style="text-align:center;padding:64px 20px;color:var(--text-muted);">
        <div style="width:48px;height:48px;border-radius:50%;background:var(--bg-subtle);border:1px solid var(--border-color);display:inline-flex;align-items:center;justify-content:center;margin-bottom:12px;color:var(--text-muted);">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        </div>
        <div style="font-weight:600;color:var(--text-heading);margin-bottom:4px;font-size:14px;">Belum Ada Akun Dibuat</div>
        <div style="font-size:12px;max-width:320px;margin:0 auto;line-height:1.5;">Tentukan jumlah akun pada formulir lalu klik tombol <b>Generate Akun Sekarang</b>.</div>
      </div>
    `;
    if (rawTextarea) rawTextarea.value = "";
    return;
  }

  // Populate raw textarea (format: email:password:token)
  if (rawTextarea) {
    rawTextarea.value = clientGeneratedAccounts.map(a => `${a.email}:${a.password}:${a.token || a.user_id || ''}`).join("\n");
  }

  // Render cards / list
  container.innerHTML = clientGeneratedAccounts.map((acc, idx) => {
    const emailEsc = (acc.email || "").replace(/"/g, "&quot;");
    const passEsc = (acc.password || "").replace(/"/g, "&quot;");
    const tokEsc = (acc.token || "").replace(/"/g, "&quot;");
    const uid = acc.user_id || "-";
    const timeStr = acc.timestamp || "-";
    const country = acc.country || "ID";

    return `
      <div class="account-item-card" style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--border-subtle);gap:12px;background:var(--card-bg);transition:background 0.15s ease;">
        <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;">
          <div style="width:28px;height:28px;border-radius:6px;background:var(--primary-subtle);color:var(--primary);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0;">
            #${clientGeneratedAccounts.length - idx}
          </div>
          <div style="min-width:0;flex:1;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;flex-wrap:wrap;">
              <span class="font-mono" style="font-weight:700;color:var(--text-heading);font-size:13px;word-break:break-all;">${acc.email}</span>
              <span class="live-pill" style="font-size:10px;padding:1px 6px;height:auto;">Aktif</span>
              <span style="font-size:11px;color:var(--text-muted);background:var(--bg-main);padding:1px 5px;border-radius:4px;border:1px solid var(--border-subtle);">${country}</span>
            </div>
            <div style="display:flex;align-items:center;gap:12px;font-size:11px;color:var(--text-muted);flex-wrap:wrap;">
              <span>Pass: <b class="font-mono" style="color:var(--text-body);">${acc.password}</b></span>
              <span>UID: <span class="font-mono">${uid}</span></span>
              <span>Jam: ${timeStr}</span>
            </div>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0;">
          <button class="btn btn-secondary btn-sm" onclick="copySingleAccount('${emailEsc}', '${passEsc}', '${tokEsc}')" title="Salin Email:Pass" style="padding:4px 8px;font-size:11px;display:inline-flex;align-items:center;gap:4px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            Salin
          </button>
        </div>
      </div>
    `;
  }).join("");
}

function copySingleAccount(email, password, token) {
  const text = `${email}:${password}`;
  navigator.clipboard.writeText(text).then(() => {
    alert(`Disalin: ${text}`);
  }).catch(() => {
    prompt("Salin akun:", text);
  });
}

function copyAllGeneratedAccounts(format) {
  if (clientGeneratedAccounts.length === 0) {
    alert("Belum ada akun yang digenerate.");
    return;
  }

  let text = "";
  if (format === "email_pass") {
    text = clientGeneratedAccounts.map(a => `${a.email}:${a.password}`).join("\n");
  } else {
    text = clientGeneratedAccounts.map(a => `${a.email}:${a.password}:${a.token || ''}`).join("\n");
  }

  navigator.clipboard.writeText(text).then(() => {
    alert(`Berhasil menyalin ${clientGeneratedAccounts.length} akun ke clipboard!`);
  }).catch(() => {
    const el = document.getElementById("clientGenRawTextarea");
    if (el) {
      el.select();
      document.execCommand("copy");
      alert(`Berhasil menyalin ${clientGeneratedAccounts.length} akun!`);
    }
  });
}

function downloadGeneratedAccountsTxt() {
  if (clientGeneratedAccounts.length === 0) {
    alert("Belum ada akun untuk diunduh.");
    return;
  }

  const lines = clientGeneratedAccounts.map(a => `${a.email}:${a.password}:${a.token || ''}`);
  const blob = new Blob([lines.join("\r\n")], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `akun_webnovel_${new Date().toISOString().slice(0,10)}_${Date.now().toString().slice(-4)}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function clearGeneratedAccounts() {
  if (clientGeneratedAccounts.length === 0) return;
  if (confirm("Hapus daftar akun yang ditampilkan di layar? (File akun di server tetap tersimpan aman).")) {
    clientGeneratedAccounts = [];
    renderClientGeneratedAccounts();
  }
}



