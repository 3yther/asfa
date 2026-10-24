/* ══════════════════════════════════════════════════════════════════════════
   SCOUT HUB — Find roles / Upcoming / Tracker / Alerts

   Four screens off one page, switched on the URL hash so each one is
   linkable and the back button works on a phone.

   Two rules this file does not bend:

     * every request goes through api() / apiSend(), which set
       credentials: "include". The session cookie is what gets past the auth
       gate; a fetch without it gets a 401 and the screen renders empty for no
       visible reason.
     * every string from the database reaches the DOM through esc(). Titles,
       employer names and descriptions are SCRAPED, so they are somebody
       else's text, and the only safe assumption is that one of them contains
       a less-than sign.
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // ── HTTP ────────────────────────────────────────────────────────────────
  async function api(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  async function apiSend(url, body, method) {
    const opts = { method: method || "POST", credentials: "include" };
    if (body !== undefined) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(url, opts);          // _csrf.html adds X-CSRF-Token
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  // ── Helpers ─────────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  function esc(v) {
    return String(v === null || v === undefined ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function toast(msg, bad) {
    const box = $("sr-toasts");
    const t = document.createElement("div");
    t.className = "sr-toast" + (bad ? " bad" : "");
    t.textContent = msg;
    box.appendChild(t);
    setTimeout(() => t.remove(), 3600);
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : (many || one + "s"));
  }

  // ── State ───────────────────────────────────────────────────────────────
  const state = {
    filters: {},
    sort: "recent",
    view: null,          // last roles_view payload
    visited: false,      // visit=1 is sent once per page load, never again
    loaded: {},          // which screens have fetched
  };

  // ══ TABS ════════════════════════════════════════════════════════════════

  const SCREENS = ["find", "upcoming", "tracker", "alerts"];
  const LOADERS = {
    find: loadRoles, upcoming: loadUpcoming,
    tracker: loadTracker, alerts: loadAlerts,
  };

  function showScreen(name, push) {
    if (SCREENS.indexOf(name) === -1) name = "find";
    SCREENS.forEach((s) => {
      const panel = $("screen-" + s), tab = $("tab-" + s);
      panel.hidden = s !== name;
      tab.setAttribute("aria-selected", String(s === name));
    });
    // The Excluded view is not a tab; leaving Find roles closes it.
    if (name !== "find") closeExcluded();
    if (push && location.hash !== "#" + name) {
      history.pushState(null, "", "#" + name);
    }
    if (!state.loaded[name]) { state.loaded[name] = true; LOADERS[name](); }
  }

  function initTabs() {
    SCREENS.forEach((s) => {
      $("tab-" + s).addEventListener("click", () => showScreen(s, true));
    });
    // Arrow keys move between tabs, which is what a tablist is expected to do.
    $("tab-find").parentElement.addEventListener("keydown", (e) => {
      const i = SCREENS.indexOf(document.activeElement.dataset.screen);
      if (i === -1) return;
      let next = null;
      if (e.key === "ArrowRight") next = (i + 1) % SCREENS.length;
      if (e.key === "ArrowLeft") next = (i - 1 + SCREENS.length) % SCREENS.length;
      if (next === null) return;
      e.preventDefault();
      $("tab-" + SCREENS[next]).focus();
      showScreen(SCREENS[next], true);
    });
    window.addEventListener("popstate", () => {
      showScreen((location.hash || "#find").slice(1), false);
    });
    showScreen((location.hash || "#find").slice(1), false);
  }

  // ══ FIND ROLES ══════════════════════════════════════════════════════════

  function filterQuery() {
    const p = new URLSearchParams();
    Object.keys(state.filters).forEach((k) => {
      const v = state.filters[k];
      if (v === true) p.set(k, "1");
      else if (v) p.set(k, v);
    });
    p.set("sort", state.sort);
    if (!state.visited) { p.set("visit", "1"); state.visited = true; }
    return p.toString();
  }

  async function loadRoles() {
    const groups = $("sr-groups");
    groups.innerHTML = '<div class="sr-empty">Loading roles&hellip;</div>';
    try {
      state.view = await api("/api/scout/roles?" + filterQuery());
    } catch (e) {
      groups.innerHTML = '<div class="sr-empty">Could not load roles (' +
        esc(e.message) + ').</div>';
      return;
    }
    fillFilterOptions(state.view);
    renderChips();
    renderCountline(state.view.counts, state.view.last_visited);
    renderGroups(state.view.groups);
  }

  function fillFilterOptions(view) {
    const lvl = $("sr-f-level");
    if (lvl.options.length <= 1) {
      (view.levels || []).forEach((l) => {
        lvl.add(new Option("Level " + l + " and above", String(l)));
      });
    }
    const src = $("sr-f-source");
    if (src.options.length <= 1) {
      (view.sources || []).forEach((s) => src.add(new Option(s, s)));
    }
    // A datalist, not a select: you can type a city the current results do not
    // mention yet, which is the point when you are deciding where to move to.
    // `.add()` is a select method — a datalist takes appended <option>s.
    const places = $("sr-f-location-list");
    if (!places.children.length) {
      (view.locations || []).forEach((l) => {
        const opt = document.createElement("option");
        opt.value = l;
        places.appendChild(opt);
      });
    }
  }

  const CHIP_LABELS = {
    watchlist_only: "Watchlist only",
    hide_closed: "Open listings only",
    new_only: "New since last visit",
  };

  function renderChips() {
    const box = $("sr-chips");
    const bits = [];
    Object.keys(state.filters).forEach((key) => {
      const v = state.filters[key];
      let label;
      if (key === "q") label = 'Search: "' + v + '"';
      else if (key === "level") label = "Level " + v + "+";
      else if (key === "source") label = "Provider: " + v;
      else if (key === "location") label = "In: " + v;
      else label = CHIP_LABELS[key] || key;
      bits.push('<button type="button" class="sr-chip" data-drop="' + esc(key) +
        '" aria-label="Remove filter: ' + esc(label) + '">' + esc(label) +
        '<span class="x" aria-hidden="true">&#x2715;</span></button>');
    });
    if (bits.length > 1) {
      bits.push('<button type="button" class="sr-chip" data-drop="*" ' +
        'aria-label="Clear all filters">Clear all' +
        '<span class="x" aria-hidden="true">&#x2715;</span></button>');
    }
    box.innerHTML = bits.join("");
  }

  function renderCountline(c, lastVisited) {
    const bits = [];
    bits.push("<span><b>" + c.shown + "</b> of " + plural(c.total, "role") +
      " shown</span>");
    if (c.new_since_last_visit) {
      bits.push('<span class="new">' + c.new_since_last_visit + " new since " +
        (lastVisited ? "your last visit" : "the first scan") + "</span>");
    }
    if (c.saved) bits.push("<span>" + c.saved + " on the tracker</span>");
    if (c.review) bits.push("<span>" + c.review + " to review</span>");
    $("sr-countline").innerHTML = bits.join("");
  }

  function badges(card) {
    const out = [];
    if (card.is_new) out.push(['new', 'NEW']);
    if (card.starred) out.push(['star', '★ WATCHLIST']);
    if (card.level) out.push(['level', 'LEVEL ' + card.level]);
    else out.push(['unknown', 'LEVEL UNKNOWN']);
    if (card.location_count > 1) {
      out.push(['multi', card.location_count + ' LOCATIONS']);
    }
    if (card.status && card.status !== "open") {
      out.push(['closed', String(card.status).toUpperCase()]);
    }
    if (card.days_left !== null && card.days_left !== undefined &&
        card.days_left >= 0 && card.days_left <= 7) {
      out.push(['soon', card.days_left === 0 ? 'CLOSES TODAY'
        : plural(card.days_left, 'DAY', 'DAYS') + ' LEFT']);
    }
    if (card.needs_review) out.push(['review', 'REVIEW']);
    return '<div class="sr-badges">' + out.map((b) =>
      '<span class="sr-badge sr-badge-' + b[0] + '">' + esc(b[1]) + '</span>'
    ).join("") + '</div>';
  }

  function stageSelect(card) {
    const view = state.view || {};
    const stages = view.stages || STAGE_FALLBACK;
    const auto = view.auto_stage || "saved";
    // Scout's backfill parks every scraped listing at the auto stage, so a card
    // sitting there has not been tracked by anyone — labelling it "Saved" would
    // claim a decision the user never made. It is also the one stage the 14-day
    // cleanup may delete, which is exactly what "not tracked" means here.
    const opts = ['<option value="' + esc(auto) + '">Not tracked</option>'];
    stages.forEach((s) => {
      if (s.key === auto) return;
      opts.push('<option value="' + esc(s.key) + '"' +
        (card.stage === s.key ? " selected" : "") + ">" + esc(s.label) +
        "</option>");
    });
    return '<label class="sr-sr-only" for="stage-' + esc(card.ref) +
      '">Stage for ' + esc(card.title) + '</label>' +
      '<select class="sr-select" id="stage-' + esc(card.ref) +
      '" data-stage-for="' + esc(card.ref) + '">' + opts.join("") + "</select>";
  }

  // Used until /api/scout/roles/<ref> has told us the real list. Kept in step
  // with database.SCOUT_STAGES.
  const STAGE_FALLBACK = [
    { key: "saved", label: "Saved" },
    { key: "interested", label: "Interested" },
    { key: "applied", label: "Applied" },
    { key: "online_assessment", label: "Online Assessment" },
    { key: "video_interview", label: "Video Interview" },
    { key: "assessment_centre", label: "Assessment Centre" },
    { key: "offer", label: "Offer" },
    { key: "rejected", label: "Rejected" },
    { key: "withdrawn", label: "Withdrawn" },
  ];

  function cardHtml(card) {
    const meta = [];
    if (card.course) meta.push(esc(card.course));
    if (card.locations && card.locations.length) meta.push(esc(card.locations[0]));
    if (card.wage) meta.push(esc(card.wage));
    if (card.closing_date) meta.push("Closes " + esc(card.closing_date));
    if (card.source) meta.push(esc(card.source));

    return '<article class="sr-card' + (card.is_new ? " is-new" : "") +
      '" style="--sr-tile: var(' + esc(card.tile_token) + ');">' +
      '<span class="sr-tile" aria-hidden="true">' + esc(card.monogram) + "</span>" +
      '<div class="sr-card-main">' +
        badges(card) +
        '<h3 class="sr-card-title"><a href="#" data-open="' + esc(card.ref) +
          '">' + esc(card.title) + "</a></h3>" +
        '<p class="sr-card-employer">' + esc(card.employer) + "</p>" +
        (card.needs_review && card.review_standard
          ? '<p class="sr-card-meta">Standard: ' + esc(card.review_standard) +
            " &mdash; not a digital standard, kept because the employer is " +
            "on your watchlist.</p>"
          : "") +
        '<p class="sr-card-meta">' + meta.join(" &middot; ") + "</p>" +
        '<div class="sr-card-actions">' +
          '<button type="button" class="sr-btn" data-open="' + esc(card.ref) +
            '">View</button>' + stageSelect(card) +
        "</div>" +
      "</div></article>";
  }

  function groupBlock(title, note, cards, opts) {
    opts = opts || {};
    const inner = cards.length
      ? '<div class="sr-grid">' + cards.map(cardHtml).join("") + "</div>"
      : '<div class="sr-empty">Nothing here.</div>';
    const head = '<h2>' + esc(title) + '</h2><span class="n">' +
      cards.length + "</span>";
    if (opts.collapsible) {
      return '<details class="sr-group sr-collapsible' +
        (opts.review ? " sr-review" : "") + '"' + (opts.open ? " open" : "") +
        "><summary>" + head + "</summary>" +
        (note ? '<p class="sr-group-note">' + esc(note) + "</p>" : "") +
        inner + "</details>";
    }
    return '<section class="sr-group"><div class="sr-group-head">' + head +
      "</div>" + (note ? '<p class="sr-group-note">' + esc(note) + "</p>" : "") +
      inner + "</section>";
  }

  function renderGroups(g) {
    const html = [];
    html.push(groupBlock("Level 4 and above", "", g.level_4_plus || []));
    html.push(groupBlock(
      "Level unknown (off-site listings)",
      "gov.uk never states a level for these, so they are shown separately "
      + "rather than assumed to be below Level 4.",
      g.level_unknown || [], { collapsible: true, open: true }));
    html.push(groupBlock(
      "Review",
      "The listed standard is not a digital one, but the employer is on your "
      + "watchlist, so Scout kept it for you to judge.",
      g.review || [], { collapsible: true, review: true }));
    $("sr-groups").innerHTML = html.join("");
  }

  function readDrawer() {
    const f = {};
    const q = $("sr-q").value.trim();
    if (q) f.q = q;
    if ($("sr-f-level").value) f.level = $("sr-f-level").value;
    if ($("sr-f-source").value) f.source = $("sr-f-source").value;
    const place = $("sr-f-location").value.trim();
    if (place) f.location = place;
    if ($("sr-f-watch").checked) f.watchlist_only = true;
    if ($("sr-f-open").checked) f.hide_closed = true;
    if ($("sr-f-new").checked) f.new_only = true;
    return f;
  }

  function writeDrawer(f) {
    $("sr-q").value = f.q || "";
    $("sr-f-level").value = f.level || "";
    $("sr-f-source").value = f.source || "";
    $("sr-f-location").value = f.location || "";
    $("sr-f-watch").checked = !!f.watchlist_only;
    $("sr-f-open").checked = !!f.hide_closed;
    $("sr-f-new").checked = !!f.new_only;
  }

  function initFind() {
    $("sr-search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      state.filters = readDrawer();
      loadRoles();
    });
    $("sr-sort").addEventListener("change", () => {
      state.sort = $("sr-sort").value;
      loadRoles();
    });
    $("sr-filters-btn").addEventListener("click", () => {
      const open = $("sr-drawer").hidden;
      $("sr-drawer").hidden = !open;
      $("sr-filters-btn").setAttribute("aria-expanded", String(open));
    });
    $("sr-apply").addEventListener("click", () => {
      state.filters = readDrawer();
      loadRoles();
    });
    $("sr-clear").addEventListener("click", () => {
      state.filters = {};
      writeDrawer({});
      loadRoles();
    });
    $("sr-chips").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-drop]");
      if (!btn) return;
      if (btn.dataset.drop === "*") state.filters = {};
      else delete state.filters[btn.dataset.drop];
      writeDrawer(state.filters);
      loadRoles();
    });
    $("sr-create-alert").addEventListener("click", createAlertFromFilters);
    $("sr-excluded-btn").addEventListener("click", toggleExcluded);
    $("sr-ex-back").addEventListener("click", toggleExcluded);

    $("sr-groups").addEventListener("click", (e) => {
      const open = e.target.closest("[data-open]");
      if (!open) return;
      e.preventDefault();
      openPanel(open.dataset.open);
    });
    $("sr-groups").addEventListener("change", (e) => {
      const sel = e.target.closest("[data-stage-for]");
      if (sel) setRoleStage(sel.dataset.stageFor, sel.value);
    });
  }

  async function setRoleStage(ref, stage) {
    try {
      await apiSend("/api/scout/roles/" + encodeURIComponent(ref) + "/stage",
        { stage: stage });
      toast(stage ? "Moved to " + stage.replace(/_/g, " ") : "Saved");
      // The Tracker reads the same rows, so its cache is now stale.
      state.loaded.tracker = false;
    } catch (e) {
      toast("Could not set the stage (" + e.message + ")", true);
    }
  }

  // ══ EXCLUDED ════════════════════════════════════════════════════════════

  // The audit REPLACES the roles list rather than stacking under it. Both on
  // one page meant scrolling past every card to reach the thing you opened.
  function closeExcluded() {
    $("screen-excluded").hidden = true;
    $("sr-excluded-btn").setAttribute("aria-expanded", "false");
    if (SCREENS.indexOf((location.hash || "#find").slice(1)) <= 0) {
      $("screen-find").hidden = false;
    }
  }

  function toggleExcluded() {
    const show = $("screen-excluded").hidden;
    $("screen-excluded").hidden = !show;
    $("screen-find").hidden = show;
    $("sr-excluded-btn").setAttribute("aria-expanded", String(show));
    if (show) {
      loadExcluded();
      $("sr-ex-back").focus();
      window.scrollTo(0, 0);
    } else {
      $("sr-excluded-btn").focus();
    }
  }

  const REASON_TEXT = {
    title_excluded: "a word in the title is on your exclusion list",
    employer_excluded: "the employer is on your exclusion list",
    non_digital_standard: "the stated apprenticeship standard is not a digital one",
    no_digital_signal: "no digital term anywhere in the title or standard",
  };

  async function loadExcluded() {
    const list = $("sr-ex-list");
    list.innerHTML = '<div class="sr-empty">Loading&hellip;</div>';
    let data;
    try {
      data = await api("/api/scout/excluded");
    } catch (e) {
      list.innerHTML = '<div class="sr-empty">Could not load the exclusion log ('
        + esc(e.message) + ").</div>";
      return;
    }
    const counts = (data.counts || {}).by_reason || {};
    const total = (data.counts || {}).total || 0;
    const tally = Object.keys(counts).sort().map((k) =>
      '<span class="sr-badge sr-badge-review">' + esc(k.replace(/_/g, " ")) +
      " " + counts[k] + "</span>");
    if (total) {
      tally.unshift('<span class="sr-badge sr-badge-level">EXCLUDED ' +
        total + "</span>");
    }
    $("sr-ex-tally").innerHTML = tally.join("");

    renderAllowed(data.allowed || []);

    const rows = data.listings || [];
    if (!rows.length) {
      list.innerHTML = '<div class="sr-empty">Nothing has been excluded yet.</div>';
      return;
    }
    list.innerHTML = rows.map((r) => {
      const reason = r.reason || "";
      const acts = [];
      if (reason === "non_digital_standard" && r.detail) {
        acts.push('<button type="button" class="sr-btn" data-allow="standard" ' +
          'data-value="' + esc(r.detail) + '">Never exclude this standard</button>');
      }
      if (r.employer) {
        acts.push('<button type="button" class="sr-btn" data-allow="employer" ' +
          'data-value="' + esc(r.employer) + '">Never exclude this employer</button>');
      }
      return '<div class="sr-ex-row">' +
        "<h3>" + esc(r.title || "Untitled") + "</h3>" +
        '<p class="sr-card-employer">' + esc(r.employer || "") + "</p>" +
        '<p class="sr-ex-why">Dropped because <code>' + esc(reason) +
          "</code>" + (REASON_TEXT[reason] ? " &mdash; " + esc(REASON_TEXT[reason]) : "") +
          (r.detail ? ' (matched "' + esc(r.detail) + '")' : "") + ".</p>" +
        '<div class="sr-ex-actions">' + acts.join("") + "</div></div>";
    }).join("");
  }

  // Corrections already made, with a way back. They are stored in the database
  // so a Railway deploy cannot lose them — which also means a mis-click would
  // be permanent without this.
  function renderAllowed(rows) {
    const box = $("sr-allowed");
    if (!rows.length) { box.innerHTML = ""; box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<h2 class="sr-h1" style="font-size:1rem;">Allowed</h2>' +
      '<p class="sr-group-note" style="margin-bottom:12px;">Corrections you ' +
      "have made. These are stored in the database, so they survive a " +
      "redeploy.</p>" +
      rows.map((r) =>
        '<div class="sr-ex-row"><h3>' + esc(r.value) + "</h3>" +
        '<p class="sr-ex-why">' + esc(r.label) +
          (r.created_at ? " &middot; " + esc(String(r.created_at).slice(0, 10))
                        : "") + "</p>" +
        '<div class="sr-ex-actions"><button type="button" class="sr-btn" ' +
          'data-undo="' + r.id + '" aria-label="Undo: ' + esc(r.label) +
          " " + esc(r.value) + '">Undo</button></div></div>').join("");
  }

  $("sr-allowed").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-undo]");
    if (!btn) return;
    btn.disabled = true;
    try {
      const res = await apiSend("/api/scout/excluded/allow/" +
        encodeURIComponent(btn.dataset.undo), undefined, "DELETE");
      renderAllowed(res.allowed || []);
      toast("Correction undone.");
    } catch (err) {
      toast("Could not undo that (" + err.message + ")", true);
      btn.disabled = false;
    }
  });

  $("sr-ex-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-allow]");
    if (!btn) return;
    btn.disabled = true;
    try {
      const res = await apiSend("/api/scout/excluded/allow",
        { kind: btn.dataset.allow, value: btn.dataset.value });
      const added = res.added || {};
      const touched = Object.keys(added.added || {}).concat(
        Object.keys(added.removed || {}));
      renderAllowed(res.allowed || []);
      toast(touched.length
        ? "Saved. Undo it under \u201cAllowed\u201d if that was wrong."
        : "Already allowed — nothing to change.");
    } catch (err) {
      toast("Could not update the filter config (" + err.message + ")", true);
      btn.disabled = false;
    }
  });

  // ══ ROLE DETAIL SLIDE-OVER ══════════════════════════════════════════════

  let panelRef = null;
  let panelReturn = null;

  function field(label, value) {
    const has = value !== null && value !== undefined && String(value).trim();
    return "<div><dt>" + esc(label) + "</dt><dd" + (has ? "" : ' class="none"') +
      ">" + (has ? esc(value) : "&mdash;") + "</dd></div>";
  }

  async function openPanel(ref) {
    panelRef = ref;
    panelReturn = document.activeElement;
    $("sr-panel-body").innerHTML = '<div class="sr-empty">Loading&hellip;</div>';
    $("sr-panel").hidden = false;
    $("sr-scrim").hidden = false;
    document.body.style.overflow = "hidden";
    $("sr-panel-close").focus();
    try {
      renderPanel(await api("/api/scout/roles/" + encodeURIComponent(ref)));
    } catch (e) {
      $("sr-panel-body").innerHTML = '<div class="sr-empty">Could not load this role ('
        + esc(e.message) + ").</div>";
    }
  }

  function renderPanel(card) {
    $("sr-panel-title").textContent = card.title || "Role";
    $("sr-panel-employer").textContent = card.employer || "";
    const link = $("sr-panel-link");
    if (card.url) { link.href = card.url; link.hidden = false; }
    else { link.hidden = true; }

    const locs = (card.locations || []).join(", ");
    const body = [badges(card), '<dl class="sr-dl">',
      field("Standard", card.course),
      field("Level", card.level ? "Level " + card.level : ""),
      field("Wage", card.wage),
      field("Hours", card.hours),
      field("Starts", card.start_date),
      field("Duration", card.duration),
      field("Closes", card.closing_date && card.days_left !== null
        ? card.closing_date + " (" + (card.days_left < 0 ? "passed"
            : plural(card.days_left, "day") + " left") + ")"
        : card.closing_date),
      field("Location" + ((card.locations || []).length > 1 ? "s" : ""), locs),
      field("Provider", card.source),
      field("Found", card.found_date),
      "</dl>",
      '<div class="sr-card-actions" style="margin-bottom:16px;">' +
        stageSelect(card) + "</div>",
    ];
    if (card.description) {
      body.push("<h3 style=\"font-size:.72rem;letter-spacing:.8px;" +
        "text-transform:uppercase;margin:0 0 6px;\">Description</h3>");
      body.push('<p class="sr-desc">' + esc(card.description) + "</p>");
    } else {
      body.push('<p class="sr-group-note">No description stored yet. ' +
        "&ldquo;Fetch details&rdquo; requests this one listing&rsquo;s page, " +
        "once, and caches it for 24 hours.</p>");
    }
    $("sr-panel-body").innerHTML = body.join("");
    $("sr-panel-fetch").disabled = !card.url;
  }

  function closePanel() {
    $("sr-panel").hidden = true;
    $("sr-scrim").hidden = true;
    document.body.style.overflow = "";
    panelRef = null;
    if (panelReturn && panelReturn.focus) panelReturn.focus();
  }

  async function fetchDetail() {
    if (!panelRef) return;
    const btn = $("sr-panel-fetch");
    btn.disabled = true;
    btn.textContent = "Fetching…";
    try {
      const res = await apiSend("/api/scout/roles/" +
        encodeURIComponent(panelRef) + "/detail", {});
      if (res.cached) toast("Already fetched within the last 24 hours.");
      else if (res.status === "ok") toast("Details fetched.");
      else if (res.status === "backoff") {
        toast("gov.uk asked us to slow down — stopped without retrying.", true);
      } else toast("Nothing could be parsed from that page.", true);
      renderPanel(await api("/api/scout/roles/" + encodeURIComponent(panelRef)));
    } catch (e) {
      toast("Could not fetch the details (" + e.message + ")", true);
    } finally {
      btn.textContent = "Fetch details";
      btn.disabled = false;
    }
  }

  function initPanel() {
    $("sr-panel-close").addEventListener("click", closePanel);
    $("sr-scrim").addEventListener("click", closePanel);
    $("sr-panel-fetch").addEventListener("click", fetchDetail);
    $("sr-panel-body").addEventListener("change", (e) => {
      const sel = e.target.closest("[data-stage-for]");
      if (sel) setRoleStage(sel.dataset.stageFor, sel.value);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("sr-panel").hidden) closePanel();
      // A modal keeps focus inside it; otherwise Tab walks off into the page
      // behind the scrim, where nothing is clickable.
      if (e.key === "Tab" && !$("sr-panel").hidden) {
        const nodes = $("sr-panel").querySelectorAll(
          'a[href], button:not([disabled]), select, input, [tabindex]:not([tabindex="-1"])');
        if (!nodes.length) return;
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault(); first.focus();
        }
      }
    });
  }

  // ══ TRACKER ═════════════════════════════════════════════════════════════

  async function loadTracker() {
    const board = $("sr-board");
    board.innerHTML = '<div class="sr-empty">Loading&hellip;</div>';
    let data;
    try {
      data = await api("/api/scout/tracker");
    } catch (e) {
      board.innerHTML = '<div class="sr-empty">Could not load the tracker ('
        + esc(e.message) + ").</div>";
      return;
    }
    state.tracker = data;
    renderCleanupNote(data);

    const closed = data.closed_stages || [];
    const cols = [];
    cols.push(column("Not started", "", data.not_started || [], data.stages));
    (data.stages || []).forEach((s) => {
      if (closed.indexOf(s.key) !== -1) return;
      // The auto stage is where Scout's backfill parks every scraped listing.
      // Nothing is ever MOVED there, so a column for it is always empty.
      if (s.key === data.auto_stage) return;
      cols.push(column(s.label, s.key, (data.by_stage || {})[s.key] || [],
        data.stages));
    });
    // One muted Closed column rather than a column per outcome: a rejection and
    // a withdrawal are both "over", and two near-empty columns is just noise.
    const closedRows = [];
    closed.forEach((k) => {
      ((data.by_stage || {})[k] || []).forEach((r) => closedRows.push(r));
    });
    cols.push(column("Closed", "", closedRows, data.stages, true));
    board.innerHTML = cols.join("");
    initDnd();
  }

  function renderCleanupNote(data) {
    const note = $("sr-cleanup-note");
    const label = data.auto_stage_label || data.auto_stage || "Saved";
    const days = data.cleanup_days || 14;
    const risk = data.cleanup_at_risk;
    const exempt = data.cleanup_exempt;
    const bits = ["<b>Housekeeping.</b> Listings still at &ldquo;" +
      esc(label) + "&rdquo; are cleared " + days +
      " days after Scout found them. Move one to any other stage and it " +
      "stays for good \u2014 an apprenticeship cycle runs for months, so " +
      "age alone says nothing about whether a role still matters."];
    if (risk !== null && risk !== undefined) {
      bits.push("The next run would clear " + plural(risk, "listing") +
        (exempt ? " and keep " + plural(exempt, "tracked one") : "") + ".");
    }
    note.hidden = false;
    note.innerHTML = bits.join(" ");
  }

  function column(label, stageKey, rows, stages, isClosed) {
    const cards = rows.length
      ? rows.map((r) => trackerCard(r, stages)).join("")
      : '<p class="sr-group-note" style="padding:4px 6px;">&mdash;</p>';
    return '<section class="sr-col' + (isClosed ? " is-closed" : "") +
      '" data-stage="' + esc(stageKey) + '">' +
      '<div class="sr-col-head"><h3>' + esc(label) + '</h3>' +
      '<span class="n">' + rows.length + "</span></div>" + cards + "</section>";
  }

  function trackerCard(row, stages) {
    const kind = row.kind || "programme";
    const when = [];
    if (row.next_step) when.push(esc(row.next_step));
    if (row.next_step_date) when.push("by " + esc(row.next_step_date));
    else if (row.applied_date) when.push("applied " + esc(row.applied_date));
    else if (row.deadline) when.push("closes " + esc(row.deadline));
    // A programme can go back to "not started"; a listing goes back to
    // untracked, which is Scout's auto stage and therefore deletable again.
    const opts = ['<option value="">' +
      (kind === "listing" ? "Not tracked" : "Not started") + "</option>"];
    (stages || STAGE_FALLBACK).forEach((s) => {
      opts.push('<option value="' + esc(s.key) + '"' +
        (row.stage === s.key ? " selected" : "") + ">" + esc(s.label) +
        "</option>");
    });
    const sid = "tstage-" + kind + "-" + row.id;
    return '<article class="sr-tcard" draggable="true" data-id="' + row.id +
      '" data-kind="' + esc(kind) + '">' +
      "<h4>" + esc(row.title || row.company || "Programme") + "</h4>" +
      '<p class="co">' + esc(row.company || "") +
        (row.level ? " &middot; " + esc(row.level) : "") +
        ' &middot; <span class="kind">' +
        (kind === "listing" ? "live vacancy" : "programme") + "</span></p>" +
      (when.length ? '<p class="when">' + when.join(" &middot; ") + "</p>" : "") +
      '<label class="sr-sr-only" for="' + sid + '">Stage for ' +
        esc(row.title || row.company) + "</label>" +
      '<select class="sr-select" id="' + sid + '" data-tstage="' + row.id +
        '" data-tkind="' + esc(kind) + '">' + opts.join("") + "</select>" +
      (row.link ? '<p style="margin:4px 0 0;"><a class="plink" href="' +
        esc(row.link) + '" target="_blank" rel="noopener noreferrer">' +
        (kind === "listing" ? "Open vacancy" : "Open programme page") +
        "</a></p>" : "") +
      "</article>";
  }

  async function setProgrammeStage(id, stage, kind) {
    try {
      await apiSend("/api/scout/tracker/" +
        encodeURIComponent(kind || "programme") + "/" +
        encodeURIComponent(id) + "/stage", { stage: stage });
      toast(stage ? "Moved to " + stage.replace(/_/g, " ") : "Moved to not started");
      loadTracker();
    } catch (e) {
      toast("Could not move that card (" + e.message + ")", true);
    }
  }

  // Drag and drop is an ENHANCEMENT. The select on every card does the same
  // job, works with a keyboard and works on a phone, so nothing here is the
  // only way to move a card.
  function initDnd() {
    const board = $("sr-board");
    board.querySelectorAll(".sr-tcard").forEach((card) => {
      card.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain",
          card.dataset.kind + ":" + card.dataset.id);
        e.dataTransfer.effectAllowed = "move";
        card.classList.add("dragging");
      });
      card.addEventListener("dragend", () => card.classList.remove("dragging"));
    });
    board.querySelectorAll(".sr-col").forEach((col) => {
      col.addEventListener("dragover", (e) => {
        e.preventDefault();
        col.classList.add("is-over");
      });
      col.addEventListener("dragleave", () => col.classList.remove("is-over"));
      col.addEventListener("drop", (e) => {
        e.preventDefault();
        col.classList.remove("is-over");
        const moved = e.dataTransfer.getData("text/plain");
        const at = moved.indexOf(":");
        if (at > 0) {
          setProgrammeStage(moved.slice(at + 1), col.dataset.stage || "",
            moved.slice(0, at));
        }
      });
    });
  }

  function initTracker() {
    $("sr-board").addEventListener("change", (e) => {
      const sel = e.target.closest("[data-tstage]");
      if (sel) setProgrammeStage(sel.dataset.tstage, sel.value, sel.dataset.tkind);
    });
  }

  // ══ UPCOMING ════════════════════════════════════════════════════════════

  async function loadUpcoming() {
    const grid = $("sr-year"), cards = $("sr-upcoming-cards");
    cards.innerHTML = '<div class="sr-empty">Loading&hellip;</div>';
    let data;
    try {
      data = await api("/api/scout/upcoming");
    } catch (e) {
      cards.innerHTML = '<div class="sr-empty">Could not load the calendar ('
        + esc(e.message) + ").</div>";
      return;
    }
    const months = data.months || [];
    const now = data.current_month;
    if (!(data.rows || []).length) {
      const msg = '<div class="sr-empty">No programmes yet. Import your ' +
        "spreadsheet to fill this in.</div>";
      grid.innerHTML = ""; cards.innerHTML = msg;
      return;
    }

    const head = ['<div class="sr-year-head"><span></span>'];
    months.forEach((m) => {
      head.push('<span' + (m === now ? ' class="now"' : "") + ">" +
        esc(m) + "</span>");
    });
    head.push("</div>");

    const rows = data.rows.map((r) => {
      const confirmed = !!(r.confirmed_open_date || r.confirmed_close_date);
      const rolling = /^(y|yes|true|1|rolling)$/i.test(r.rolling || "");
      const cells = months.map((m) => {
        const on = r.open_months.indexOf(m) !== -1;
        let inner = "";
        if (rolling) inner = '<i class="sr-mark-rolling" aria-hidden="true"></i>';
        else if (on) {
          inner = '<i class="sr-mark-open' + (confirmed ? " confirmed" : "") +
            '" aria-hidden="true"></i>';
        }
        return '<div class="sr-cell' + (m === now ? " now" : "") + '">' +
          inner + "</div>";
      }).join("");
      return '<div class="sr-year-row"><div class="sr-year-label">' +
        "<h3>" + esc(r.company) + "</h3><p>" + esc(r.programme) +
        (confirmed ? "" : " &middot; last cycle") + "</p></div>" + cells + "</div>";
    });
    grid.innerHTML = head.join("") + rows.join("");

    cards.innerHTML = data.rows.map((r) => {
      const confirmed = !!(r.confirmed_open_date || r.confirmed_close_date);
      const rolling = /^(y|yes|true|1|rolling)$/i.test(r.rolling || "");
      const strip = months.map((m) => {
        const on = r.open_months.indexOf(m) !== -1;
        const cls = [];
        if (rolling) cls.push("rolling");
        else if (on) { cls.push("on"); if (confirmed) cls.push("confirmed"); }
        if (m === now) cls.push("now");
        return '<span class="' + cls.join(" ") + '">' +
          esc(m.slice(0, 1).toUpperCase()) + "</span>";
      }).join("");
      const bits = [];
      if (r.confirmed_open_date) bits.push("Opens " + esc(r.confirmed_open_date));
      if (r.confirmed_close_date) bits.push("Closes " + esc(r.confirmed_close_date));
      if (!bits.length && r.typical_close) {
        bits.push("Typically closes " + esc(r.typical_close));
      }
      if (r.stage_label) bits.push(esc(r.stage_label));
      return '<article class="sr-saved-row"><h3>' + esc(r.company) + "</h3>" +
        '<p class="terms">' + esc(r.programme) +
          (confirmed ? "" : " &middot; last cycle's window") + "</p>" +
        '<div class="sr-strip" role="img" aria-label="' + (rolling
          ? "Applications open all year (rolling)"
          : "Open months: " + esc(r.open_months.join(", ") || "none recorded")) +
          '">' + strip + "</div>" +
        (bits.length ? '<p class="terms">' + bits.join(" &middot; ") + "</p>" : "") +
        "</article>";
    }).join("");
  }

  // ══ ALERTS ══════════════════════════════════════════════════════════════

  async function loadAlerts() {
    const chans = $("sr-channels"), saved = $("sr-saved");
    saved.innerHTML = '<div class="sr-empty">Loading&hellip;</div>';
    let data;
    try {
      data = await api("/api/scout/alerts");
    } catch (e) {
      saved.innerHTML = '<div class="sr-empty">Could not load alerts ('
        + esc(e.message) + ").</div>";
      return;
    }
    // Channel state is a boolean and a sentence. The API never returns a token,
    // a chat id or an address, so there is nothing secret to leak here.
    chans.innerHTML = (data.channels || []).map((c) =>
      '<div class="sr-channel"><span class="lbl">' + esc(c.label) + "</span>" +
      '<span class="sr-state ' + (c.configured ? "on" : "off") + '">' +
        (c.configured ? "READY" : "NOT SET UP") + "</span>" +
      '<p class="why">' + esc(c.detail) + "</p></div>").join("");

    const rows = data.searches || [];
    saved.innerHTML = (rows.length ? rows.map(savedRow).join("") : "") +
      '<div class="sr-saved-row"><h3>New saved search</h3>' +
      '<p class="terms">Set the filters you want on Find roles, then press ' +
      "&ldquo;Create alert&rdquo; there. It saves exactly what you are looking at.</p>" +
      "</div>";
  }

  function savedRow(s) {
    let terms = {};
    try { terms = s.filters || {}; } catch (e) { terms = {}; }
    const desc = Object.keys(terms).map((k) =>
      k === "q" ? '"' + terms[k] + '"' : k.replace(/_/g, " ") +
      (terms[k] === true ? "" : ": " + terms[k])).join(" · ");
    const chans = (s.channel_list || []).join(", ") || "bell";
    const last = s.last_run_at
      ? "Last checked " + s.last_run_at + (s.last_match_count !== null &&
          s.last_match_count !== undefined
          ? " · " + plural(s.last_match_count, "match", "matches") : "")
      : "Not checked yet";
    return '<div class="sr-saved-row" data-search="' + s.id + '">' +
      "<h3>" + esc(s.name) + "</h3>" +
      '<p class="terms">' + (desc ? esc(desc) : "Everything") +
        " &middot; via " + esc(chans) + "</p>" +
      '<p class="terms">' + esc(last) + "</p>" +
      '<div class="sr-saved-actions">' +
        '<label class="sr-check"><input type="checkbox" data-toggle="' + s.id +
          '"' + (s.enabled ? " checked" : "") + "> Enabled</label>" +
        '<button type="button" class="sr-btn" data-del="' + s.id +
          '" aria-label="Delete saved search ' + esc(s.name) +
          '">Delete</button>' +
      "</div></div>";
  }

  async function createAlertFromFilters() {
    const name = window.prompt(
      "Name this alert (it saves the filters you have set):",
      describeFilters());
    if (!name) return;
    try {
      await apiSend("/api/scout/alerts", {
        name: name.trim(),
        filters: state.filters,
        channels: ["bell", "telegram"],
      });
      toast("Alert saved. It runs with the nightly scan.");
      state.loaded.alerts = false;
    } catch (e) {
      toast("Could not save that alert (" + e.message + ")", true);
    }
  }

  function describeFilters() {
    const keys = Object.keys(state.filters);
    if (!keys.length) return "All digital apprenticeships";
    return keys.map((k) => k === "q" ? state.filters[k]
      : (CHIP_LABELS[k] || k.replace(/_/g, " ") +
         (state.filters[k] === true ? "" : " " + state.filters[k]))).join(" + ");
  }

  function initAlerts() {
    $("sr-saved").addEventListener("change", async (e) => {
      const box = e.target.closest("[data-toggle]");
      if (!box) return;
      try {
        await apiSend("/api/scout/alerts/" + encodeURIComponent(box.dataset.toggle),
          { enabled: box.checked });
        toast(box.checked ? "Alert enabled" : "Alert paused");
      } catch (err) {
        toast("Could not change that alert (" + err.message + ")", true);
        box.checked = !box.checked;
      }
    });
    $("sr-saved").addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-del]");
      if (!btn) return;
      if (!window.confirm("Delete this saved search?")) return;
      try {
        await apiSend("/api/scout/alerts/" + encodeURIComponent(btn.dataset.del),
          undefined, "DELETE");
        loadAlerts();
      } catch (err) {
        toast("Could not delete that alert (" + err.message + ")", true);
      }
    });
  }

  // ── Boot ────────────────────────────────────────────────────────────────
  initFind();
  initPanel();
  initTracker();
  initAlerts();
  initTabs();
})();
