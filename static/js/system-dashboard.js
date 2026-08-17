/*
 * System dashboard — polls the auth-gated /api/system/health/full (the public
 * /api/system/health is a stripped probe payload) and renders the health grid.
 * Standalone page (does not load main.js); uses a tiny credentialed fetch.
 */
(function () {
  "use strict";

  const COLORS = { healthy: "#00ff88", warning: "#ffcc00", critical: "#ff4455" };
  function colorFor(status) { return COLORS[status] || "#88aaff"; }

  // Server strings here include raw exception text — escape everything.
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function apiGet(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  function renderHealthDashboard(health) {
    // ── Overall + database ───────────────────────────────────────────────
    const statusColor = colorFor(health.status);
    const db = health.database || {};
    const dbColor = colorFor(db.status);
    document.getElementById("system-health-container").innerHTML = `
      <div style="font-size:24px;font-weight:700;color:${statusColor};">● ${esc(String(health.status).toUpperCase())}</div>
      <p style="font-size:12px;color:#88a;">Last checked: ${new Date(health.timestamp).toLocaleTimeString()}</p>
      <p style="color:${dbColor};">Database: ${esc(db.status || "?")}${db.error ? " — " + esc(db.error) : ""}</p>
      <p style="font-size:12px;color:#88a;">${db.agents_tracked != null ? esc(db.agents_tracked) + " agents tracked" : ""}</p>
    `;

    // ── Backups ──────────────────────────────────────────────────────────
    const bk = health.backups || {};
    document.getElementById("backup-status").innerHTML = `
      <p style="color:${colorFor(bk.status)};">${esc(bk.message || bk.status || "unknown")}</p>
      <p style="font-size:12px;color:#88a;">Last: ${esc(bk.last_backup || "Never")}</p>
    `;

    // ── Scheduled jobs ───────────────────────────────────────────────────
    const jobs = health.scheduled_jobs || {};
    const missing = (jobs.missing && jobs.missing.length)
      ? `<p style="font-size:12px;color:${COLORS.critical};">Missing: ${esc(jobs.missing.join(", "))}</p>` : "";
    document.getElementById("scheduled-jobs").innerHTML = `
      <p style="color:${colorFor(jobs.status)};">${Number(jobs.total_jobs) || 0} jobs scheduled</p>
      <p style="font-size:12px;color:#88a;">${esc(jobs.message || "")}</p>
      ${missing}
    `;

    // ── Critical agents ──────────────────────────────────────────────────
    const agents = health.agents || {};
    const lines = [];
    (agents.critical || []).forEach(a => lines.push(`<p style="color:${COLORS.critical};">❌ ${esc(a)}</p>`));
    (agents.warnings || []).forEach(a => lines.push(`<p style="color:${COLORS.warning};">⚠ ${esc(a)}</p>`));
    document.getElementById("critical-alerts").innerHTML =
      lines.length ? lines.join("") : `<p style="color:${COLORS.healthy};">✓ All agents healthy</p>`;

    // ── External integrations ────────────────────────────────────────────
    const apis = (health.external_apis && health.external_apis.apis) || {};
    const apisHtml = Object.entries(apis).map(([api, status]) => {
      const color = status === "configured" ? COLORS.healthy : COLORS.warning;
      return `<p style="color:${color};">${esc(api)}: ${esc(status)}</p>`;
    }).join("");
    document.getElementById("external-apis").innerHTML = apisHtml || "<p>No integrations checked</p>";
  }

  function loadSystemHealth() {
    apiGet("/api/system/health/full")
      .then(renderHealthDashboard)
      .catch(e => {
        document.getElementById("system-health-container").innerHTML =
          `<p style="color:${COLORS.critical};">Error: ${esc(e.message || e)}</p>`;
      })
      .finally(() => setTimeout(loadSystemHealth, 30000));
  }

  loadSystemHealth();
})();


/*
 * System clock — set a simulated "now" so logs (meals, workouts, cardio, water)
 * are backfilled to an earlier date/time until reset. POSTs to
 * /api/settings/simulated-time; the patched window.fetch (_csrf.html, included
 * via nav) adds the X-CSRF-Token header automatically.
 */
(function () {
  "use strict";

  const GREEN = "#00ff88", AMBER = "#ffcc00", DIM = "#88aaff";
  const container = document.getElementById("system-clock-container");
  if (!container) return;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  async function apiGet(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }
  async function apiPost(url, body) {
    const r = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  // Real wall clock, formatted in the app timezone (Europe/London) regardless of
  // the browser's own timezone. Ticks locally once a second.
  const REAL_FMT = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London", weekday: "short", hour: "2-digit",
    minute: "2-digit", second: "2-digit", hour12: true,
  });
  // Simulated instant: server sends an ISO string already offset to the app tz,
  // so format it without re-applying a timezone.
  const SIM_FMT = new Intl.DateTimeFormat("en-GB", {
    weekday: "long", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit", hour12: true,
  });

  let state = { is_simulated: false, simulated_time: null };
  let realTimer = null;

  // Value for <input type="datetime-local"> (local "YYYY-MM-DDTHH:MM") from an
  // ISO string, or the current minute when none is given.
  function inputValue(iso) {
    const d = iso ? new Date(iso) : new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
      `T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function render() {
    const sim = state.is_simulated;
    const statusColor = sim ? AMBER : GREEN;
    const statusText = sim
      ? `Simulated: ${esc(SIM_FMT.format(new Date(state.simulated_time)))}`
      : "Using real time (Europe/London)";
    container.innerHTML = `
      <p style="font-size:12px;color:${DIM};margin:0;">Real time (Europe/London)</p>
      <div id="clk-real" style="font-size:20px;font-weight:700;color:${GREEN};margin:2px 0 10px;">—</div>
      <p style="font-weight:600;color:${statusColor};margin:0 0 10px;">${sim ? "◉" : "●"} ${statusText}</p>
      <input type="datetime-local" id="clk-input" value="${inputValue(state.simulated_time)}"
        style="width:100%;box-sizing:border-box;background:#0a0f1a;color:#cfe;border:1px solid #244;
               border-radius:6px;padding:8px;font-family:inherit;margin-bottom:10px;">
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button id="clk-set" style="flex:1;min-width:110px;background:#123;color:${AMBER};
          border:1px solid ${AMBER};border-radius:6px;padding:8px;cursor:pointer;font-family:inherit;">Set Clock</button>
        <button id="clk-reset" style="flex:1;min-width:110px;background:#123;color:${GREEN};
          border:1px solid ${GREEN};border-radius:6px;padding:8px;cursor:pointer;font-family:inherit;">Reset to Now</button>
      </div>
      <p id="clk-msg" style="font-size:12px;color:${DIM};min-height:1em;margin:8px 0 0;"></p>
    `;

    document.getElementById("clk-set").onclick = onSet;
    document.getElementById("clk-reset").onclick = onReset;

    if (realTimer) clearInterval(realTimer);
    const tick = () => {
      const el = document.getElementById("clk-real");
      if (el) el.textContent = REAL_FMT.format(new Date());
    };
    tick();
    realTimer = setInterval(tick, 1000);
  }

  function msg(text, color) {
    const el = document.getElementById("clk-msg");
    if (el) { el.textContent = text; el.style.color = color || DIM; }
  }

  async function onSet() {
    const val = document.getElementById("clk-input").value;
    if (!val) { msg("Pick a date and time first.", AMBER); return; }
    msg("Setting…");
    try {
      const r = await apiPost("/api/settings/simulated-time", { dt: val });
      state = r;
      render();
      // Nudge the shared nav clock (nav.html) to re-read the override now.
      window.dispatchEvent(new CustomEvent("asfa:clock-changed"));
      msg("Clock set — new logs will backfill here.", AMBER);
    } catch (e) { msg("Failed: " + esc(e.message || e), "#ff4455"); }
  }

  async function onReset() {
    msg("Resetting…");
    try {
      const r = await apiPost("/api/settings/simulated-time", { dt: null });
      state = r;
      render();
      window.dispatchEvent(new CustomEvent("asfa:clock-changed"));
      msg("Back on real time.", GREEN);
    } catch (e) { msg("Failed: " + esc(e.message || e), "#ff4455"); }
  }

  apiGet("/api/settings/current-time")
    .then((r) => { state = r; render(); })
    .catch((e) => {
      container.innerHTML = `<p style="color:#ff4455;">Error: ${esc(e.message || e)}</p>`;
    });
})();


/*
 * Settings — Notifications & Alerts (Phase 1) + Privacy & Account (Phase 2).
 * Self-contained: its own credentialed fetch helpers + a shared toast. The
 * patched window.fetch (_csrf.html via nav) adds X-CSRF-Token to writes.
 */
(function () {
  "use strict";

  const CYAN = "#00d9ff", GREEN = "#00ff88", AMBER = "#ffcc00",
        RED = "#ff4455", DIM = "#88aaff";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  async function apiGet(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }
  async function apiSend(url, method, body) {
    const r = await fetch(url, {
      method,
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch { /* empty body */ }
    if (!r.ok) {
      const msg = (data && data.error) || `HTTP ${r.status}`;
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return data;
  }

  let toastTimer = null;
  function toast(text, color) {
    const el = document.getElementById("settings-toast");
    if (!el) return;
    el.textContent = text;
    el.style.borderColor = color || CYAN;
    el.style.color = "#e6f6ff";
    el.style.opacity = "1";
    el.style.transform = "translateX(-50%) translateY(0)";
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateX(-50%) translateY(20px)";
    }, 2800);
  }

  // Small reusable controls ---------------------------------------------------
  function row(label, controlHtml) {
    return `<div style="display:flex;align-items:center;justify-content:space-between;
      gap:12px;padding:7px 0;border-bottom:1px solid #14202e;">
      <span style="color:#cfe;font-size:13px;">${esc(label)}</span>
      <span>${controlHtml}</span></div>`;
  }
  function toggle(id, on) {
    return `<input type="checkbox" id="${id}" ${on ? "checked" : ""}
      style="width:18px;height:18px;accent-color:${CYAN};cursor:pointer;">`;
  }
  const inputStyle = `background:#0a0f1a;color:#cfe;border:1px solid #244;border-radius:6px;
    padding:6px 8px;font-family:inherit;font-size:13px;`;
  const btnStyle = (c) => `background:#123;color:${c};border:1px solid ${c};border-radius:6px;
    padding:8px 12px;cursor:pointer;font-family:inherit;font-size:13px;`;

  // ── Notifications & Alerts ──────────────────────────────────────────────────
  (function initNotifications() {
    const container = document.getElementById("notifications-container");
    if (!container) return;

    const ALERTS = [
      ["alert_low_steps", "Low steps (< 5,000)"],
      ["alert_missed_meal", "Missed meal"],
      ["alert_below_calorie_target", "Below calorie target"],
      ["alert_skipped_workout", "Skipped workout"],
      ["alert_water_intake", "Water intake"],
    ];

    function render(p) {
      const freq = (v) => `<option value="${v}" ${p.email_frequency === v ? "selected" : ""}>`;
      container.innerHTML = `
        ${row("Email frequency",
          `<select id="ntf-email-freq" style="${inputStyle}">
            ${freq("daily")}Daily</option>
            ${freq("weekly")}Weekly</option>
            ${freq("never")}Never</option>
          </select>`)}
        ${row("Quiet hours", toggle("ntf-quiet-enabled", p.quiet_hours_enabled))}
        <div id="ntf-quiet-times" style="display:${p.quiet_hours_enabled ? "flex" : "none"};
          gap:8px;align-items:center;padding:7px 0;border-bottom:1px solid #14202e;">
          <span style="color:${DIM};font-size:12px;">From</span>
          <input type="time" id="ntf-quiet-start" value="${esc(p.quiet_hours_start)}" style="${inputStyle}">
          <span style="color:${DIM};font-size:12px;">to</span>
          <input type="time" id="ntf-quiet-end" value="${esc(p.quiet_hours_end)}" style="${inputStyle}">
        </div>
        ${row("Telegram notifications", toggle("ntf-telegram", p.telegram_notifications))}
        ${row("Job alerts", toggle("ntf-job-alerts", p.job_alerts_enabled))}
        <p style="color:${DIM};font-size:12px;margin:12px 0 4px;">Alert types</p>
        ${ALERTS.map(([k, label]) => row(label, toggle("ntf-" + k, p[k]))).join("")}
        <button id="ntf-save" style="${btnStyle(CYAN)}margin-top:14px;width:100%;">Save Preferences</button>
      `;

      const quietChk = document.getElementById("ntf-quiet-enabled");
      quietChk.addEventListener("change", () => {
        document.getElementById("ntf-quiet-times").style.display =
          quietChk.checked ? "flex" : "none";
      });

      document.getElementById("ntf-save").addEventListener("click", async () => {
        const payload = {
          email_frequency: document.getElementById("ntf-email-freq").value,
          quiet_hours_enabled: document.getElementById("ntf-quiet-enabled").checked,
          quiet_hours_start: document.getElementById("ntf-quiet-start").value,
          quiet_hours_end: document.getElementById("ntf-quiet-end").value,
          telegram_notifications: document.getElementById("ntf-telegram").checked,
          job_alerts_enabled: document.getElementById("ntf-job-alerts").checked,
        };
        ALERTS.forEach(([k]) => { payload[k] = document.getElementById("ntf-" + k).checked; });
        try {
          const r = await apiSend("/api/settings/notifications", "POST", payload);
          if (r && r.preferences) render(r.preferences);
          toast("Preferences saved", GREEN);
        } catch (e) {
          toast("Could not save: " + esc(e.message || e), RED);
        }
      });
    }

    apiGet("/api/settings/notifications")
      .then((r) => render(r.preferences))
      .catch((e) => {
        container.innerHTML = `<p style="color:${RED};">Error: ${esc(e.message || e)}</p>`;
      });
  })();

  // ── Active Sessions ─────────────────────────────────────────────────────────
  (function initActiveSessions() {
    const container = document.getElementById("active-sessions-container");
    if (!container) return;

    const FMT = new Intl.DateTimeFormat("en-GB", {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: true,
    });
    function when(v) {
      if (!v) return "—";
      const d = new Date(String(v).replace(" ", "T"));
      return isNaN(d) ? esc(v) : FMT.format(d);
    }
    function shortUA(ua) {
      ua = String(ua || "");
      if (ua.length <= 60) return ua;
      return ua.slice(0, 57) + "…";
    }

    function render(sessions) {
      if (!sessions.length) {
        container.innerHTML = `<p style="color:${DIM};">No active sessions.</p>`;
        return;
      }
      container.innerHTML = sessions.map((s) => `
        <div style="padding:9px 0;border-bottom:1px solid #14202e;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <span style="color:#cfe;font-size:13px;font-weight:600;">${esc(s.ip)}</span>
            ${s.is_current
              ? `<span style="color:${GREEN};border:1px solid ${GREEN};border-radius:4px;
                   padding:2px 7px;font-size:11px;">This device</span>`
              : `<button data-revoke="${s.session_id}" style="${btnStyle(RED)}padding:4px 10px;">Log out</button>`}
          </div>
          <p style="color:${DIM};font-size:12px;margin:4px 0 0;">${esc(shortUA(s.user_agent))}</p>
          <p style="color:${DIM};font-size:11px;margin:2px 0 0;">
            Signed in ${when(s.created_at)} · Last active ${when(s.last_activity)}</p>
        </div>`).join("");

      container.querySelectorAll("[data-revoke]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          try {
            const r = await apiSend("/api/settings/active-sessions/" + btn.dataset.revoke, "DELETE");
            if (r && r.is_current) { window.location.href = "/login"; return; }
            toast("Session logged out", GREEN);
            load();
          } catch (e) {
            btn.disabled = false;
            toast("Could not log out session: " + esc(e.message || e), RED);
          }
        });
      });
    }

    function load() {
      apiGet("/api/settings/active-sessions")
        .then((r) => render(r.sessions || []))
        .catch((e) => {
          container.innerHTML = `<p style="color:${RED};">Error: ${esc(e.message || e)}</p>`;
        });
    }
    load();
  })();

  // ── Data Export ─────────────────────────────────────────────────────────────
  (function initDataExport() {
    const container = document.getElementById("data-export-container");
    if (!container) return;
    container.innerHTML = `
      <p style="color:${DIM};font-size:12px;margin:0 0 12px;">
        Download all logged gym, nutrition, steps, sleep and cardio data.</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button id="exp-csv" style="${btnStyle(CYAN)}flex:1;min-width:120px;">Export as CSV</button>
        <button id="exp-json" style="${btnStyle(GREEN)}flex:1;min-width:120px;">Export as JSON</button>
      </div>`;
    // GET download: a same-origin navigation carries the session cookie, and the
    // Content-Disposition header makes the browser save rather than navigate.
    document.getElementById("exp-csv").addEventListener("click", () => {
      window.location.href = "/api/settings/export-data?format=csv";
      toast("Preparing CSV export…", CYAN);
    });
    document.getElementById("exp-json").addEventListener("click", () => {
      window.location.href = "/api/settings/export-data?format=json";
      toast("Preparing JSON export…", CYAN);
    });
  })();

  // ── Password & Security ─────────────────────────────────────────────────────
  (function initPassword() {
    const container = document.getElementById("password-security-container");
    if (!container) return;
    const field = (id, ph) => `<input type="password" id="${id}" placeholder="${ph}"
      autocomplete="new-password" style="${inputStyle}width:100%;box-sizing:border-box;margin-bottom:8px;">`;
    container.innerHTML = `
      ${field("pw-old", "Current password")}
      ${field("pw-new", "New password (min 8 chars)")}
      ${field("pw-confirm", "Confirm new password")}
      <button id="pw-save" style="${btnStyle(CYAN)}width:100%;margin-top:4px;">Change Password</button>`;

    document.getElementById("pw-save").addEventListener("click", async () => {
      const old_password = document.getElementById("pw-old").value;
      const new_password = document.getElementById("pw-new").value;
      const confirm_password = document.getElementById("pw-confirm").value;
      if (!old_password || !new_password) { toast("Fill in every field", AMBER); return; }
      try {
        await apiSend("/api/settings/change-password", "POST",
          { old_password, new_password, confirm_password });
        ["pw-old", "pw-new", "pw-confirm"].forEach((id) => { document.getElementById(id).value = ""; });
        toast("Password changed", GREEN);
      } catch (e) {
        toast(esc(e.message || "Could not change password"), RED);
      }
    });
  })();
})();
