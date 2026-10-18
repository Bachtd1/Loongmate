/**
 * LoongMate Handheld Simulator Frontend Logic
 * Miniloong Pocket 1 (Leaf CFW)
 */

document.addEventListener("DOMContentLoaded", () => {
  // App state
  const state = {
    currentView: "home", // "home", "savevault", "doctor", "health"
    focusedCardIndex: 0, // 0: Device, 1: Storage, 2: SaveVault, 3: Doctor
    readinessData: null,
    auditData: null,
    snapshots: [],
    hardwareState: {
      battery_percent: 88,
      is_charging: false,
      temperature_c: 41.5,
      sd2_mounted: true,
      sd2_filesystem: "vfat",
      sd2_writable: true
    }
  };

  // DOM Elements
  const cards = [
    document.getElementById("card-device"),
    document.getElementById("card-storage"),
    document.getElementById("card-savevault"),
    document.getElementById("card-doctor")
  ];

  const views = {
    home: document.getElementById("view-home"),
    savevault: document.getElementById("view-savevault"),
    doctor: document.getElementById("view-doctor"),
    health: document.getElementById("view-health")
  };

  const toast = document.getElementById("screen-toast");
  const toastText = document.getElementById("toast-text");
  const termBox = document.getElementById("wb-terminal");

  // Status Bar Elements
  const sbClock = document.getElementById("sb-clock");
  const sbSd1 = document.getElementById("sb-sd1-badge");
  const sbSd2 = document.getElementById("sb-sd2-badge");
  const sbTemp = document.getElementById("sb-temp-badge");
  const sbBatteryFill = document.getElementById("sb-battery-fill");
  const sbBatteryText = document.getElementById("sb-battery-text");
  const sbBatteryBolt = document.getElementById("sb-battery-bolt");

  // Clock ticker
  function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, "0");
    const m = String(now.getMinutes()).padStart(2, "0");
    sbClock.textContent = `${h}:${m}`;
  }
  setInterval(updateClock, 1000);
  updateClock();

  // Logging utility
  function logTerm(msg, type = "info") {
    const line = document.createElement("div");
    line.className = `term-line ${type}`;
    const time = new Date().toLocaleTimeString();
    line.textContent = `[${time}] ${msg}`;
    termBox.appendChild(line);
    termBox.scrollTop = termBox.scrollHeight;
  }

  // Toast Notification
  let toastTimer = null;
  function showToast(message, isSuccess = true) {
    if (toastTimer) clearTimeout(toastTimer);
    toastText.textContent = message;
    document.getElementById("toast-icon").textContent = isSuccess ? "✓" : "⚠";
    toast.classList.add("show");
    toastTimer = setTimeout(() => {
      toast.classList.remove("show");
    }, 2500);
  }

  // Navigation Logic
  function setFocus(index) {
    state.focusedCardIndex = (index + 4) % 4;
    cards.forEach((c, idx) => {
      if (idx === state.focusedCardIndex) {
        c.classList.add("focused");
      } else {
        c.classList.remove("focused");
      }
    });
  }

  function switchView(viewName) {
    state.currentView = viewName;
    Object.keys(views).forEach(k => {
      if (k === viewName) {
        views[k].classList.add("active");
      } else {
        views[k].classList.remove("active");
      }
    });

    logTerm(`Chuyển màn hình: [${viewName.toUpperCase()}]`, "info");

    if (viewName === "savevault") loadSnapshots();
    if (viewName === "doctor") loadDoctorAudit();
    if (viewName === "health") loadHealthDiagnostics();
  }

  // Button Action Handlers
  function handleButtonPress(action) {
    // Visual flash on physical button
    const btnMap = {
      up: "btn-dpad-up",
      down: "btn-dpad-down",
      left: "btn-dpad-left",
      right: "btn-dpad-right",
      a: "btn-face-a",
      b: "btn-face-b",
      x: "btn-face-x",
      y: "btn-face-y",
      l1: "btn-l1",
      r1: "btn-r1"
    };

    if (btnMap[action]) {
      const el = document.getElementById(btnMap[action]);
      if (el) {
        el.classList.add("pressed");
        setTimeout(() => el.classList.remove("pressed"), 120);
      }
    }

    if (state.currentView === "home") {
      if (action === "up") {
        setFocus(state.focusedCardIndex - 2);
      } else if (action === "down") {
        setFocus(state.focusedCardIndex + 2);
      } else if (action === "left") {
        if (state.focusedCardIndex % 2 === 1) setFocus(state.focusedCardIndex - 1);
      } else if (action === "right") {
        if (state.focusedCardIndex % 2 === 0) setFocus(state.focusedCardIndex + 1);
      } else if (action === "a") {
        // Open selected card
        if (state.focusedCardIndex === 0) switchView("health");
        else if (state.focusedCardIndex === 1) switchView("health");
        else if (state.focusedCardIndex === 2) switchView("savevault");
        else if (state.focusedCardIndex === 3) switchView("doctor");
      } else if (action === "x") {
        triggerQuickBackup();
      } else if (action === "y") {
        triggerSupportBundle();
      }
    } else {
      // Sub-screen navigation
      if (action === "b") {
        switchView("home");
      } else if (action === "a") {
        if (state.currentView === "savevault") triggerQuickBackup();
        else if (state.currentView === "doctor") triggerSafeFixes();
        else if (state.currentView === "health") triggerSupportBundle();
      }
    }
  }

  // Card click events
  cards.forEach((card, idx) => {
    card.addEventListener("click", () => {
      setFocus(idx);
      if (idx === 0 || idx === 1) switchView("health");
      else if (idx === 2) switchView("savevault");
      else if (idx === 3) switchView("doctor");
    });
  });

  // Physical Gamepad Button Clicks
  document.getElementById("btn-dpad-up").addEventListener("click", () => handleButtonPress("up"));
  document.getElementById("btn-dpad-down").addEventListener("click", () => handleButtonPress("down"));
  document.getElementById("btn-dpad-left").addEventListener("click", () => handleButtonPress("left"));
  document.getElementById("btn-dpad-right").addEventListener("click", () => handleButtonPress("right"));

  document.getElementById("btn-face-a").addEventListener("click", () => handleButtonPress("a"));
  document.getElementById("btn-face-b").addEventListener("click", () => handleButtonPress("b"));
  document.getElementById("btn-face-x").addEventListener("click", () => handleButtonPress("x"));
  document.getElementById("btn-face-y").addEventListener("click", () => handleButtonPress("y"));

  document.getElementById("btn-l1").addEventListener("click", () => handleButtonPress("l1"));
  document.getElementById("btn-r1").addEventListener("click", () => handleButtonPress("r1"));
  document.getElementById("btn-start").addEventListener("click", () => handleButtonPress("a"));
  document.getElementById("btn-select").addEventListener("click", () => handleButtonPress("b"));

  // Keyboard Navigation
  window.addEventListener("keydown", (e) => {
    if (["ArrowUp", "KeyW"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("up");
    } else if (["ArrowDown", "KeyS"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("down");
    } else if (["ArrowLeft", "KeyA"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("left");
    } else if (["ArrowRight", "KeyD"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("right");
    } else if (["Enter", "KeyJ"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("a");
    } else if (["Escape", "KeyK"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("b");
    } else if (["KeyU", "KeyX"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("x");
    } else if (["KeyI", "KeyY"].includes(e.code)) {
      e.preventDefault();
      handleButtonPress("y");
    } else if (e.code === "KeyQ") {
      handleButtonPress("l1");
    } else if (e.code === "KeyE") {
      handleButtonPress("r1");
    }
  });

  // REST API Integrations
  async function fetchReadiness() {
    try {
      const res = await fetch("/api/readiness");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      state.readinessData = data;
      renderReadiness(data);
    } catch (err) {
      logTerm("Không thể kết nối API readiness: " + err.message, "error");
    }
  }

  function renderReadiness(data) {
    const dev = data.device;
    const st = data.storage;
    const bat = dev.battery;
    const therm = dev.thermal;

    // Status bar updates
    sbBatteryText.textContent = `${bat.percent}%`;
    sbBatteryFill.style.width = `${bat.percent}%`;
    sbBatteryFill.style.backgroundColor = bat.percent <= 15 ? "var(--alert-red)" : bat.percent <= 25 ? "var(--warn-yellow)" : "var(--leaf-green)";
    sbBatteryBolt.className = `battery-bolt ${bat.is_charging ? "active" : ""}`;

    sbTemp.textContent = `${Math.round(therm.current_celsius)}°C`;
    sbTemp.style.color = therm.current_celsius >= 70 ? "var(--alert-red)" : therm.current_celsius >= 58 ? "var(--warn-yellow)" : "var(--leaf-green)";

    sbSd1.textContent = `SD1: ${st.primary.writable ? "OK" : "RO"}`;
    if (st.secondary && st.secondary.mounted) {
      sbSd2.textContent = `SD2: ${st.secondary.is_fat32 ? "FAT32" : "ERR"}`;
      sbSd2.style.color = st.secondary.is_fat32 ? "var(--leaf-green)" : "var(--alert-red)";
    } else {
      sbSd2.textContent = "SD2: N/A";
      sbSd2.style.color = "var(--text-muted)";
    }

    // Card 0: Device
    const devCard = document.getElementById("card-device-indicator");
    const devBadge = document.getElementById("badge-device-state");
    devCard.className = `card-status-bar status-${dev.status}`;
    devBadge.className = `card-status-badge badge-${dev.status}`;
    devBadge.textContent = dev.status === "green" ? "HOẠT ĐỘNG TỐT" : dev.status === "yellow" ? "ẤM / PIN GIẢM" : "CẢNH BÁO";
    document.getElementById("text-device-metric").textContent = `Pin ${bat.percent}% (${bat.is_charging ? "Đang sạc" : "Dùng pin"}) • ${therm.current_celsius}°C`;

    // Card 1: Storage
    const stCard = document.getElementById("card-storage-indicator");
    const stBadge = document.getElementById("badge-storage-state");
    stCard.className = `card-status-bar status-${st.status}`;
    stBadge.className = `card-status-badge badge-${st.status}`;
    stBadge.textContent = st.status === "green" ? "SD1 + SD2 OK" : st.status === "yellow" ? "DUNG LƯỢNG THẤP" : "LỖI ĐỊNH DẠNG";
    const secText = st.secondary && st.secondary.mounted ? `${Math.round(st.secondary.free_mb / 1024)} GB (SD2)` : "SD2: Chưa gắn";
    document.getElementById("text-storage-metric").textContent = `${Math.round(st.primary.free_mb / 1024)} GB (SD1) • ${secText}`;
    document.getElementById("text-storage-detail").textContent = st.secondary && !st.secondary.is_fat32 ?
      "CẢNH BÁO: SD2 không phải FAT32, Leaf không thể nhận game!" :
      "Hệ thống thẻ nhớ sẵn sàng hoạt động an toàn";

    // Card 2: SaveVault
    if (data.savevault) {
      document.getElementById("text-savevault-metric").textContent = `${data.savevault.total_snapshots} bản sao lưu an toàn`;
      document.getElementById("text-savevault-detail").textContent = data.savevault.latest ?
        `Mới nhất: ${data.savevault.latest.created_at}` : "Chưa tạo snapshot";
    }

    // Card 3: Library Doctor
    if (data.library) {
      const docCard = document.getElementById("card-doctor-indicator");
      const docBadge = document.getElementById("badge-doctor-state");
      const docStatus = data.library.status;
      docCard.className = `card-status-bar status-${docStatus}`;
      docBadge.className = `card-status-badge badge-${docStatus}`;
      docBadge.textContent = `${data.library.total_issues} VẤN ĐỀ`;
      document.getElementById("text-doctor-metric").textContent = `${data.library.critical} Lỗi nghiêm trọng • ${data.library.warning} Cảnh báo`;
    }
  }

  // Sub-screens load functions
  async function loadSnapshots() {
    const listEl = document.getElementById("snapshot-items-list");
    listEl.innerHTML = `<div class="term-line info">Đang tải danh sách snapshot SaveVault...</div>`;
    try {
      const res = await fetch("/api/snapshots");
      const data = await res.json();
      state.snapshots = data.snapshots || [];
      if (state.snapshots.length === 0) {
        listEl.innerHTML = `<div class="term-line info">Chưa có snapshot nào. Nhấn "Tạo Snapshot mới ngay" để bắt đầu!</div>`;
        return;
      }

      listEl.innerHTML = "";
      state.snapshots.forEach(s => {
        const item = document.createElement("div");
        item.className = "snapshot-item";
        item.innerHTML = `
          <div>
            <div class="snap-id">📦 ${s.id}</div>
            <div class="snap-meta">Thời gian: ${s.created_at} • ${s.total_files} files (${Math.round(s.total_size_bytes / 1024)} KB)</div>
          </div>
          <button class="btn-module-action btn-secondary" onclick="restoreSnapshot('${s.id}')">Khôi phục</button>
        `;
        listEl.appendChild(item);
      });
    } catch (e) {
      listEl.innerHTML = `<div class="term-line error">Lỗi tải snapshots: ${e.message}</div>`;
    }
  }

  async function loadDoctorAudit() {
    const listEl = document.getElementById("doctor-issues-list");
    listEl.innerHTML = `<div class="term-line info">Đang quét thư viện ROM, Artwork và Playlists...</div>`;
    try {
      const res = await fetch("/api/audit");
      const data = await res.json();
      state.auditData = data;
      document.getElementById("doctor-issues-header").textContent = `Tìm thấy ${data.total_issues} vấn đề cần lưu ý`;

      if (data.issues.length === 0) {
        listEl.innerHTML = `<div class="term-line success">🎉 Tuyệt vời! Không phát hiện lỗi cấu trúc, CUE, M3U hay artwork nào.</div>`;
        return;
      }

      listEl.innerHTML = "";
      data.issues.forEach(iss => {
        const card = document.createElement("div");
        card.className = `issue-card ${iss.severity}`;
        card.innerHTML = `
          <div class="issue-header">
            <span>[${iss.category}] ${iss.title}</span>
            <span class="card-status-badge badge-${iss.severity === 'critical' ? 'red' : iss.severity === 'warning' ? 'yellow' : 'blue'}">${iss.severity.toUpperCase()}</span>
          </div>
          <div class="issue-desc">${iss.description}</div>
          ${iss.guide ? `<div class="snap-meta" style="color:var(--warn-yellow); margin-top:4px;">💡 Khuyến nghị: ${iss.guide}</div>` : ""}
        `;
        listEl.appendChild(card);
      });
    } catch (e) {
      listEl.innerHTML = `<div class="term-line error">Lỗi quét thư viện: ${e.message}</div>`;
    }
  }

  async function loadHealthDiagnostics() {
    const listEl = document.getElementById("health-test-list");
    listEl.innerHTML = `<div class="term-line info">Đang chạy bộ chẩn đoán phần cứng và hệ thống...</div>`;
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      listEl.innerHTML = "";
      data.tests.forEach(t => {
        const row = document.createElement("div");
        row.className = "test-row";
        row.innerHTML = `
          <div>
            <strong>${t.test}</strong>
            <div class="snap-meta">${t.description}</div>
          </div>
          <span class="${t.passed ? 'test-status-ok' : 'badge-red'}">${t.passed ? '✓ ĐẠT' : '✗ KHÔNG ĐẠT'}</span>
        `;
        listEl.appendChild(row);
      });
    } catch (e) {
      listEl.innerHTML = `<div class="term-line error">Lỗi chẩn đoán: ${e.message}</div>`;
    }
  }

  // Actions
  async function triggerQuickBackup() {
    logTerm("Bắt đầu sao lưu nhanh SaveVault...", "info");
    showToast("Đang tạo bản snapshot SaveVault...");
    try {
      const res = await fetch("/api/backup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: "quick_sim" })
      });
      const data = await res.json();
      if (data.success) {
        logTerm(`Sao lưu thành công! ID: ${data.snapshot_id} (${data.files_count} files)`, "success");
        showToast(`SaveVault: Đã sao lưu an toàn ${data.files_count} files!`, true);
        fetchReadiness();
        if (state.currentView === "savevault") loadSnapshots();
      } else {
        logTerm(`Sao lưu thất bại: ${data.message}`, "error");
        showToast("Lỗi sao lưu: " + data.message, false);
      }
    } catch (e) {
      logTerm("Lỗi kết nối sao lưu: " + e.message, "error");
    }
  }

  async function triggerSafeFixes() {
    if (!state.auditData || !state.auditData.fixable_actions || state.auditData.fixable_actions.length === 0) {
      showToast("Không có hành động sửa chữa nào cần thực hiện.");
      return;
    }

    logTerm("Đang áp dụng các hành động sửa chữa an toàn (chuyển vào Quarantine)...", "info");
    showToast("Đang xử lý Fix Plan an toàn...");
    let fixed = 0;

    // /api/fix không còn nhận path/action tuỳ ý từ client (server tự sinh
    // fixable_actions và chỉ nhận action_index). Vì server re-audit trước khi
    // thực thi, danh sách có thể đổi sau mỗi lần sửa, nên mỗi vòng lặp phải lấy
    // lại /api/audit mới nhất rồi luôn sửa hành động đầu tiên cho tới khi hết.
    while (true) {
      const auditRes = await fetch("/api/audit");
      const auditData = await auditRes.json();
      state.auditData = auditData;
      if (!auditData.fixable_actions || auditData.fixable_actions.length === 0) break;

      const act = auditData.fixable_actions[0];
      try {
        const res = await fetch("/api/fix", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action_index: 0, confirm: true })
        });
        const r = await res.json();
        if (r.success) {
          logTerm(`Đã sửa: ${act.label} -> ${r.message}`, "success");
          fixed++;
        } else {
          logTerm(`Dừng sửa: ${act.label} -> ${r.message}`, "error");
          break;
        }
      } catch (e) {
        logTerm(`Lỗi sửa ${act.label}: ${e.message}`, "error");
        break;
      }
    }

    showToast(`Đã xử lý an toàn ${fixed} vấn đề!`);
    loadDoctorAudit();
    fetchReadiness();
  }

  function triggerSupportBundle() {
    logTerm("Đang xuất gói chẩn đoán Support Bundle ZIP đã lọc mật khẩu...", "info");
    showToast("Đang tải xuống Support Bundle...");
    window.location.href = "/api/bundle";
    logTerm("Đã tạo và gửi file loongmate_support.zip an toàn", "success");
  }

  // Interactive buttons in sub-screens
  document.getElementById("btn-create-snapshot").addEventListener("click", triggerQuickBackup);
  document.getElementById("btn-preview-restore").addEventListener("click", async () => {
    logTerm("Chạy xem trước khôi phục (Dry-Run Preview)...", "info");
    const res = await fetch("/api/restore/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    const data = await res.json();
    logTerm(`Xem trước khôi phục: Thêm mới: ${data.to_add.length}, Cập nhật: ${data.to_update.length}, Xung đột bảo toàn: ${data.conflicts.length}`, "warning");
    showToast(`Dry-run: ${data.total_changes} thay đổi được dự kiến`);
  });

  document.getElementById("btn-run-audit").addEventListener("click", loadDoctorAudit);
  document.getElementById("btn-apply-safe-fixes").addEventListener("click", triggerSafeFixes);
  document.getElementById("btn-run-self-tests").addEventListener("click", loadHealthDiagnostics);
  document.getElementById("btn-download-bundle").addEventListener("click", triggerSupportBundle);

  // Workbench Sim Controls
  const simBattery = document.getElementById("slider-sim-battery");
  const simCharging = document.getElementById("toggle-sim-charging");
  const simTemp = document.getElementById("slider-sim-temp");
  const simSd2 = document.getElementById("select-sim-sd2");

  async function updateSimHardware() {
    const payload = {
      battery_percent: parseInt(simBattery.value, 10),
      is_charging: simCharging.checked,
      temperature_c: parseFloat(simTemp.value),
      sd2_mounted: simSd2.value !== "none",
      sd2_filesystem: simSd2.value === "exfat" ? "exfat" : "vfat",
      sd2_writable: simSd2.value !== "ro"
    };

    document.getElementById("label-sim-battery").textContent = `${payload.battery_percent}%`;
    document.getElementById("label-sim-temp").textContent = `${payload.temperature_c}°C`;

    await fetch("/api/override_sim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    fetchReadiness();
  }

  simBattery.addEventListener("input", updateSimHardware);
  simCharging.addEventListener("change", updateSimHardware);
  simTemp.addEventListener("input", updateSimHardware);
  simSd2.addEventListener("change", () => {
    updateSimHardware();
    logTerm(`Mô phỏng khe thẻ SD2 chuyển sang: [${simSd2.options[simSd2.selectedIndex].text}]`, "warning");
  });

  // Workbench CLI shortcuts
  document.getElementById("wb-run-readiness").addEventListener("click", () => {
    logTerm("$ loongmate readiness", "info");
    fetchReadiness();
  });
  document.getElementById("wb-run-scan").addEventListener("click", () => {
    logTerm("$ loongmate scan", "info");
    switchView("doctor");
  });
  document.getElementById("wb-run-backup").addEventListener("click", () => {
    logTerm("$ loongmate backup", "info");
    triggerQuickBackup();
  });
  document.getElementById("wb-run-restore-preview").addEventListener("click", () => {
    logTerm("$ loongmate restore --dry-run", "info");
    switchView("savevault");
    document.getElementById("btn-preview-restore").click();
  });
  document.getElementById("wb-run-health").addEventListener("click", () => {
    logTerm("$ loongmate health", "info");
    switchView("health");
  });
  document.getElementById("wb-run-bundle").addEventListener("click", () => {
    logTerm("$ loongmate bundle", "info");
    triggerSupportBundle();
  });
  document.getElementById("btn-clear-term").addEventListener("click", () => {
    termBox.innerHTML = "";
  });

  // Initial load
  fetchReadiness();
});
