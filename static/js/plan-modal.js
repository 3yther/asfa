/* ── Plan Approval modal controller (Phase 5 UI) ────────────────────────────
 * Drives the #plan-modal overlay: fetches a decomposed plan, renders its
 * step timeline, wires approve / reject / execute, and streams execution
 * results by polling /api/plan/<id>/results.
 *
 * Backend contract (see app.py / services/planner.py):
 *   GET  /api/plan/<id>            → {plan_id, user_request, decomposition,
 *                                     status, reasoning, ...}
 *   POST /api/plan/<id>/approve    → {ok, status:"approved"}
 *   POST /api/plan/<id>/reject     → {ok, status:"rejected"}
 *   POST /api/plan/<id>/execute    → {ok, results:[...]}  (synchronous stub)
 *   GET  /api/plan/<id>/results    → [{step, agent, skill, input, output,
 *                                      status, error, duration, executed_at}]
 * Step status from results: "success" → complete, "failure" → failed.
 */
(function () {
  "use strict";

  // Local HTTP helpers (the command page also exposes apiGet/apiPost from
  // main.js, but plan-modal.js loads on the standalone /plans page too).
  async function getJSON(url) {
    const r = await fetch(url, { credentials: "include" });
    return { ok: r.ok, status: r.status, body: await r.json().catch(() => null) };
  }
  async function postJSON(url) {
    const r = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    return { ok: r.ok, status: r.status, body: await r.json().catch(() => null) };
  }

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Pretty-print + lightly syntax-tint a JSON value.
  function highlightJSON(value) {
    let json;
    try { json = JSON.stringify(value, null, 2); } catch (e) { json = String(value); }
    if (json === undefined) json = "null";
    return escHtml(json).replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
      (m) => {
        let cls = "jn"; // number
        if (/^"/.test(m)) cls = /:$/.test(m) ? "jk" : "js";       // key vs string
        else if (/true|false|null/.test(m)) cls = "jb";            // bool/null
        return '<span class="' + cls + '">' + m + "</span>";
      }
    );
  }

  const STEP_STATE = { success: "complete", failure: "failed", error: "failed" };

  class PlanModal {
    constructor() {
      this.currentPlanId = null;
      this.plan = null;
      this.autoRefresh = null;
      this.el = {};
      this._bind();
    }

    _bind() {
      this.el.modal = document.getElementById("plan-modal");
      if (!this.el.modal) return;
      this.el.close = document.getElementById("plan-modal-close");
      this.el.badge = document.getElementById("plan-status-badge");
      this.el.request = document.getElementById("plan-request");
      this.el.reasoning = document.getElementById("plan-reasoning");
      this.el.timeline = document.getElementById("plan-timeline");
      this.el.progress = document.getElementById("plan-progress-bar");
      this.el.approve = document.getElementById("plan-approve-btn");
      this.el.reject = document.getElementById("plan-reject-btn");
      this.el.execute = document.getElementById("plan-execute-btn");
      this.el.feedback = document.getElementById("plan-feedback");

      this.el.close.addEventListener("click", () => this.close());
      this.el.modal.addEventListener("click", (e) => {
        if (e.target === this.el.modal) this.close();
      });
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !this.el.modal.classList.contains("hidden")) this.close();
      });
      this.el.approve.addEventListener("click", () => this.approvePlan());
      this.el.reject.addEventListener("click", () => this.rejectPlan());
      this.el.execute.addEventListener("click", () => this.executePlan());
    }

    _feedback(msg, kind) {
      if (!this.el.feedback) return;
      this.el.feedback.textContent = msg || "";
      this.el.feedback.className = "plan-feedback" + (kind ? " " + kind : "");
    }

    async openPlan(planId) {
      if (!this.el.modal) return;
      this.currentPlanId = planId;
      this._feedback("");
      this.el.modal.classList.remove("hidden");
      this.el.modal.setAttribute("aria-hidden", "false");
      this.el.timeline.innerHTML = '<div class="muted mono">// LOADING PLAN…</div>';

      const res = await getJSON("/api/plan/" + encodeURIComponent(planId));
      if (res.status === 404) {
        this.el.timeline.innerHTML = '<div class="plan-step-error">Plan not found.</div>';
        this._setStatus("failed");
        return;
      }
      if (!res.ok || !res.body) {
        this.el.timeline.innerHTML = '<div class="plan-step-error">Failed to load plan.</div>';
        return;
      }
      this.plan = res.body;
      this.el.request.textContent = this.plan.user_request || "—";
      this.el.reasoning.textContent = this.plan.reasoning || "";
      this._renderTimeline(this.plan.decomposition || []);
      this._setStatus(this.plan.status || "pending_approval");

      // If execution already happened (or is mid-flight), fold in results.
      const st = this.plan.status;
      if (st === "executing" || st === "complete" || st === "failed") {
        if (st === "executing") this._poll();
        else await this._applyResults();
      }
    }

    _renderTimeline(steps) {
      if (!steps.length) {
        this.el.timeline.innerHTML = '<div class="muted mono">// NO STEPS</div>';
        return;
      }
      this.el.timeline.innerHTML = steps.map((s, i) => {
        const idx = (s.step != null) ? s.step : i;
        const deps = Array.isArray(s.depends_on) && s.depends_on.length
          ? s.depends_on.map((d) => "Step " + d).join(", ") : "none";
        return (
          '<div class="plan-step pending" id="plan-step-' + idx + '">' +
            '<span class="plan-step-icon"></span>' +
            '<div class="plan-step-header">' +
              '<span class="plan-step-num">[Step ' + idx + ']</span>' +
              '<span>' + escHtml(s.agent || "?") + "." + escHtml(s.skill || "?") + "</span>" +
              '<span class="plan-step-state" data-state>PENDING</span>' +
            "</div>" +
            '<div class="plan-step-deps">└─ Depends on: ' + escHtml(deps) + "</div>" +
            '<div class="plan-code-label">Input</div>' +
            '<pre class="plan-code input">' + highlightJSON(s.params || {}) + "</pre>" +
            '<div class="plan-step-body" data-body></div>' +
          "</div>"
        );
      }).join("");
    }

    _setStatus(status) {
      const badge = this.el.badge;
      badge.textContent = status;
      badge.className = "plan-status-badge status-" +
        (status === "pending_approval" ? "pending" : status);

      const isPending = status === "pending_approval";
      const isApproved = status === "approved";
      const isDone = status === "complete" || status === "failed" || status === "rejected";
      const isExecuting = status === "executing";

      this.el.approve.disabled = !isPending;
      this.el.reject.disabled = !isPending && !isApproved;
      this.el.approve.classList.toggle("hidden", isApproved || isExecuting || isDone);
      this.el.reject.classList.toggle("hidden", isExecuting || status === "complete" || status === "rejected");

      // Execute appears once approved; becomes Retry after a failure.
      const showExec = isApproved || status === "failed";
      this.el.execute.classList.toggle("hidden", !showExec);
      this.el.execute.disabled = isExecuting;
      this.el.execute.textContent = status === "failed" ? "↻ RETRY" : "▶ EXECUTE";
    }

    async approvePlan() {
      if (!this.currentPlanId) return;
      this._feedback("Approving…");
      const res = await postJSON("/api/plan/" + this.currentPlanId + "/approve");
      if (res.ok && res.body && res.body.ok) {
        this._setStatus("approved");
        this._feedback("Approved — ready to execute.", "ok");
      } else {
        this._feedback("Approve failed.", "error");
      }
    }

    async rejectPlan() {
      if (!this.currentPlanId) return;
      this._feedback("Rejecting…");
      const res = await postJSON("/api/plan/" + this.currentPlanId + "/reject");
      if (res.ok && res.body && res.body.ok) {
        this._setStatus("rejected");
        this._feedback("Plan rejected.", "error");
        setTimeout(() => this.close(), 900);
      } else {
        this._feedback("Reject failed.", "error");
      }
    }

    async executePlan() {
      if (!this.currentPlanId) return;
      // Retry path: a failed plan must be re-approved before it can run again.
      if (this.el.execute.textContent.indexOf("RETRY") !== -1) {
        await postJSON("/api/plan/" + this.currentPlanId + "/approve");
      }
      this._setStatus("executing");
      this._feedback("Executing…");
      const res = await postJSON("/api/plan/" + this.currentPlanId + "/execute");
      if (!res.ok || !res.body || !res.body.ok) {
        this._feedback((res.body && res.body.error) || "Execution failed to start.", "error");
        this._setStatus("approved");
        return;
      }
      // Stream results (execute is synchronous today, but poll so this keeps
      // working when execution goes async).
      this._poll();
    }

    _poll() {
      if (this.autoRefresh) clearInterval(this.autoRefresh);
      this.autoRefresh = setInterval(() => this._applyResults(), 500);
      this._applyResults();
    }

    async _applyResults() {
      if (!this.currentPlanId) return;
      const [resR, resP] = await Promise.all([
        getJSON("/api/plan/" + this.currentPlanId + "/results"),
        getJSON("/api/plan/" + this.currentPlanId),
      ]);
      const results = (resR.ok && Array.isArray(resR.body)) ? resR.body : [];
      const total = (this.plan && this.plan.decomposition || []).length || results.length || 1;

      let done = 0, failed = false;
      results.forEach((r) => {
        const stepEl = document.getElementById("plan-step-" + r.step);
        if (!stepEl) return;
        const state = STEP_STATE[r.status] || "executing";
        stepEl.className = "plan-step " + state;
        const stateLabel = stepEl.querySelector("[data-state]");
        if (stateLabel) stateLabel.textContent = (state === "complete" ? "✓ COMPLETE" : state === "failed" ? "✗ FAILED" : "EXECUTING");
        const body = stepEl.querySelector("[data-body]");
        if (body) {
          let html = "";
          if (r.output != null) {
            html += '<div class="plan-code-label">Output</div>' +
                    '<pre class="plan-code output">' + highlightJSON(r.output) + "</pre>";
          }
          if (r.error) html += '<div class="plan-step-error">⚠ ' + escHtml(r.error) + "</div>";
          if (r.duration != null) html += '<div class="plan-step-duration">completed in ' + escHtml(r.duration) + "ms</div>";
          body.innerHTML = html;
        }
        if (state === "complete") done++;
        if (state === "failed") { failed = true; done++; }
      });

      if (this.el.progress) this.el.progress.style.width = Math.min(100, (done / total) * 100) + "%";

      const planStatus = (resP.ok && resP.body) ? resP.body.status : null;
      const finished = planStatus === "complete" || planStatus === "failed" ||
        (done >= total && total > 0);
      if (finished) {
        if (this.autoRefresh) { clearInterval(this.autoRefresh); this.autoRefresh = null; }
        const final = failed ? "failed" : (planStatus || "complete");
        this._setStatus(final);
        this._feedback(failed ? "Execution finished with errors." : "Execution complete.", failed ? "error" : "ok");
      }
    }

    close() {
      if (this.autoRefresh) { clearInterval(this.autoRefresh); this.autoRefresh = null; }
      if (this.el.modal) {
        this.el.modal.classList.add("hidden");
        this.el.modal.setAttribute("aria-hidden", "true");
      }
      this.currentPlanId = null;
    }
  }

  function init() {
    if (!document.getElementById("plan-modal")) return;
    window.planModal = new PlanModal();

    // "Plan a Task" card on the command screen.
    const btn = document.getElementById("decompose-btn");
    const input = document.getElementById("request-input");
    const statusEl = document.getElementById("decompose-status");
    if (btn && input) {
      const run = async () => {
        const text = input.value.trim();
        if (!text) { if (statusEl) { statusEl.textContent = "Enter a request first."; statusEl.classList.add("error"); } return; }
        if (statusEl) { statusEl.textContent = "Decomposing with Claude…"; statusEl.classList.remove("error"); }
        btn.disabled = true;
        try {
          const r = await fetch("/api/plan/decompose", {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ request: text }),
          });
          const data = await r.json().catch(() => null);
          if (r.ok && data && data.ok) {
            if (statusEl) statusEl.textContent = "";
            input.value = "";
            window.planModal.openPlan(data.plan_id);
          } else {
            if (statusEl) { statusEl.textContent = (data && data.error) || "Decomposition failed."; statusEl.classList.add("error"); }
          }
        } catch (e) {
          if (statusEl) { statusEl.textContent = "Request error: " + e.message; statusEl.classList.add("error"); }
        } finally {
          btn.disabled = false;
        }
      };
      btn.addEventListener("click", run);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } });
    }

    // Deep-link: /plans/<id> sets data-open-plan on <body>.
    const openId = document.body.getAttribute("data-open-plan");
    if (openId) window.planModal.openPlan(openId);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
