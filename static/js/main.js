/* ASFA command centre — frontend brain */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

async function api(path, opts = {}) {
  if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 2600);
}

function countUp(el, target, { prefix = "", decimals = 0, ms = 1100 } = {}) {
  const start = performance.now();
  function tick(now) {
    const p = Math.min(1, (now - start) / ms);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = prefix + (target * eased).toFixed(decimals);
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function typewriter(el, text, speed = 12) {
  el.textContent = "";
  const caret = document.createElement("span");
  caret.className = "typing-caret";
  caret.innerHTML = "&nbsp;";
  el.appendChild(caret);
  let i = 0;
  return new Promise((resolve) => {
    (function step() {
      const chunk = text.slice(i, i + 2);
      i += 2;
      caret.before(document.createTextNode(chunk));
      el.closest(".chat-log")?.scrollTo(0, 1e6);
      if (i < text.length) setTimeout(step, speed);
      else { caret.remove(); resolve(); }
    })();
  });
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ── Clock & particles ─────────────────────────────────────────────── */
setInterval(() => {
  $("#clock").textContent = new Date().toLocaleTimeString("en-GB");
}, 1000);

(function particles() {
  const box = $("#particles");
  for (let i = 0; i < 26; i++) {
    const p = document.createElement("div");
    p.className = "particle";
    p.style.left = Math.random() * 100 + "vw";
    p.style.animationDuration = 14 + Math.random() * 18 + "s";
    p.style.animationDelay = -Math.random() * 20 + "s";
    p.style.background = Math.random() > 0.5 ? "#06B6D4" : "#7C3AED";
    box.appendChild(p);
  }
})();

/* stagger card entrance */
$$(".card").forEach((c, i) => (c.style.animationDelay = i * 100 + "ms"));

/* ── Tabs (mobile bottom nav) ──────────────────────────────────────── */
document.body.dataset.tab = "home";
$$(".nav-btn").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".nav-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.body.dataset.tab = btn.dataset.tab;
    window.scrollTo({ top: 0, behavior: "smooth" });
  })
);

/* ── Chart defaults ────────────────────────────────────────────────── */
Chart.defaults.color = "#8B949E";
Chart.defaults.font.family = "'JetBrains Mono', monospace";
Chart.defaults.font.size = 10;
Chart.defaults.borderColor = "rgba(139,148,158,.12)";
Chart.defaults.animation.duration = 1300;
const charts = {};
function makeChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($("#" + id), config);
}

/* ── Briefing ──────────────────────────────────────────────────────── */
let briefingText = "";
async function loadBriefing(refresh = false) {
  $("#briefing-scan").classList.remove("hidden");
  $("#briefing-body").innerHTML = '<span class="muted">Generating briefing…</span>';
  try {
    const b = await api("/api/briefing" + (refresh ? "?refresh=1" : ""));
    briefingText = b.text || b.content;
    $("#briefing-body").textContent = b.content;
  } catch (e) {
    $("#briefing-body").innerHTML = `<span class="muted">Briefing unavailable: ${esc(e.message)}</span>`;
  } finally {
    $("#briefing-scan").classList.add("hidden");
  }
}
$("#briefing-refresh").addEventListener("click", () => loadBriefing(true));
$("#briefing-play").addEventListener("click", () => speak(briefingText || "No briefing available yet."));

/* ── Score ring ────────────────────────────────────────────────────── */
async function loadScore() {
  try {
    const s = await api("/api/score");
    const arc = $("#score-arc");
    const C = 2 * Math.PI * 52;
    arc.style.strokeDashoffset = C - (C * s.score) / 100;
    arc.style.stroke = s.score < 40 ? "#F0506E" : s.score <= 70 ? "#F5C542" : "#2EE6A8";
    $("#score-num").style.color = arc.style.stroke;
    countUp($("#score-num"), s.score);
    const hist = s.history || [];
    makeChart("score-chart", {
      type: "line",
      data: {
        labels: hist.map((h) => h.date.slice(5)),
        datasets: [{ data: hist.map((h) => h.score), borderColor: "#06B6D4",
          backgroundColor: "rgba(6,182,212,.12)", fill: true, tension: 0.4, pointRadius: 2 }],
      },
      options: { plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100 } } },
    });
  } catch (e) { console.warn("score", e); }
}

