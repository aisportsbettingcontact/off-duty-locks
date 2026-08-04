/* Off Duty Locks dashboard: detail panel + inline-SVG line-history chart +
   rankings tabs. Vanilla JS, no dependencies; every state change ~140ms. */
"use strict";

const ACCENT = "#FF5C1C";
const MUTED = "#6B7280";

const detail = document.getElementById("detail");
const chartBox = document.getElementById("chart");
const snapsBox = document.getElementById("snaps");
const titleEl = document.getElementById("detail-title");
let history = null;
let market = "spread";

// Scraped strings (team names, book labels, snapshot values) are interpolated
// into innerHTML below; entity-escape them so upstream data can never become
// markup. Numbers pass through String() unchanged.
function esc(v) {
  return String(v).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET";
}

function seriesFor(mkt) {
  if (!history) return { points: [], opening: null, label: "" };
  const snaps = history.snapshots || [];
  if (mkt === "spread") return { points: snaps.map(s => [s.captured_at_utc, s.spread]), opening: history.opening.spread, label: "Spread (away)" };
  if (mkt === "total") return { points: snaps.map(s => [s.captured_at_utc, s.total]), opening: history.opening.total, label: "Total" };
  return { points: snaps.map(s => [s.captured_at_utc, s.ml_away]), opening: history.opening.ml_away, label: "Moneyline (away)" };
}

function drawChart() {
  const { points, opening, label } = seriesFor(market);
  const data = points.filter(p => p[1] !== null && p[1] !== undefined);
  if (!data.length) {
    chartBox.innerHTML = '<p class="empty">No movement history yet. Snapshots record on each scrape run (about every 30 minutes as GitHub delivers them).' +
      (opening !== null && opening !== undefined ? ` Opening ${label.toLowerCase()}: <strong class="num">${esc(opening)}</strong>.` : "") + "</p>";
    return;
  }
  const W = 720, H = 220, PAD = 42;
  const xs = data.map(p => new Date(p[0]).getTime());
  const ys = data.map(p => p[1]).concat(opening !== null && opening !== undefined ? [opening] : []);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const X = t => PAD + ((t - x0) / (x1 - x0 || 1)) * (W - 2 * PAD);
  const Y = v => H - PAD - ((v - yMin) / (yMax - yMin)) * (H - 2 * PAD);

  let path = "";
  data.forEach((p, i) => {
    const px = X(new Date(p[0]).getTime()), py = Y(p[1]);
    path += i === 0 ? `M${px},${py}` : `H${px}V${py}`; // step chart
  });

  const dots = data.map(p =>
    `<circle cx="${X(new Date(p[0]).getTime())}" cy="${Y(p[1])}" r="3.5" fill="${ACCENT}"><title>${esc(fmtTime(p[0]))}: ${esc(p[1])}</title></circle>`).join("");
  const openLine = (opening !== null && opening !== undefined)
    ? `<line x1="${PAD}" x2="${W - PAD}" y1="${Y(opening)}" y2="${Y(opening)}" stroke="${MUTED}" stroke-dasharray="4 4"/>` +
      `<text x="${W - PAD}" y="${Y(opening) - 6}" fill="${MUTED}" font-size="11" text-anchor="end">open ${esc(opening)}</text>`
    : "";
  const yTicks = [yMin, (yMin + yMax) / 2, yMax].map(v =>
    `<text x="${PAD - 8}" y="${Y(v) + 4}" fill="${MUTED}" font-size="11" text-anchor="end" class="num">${(+v).toFixed(1)}</text>`).join("");
  const tLabels = [data[0], data[data.length - 1]].map(p =>
    `<text x="${X(new Date(p[0]).getTime())}" y="${H - PAD + 18}" fill="${MUTED}" font-size="11" text-anchor="middle">${esc(fmtTime(p[0]))}</text>`).join("");

  chartBox.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${label} movement">` +
    `<line x1="${PAD}" x2="${W - PAD}" y1="${H - PAD}" y2="${H - PAD}" stroke="#26262B"/>` +
    openLine + yTicks + tLabels +
    `<path d="${path}" fill="none" stroke="${ACCENT}" stroke-width="2"/>` + dots +
    `</svg>`;
}

