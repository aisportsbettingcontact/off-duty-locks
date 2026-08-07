/* Off Duty Locks — research interface behaviour.
   Vanilla, no dependencies, no build step. Responsibilities are separated into
   labelled sections: escaping, formatting, history transport, chart rendering,
   analysis disclosure, deep links, freshness.

   Server-rendered content is the product; this file only adds what genuinely
   needs state: expanding a game, drawing its line history, and keeping the
   freshness stamps honest. Nothing here re-renders data the server already
   sent. */
"use strict";

const PAGE = document.body.dataset.page || "dashboard";

/* ---- 1. escaping ---------------------------------------------------------
   Scraped strings (team names, book labels, snapshot values) are interpolated
   into innerHTML below; entity-escape them so upstream data can never become
   markup. tests/test_dashboard_js.py statically enforces that every
   data-derived placeholder in this file is a whole esc()/X()/Y() call. */
function esc(v) {
  return String(v).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---- 2. formatting ------------------------------------------------------ */
const ACCENT = "#FF5C1C";

function fmtTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", {
    hour: "numeric", minute: "2-digit", timeZone: "America/New_York",
  }) + " ET";
}

function fmtDay(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-US", {
    month: "short", day: "numeric", timeZone: "America/New_York",
  });
}

// Signed one-decimal for spreads; plain for totals; integer for moneylines.
function fmtValue(market, value) {
  if (value === null || value === undefined) return "—";
  if (market === "spread") return (value > 0 ? "+" : "") + Number(value).toFixed(1);
  if (market === "total") return Number(value).toFixed(1);
  return (value > 0 ? "+" : "") + String(Math.round(value));
}

const MARKET_LABEL = {
  spread: "Spread (away side)", total: "Total", moneyline: "Moneyline (away)",
};

/* ---- 3. history transport ------------------------------------------------
   One in-memory cache per page session keyed by game, and one AbortController
   so a fast second click supersedes the first request instead of racing it. */
const historyCache = new Map();
let pending = null;

async function loadHistory(key) {
  if (historyCache.has(key)) return historyCache.get(key);
  if (pending) pending.abort();
  pending = new AbortController();
  const r = await fetch(`/api/games/${encodeURIComponent(key)}/history`,
                        { signal: pending.signal });
  if (!r.ok) throw new Error(String(r.status));
  const payload = await r.json();
  historyCache.set(key, payload);
  return payload;
}

/* ---- 4. chart ------------------------------------------------------------
   Inline SVG step chart: sportsbook lines change discretely, so a straight
   interpolation between snapshots would draw movement that never happened. */
function seriesFor(payload, market) {
  const snaps = (payload && payload.snapshots) || [];
  const field = market === "spread" ? "spread"
              : market === "total" ? "total" : "ml_away";
  const openField = market === "spread" ? "spread"
                  : market === "total" ? "total" : "ml_away";
  const points = snaps
    .map(s => [s.captured_at_utc, s[field]])
    .filter(pt => pt[1] !== null && pt[1] !== undefined);
  const open = payload && payload.opening ? payload.opening[openField] : null;
  return { points, open };
}