/* ── Habits ────────────────────────────────────────────────────────── */
async function loadHabits() {
  try {
    const h = await api("/api/habits");
    const water = h.today.water_ml || 0;
    countUp($("#water-num"), water);
    $("#water-bar").style.width = Math.min(100, (water / 2000) * 100) + "%";
    const s = h.water_streak;
    const fireSize = Math.min(2, 1 + s * 0.08);
    $("#water-streak").innerHTML = s > 0
      ? `<span class="fire" style="font-size:${fireSize}em">🔥</span> <span class="mono">${s}d</span>` : "";
    const days = [...h.history].reverse();
    makeChart("sleep-chart", {
      type: "bar",
      data: {
        labels: days.map((d) => d.date.slice(5)),
        datasets: [{ data: days.map((d) => d.sleep_hours || 0),
          backgroundColor: days.map((d) => (d.sleep_hours >= 7 ? "rgba(46,230,168,.7)" : "rgba(124,58,237,.6)")),
          borderRadius: 6 }],
      },
      options: { plugins: { legend: { display: false } }, scales: { y: { suggestedMax: 9 } } },
    });
  } catch (e) { console.warn("habits", e); }
}

$$(".water-add").forEach((b) =>
  b.addEventListener("click", async () => {
    await api("/api/habits/water", { method: "POST", body: { ml: +b.dataset.ml } });
    toast(`💧 +${b.dataset.ml}ml logged`);
    loadHabits(); loadScore();
  })
);

$("#sleep-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const hours = parseFloat($("#sleep-hours").value);
  if (!hours) return;
  await api("/api/habits/sleep", { method: "POST", body: { hours } });
  toast(`😴 ${hours}h sleep logged`);
  e.target.reset(); loadHabits(); loadScore();
});

/* ── Gym ───────────────────────────────────────────────────────────── */
async function loadGym() {
  try {
    const g = await api("/api/gym");
    $("#pbs-body").innerHTML = g.pbs.length
      ? g.pbs.map((p) => `<div class="list-item"><span>${esc(p.exercise)}</span>
          <span class="mono glow" style="margin-left:auto">${p.best_weight}kg × ${p.best_reps}</span></div>`).join("")
      : '<span class="muted">No PBs yet — log a workout</span>';
    const warn = $("#neglected-warn");
    if (g.neglected.length) {
      warn.textContent = `⚠️ Not trained in 7+ days: ${g.neglected.join(", ")}`;
      warn.classList.remove("hidden");
    } else warn.classList.add("hidden");
    const groups = ["chest", "back", "legs", "shoulders", "arms", "core"];
    makeChart("balance-chart", {
      type: "radar",
      data: { labels: groups,
        datasets: [{ data: groups.map((x) => g.balance[x] || 0), borderColor: "#7C3AED",
          backgroundColor: "rgba(124,58,237,.22)", pointBackgroundColor: "#06B6D4" }] },
      options: { plugins: { legend: { display: false } },
        scales: { r: { ticks: { display: false }, grid: { color: "rgba(139,148,158,.15)" },
          angleLines: { color: "rgba(139,148,158,.15)" }, pointLabels: { color: "#8B949E" } } } },
    });
    makeChart("weight-chart", {
      type: "line",
      data: { labels: g.body_weight.map((w) => w.date.slice(5)),
        datasets: [{ data: g.body_weight.map((w) => w.weight_kg), borderColor: "#2EE6A8",
          backgroundColor: "rgba(46,230,168,.1)", fill: true, tension: 0.35, pointRadius: 2 }] },
      options: { plugins: { legend: { display: false } } },
    });
  } catch (e) { console.warn("gym", e); }
}