function drawSnaps() {
  const snaps = (history && history.snapshots) || [];
  if (!snaps.length) { snapsBox.innerHTML = ""; return; }
  const rows = snaps.map(s => `<tr>
    <td>${esc(fmtTime(s.captured_at_utc))}</td>
    <td class="num">${esc(s.spread ?? "—")}</td>
    <td class="num">${esc(s.total ?? "—")}</td>
    <td class="num">${esc(s.ml_away ?? "—")} / ${esc(s.ml_home ?? "—")}</td>
    <td class="num">${esc(s.spread_pct_bets_away ?? "—")}% / ${esc(s.spread_pct_bets_away != null ? 100 - s.spread_pct_bets_away : "—")}%</td>
    <td class="num">${esc(s.spread_pct_money_away ?? "—")}% / ${esc(s.spread_pct_money_away != null ? 100 - s.spread_pct_money_away : "—")}%</td>
    <td>${esc(s.public_book ?? "—")}</td>
  </tr>`).join("");
  snapsBox.innerHTML = `<table class="snaps">
    <thead><tr><th>Time</th><th>Spread</th><th>Total</th><th>ML a/h</th><th>Tickets a/h</th><th>Money a/h</th><th>Book</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function openGame(key) {
  detail.hidden = false;
  requestAnimationFrame(() => detail.classList.add("open")); // materialize (fade + rise)
  titleEl.textContent = "Line History: " + key.split(":").pop();
  chartBox.innerHTML = '<p class="empty">Loading…</p>';
  snapsBox.innerHTML = "";
  try {
    const r = await fetch(`/api/games/${encodeURIComponent(key)}/history`);
    if (!r.ok) throw new Error(String(r.status));
    history = await r.json();
  } catch {
    history = null;
    chartBox.innerHTML = '<p class="empty">History unavailable.</p>';
    return;
  }
  drawChart();
  drawSnaps();
  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  window.location.hash = "game=" + encodeURIComponent(key);
}

document.querySelectorAll(".games tbody tr[data-game-key]").forEach(tr => {
  tr.addEventListener("click", () => openGame(tr.dataset.gameKey));
  tr.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openGame(tr.dataset.gameKey);
    }
  });
});

document.querySelectorAll(".detail .tabs button").forEach(btn =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".detail .tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    market = btn.dataset.market;
    drawChart();
  }));

document.querySelectorAll(".rank-tabs button").forEach(btn =>
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".rank-tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const body = document.querySelector("#ranks tbody");
    try {
      const r = await fetch(`/api/team-stats?split=${btn.dataset.split}`);
      const data = await r.json();
      body.innerHTML = (data.teams || []).map((t, i) => `<tr>
        <td class="rank">${i + 1}</td><td>${esc(t.team_name)}</td>
        <td class="num">${t.offensive_rating?.toFixed?.(1) ?? "—"}</td>
        <td class="num">${t.possessions?.toFixed?.(1) ?? "—"}</td>
        <td class="num">${t.points?.toFixed?.(1) ?? "—"}</td>
        <td class="num">${esc(t.wins ?? "—")}-${esc(t.losses ?? "—")}</td></tr>`).join("") ||
        '<tr><td colspan="6" class="empty">No team stats published yet.</td></tr>';
    } catch {
      body.innerHTML = '<tr><td colspan="6" class="empty">Rankings unavailable.</td></tr>';
    }
  }));

const m = window.location.hash.match(/game=([^&]+)/);
if (m) openGame(decodeURIComponent(m[1]));

/* ---- Real-time freshness: precision stamps + /api/status poll ----------- */

// Relative time, precision-graded: seconds under a minute, minutes under an
// hour, then hours+minutes. Never a vague catch-all label.
function relTime(ms) {
  const secs = Math.max(0, Math.floor(ms / 1000));
  if (secs < 60) return secs + "s ago";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + "m ago";
  return Math.floor(mins / 60) + "h " + (mins % 60) + "m ago";
}

// Stamps render via textContent only — timestamp data never becomes markup.
function renderStamp(id, label) {
  const el = document.getElementById(id);
  if (!el) return;
  const at = Date.parse(el.dataset.iso || "");
  if (Number.isNaN(at)) { el.textContent = ""; el.removeAttribute("title"); return; }
  el.textContent = label + " " + relTime(Date.now() - at);
  el.title = new Date(at).toISOString(); // absolute UTC on hover
}

function renderStamps() {
  renderStamp("updated", "Slate updated");
  renderStamp("stats-updated", "Stats as of");
}

// The slate timestamp this page actually rendered from — the reference for
// the strictly-newer reload comparison below.
const updatedEl = document.getElementById("updated");
const renderedSlateMs = Date.parse((updatedEl && updatedEl.dataset.iso) || "");

// Older of the two split timestamps: the stats stamp never overstates the
// freshness of its stalest split (mirrors _older_iso in web.py).
function olderIso(a, b) {
  if (!a || !b) return a || b || "";
  return Date.parse(a) <= Date.parse(b) ? a : b;
}

// Reload only when betting data is STRICTLY newer than what this page
// rendered, at most once per 2 minutes. The guard timestamp lives in
// sessionStorage so it survives the reload it causes — an in-memory guard
// would reset on reload and loop.
function maybeReload(iso) {
  const fresh = Date.parse(iso || "");
  if (Number.isNaN(fresh) || Number.isNaN(renderedSlateMs) || fresh <= renderedSlateMs) return;
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
  if (updatedEl && betting && betting.fetched_at_utc) {
    updatedEl.dataset.iso = betting.fetched_at_utc;
  }
  const statsEl = document.getElementById("stats-updated");
  if (statsEl && teamStats) {
    statsEl.dataset.iso = olderIso(
      teamStats.last7 && teamStats.last7.updated_at,
      teamStats.ytd && teamStats.ytd.updated_at);
  }
  renderStamps();
  if (betting) maybeReload(betting.fetched_at_utc);
}

renderStamps();
setInterval(renderStamps, 1000);    // relative stamps tick every second
setInterval(pollStatus, 60 * 1000); // a 60s no-store poll IS the honest real-time bound

// Data lands on each scrape run (about every 30 minutes as GitHub delivers
// them); the status poll above reloads the page as soon as strictly newer
// betting data exists, so this hard reload is only a last-resort fallback.
setInterval(() => location.reload(), 60 * 60 * 1000);