function drawChart(box, payload, market) {
  const { points, open } = seriesFor(payload, market);

  if (!points.length) {
    box.innerHTML = '<p class="empty empty--inline">' +
      "No movement recorded yet for this market. Snapshots are captured on " +
      "every scrape run, so the chart fills in as the market moves." +
      "</p>";
    return;
  }

  // The viewBox is sized to the container so one user unit is one CSS pixel.
  // A fixed 760-unit box scaled into a 360px phone would shrink every label to
  // about six pixels — legible in the mockup, unreadable on the device.
  const W = Math.max(300, Math.round(box.clientWidth) || 760);
  const narrow = W < 520;
  const H = narrow ? 190 : 240;
  const PADX = narrow ? 38 : 48;
  const PADY = narrow ? 22 : 28;
  const xs = points.map(pt => new Date(pt[0]).getTime());
  const ys = points.map(pt => pt[1]);
  const all = open === null || open === undefined ? ys : ys.concat([open]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let yMin = Math.min(...all), yMax = Math.max(...all);
  if (yMin === yMax) { yMin -= 1; yMax += 1; }

  const X = t => PADX + ((t - x0) / ((x1 - x0) || 1)) * (W - PADX - 16);
  const Y = v => H - PADY - ((v - yMin) / (yMax - yMin)) * (H - PADY * 2);

  let path = "";
  points.forEach((pt, i) => {
    const px = X(new Date(pt[0]).getTime()), py = Y(pt[1]);
    path += i === 0 ? `M${px},${py}` : `H${px}V${py}`;
  });

  const last = points[points.length - 1];
  const openLine = (open === null || open === undefined) ? "" :
    `<line class="chart__open" x1="${PADX}" x2="${W - 16}" y1="${Y(open)}" y2="${Y(open)}"/>` +
    `<text x="${PADX}" y="${Y(open) - 6}">open ${esc(fmtValue(market, open))}</text>`;

  const ticks = [yMin, (yMin + yMax) / 2, yMax].map(v =>
    `<text x="${PADX - 10}" y="${Y(v) + 4}" text-anchor="end">${(+v).toFixed(1)}</text>` +
    `<line class="chart__axis" x1="${PADX}" x2="${W - 16}" y1="${Y(v)}" y2="${Y(v)}" opacity="0.35"/>`
  ).join("");

  const ends = narrow ? [last] : [points[0], last];
  const stamps = ends.map((pt, i) =>
    `<text x="${X(new Date(pt[0]).getTime())}" y="${H - 6}" text-anchor="${(narrow || i) ? "end" : "start"}">` +
    `${esc(fmtTime(pt[0]))}</text>`).join("");

  const dots = points.map(pt =>
    `<circle class="chart__dot" cx="${X(new Date(pt[0]).getTime())}" cy="${Y(pt[1])}" r="2.5"/>`
  ).join("");

  const nowDot =
    `<circle class="chart__now" cx="${X(new Date(last[0]).getTime())}" cy="${Y(last[1])}" r="4.5"/>` +
    `<text x="${X(new Date(last[0]).getTime())}" y="${Y(last[1]) - 12}" text-anchor="end" fill="${ACCENT}">` +
    `${esc(fmtValue(market, last[1]))}</text>`;

  const summary = `${MARKET_LABEL[market]} movement, ` +
    `${points.length} snapshots. Full values are listed in the table below.`;

  box.innerHTML =
    `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(summary)}">` +
    ticks + openLine +
    `<path class="chart__line" d="${path}"/>` + dots + nowDot + stamps +
    `<line class="chart__cursor" x1="0" x2="0" y1="${PADY}" y2="${H - PADY}" opacity="0"/>` +
    `<rect class="chart__hit" x="${PADX}" y="0" width="${W - PADX - 16}" height="${H}"/>` +
    `</svg>` +
    `<div class="tip" hidden><span class="tip__t"></span> <span class="tip__v"></span></div>`;

  attachTip(box, points, market, X, Y);
}

/* Pointer inspection. Keyboard and screen-reader users are not served by a
   hover tooltip, so every value it can show is also in the snapshot table
   below the chart — the tooltip is an accelerator, never the only route. */
function attachTip(box, points, market, X, Y) {
  const svg = box.querySelector("svg.chart");
  const hit = box.querySelector(".chart__hit");
  const cursor = box.querySelector(".chart__cursor");
  const tip = box.querySelector(".tip");
  if (!svg || !hit || !tip) return;

  const times = points.map(pt => new Date(pt[0]).getTime());

  function hide() { tip.hidden = true; cursor.setAttribute("opacity", "0"); }

  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("pointermove", event => {
    const rect = svg.getBoundingClientRect();
    const scale = svg.viewBox.baseVal.width / rect.width;
    const ux = (event.clientX - rect.left) * scale;

    // Nearest snapshot to the pointer, in user units.
    let best = 0, bestDist = Infinity;
    times.forEach((t, i) => {
      const d = Math.abs(X(t) - ux);
      if (d < bestDist) { bestDist = d; best = i; }
    });

    const pt = points[best];
    const px = X(times[best]), py = Y(pt[1]);
    cursor.setAttribute("x1", px);
    cursor.setAttribute("x2", px);
    cursor.setAttribute("opacity", "1");

    // textContent, never innerHTML: upstream values never become markup.
    tip.querySelector(".tip__t").textContent = fmtTime(pt[0]);
    tip.querySelector(".tip__v").textContent = fmtValue(market, pt[1]);
    tip.hidden = false;
    tip.style.left = `${(px / scale)}px`;
    tip.style.top = `${(py / scale) - 10}px`;
  });
}

function drawSnaps(box, payload, market) {
  const snaps = (payload && payload.snapshots) || [];
  const rows = snaps.filter(s => s.spread !== null || s.total !== null || s.ml_away !== null);
  if (!rows.length) { box.innerHTML = ""; return; }

  const body = rows.map(s => `<tr>
      <td>${esc(fmtDay(s.captured_at_utc))}</td>
      <td>${esc(fmtTime(s.captured_at_utc))}</td>
      <td class="num">${esc(s.spread ?? "—")}</td>
      <td class="num">${esc(s.total ?? "—")}</td>
      <td class="num">${esc(s.ml_away ?? "—")} / ${esc(s.ml_home ?? "—")}</td>
      <td>${esc(s.public_book ?? "—")}</td>
    </tr>`).join("");

  box.innerHTML = `<table class="snaps">
      <caption>Every recorded snapshot — the same values the chart plots.</caption>
      <thead><tr>
        <th scope="col">Date</th><th scope="col">Time</th>
        <th scope="col">Spread</th><th scope="col">Total</th>
        <th scope="col">ML away / home</th><th scope="col">Book</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}

/* ---- 5. analysis disclosure ---------------------------------------------
   Expansion is acknowledged before the network is touched: the panel opens and
   shows a sized skeleton, so the layout never jumps when data lands. */
function skeleton(box) {
  box.innerHTML = '<div class="skeleton" aria-hidden="true">' +
    '<div class="skeleton__chart"></div>' +
    '<div class="skeleton__bar" style="width:40%"></div>' +
    '<div class="skeleton__bar" style="width:65%"></div>' +
    "</div>" +
    '<p class="visually-hidden">Loading line history…</p>';
}

function parts(article) {
  const slug = article.id.replace(/^game-/, "");
  return {
    key: article.dataset.game,
    panel: document.getElementById(`analysis-${slug}`),
    chart: document.getElementById(`chart-${slug}`),
    snaps: document.getElementById(`snaps-${slug}`),
    toggle: article.querySelector(".disclose"),
    tabs: Array.from(article.querySelectorAll('[role="tab"]')),
  };
}

function selectMarket(article, market) {
  const { chart, snaps, tabs, key } = parts(article);
  tabs.forEach(tab => {
    const on = tab.dataset.market === market;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1;
    if (on) chart.setAttribute("aria-labelledby", tab.id);
  });
  const payload = historyCache.get(key);
  if (payload) { drawChart(chart, payload, market); drawSnaps(snaps, payload, market); }
  article.dataset.market = market;
}

async function openAnalysis(article, market) {
  const { key, panel, chart, snaps, toggle } = parts(article);
  const wasOpen = toggle.getAttribute("aria-expanded") === "true";

  panel.hidden = false;
  toggle.setAttribute("aria-expanded", "true");
  article.classList.add("is-open");

  const target = market || article.dataset.market || "spread";
  if (!historyCache.has(key) && !wasOpen) skeleton(chart);

  try {
    await loadHistory(key);
    selectMarket(article, target);
  } catch (err) {
    if (err && err.name === "AbortError") return;
    chart.innerHTML = '<p class="empty empty--inline">' +
      "Line history could not be loaded. The rest of this analysis is " +
      "unaffected — it comes from data already on the page." + "</p>";
    snaps.innerHTML = "";
  }
}

function closeAnalysis(article) {
  const { panel, toggle } = parts(article);
  panel.hidden = true;
  toggle.setAttribute("aria-expanded", "false");
  article.classList.remove("is-open");
}

function toggleAnalysis(article, market) {
  const { toggle } = parts(article);
  const open = toggle.getAttribute("aria-expanded") === "true";
  if (open && !market) { closeAnalysis(article); clearHash(); return; }
  openAnalysis(article, market);
  setHash(article.dataset.game, market || article.dataset.market || "spread");
}

/* ---- 6. events ----------------------------------------------------------- */
document.addEventListener("click", event => {
  const marketBtn = event.target.closest("button.market");
  if (marketBtn) {
    const article = marketBtn.closest(".game");
    toggleAnalysis(article, marketBtn.dataset.market);
    return;
  }
  const disclose = event.target.closest(".disclose");
  if (disclose) {
    toggleAnalysis(disclose.closest(".game"), null);
    return;
  }
  const tab = event.target.closest('[role="tab"]');
  if (tab && tab.dataset.market) {
    selectMarket(tab.closest(".game"), tab.dataset.market);
    setHash(tab.closest(".game").dataset.game, tab.dataset.market);
  }
});

/* The six market cells of a game are one composite control, not six tab stops.
   Without this a keyboard user pays 6 stops per game just to pass it by; with
   it each game costs one, and arrows move around the 2x3 matrix — the standard
   roving-tabindex pattern. Progressive enhancement: with JS off every cell
   stays individually tabbable, which still works, just more slowly. */
function marketMatrix(game) {
  return Array.from(game.querySelectorAll(".game__row")).map(row =>
    Array.from(row.querySelectorAll("button.market")));
}

function initRovingMarkets(game) {
  const cells = marketMatrix(game).flat();
  cells.forEach((cell, i) => { cell.tabIndex = i === 0 ? 0 : -1; });
}

document.querySelectorAll(".game").forEach(initRovingMarkets);

document.addEventListener("keydown", event => {
  const cell = event.target.closest("button.market");
  if (!cell) return;
  const step = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] };
  if (!(event.key in step) && event.key !== "Home" && event.key !== "End") return;

  const game = cell.closest(".game");
  const grid = marketMatrix(game);
  let r = grid.findIndex(row => row.includes(cell));
  let c = grid[r].indexOf(cell);

  if (event.key === "Home") c = 0;
  else if (event.key === "End") c = grid[r].length - 1;
  else {
    const [dr, dc] = step[event.key];
    r = Math.min(Math.max(r + dr, 0), grid.length - 1);
    c = Math.min(Math.max(c + dc, 0), grid[r].length - 1);
  }
  const next = grid[r] && grid[r][c];
  if (!next || next === cell) return;
  event.preventDefault();
  cell.tabIndex = -1;
  next.tabIndex = 0;
  next.focus();
});

// Arrow-key movement inside a tablist, per the WAI-ARIA tabs pattern.
document.addEventListener("keydown", event => {
  const tab = event.target.closest('[role="tab"]');
  if (!tab) return;
  const keys = { ArrowRight: 1, ArrowLeft: -1, Home: 0, End: -0 };
  if (!(event.key in keys)) return;
  const list = Array.from(tab.parentElement.querySelectorAll('[role="tab"]'));
  const at = list.indexOf(tab);
  let next;
  if (event.key === "Home") next = list[0];
  else if (event.key === "End") next = list[list.length - 1];
  else next = list[(at + keys[event.key] + list.length) % list.length];
  event.preventDefault();
  next.focus();
  selectMarket(next.closest(".game"), next.dataset.market);
});

/* ---- 7. deep links ------------------------------------------------------- */
function setHash(key, market) {
  const next = `#game=${encodeURIComponent(key)}&market=${encodeURIComponent(market)}`;
  if (window.location.hash !== next) history.replaceState(null, "", next);
}

function clearHash() {
  history.replaceState(null, "", window.location.pathname + window.location.search);
}

function openFromHash() {
  const match = window.location.hash.match(/game=([^&]+)(?:&market=([^&]+))?/);
  if (!match) return;
  // A malformed escape ("#game=%") makes decodeURIComponent throw URIError.
  // This runs at boot, before the hashchange and resize listeners register, so
  // an uncaught throw would orphan both for the rest of the session.
  let key, market;
  try {
    key = decodeURIComponent(match[1]);
    market = match[2] ? decodeURIComponent(match[2]) : "spread";
  } catch {
    return;
  }
  const article = document.querySelector(`.game[data-game="${CSS.escape(key)}"]`);
  if (!article) return;
  openAnalysis(article, market);
  article.scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ---- 8. freshness --------------------------------------------------------
   The /api/status poll is the honest real-time bound: the stamps say exactly
   how old the data is, and the page reloads only when strictly newer betting
   data exists. */
function relTime(ms) {
  const secs = Math.max(0, Math.floor(ms / 1000));
  if (secs < 60) return secs + "s ago";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + "m ago";
  const hours = Math.floor(mins / 60);
  if (hours < 48) return hours + "h " + (mins % 60) + "m ago";
  return Math.floor(hours / 24) + "d " + (hours % 24) + "h ago";
}

// Stamps render via textContent only — timestamp data never becomes markup.
function renderStamp(id, fallbackLabel) {
  const el = document.getElementById(id);
  if (!el) return;
  const at = Date.parse(el.dataset.iso || "");
  if (Number.isNaN(at)) {
    const blank = el.querySelector(".freshness__text");
    if (blank) blank.textContent = ""; else el.textContent = "";
    el.removeAttribute("title");
    return;
  }
  const label = el.dataset.label || fallbackLabel;
  const age = Date.now() - at;
  const slot = el.querySelector(".freshness__text");
  const text = label + " " + relTime(age);
  if (slot) slot.textContent = text; else el.textContent = text;
  el.title = new Date(at).toISOString();
  if (el.classList.contains("freshness")) {
    el.classList.toggle("freshness--stale", age > 60 * 60 * 1000);
  }
}

function renderStamps() {
  renderStamp("updated", "Updated");
  renderStamp("stats-updated", "Statistics as of");
}

const updatedEl = document.getElementById("updated");
const renderedSlateMs = Date.parse((updatedEl && updatedEl.dataset.iso) || "");

// Older of the two split timestamps: the stats stamp never overstates the
// freshness of its stalest split (close cousin of web.py's _older_iso).
function olderIso(a, b) {
  if (!a || !b) return a || b || "";
  return Date.parse(a) <= Date.parse(b) ? a : b;
}

// Reload only when betting data is STRICTLY newer than what this page
// rendered, at most once per 2 minutes. The guard timestamp lives in
// sessionStorage so it survives the reload it causes — an in-memory guard
// would reset on reload and loop.
//
// A page rendered with NO slate stamp (empty betting window, or the degraded
// db_ok=false render) has renderedSlateMs = NaN. That page must still reload
// once valid data exists.
function maybeReload(iso) {
  const fresh = Date.parse(iso || "");
  if (Number.isNaN(fresh)) return;
  if (!Number.isNaN(renderedSlateMs) && fresh <= renderedSlateMs) return;
  const KEY = "odl-status-reload-at";
  let last = 0;
  try { last = Number(sessionStorage.getItem(KEY)) || 0; } catch { /* storage off */ }
  if (Date.now() - last < 2 * 60 * 1000) return;
  try { sessionStorage.setItem(KEY, String(Date.now())); } catch { /* storage off */ }
  location.reload();
}

async function pollStatus() {
  let status;
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (!r.ok) return;
    status = await r.json();
  } catch { return; }

  const betting = status && status.betting;
  const teamStats = status && status.team_stats;

  if (updatedEl) {
    updatedEl.classList.toggle("freshness--down", status && status.db_ok === false);
    if (PAGE === "dashboard" && betting && betting.fetched_at_utc) {
      updatedEl.dataset.iso = betting.fetched_at_utc;
    }
  }
  const statsEl = document.getElementById("stats-updated");
  if (statsEl && teamStats) {
    statsEl.dataset.iso = olderIso(
      teamStats.last7 && teamStats.last7.updated_at,
      teamStats.ytd && teamStats.ytd.updated_at);
  }
  renderStamps();
  // Only the dashboard renders the slate, so only the dashboard reloads for
  // it. On other pages the header stamp tracks team stats, and comparing it
  // against a betting timestamp would reload forever.
  if (PAGE === "dashboard" && betting) maybeReload(betting.fetched_at_utc);
}

/* ---- 9. boot ------------------------------------------------------------- */
renderStamps();
setInterval(renderStamps, 1000);      // relative stamps tick every second
setInterval(pollStatus, 60 * 1000);   // a 60s no-store poll IS the honest bound
setInterval(() => location.reload(), 60 * 60 * 1000);  // last-resort fallback
openFromHash();
window.addEventListener("hashchange", openFromHash);

// Keep the chart's user units at 1:1 with CSS pixels across resizes.
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    document.querySelectorAll(".game.is-open").forEach(game =>
      selectMarket(game, game.dataset.market || "spread"));
  }, 150);
});