$("#workout-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await api("/api/gym/workout", { method: "POST", body: {
    exercise: $("#wo-exercise").value, weight_kg: $("#wo-weight").value || 0,
    reps: $("#wo-reps").value || 0, muscle_group: $("#wo-muscle").value } });
  if (r.is_pb) {
    const pb = $("#pb-celebrate");
    pb.classList.remove("hidden");
    setTimeout(() => pb.classList.add("hidden"), 3200);
  }
  toast(r.is_pb ? "🏆 NEW PB!" : "🏋️ Logged");
  e.target.reset(); loadGym(); loadScore();
});

$("#weight-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const kg = parseFloat($("#bw-kg").value);
  if (!kg) return;
  await api("/api/gym/weight", { method: "POST", body: { weight_kg: kg } });
  toast(`⚖️ ${kg}kg logged`);
  e.target.reset(); loadGym();
});

/* ── Money ─────────────────────────────────────────────────────────── */
async function loadMoney() {
  try {
    const m = await api("/api/money");
    countUp($("#money-week"), m.total, { prefix: "£", decimals: 2 });
    countUp($("#money-month"), m.monthly_total, { prefix: "£", decimals: 2 });
    const cats = Object.keys(m.by_category);
    makeChart("money-chart", {
      type: "doughnut",
      data: { labels: cats,
        datasets: [{ data: cats.map((c) => m.by_category[c]),
          backgroundColor: ["#7C3AED", "#06B6D4", "#2EE6A8", "#F5C542", "#F0506E", "#8B949E"],
          borderColor: "#050507", borderWidth: 3 }] },
      options: { plugins: { legend: { position: "right" } }, cutout: "68%" },
    });
    $("#spend-recent").innerHTML = m.spending.slice(0, 6).map((s) =>
      `<div class="list-item"><span class="time">${s.date.slice(5)}</span>
       <span>${esc(s.note || s.category)}</span>
       <span class="mono" style="margin-left:auto">£${s.amount.toFixed(2)}</span></div>`).join("");
  } catch (e) { console.warn("money", e); }
}

$("#spend-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/money", { method: "POST", body: {
    amount: $("#sp-amount").value, category: $("#sp-category").value, note: $("#sp-note").value } });
  toast("💸 Spend logged");
  e.target.reset(); loadMoney(); loadScore();
});

/* ── Bots ──────────────────────────────────────────────────────────── */
function botRow(b) {
  if (!b.online) return `<div class="bot-row"><div class="bot-name">${esc(b.bot_name)}</div>
    <div class="offline">⚪ offline ${esc(b.error || "")}</div></div>`;
  const equity = b.equity ?? b.portfolio_value ?? "—";
  const pnl = b.pnl ?? b.daily_pnl ?? b.total_pnl ?? 0;
  const pnlNum = parseFloat(pnl) || 0;
  let positions = b.positions ?? b.open_positions ?? [];
  const posCount = Array.isArray(positions) ? positions.length : positions;
  return `<div class="bot-row"><div class="bot-name">🟢 ${esc(b.bot_name)}</div>
    <div class="bot-stats">
      <div class="bot-stat"><div class="label">Equity</div><div class="val mono glow">$${esc(equity)}</div></div>
      <div class="bot-stat"><div class="label">P&amp;L</div>
        <div class="val mono ${pnlNum >= 0 ? "pos" : "neg"}">${pnlNum >= 0 ? "+" : ""}${esc(pnl)}</div></div>
      <div class="bot-stat"><div class="label">Positions</div><div class="val mono">${esc(posCount)}</div></div>
    </div></div>`;
}

async function loadBots() {
  try {
    const b = await api("/api/bots");
    $("#bots-body").innerHTML = botRow(b.scanner);
  } catch (e) {
    $("#bots-body").innerHTML = '<span class="muted">Bots unreachable</span>';
  }
}
$("#bots-refresh").addEventListener("click", loadBots);

