/*
 * System dashboard — polls /api/system/health and renders the health grid.
 * Standalone page (does not load main.js); uses a tiny credentialed fetch.
 */
(function () {
  "use strict";

  const COLORS = { healthy: "#00ff88", warning: "#ffcc00", critical: "#ff4455" };
  function colorFor(status) { return COLORS[status] || "#88aaff"; }

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
      <div style="font-size:24px;font-weight:700;color:${statusColor};">● ${String(health.status).toUpperCase()}</div>
      <p style="font-size:12px;color:#88a;">Last checked: ${new Date(health.timestamp).toLocaleTimeString()}</p>
      <p style="color:${dbColor};">Database: ${db.status || "?"}${db.error ? " — " + db.error : ""}</p>
      <p style="font-size:12px;color:#88a;">${db.agents_tracked != null ? db.agents_tracked + " agents tracked" : ""}</p>
    `;

    // ── Backups ──────────────────────────────────────────────────────────
    const bk = health.backups || {};
    document.getElementById("backup-status").innerHTML = `
      <p style="color:${colorFor(bk.status)};">${bk.message || bk.status || "unknown"}</p>
      <p style="font-size:12px;color:#88a;">Last: ${bk.last_backup || "Never"}</p>
    `;

    // ── Scheduled jobs ───────────────────────────────────────────────────
    const jobs = health.scheduled_jobs || {};
    const missing = (jobs.missing && jobs.missing.length)
      ? `<p style="font-size:12px;color:${COLORS.critical};">Missing: ${jobs.missing.join(", ")}</p>` : "";
    document.getElementById("scheduled-jobs").innerHTML = `
      <p style="color:${colorFor(jobs.status)};">${jobs.total_jobs != null ? jobs.total_jobs : 0} jobs scheduled</p>
      <p style="font-size:12px;color:#88a;">${jobs.message || ""}</p>
      ${missing}
    `;

    // ── Critical agents ──────────────────────────────────────────────────
    const agents = health.agents || {};
    const lines = [];
    (agents.critical || []).forEach(a => lines.push(`<p style="color:${COLORS.critical};">❌ ${a}</p>`));
    (agents.warnings || []).forEach(a => lines.push(`<p style="color:${COLORS.warning};">⚠ ${a}</p>`));
    document.getElementById("critical-alerts").innerHTML =
      lines.length ? lines.join("") : `<p style="color:${COLORS.healthy};">✓ All agents healthy</p>`;

    // ── External integrations ────────────────────────────────────────────
    const apis = (health.external_apis && health.external_apis.apis) || {};
    const apisHtml = Object.entries(apis).map(([api, status]) => {
      const color = status === "configured" ? COLORS.healthy : COLORS.warning;
      return `<p style="color:${color};">${api}: ${status}</p>`;
    }).join("");
    document.getElementById("external-apis").innerHTML = apisHtml || "<p>No integrations checked</p>";
  }

  function loadSystemHealth() {
    apiGet("/api/system/health")
      .then(renderHealthDashboard)
      .catch(e => {
        document.getElementById("system-health-container").innerHTML =
          `<p style="color:${COLORS.critical};">Error: ${e.message || e}</p>`;
      })
      .finally(() => setTimeout(loadSystemHealth, 30000));
  }

  loadSystemHealth();
})();