/* ── Weather, news ─────────────────────────────────────────────────── */
async function loadWeather() {
  try {
    const w = await api("/api/weather");
    $("#weather-chip").textContent = `${w.current.temp}°C · ${w.current.description} · London`;
  } catch { /* chip stays dash */ }
}

async function loadNews() {
  try {
    const n = await api("/api/news");
    const items = [...n.top.slice(0, 4), ...n.finance.slice(0, 3)];
    $("#news-body").innerHTML = items.length
      ? items.map((a) => `<div class="list-item"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></div>`).join("")
      : '<span class="muted">Set NEWS_API_KEY for headlines</span>';
  } catch { /* leave placeholder */ }
}

/* ── Calendar & inbox ──────────────────────────────────────────────── */
const fmtTime = (iso) => (iso && iso.includes("T")) ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "all day";

async function loadCalendar() {
  try {
    const c = await api("/api/calendar");
    if (!c.connected) { $("#calendar-body").innerHTML = '<span class="muted">Connect Google to see events</span>'; return; }
    const block = (label, evs) => evs.filter((e) => !e.error).length
      ? `<div class="label" style="margin:6px 0 2px">${label}</div>` + evs.filter((e) => !e.error).map((e) =>
        `<div class="list-item"><span class="time">${fmtTime(e.start)}</span><span>${esc(e.title)}</span></div>`).join("")
      : `<div class="label" style="margin:6px 0 2px">${label}</div><div class="muted small">Nothing scheduled</div>`;
    $("#calendar-body").innerHTML = block("Today", c.today) + block("Tomorrow", c.tomorrow);
  } catch (e) { console.warn("calendar", e); }
}

$("#event-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const start = new Date($("#ev-start").value);
  const end = new Date(start.getTime() + 3600e3);
  const isoLocal = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60e3).toISOString().slice(0, 19);
  try {
    await api("/api/calendar", { method: "POST", body: {
      title: $("#ev-title").value, start: isoLocal(start), end: isoLocal(end) } });
    toast("📅 Event added"); e.target.reset(); loadCalendar();
  } catch (err) { toast("Calendar error: " + err.message); }
});

async function loadInbox() {
  try {
    const m = await api("/api/emails");
    if (!m.connected) { $("#inbox-body").innerHTML = '<span class="muted">Connect Google to see emails</span>'; return; }
    const emails = m.emails.filter((e) => !e.error);
    $("#inbox-body").innerHTML = emails.length
      ? emails.slice(0, 6).map((e) =>
        `<div class="list-item"><div><strong class="small">${esc((e.from || "").replace(/<.*>/, "").trim())}</strong><br>
         <span class="small muted">${esc(e.summary || e.subject)}</span></div></div>`).join("")
      : '<span class="muted">Inbox zero — nothing unread 🎉</span>';
    $("#suggested-events").innerHTML = (m.suggested_events || []).map((s) =>
      `<div class="suggest-row"><span>📅 ${esc(s.title)} — ${esc(s.start).replace("T", " ").slice(0, 16)}</span>
       <button class="btn btn-grad btn-suggest" data-ev='${esc(JSON.stringify(s))}'>Add</button></div>`).join("");
    $$(".btn-suggest").forEach((b) => b.addEventListener("click", async () => {
      const s = JSON.parse(b.dataset.ev);
      try {
        await api("/api/calendar", { method: "POST", body: { title: s.title, start: s.start, end: s.end } });
        toast("📅 Added to calendar"); b.disabled = true; b.textContent = "✓"; loadCalendar();
      } catch (err) { toast("Error: " + err.message); }
    }));
  } catch (e) { console.warn("inbox", e); }
}

/* ── Goals & review ────────────────────────────────────────────────── */
async function loadGoals() {
  try {
    const goals = await api("/api/goals");
    $("#goals-body").innerHTML = goals.length
      ? goals.map((g) => `<div class="goal-row">
          <div class="goal-top"><span>${esc(g.title)}</span><span class="mono">${g.progress}%</span></div>
          <div class="progress"><div class="progress-fill" style="width:${g.progress}%"></div></div>
          <input type="range" min="0" max="100" value="${g.progress}" data-id="${g.id}" class="goal-slider">
        </div>`).join("")
      : '<span class="muted">No goals yet</span>';
    $$(".goal-slider").forEach((s) => s.addEventListener("change", async () => {
      await api("/api/goals/" + s.dataset.id, { method: "PATCH", body: { progress: +s.value } });
      loadGoals();
    }));
  } catch (e) { console.warn("goals", e); }
}

$("#goal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/goals", { method: "POST", body: { title: $("#goal-title").value } });
  e.target.reset(); loadGoals();
});

$("#review-btn").addEventListener("click", async () => {
  const body = $("#review-body");
  body.classList.remove("hidden");
  body.textContent = "Generating weekly review…";
  const r = await api("/api/review");
  body.textContent = r.content;
});

/* ── Reflection ────────────────────────────────────────────────────── */
$("#refl-score").addEventListener("input", (e) => ($("#refl-score-label").textContent = e.target.value));
$("#reflection-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/reflection", { method: "POST", body: {
    score: +$("#refl-score").value, content: $("#refl-text").value } });
  toast("📝 Reflection saved");
  $("#refl-text").value = "";
  loadReflections();
});
async function loadReflections() {
  try {
    const rs = await api("/api/reflection");
    $("#refl-recent").innerHTML = rs.slice(0, 3).map((r) =>
      `<div class="list-item"><span class="time">${r.date.slice(5)}</span>
       <span>${r.score}/10 — ${esc((r.content || "").slice(0, 60))}</span></div>`).join("");
  } catch (e) { console.warn("refl", e); }
}

/* ── Ideas ─────────────────────────────────────────────────────────── */
async function loadIdeas() {
  try {
    const ideas = await api("/api/ideas");
    $("#ideas-body").innerHTML = ideas.slice(0, 8).map((i) =>
      `<div class="list-item">💡 ${esc(i.content)}</div>`).join("") || '<span class="muted">Capture your first idea</span>';
  } catch (e) { console.warn("ideas", e); }
}
$("#idea-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/ideas", { method: "POST", body: { content: $("#idea-text").value } });
  e.target.reset(); loadIdeas();
});

/* ── Notifications ─────────────────────────────────────────────────── */
async function loadNotifications() {
  try {
    const n = await api("/api/notifications");
    const badge = $("#bell-badge");
    badge.textContent = n.unread;
    badge.classList.toggle("hidden", n.unread === 0);
    $("#notif-list").innerHTML = n.notifications.length
      ? n.notifications.map((x) =>
        `<div class="notif-item ${x.is_read ? "" : "unread"}">${esc(x.message)}
         <span class="time">${esc(x.created_at)}</span></div>`).join("")
      : '<div class="muted">No notifications yet</div>';
  } catch (e) { console.warn("notif", e); }
}
$("#bell-btn").addEventListener("click", () => $("#notif-panel").classList.toggle("hidden"));
$("#notif-clear").addEventListener("click", async () => {
  await api("/api/notifications/read", { method: "POST" });
  loadNotifications();
});

/* ── Chat + voice ──────────────────────────────────────────────────── */
const orb = $("#orb");
const chatLog = $("#chat-log");

function addMsg(text, who) {
  const div = document.createElement("div");
  div.className = "msg msg-" + who;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTo(0, 1e6);
  return div;
}

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/[#*_]/g, ""));
  u.lang = "en-GB"; u.rate = 1.04;
  u.onstart = () => orb.className = "orb orb-speaking";
  u.onend = () => orb.className = "orb orb-idle";
  speechSynthesis.speak(u);
}

async function sendChat(message, viaVoice = false) {
  addMsg(message, "user");
  const aiDiv = addMsg("…", "ai");
  try {
    const r = await api("/api/chat", { method: "POST", body: { message } });
    (r.actions || []).forEach((a) => {
      const act = document.createElement("div");
      act.className = "msg msg-action";
      act.textContent = "✓ " + a;
      aiDiv.before(act);
    });
    if (viaVoice) speak(r.reply);
    await typewriter(aiDiv, r.reply);
    loadHabits(); loadScore(); loadMoney(); loadGym();
  } catch (e) {
    aiDiv.textContent = "Error: " + e.message;
  }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("#chat-input").value.trim();
  if (!v) return;
  $("#chat-input").value = "";
  sendChat(v);
});

/* voice via the orb */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizing = false;
orb.addEventListener("click", () => {
  if (!SR) { toast("Voice not supported in this browser"); return; }
  if (recognizing) return;
  const rec = new SR();
  rec.lang = "en-GB";
  rec.interimResults = false;
  recognizing = true;
  orb.className = "orb orb-listening";
  $("#orb-hint").textContent = "listening…";
  rec.onresult = (e) => {
    const transcript = e.results[0][0].transcript;
    document.body.dataset.tab = "chat";
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === "chat"));
    sendChat(transcript, true);
  };
  rec.onend = () => {
    recognizing = false;
    if (!orb.classList.contains("orb-speaking")) orb.className = "orb orb-idle";
    $("#orb-hint").textContent = "tap the orb & speak — “log 500ml water”, “how's my day?”";
  };
  rec.onerror = rec.onend;
  rec.start();
});

/* ── Photo logging ─────────────────────────────────────────────────── */
$("#photo-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  addMsg("📷 (photo uploaded)", "user");
  const aiDiv = addMsg("Analysing photo…", "ai");
  const fd = new FormData();
  fd.append("photo", file);
  try {
    const r = await api("/api/photo", { method: "POST", body: fd });
    let data = null;
    try {
      const m = r.analysis.match(/\{[\s\S]*\}/);
      if (m) data = JSON.parse(m[0]);
    } catch { /* not JSON, show raw */ }
    await typewriter(aiDiv, r.analysis);
    if (data && data.type) {
      const btn = document.createElement("button");
      btn.className = "btn btn-grad";
      btn.style.alignSelf = "center";
      btn.textContent = "✓ Confirm & log " + data.type;
      btn.onclick = async () => {
        const res = await api("/api/photo/confirm", { method: "POST", body: {
          type: data.type, amount: data.total ?? data.amount,
          exercise: data.exercise || (data.exercises && data.exercises[0]?.name),
          weight_kg: data.weight_kg || (data.exercises && data.exercises[0]?.weight),
          reps: data.reps || (data.exercises && data.exercises[0]?.reps),
          category: data.category, note: data.merchant || data.note || JSON.stringify(data).slice(0, 120) } });
        toast(res.message || "Logged");
        btn.remove(); loadMoney(); loadGym(); loadScore();
      };
      chatLog.appendChild(btn);
    }
  } catch (err) { aiDiv.textContent = "Photo error: " + err.message; }
  e.target.value = "";
});

/* ── Conversation history ──────────────────────────────────────────── */
async function loadConversation() {
  try {
    const msgs = await api("/api/conversation");
    msgs.slice(-12).forEach((m) => addMsg(m.content, m.role === "user" ? "user" : "ai"));
  } catch (e) { console.warn("conv", e); }
}

/* ── Boot ──────────────────────────────────────────────────────────── */
loadWeather(); loadScore(); loadHabits(); loadBots(); loadGym();
loadMoney(); loadNews(); loadGoals(); loadReflections(); loadIdeas();
loadNotifications(); loadConversation();
loadBriefing();
loadCalendar(); loadInbox();

setInterval(loadBots, 120e3);
setInterval(loadNotifications, 60e3);
