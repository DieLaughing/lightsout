// Lights Out web app. No build step: plain modules, hash routing.
// Settings (series, follows, calendar kinds, alerts) live on your account so alerts know who you follow;
// the first visit after that change copies this browser's old settings up.
import { matchName, normalise, splitNames } from "./match.js?v=2";

const view = document.getElementById("view");

const SYNCED = ["series", "follows", "kinds", "dismissed", "notify", "seenSeries", "seenChanges", "units", "animations", "odds", "nickname", "show", "home", "nearMiles"];
let SETTINGS = null;      // from /api/me once signed in
let saveTimer = null;

function local(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}

const store = {
  get(key, fallback) {
    if (SETTINGS && SYNCED.includes(key)) return SETTINGS[key] === undefined ? fallback : SETTINGS[key];
    return local(key, fallback);
  },
  set(key, value) {
    if (SETTINGS && SYNCED.includes(key)) {
      if (JSON.stringify(SETTINGS[key]) === JSON.stringify(value)) return;   // unchanged: no save (it drops the Wire copies)
      SETTINGS[key] = value;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => saveSettings().catch(() => {}), 400);
    }
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

const saveSettings = () => {
  for (const k of GET_CACHE.keys()) if (k.startsWith("/api/wire")) GET_CACHE.delete(k);  // "mine" depends on follows
  return fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(SETTINGS) }).then(r => r.json());
};

// A series added to the app later (GT World Challenge, 2026-10-12) is switched on once for everyone who has
// a saved series list; after that, their own choice stands.
function switchOnNewSeries() {
  const chosen = store.get("series", null);
  const seen = store.get("seenSeries", null);
  const all = SERIES.map(s => s.id);
  if (!chosen) { store.set("seenSeries", all); return; }   // no saved list: the defaults already include it
  // Before this existed (2026-10-12), everyone had seen every series except the ones added since.
  const known = new Set(seen || all.filter(id => !["gtwce", "nls"].includes(id)));
  const fresh = SERIES.filter(s => s.default_on && !known.has(s.id)).map(s => s.id);
  if (fresh.length) store.set("series", [...chosen, ...fresh]);
  store.set("seenSeries", all);
}

async function loadSettings() {
  if (ME.settings) { SETTINGS = ME.settings; return; }
  // Never saved on the server: bring this browser's settings along.
  SETTINGS = {};
  for (const k of SYNCED) {
    const v = local(k, undefined);
    if (v !== undefined) SETTINGS[k] = v;
  }
  if (Object.keys(SETTINGS).length) await saveSettings().catch(() => {});
}

const KINDS = [
  ["practice", "Practice"], ["qualifying", "Qualifying"], ["sprint", "Sprints"],
  ["race", "Races"], ["rally_day", "Rally days"], ["event", "Multi-day events"],
];

let SERIES = [];          // from /api/series
let BY_ID = {};
let CONFIG = null;

const enabledSeries = () => store.get("series", SERIES.filter(s => s.default_on).map(s => s.id))
  .filter(id => BY_ID[id]);
const follows = () => store.get("follows", []);
// Where each followed name is racing, from /api/follow-status (all grids + entry lists).
let FOLLOW_STATUS = [];
async function loadFollowStatus() {
  const names = follows().map(f => f.name);
  FOLLOW_STATUS = names.length ? await fetch("/api/follow-status", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names }) }).then(r => r.json()).catch(() => []) : [];
  return FOLLOW_STATUS;
}
let FOLLOW_FIRST = null;   // the startup follow-status load, until a page has waited for it
const followedSeries = () => new Set(FOLLOW_STATUS.flatMap(f => f.series));
const dismissed = () => new Set(store.get("dismissed", []));
const newKey = (name, f) => `${name}|${f.series}|${f.event_key || "grid"}`;

// ---- helpers ---------------------------------------------------------------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Cloudflare's codes for "the Pi can't be reached" (a bug in the app is a plain 500): show the offline page.
const PI_DOWN = status => [502, 503, 504, 520, 521, 522, 523, 524, 530].includes(status);
const goOffline = () => location.replace(`/offline.html?back=${encodeURIComponent(location.href)}`);
// The phone's own copy of every page's data (path -> {at, data}): tabs open from it instantly (see fast()).
const GET_CACHE = new Map();
const SHOW_COPY_FOR = 30 * 60000;   // older copies wait for fresh data instead
const FRESH_WAIT = 150;             // ms a page waits for fresh data before drawing the phone's copy
const api = path => fetch(path, { headers: { Accept: "application/json" } }).then(r => {
  if (PI_DOWN(r.status)) goOffline();
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}).then(data => { GET_CACHE.set(path, { at: Date.now(), data }); return data; });

// A page's data, fast: fresh data if it lands within FRESH_WAIT (so the page draws once, current), otherwise the
// phone's copy straight away and one quiet redraw (after any page animation) if the fresh data differs.
const LIVE_COPY = 60000;
// `maxCopy`: the oldest phone copy worth drawing (live status: a minute, so a finished session's Live button doesn't
// flash up from an old copy).
async function fast(path, maxCopy = SHOW_COPY_FOR) {
  const hit = GET_CACHE.get(path), age = hit ? Date.now() - hit.at : Infinity;
  if (age < 3000) return hit.data;                              // just fetched (this is the redraw)
  if (age > maxCopy || QUIET_ROUTE) return api(path);
  const at = location.hash, fresh = api(path);
  const first = await Promise.race([fresh.then(d => ({ d }), () => null), new Promise(r => setTimeout(() => r(null), FRESH_WAIT))]);
  if (first) return first.d;
  fresh.then(async d => {
    if (location.hash !== at || JSON.stringify(d) === JSON.stringify(hit.data)) return;
    await Promise.all(view.getAnimations().map(a => a.finished.catch(() => {})));
    if (location.hash === at) route(true);
  }).catch(() => {});
  return hit.data;
}

function localDay(s) {
  // TBA sessions only have the event's local date; timed ones use the viewer's time zone.
  if (s.start_utc) {
    const d = new Date(s.start_utc);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  return s.day;
}

function dayLabel(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((date - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

const shortDate = iso => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" }); };

function timeCell(s) {
  if (s.start_utc) {
    const t = new Date(s.start_utc).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    return `<div class="time">${esc(t)}</div>`;
  }
  if (s.kind === "event") return `<div class="time tba">Event</div>`;
  if (s.kind === "rally_day") return `<div class="time tba">All day</div>`;
  return `<div class="time tba">Time TBA</div>`;
}

const isLive = s => s.start_utc && Date.now() >= Date.parse(s.start_utc) && Date.now() < Date.parse(s.end_utc);

function chip(id) {
  const s = BY_ID[id];
  const light = ["#ffcc00", "#c8a200"].includes(s.color) ? " light" : "";
  return `<span class="chip${light}" style="--c:${s.color}">${esc(s.short)}</span>`;
}

function badge(w) {
  if (!w) return "";
  const word = w.badge === "PAID" ? "PAID ONLY" : w.badge;
  const inner = `<b>${esc(word)}</b><span>${esc(w.label)}</span>`;
  const title = esc(w.note || "");
  return w.url
    ? `<a class="badge ${esc(w.badge)}" href="${esc(w.url)}" target="_blank" rel="noopener" title="${title}">${inner}</a>`
    : `<span class="badge ${esc(w.badge)}" title="${title}">${inner}</span>`;
}

// Forecast at the track (sessions in the next 7 days). Open-Meteo weather codes -> icon.
const WX = [[0, "☀️", "Clear"], [2, "🌤️", "Partly cloudy"], [3, "☁️", "Cloudy"], [48, "🌫️", "Fog"], [57, "🌦️", "Drizzle"],
  [67, "🌧️", "Rain"], [77, "❄️", "Snow"], [82, "🌦️", "Showers"], [86, "🌨️", "Snow showers"], [99, "⛈️", "Thunderstorms"]];
const deg = c => Math.round(store.get("units", "f") === "c" ? c : c * 9 / 5 + 32);
function weatherTag(w) {
  if (!w) return "";
  const [, icon, text] = WX.find(([max]) => w.code <= max) || [0, "", ""];
  const unit = store.get("units", "f") === "c" ? "°C" : "°F";
  const t = w.c != null ? `${deg(w.c)}${unit}` : `${deg(w.lo)}–${deg(w.hi)}${unit}`;
  const rain = w.rain >= 10 ? ` <span class="rain">${w.rain}%</span>` : "";
  return ` <span class="wx" title="${esc(text)}${w.rain != null ? `, ${w.rain}% chance of rain` : ""} (forecast)">${icon} ${t}${rain}</span>`;
}

function sessionRow(s, followed) {
  const extra = s.kind === "event" && s.end_day ? ` · through ${esc(shortDate(s.end_day))}` : "";
  return `<div class="row${followed.has(s.series) ? " followed" : ""}">
    ${timeCell(s)}
    <div class="what">
      <div class="ev">${s.event_key && s.series ? `<a class="ev-link" href="#/event/${s.series}/${encodeURIComponent(s.event_key)}">${esc(s.event_name)}</a>` : esc(s.event_name)}</div>
      <div class="ses">${chip(s.series)} ${esc(s.name)}${extra}${isLive(s) ? ' <span class="live">LIVE</span>' : ""}${weatherTag(s.weather)}${s.tickets && !isLive(s)
        ? ` <a class="tag tix" href="${esc(s.tickets.url)}" target="_blank" rel="noopener" title="${esc(s.tickets.label)}">Tickets ↗</a>` : ""}</div>
    </div>
    ${badge(s.watch)}
  </div>`;
}

// Upcoming groups a day's sessions into one card per race weekend: the series, event and watch badge once, then
// one short line per session. A badge that differs between sessions (F1 practice free, races paid) stays per line.
function eventCards(rows, followed) {
  const groups = new Map();
  for (const s of rows) {
    const k = `${s.series}|${s.event_key || s.event_name}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(s);
  }
  return [...groups.values()].map(g => eventCard(g, followed)).join("");
}

// ---- Places: "Braselton, GA", track pages, distance from home ("Near me") -----------------------------
const home = () => store.get("home", null);
const nearMiles = () => store.get("nearMiles", 300);
function milesTo(p) {
  const h = home();
  if (!h || !p) return null;
  const r = x => x * Math.PI / 180, dLat = r(p.lat - h.lat), dLon = r(p.lon - h.lon);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(r(h.lat)) * Math.cos(r(p.lat)) * Math.sin(dLon / 2) ** 2;
  return 3958.8 * 2 * Math.asin(Math.sqrt(a));
}
const km = () => store.get("units", "f") === "c";
const distLabel = mi => km() ? `${Math.round(mi * 1.609).toLocaleString()} km` : `${Math.round(mi).toLocaleString()} mi`;
// Distance only where it helps (a drive, not an ocean away).
function distTag(p) {
  const mi = milesTo(p);
  return mi != null && mi <= 1000 ? ` · <span class="dist">${mi < 15 ? "nearby" : `${distLabel(mi)} away`}</span>` : "";
}
const placeLink = p => `<a class="place-link" href="#/track/${esc(p.slug)}">${esc(p.label)}</a>`;
// "Michelin Raceway Road Atlanta · Braselton, GA": the venue when it adds something, then the town as a link.
function placeLine(venue, eventName, p, fallbackCountry) {
  if (!p) return esc([venue, fallbackCountry].filter(Boolean).join(", "));
  const town = norm(p.label.split(",")[0]);
  const showVenue = venue && !norm(eventName).includes(norm(venue)) && !norm(venue).includes(town);
  return `${showVenue ? `${esc(venue)} · ` : ""}${placeLink(p)}${distTag(p)}`;
}

function eventCard(g, followed) {
  const s0 = g[0];
  const sameWatch = g.every(s => JSON.stringify(s.watch || null) === JSON.stringify(s0.watch || null));
  const name = s0.event_key && s0.series
    ? `<a class="ev-link" href="#/event/${s0.series}/${encodeURIComponent(s0.event_key)}">${esc(s0.event_name)}</a>` : esc(s0.event_name);
  const place = placeLine(s0.venue && !norm(s0.event_name).includes(norm(s0.venue)) ? s0.venue : "", s0.event_name, s0.place, s0.country);
  const tix = g.find(s => s.tickets && !isLive(s))?.tickets;
  return `<div class="card evc${followed.has(s0.series) ? " followed" : ""}">
    <div class="evc-head"><div class="evc-title"><div class="ev">${chip(s0.series)} ${name}</div>
      ${place || tix ? `<div class="muted small">${place}${tix ? `${place ? " · " : ""}<a class="tag tix" href="${esc(tix.url)}" target="_blank" rel="noopener" title="${esc(tix.label)}">Tickets ↗</a>` : ""}</div>` : ""}</div>
      ${sameWatch ? badge(s0.watch) : ""}</div>
    ${g.map(s => {
      const extra = s.kind === "event" && s.end_day ? ` · through ${esc(shortDate(s.end_day))}` : "";
      return `<div class="evc-row${RACE_KINDS.has(s.kind) ? " race" : ""}">${timeCell(s)}
        <div class="evc-ses">${esc(s.name)}${extra}${isLive(s) ? ' <span class="live">LIVE</span>' : ""}${weatherTag(s.weather)}</div>
        ${sameWatch ? "" : badge(s.watch)}</div>`;
    }).join("")}
  </div>`;
}
const norm = t => (t || "").toLowerCase().normalize("NFD").replace(/[^a-z0-9]+/g, " ").trim();

// Per-series "Show on Upcoming" (Series page / Series list): everything, qualifying & races, or races only.
const SHOW_LEVELS = [["all", "All sessions"], ["qualifying", "Quali + races"], ["races", "Races only"]];
const showLevel = id => store.get("show", {})[id] || "all";
function shows(s) {
  const level = showLevel(s.series);
  if (level === "all" || RACE_KINDS.has(s.kind)) return true;
  return level === "qualifying" && s.kind === "qualifying";
}
function setShowLevel(id, level) {
  const all = { ...store.get("show", {}) };
  if (level === "all") delete all[id]; else all[id] = level;
  store.set("show", all);
}

// "← Back" on pages you reach from somewhere else: goes back if you came from inside the app, else to `fallback`.
let CAME_FROM_APP = false;
const backLink = (fallback, label = "Back") => `<a class="back" href="${fallback}" data-back><svg class="back-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg><span>${esc(label)}</span></a>`;
document.addEventListener("click", e => {
  const a = e.target.closest("a[data-back]");
  if (a && CAME_FROM_APP) { e.preventDefault(); history.back(); }
});

// "Loading…" only when a page takes a moment: fast loads swap straight to the new page, no flash.
function showLoading(text) {
  const before = view.firstElementChild;
  setTimeout(() => { if (view.firstElementChild === before) view.innerHTML = `<div class="empty">${text}</div>`; }, 350);
}

// idiomorph (web/idiomorph.js) lets a redraw change only what moved. Fetched after startup or by the live page; until it
// lands, redraws replace the page as before.
let morph = null;
const loadMorph = () => morph || import("./idiomorph.js?v=1").then(m => { morph = m.Idiomorph.morph; }).catch(() => {});
// An opened or closed <details> stays the way the person left it.
const MORPH = { morphStyle: "innerHTML",
  callbacks: { beforeAttributeUpdated: (name, el) => !(name === "open" && el.tagName === "DETAILS") } };
// Quiet redraws (route(true): fresher data, a profile part landing, Home's minute tick) change only what moved, so an
// open section, a scroll or a tap in progress survives; navigations replace the page.
function paint(html) {
  if (QUIET_ROUTE && morph) morph(view, html, MORPH);
  else view.innerHTML = html;
}
// Pages that paint() put their listeners on #view, not on controls (a morph keeps controls, so theirs would pile up):
// each draw takes a fresh set, and every navigation drops the last page's.
let viewOff = new AbortController();
function listen() { viewOff.abort(); viewOff = new AbortController(); return { signal: viewOff.signal }; }

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active", a.dataset.nav === name));
  movePill();
}

// The bottom bar's highlight is one pill that slides (with a little spring) to the active tab.
function movePill() {
  const bar = document.getElementById("tabs"), on = bar?.querySelector("a.active");
  if (!bar) return;
  let pill = bar.querySelector(".tab-pill");
  if (!pill) { pill = document.createElement("span"); pill.className = "tab-pill"; bar.prepend(pill); }
  pill.classList.toggle("still", !motionOn() || !pill.dataset.placed);
  if (!on) { pill.style.opacity = "0"; return; }
  pill.style.opacity = "1";
  pill.style.width = `${on.offsetWidth}px`;
  pill.style.transform = `translateX(${on.offsetLeft}px)`;
  pill.dataset.placed = "1";
}
window.addEventListener("resize", () => movePill());

// Scrolling down folds the bottom bar's icons away and slides the top bar off; any scroll up (or the top of the
// page, or a new page) brings both back. One passive listener, read once per frame.
let lastScrollY = 0, scrollQueued = false;
function setCompact(on) {
  const bar = document.getElementById("tabs");
  document.querySelector(".top")?.classList.toggle("tucked", on);
  if (bar && bar.classList.contains("compact") !== on) { bar.classList.toggle("compact", on); requestAnimationFrame(movePill); }
}
window.addEventListener("scroll", () => {
  if (scrollQueued) return;
  scrollQueued = true;
  requestAnimationFrame(() => {
    scrollQueued = false;
    const y = window.scrollY, dy = y - lastScrollY;
    if (y < 40) setCompact(false);
    else if (dy > 6) setCompact(true);
    else if (dy < -2) setCompact(false);
    lastScrollY = y;
  });
}, { passive: true });

// ---- views ------------------------------------------------------------------
// ---- Following: one hero card per championship each followed name is in ------------------------------------
// Big points, position, wins (where the series publishes them) and season form, coloured by team (colors.yaml).
// Names with no championship row (pick-your-events rallies, one-off entries) get a slim card with what's next.
function heroCards(cards) {
  const hidden = dismissed();
  const byName = new Map();
  for (const c of cards || []) byName.set(c.name, [...(byName.get(c.name) || []), c]);
  const items = FOLLOW_STATUS.flatMap(f => {
    const news = f.new.filter(n => !hidden.has(newKey(f.name, n))).map(n => `<div class="news">NEW · ${n.kind === "entry"
      ? `Entered ${esc(n.event_name)}` : `Joined the ${esc(BY_ID[n.series]?.name || n.series)} grid`}
      <button class="link" data-dismiss="${esc(newKey(f.name, n))}" aria-label="Dismiss">✕</button></div>`).join("");
    const pend = f.pending?.[0];
    // A follow can race in several series (a team name, a driver's GT side gig): say which one "Next" is.
    const nextIn = sid => f.next && f.next.series !== sid ? `${chip(f.next.series)} ` : "";
    const next = sid => f.next ? `Next: ${nextIn(sid)}<b>${esc(f.next.event_name)}</b> · ${esc(f.next.name)} · ${esc(dayLabel(localDay(f.next)))}`
      : pend ? `Registered for ${esc(BY_ID[pend.series]?.short)} · next rally ${esc(pend.event_name)}, ${esc(dayLabel(pend.day))} (entry list not out yet)`
      : f.found.length ? "Nothing scheduled yet" : "Not on any current grid or published entry list. Still watching every series.";
    const href = `#/driver/${encodeURIComponent(f.name)}`;
    const mine = byName.get(f.name) || [];
    const foot = (first, sid) => `<div class="hero-foot">${first ? news : ""}<div class="hero-next">${next(sid)}</div></div>`;
    if (!mine.length) {
      return [`<div class="hero slim" data-href="${href}" style="--tc:${esc(BY_ID[f.series[0]]?.color || "var(--muted)")}">
        <div class="hero-top">${f.series.map(chip).join(" ")}</div>
        <a class="hero-name who-link" href="${href}"><b>${esc(f.name)}</b> ›</a>${foot(true, f.series[0])}</div>`];
    }
    return mine.map((c, i) => {
      const last = (c.row || c.name).trim().split(/\s+/).pop().toLowerCase();
      return `<div class="hero" data-href="${href}" style="--tc:${esc(c.color)}">
        <span class="hero-bg" aria-hidden="true">${esc(last)}</span>
        <div class="hero-top">${chip(c.series)}${c.team ? `<span class="hero-team">${esc(c.team)}</span>` : ""}</div>
        <a class="hero-name who-link" href="${href}"><b>${esc(c.row || c.name)}</b> ›</a>
        <div class="hero-pts"><b>${pts(c.points)}</b><span>PTS</span></div>
        <div class="hero-stats">${heroStats(c)}</div>
        ${formBars(c.form)}${foot(i === 0, c.series)}</div>`;
    });
  });
  // Two columns with a mouse: an odd count makes your first follow a full-width card, so no row is left half empty.
  return `<div class="heroes${items.length % 2 ? " odd" : ""}">${items.join("")}</div>`;
}
const pts = p => Number.isInteger(p) ? String(p) : Number(p).toFixed(1);
const heroStats = c => [`<span><b>P${c.pos}</b> ${c.kind === "class" ? "in class" : c.kind === "team" ? "in the teams' championship" : "in the championship"}</span>`,
  c.wins != null ? `<span><b>${c.wins}</b> ${c.wins === 1 ? "win" : "wins"}</span>` : "",
  c.behind != null ? `<span class="muted">−${pts(c.behind)} to the leader</span>` : c.pos === 1 ? `<span class="muted">Leading</span>` : ""].join("");

// Season form: a bar per round, tall for a good day. Points when the series scores rounds, else finishing position.
function formBars(form) {
  if (!form?.length || form.length < 2) return "";
  const scored = form.some(r => r.points > 0);
  const top = Math.max(...form.map(r => scored ? r.points : 0), 1);
  const field = Math.max(...form.map(r => parseInt(r.pos) || 0), 1);
  const bars = form.slice(-24).map(r => {
    const p = parseInt(r.pos);
    const h = !r.pos ? 0 : scored ? r.points / top : p ? (field + 1 - p) / field : 0;
    const label = `${r.event}: ${!r.pos ? "didn't race" : p ? `P${p}` : r.pos}${scored && r.pos ? `, ${pts(r.points)} pts` : ""}`;
    return `<i class="${!r.pos ? "miss" : p === 1 ? "win" : ""}" style="--h:${Math.max(h, .06).toFixed(3)}" title="${esc(label)}"></i>`;
  }).join("");
  return `<div class="formbars" role="img" aria-label="Season form, ${form.length} rounds">${bars}</div>`;
}

// Next up: a big countdown to the next session that counts (qualifying, sprint, race, rally day) in a series you
// follow, else in any series on Upcoming. Practice and test days only when nothing else is timed.
function countdown(upcoming, followed) {
  const now = Date.now();
  const timed = upcoming.filter(s => s.start_utc && Date.parse(s.start_utc) > now);
  const counts = timed.filter(s => s.kind !== "practice");
  const s = counts.find(x => followed.has(x.series)) || counts[0] || timed[0];
  if (!s) return "";
  const mins = Math.max(0, Math.floor((Date.parse(s.start_utc) - now) / 60000));
  const d = Math.floor(mins / 1440), h = Math.floor(mins % 1440 / 60), m = mins % 60;
  const part = (n, u) => `<b>${String(n).padStart(2, "0")}</b><small>${u}</small>`;
  const time = d ? part(d, "d") + part(h, "h") + part(m, "min") : h ? part(h, "h") + part(m, "min") : part(m, "min");
  return `<a class="countdown" href="#/event/${esc(s.series)}/${encodeURIComponent(s.event_key)}" style="--tc:${esc(BY_ID[s.series]?.color || "var(--accent)")}">
    <div class="cd-label">${chip(s.series)} <span><b>${esc(s.event_name)}</b> · ${esc(s.name)} starts in</span></div>
    <div class="cd-time">${time}</div></a>`;
}

let BANNER = "";   // the home banner's last text, so a redraw doesn't make the page jump while it's re-checked
const bannerHtml = b => b ? `<div class="getapp"><div>${b}</div><a class="btn primary" href="#/app">Show me how</a><button class="link x" id="hidebanner" aria-label="Hide">✕</button></div>` : "";

async function homeView() {
  setNav("home");
  const ids = enabledSeries();
  if (!ids.length) {
    view.innerHTML = `<div class="empty">No series switched on. <a href="#/settings">Pick some in Settings</a>.</div>`;
    return;
  }
  // Follow status is loaded at startup (FOLLOW_FIRST: waited for with this page's data) and when follows change; on
  // later visits it refreshes behind the scenes.
  const first = FOLLOW_FIRST;
  FOLLOW_FIRST = null;
  if (!first && !QUIET_ROUTE) {
    const before = JSON.stringify(FOLLOW_STATUS);
    loadFollowStatus().then(now => { if (JSON.stringify(now) !== before && (location.hash || "#/") === "#/") route(true); });
  }
  const [sessions, allFeeds, cards] = await Promise.all([fast(`/api/sessions?series=${ids.join(",")}`), fast("/api/live", LIVE_COPY).catch(() => []),
    follows().length ? fast("/api/cards").catch(() => []) : [], first]);
  const followed = followedSeries();
  const live = sessions.filter(isLive);
  let html = "";
  const feeds = allFeeds.filter(f => ids.includes(f.series));
  if (feeds.length) {
    html += `<h2>Live timing</h2><div class="card">${feeds.map(f => `<a class="item live-card" href="#/live/${f.series}">
      <div>${chip(f.series)} <b>${esc(f.event)}</b> · ${esc(f.session)}
      <div class="muted small">${f.lap ? `Lap ${f.lap}${f.total_laps ? `/${f.total_laps}` : ""} · ` : ""}${esc(f.track || f.status)}${f.connected ? "" : " · finished"}</div></div>
      <span class="${f.connected ? "live" : "muted small"}">${f.connected ? "LIVE" : "Results"}</span></a>`).join("")}</div>`;
  }

  if (live.length) {
    html += `<h2>Live now</h2><div class="evcs">${eventCards(live, followed)}</div>`;
  }

  if (follows().length) html += `<h2>Following</h2>${heroCards(cards)}`;

  const today = localDay({ start_utc: new Date().toISOString() });
  const upcoming = sessions.filter(s => !isLive(s) && shows(s) && (s.start_utc ? Date.parse(s.end_utc) > Date.now() : (s.end_day || s.day) >= today));
  html += countdown(upcoming, followed);
  if (upcoming.length) html += filterBar(upcoming);
  html += `<div id="upcoming">${upcomingList(upcoming, followed, today)}</div>`;
  // The get-the-app / alerts banner never holds the page up (checking alerts can wait up to 2 s for the service
  // worker): the slot shows the last answer at once and updates when the check is done.
  paint(`<div id="getapp-slot">${bannerHtml(BANNER)}</div>` + newBanner() + html);
  getAppBanner().catch(() => "").then(b => {
    if (local("getappHidden", false)) b = "";   // dismissed while the check was waiting on the service worker
    const slot = view.querySelector("#getapp-slot");
    if (b === BANNER && slot?.childElementCount === (b ? 1 : 0)) return;
    BANNER = b;
    if (slot) slot.innerHTML = bannerHtml(b);
  });
  const on = listen();
  view.addEventListener("click", e => {
    const t = e.target, dismiss = t.closest("[data-dismiss]"), card = t.closest(".hero[data-href]");
    if (t.closest("#hidebanner")) {
      try { localStorage.setItem("getappHidden", "true"); } catch { /* fine */ }
      BANNER = "";
      view.querySelector(".getapp")?.remove();
    } else if (t.closest("#newbar-x")) { t.closest(".newbar")?.remove(); markChangesSeen(); }
    else if (dismiss) { store.set("dismissed", [...dismissed(), dismiss.dataset.dismiss]); homeView(); }
    // The whole card opens the driver page; its buttons (dismiss) and links keep their own job.
    else if (card && !t.closest("a, button")) location.hash = card.dataset.href;
  }, on);
  wireFilters(upcoming, followed, today, on);
}

// ---- Upcoming filters (remembered per device) ------------------------------------
const WHEN = [["all", "Any day"], ["today", "Today"], ["weekend", "This weekend"]];
const SESSION_FILTERS = [["races", "Races"], ["qualifying", "Qualifying"], ["practice", "Practice"]];
const TYPES = [["single", "Single seaters"], ["sportscar", "Sportscars & GT"], ["stock", "Stock cars"], ["rally", "Rally & off-road"], ["bikes", "Bikes"], ["drift", "Drifting"]];
const RACE_KINDS = new Set(["race", "sprint", "rally_day", "event"]);
const NO_FILTERS = { when: "all", sessions: [], types: [], free: false, near: false };

function upFilters() {
  try { return { ...NO_FILTERS, ...JSON.parse(localStorage.getItem("upFilters") || "{}") }; } catch { return { ...NO_FILTERS }; }
}
function saveUpFilters(f) {
  try { localStorage.setItem("upFilters", JSON.stringify(f)); } catch { /* fine */ }
}
const filtersOn = f => f.when !== "all" || f.sessions.length || f.types.length || f.free || (f.near && home());

const addDays = (day, n) => {
  const d = new Date(`${day}T12:00:00`);
  d.setDate(d.getDate() + n);
  return localDay({ start_utc: d.toISOString() });
};
// Friday to Sunday: this weekend while it's on, otherwise the next one.
function weekend(today) {
  const dow = new Date(`${today}T12:00:00`).getDay();  // 0 = Sunday
  if (dow === 0) return [today, today];
  if (dow >= 5) return [today, addDays(today, 7 - dow)];
  return [addDays(today, 5 - dow), addDays(today, 7 - dow)];
}

function passes(s, f, today) {
  const start = localDay(s), end = s.end_day && s.end_day > start ? s.end_day : start;
  if (f.when === "today" && !(start <= today && end >= today)) return false;
  if (f.when === "weekend") {
    const [a, b] = weekend(today);
    if (!(start <= b && end >= a)) return false;
  }
  if (f.sessions.length && !f.sessions.includes(RACE_KINDS.has(s.kind) ? "races" : s.kind)) return false;
  if (f.types.length && !f.types.includes(BY_ID[s.series]?.category)) return false;
  if (f.free && s.watch?.badge !== "FREE") return false;
  if (f.near && home() && !(s.place && milesTo(s.place) <= nearMiles())) return false;
  return true;
}

// Day = one connected switch (pick one). Everything else lives behind "Filters", in labelled groups; when the
// panel is closed, whatever is on shows as small chips you can tap off.
const filterCount = f => f.sessions.length + f.types.length + (f.free ? 1 : 0) + (f.near && home() ? 1 : 0);
const filtersOpen = () => local("filtersOpen", false);
function activeChips(f) {
  const label = (list, v) => (list.find(([x]) => x === v) || [, v])[1];
  const on = [
    ...(f.free ? [["free", "1", "Free to watch"]] : []),
    ...(f.near && home() ? [["near", "1", `Near me · ${distLabel(nearMiles())}`]] : []),
    ...f.sessions.map(v => ["sessions", v, label(SESSION_FILTERS, v)]),
    ...f.types.map(v => ["types", v, label(TYPES, v)]),
  ];
  return on.map(([g, v, l]) => `<button class="chip-btn active on-chip" data-f="${g}" data-v="${v}" aria-label="Remove ${esc(l)}">${esc(l)} <span aria-hidden="true">✕</span></button>`).join("");
}

function filterBar(upcoming) {
  const f = upFilters();
  const present = new Set(upcoming.map(s => BY_ID[s.series]?.category));
  const btn = (group, value, label, on) =>
    `<button class="chip-btn${on ? " active" : ""}" data-f="${group}" data-v="${value}" aria-pressed="${on}">${label}</button>`;
  const open = filtersOpen(), n = filterCount(f);
  const group = (title, inner) => `<div class="fgroup"><div class="flabel">${title}</div><div class="chips">${inner}</div></div>`;
  return `<div class="upfilters" id="filters">
    <div class="fbar">
      <div class="seg" role="radiogroup" aria-label="Day">${WHEN.map(([v, l]) =>
        `<button role="radio" class="${f.when === v ? "active" : ""}" data-f="when" data-v="${v}" aria-checked="${f.when === v}">${l}</button>`).join("")}</div>
      <button class="fbtn${n ? " has" : ""}" id="f-toggle" aria-expanded="${open}" aria-controls="fpanel">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>
        Filters<span class="fcount"${n ? "" : " hidden"}>${n}</span></button>
    </div>
    <div class="fpanel card" id="fpanel"${open ? "" : " hidden"}>
      ${group("Watch", btn("free", "1", "Free to watch", f.free) + (home() ? btn("near", "1", `Near me · ${distLabel(nearMiles())}`, f.near)
        : `<a class="chip-btn ghost" href="#/settings/near">Near me… set home</a>`))}
      ${group("Sessions", SESSION_FILTERS.map(([v, l]) => btn("sessions", v, l, f.sessions.includes(v))).join(""))}
      ${present.size > 1 ? group("Racing type", TYPES.filter(([v]) => present.has(v)).map(([v, l]) => btn("types", v, l, f.types.includes(v))).join("")) : ""}
    </div>
    <div class="factive" id="factive"${open || !n ? " hidden" : ""}>${open ? "" : activeChips(f)}</div>
  </div>`;
}

function upcomingList(upcoming, followed, today) {
  const f = upFilters();
  const shown = upcoming.filter(s => passes(s, f, today));
  const groups = new Map();
  for (const s of shown) {
    const d = localDay(s);
    if (!groups.has(d)) groups.set(d, []);
    groups.get(d).push(s);
  }
  let html = f.near && home() ? `<div id="near-tracks">${NEAR_BOX}</div>` : "";
  html += filtersOn(f) ? `<p class="muted small filter-count">Showing ${shown.length} of ${upcoming.length} sessions ·
    <button class="link" id="f-clear">Clear filters</button></p>` : "";
  if (!upcoming.length) html += `<div class="empty">Nothing scheduled in the next 60 days for your series.</div>`;
  else if (!shown.length) html += `<div class="empty">Nothing matches these filters in the next 60 days.</div>`;
  for (const [d, rows] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
    html += `<h2>${esc(dayLabel(d))}</h2><div class="evcs">${eventCards(rows, followed)}</div>`;
  }
  return html;
}

function wireFilters(upcoming, followed, today, on) {
  const bar = view.querySelector("#filters"), list = view.querySelector("#upcoming");
  const redraw = () => {
    const f = upFilters();
    bar.querySelectorAll("#fpanel [data-f], .seg [data-f]").forEach(b => {
      const on = b.dataset.f === "when" ? f.when === b.dataset.v : b.dataset.f === "free" || b.dataset.f === "near" ? f[b.dataset.f] : f[b.dataset.f].includes(b.dataset.v);
      b.classList.toggle("active", on);
      b.setAttribute(b.getAttribute("role") === "radio" ? "aria-checked" : "aria-pressed", on);
    });
    const n = filterCount(f), open = filtersOpen(), count = bar.querySelector(".fcount"), act = bar.querySelector("#factive");
    count.textContent = n; count.hidden = !n;
    bar.querySelector("#f-toggle").classList.toggle("has", !!n);
    act.innerHTML = open ? "" : activeChips(f);
    act.hidden = open || !n;
    list.innerHTML = upcomingList(upcoming, followed, today);
    nearTracks(list);
  };
  view.addEventListener("click", e => {
    if (e.target.closest("#f-clear")) { saveUpFilters({ ...NO_FILTERS }); redraw(); return; }
    if (!bar?.contains(e.target)) return;
    if (e.target.closest("#f-toggle")) {
      const open = !filtersOpen();
      try { localStorage.setItem("filtersOpen", JSON.stringify(open)); } catch { /* fine */ }
      bar.querySelector("#fpanel").hidden = !open;
      bar.querySelector("#f-toggle").setAttribute("aria-expanded", open);
      redraw();
      return;
    }
    const b = e.target.closest("[data-f]");
    if (!b) return;
    const f = upFilters(), g = b.dataset.f, v = b.dataset.v;
    if (g === "when") f.when = v;
    else if (g === "free" || g === "near") f[g] = !f[g];
    else f[g] = f[g].includes(v) ? f[g].filter(x => x !== v) : [...f[g], v];
    saveUpFilters(f);
    redraw();
  }, on);
  nearTracks(list);
}

// With Near me on: every track in range with its next race, from ANY series (not just the ones switched on),
// because "what can I drive to?" shouldn't depend on what you follow.
let NEAR_BOX = "";   // the last answer, drawn at once so a redraw doesn't blank the box
async function nearTracks(list) {
  const box = list.querySelector("#near-tracks");
  if (!box) return;
  const tracks = (await fast("/api/tracks").catch(() => [])).map(t => ({ ...t, mi: milesTo(t) }))
    .filter(t => t.next && t.mi <= nearMiles()).sort((a, b) => a.next.start_day.localeCompare(b.next.start_day));
  const h = home();
  box.innerHTML = `<h2>Tracks within ${distLabel(nearMiles())} of ${esc(h.label)}</h2>${tracks.length ? `<div class="card">${tracks.map(t => `
    <a class="item" href="#/track/${esc(t.slug)}"><div><b>${esc(t.name)}</b><div class="muted small">${esc(t.label)} · ${t.mi < 15 ? "nearby" : `${distLabel(t.mi)} away`}</div></div>
      <div class="small" style="text-align:right">${chip(t.next.series)}<div class="muted">${esc(shortDate(t.next.start_day))}</div></div></a>`).join("")}</div>`
    : `<div class="card"><div class="empty">No races at tracks within ${distLabel(nearMiles())} for the rest of the season. <a href="#/settings/near">Change the distance</a>.</div></div>`}
    <h2 class="muted-h">Your series near you</h2>`;
  NEAR_BOX = box.innerHTML;
}

async function seriesListView() {
  setNav("series");
  const on = new Set(enabledSeries());
  view.innerHTML = `<h1>Series</h1>
    <p class="muted small">Switch series on or off. This only affects this browser, and your calendar link (in Settings) follows the same choice.</p>
    <div class="card">${SERIES.map(s => `
      <div class="item">
        <a href="#/series/${s.id}"><div>${chip(s.id)} <b>${esc(s.name)}</b></div>
        <div class="muted small">${statusText(s.status)}</div></a>
        <div class="series-ctl">${showSelect(s, on.has(s.id))}
        <label class="switch" aria-label="Show ${esc(s.name)}"><input type="checkbox" data-series="${s.id}" ${on.has(s.id) ? "checked" : ""}><span></span></label></div>
      </div>`).join("")}</div>`;
  view.querySelectorAll("input[data-series]").forEach(input => input.addEventListener("change", () => {
    const set = new Set(enabledSeries());
    input.checked ? set.add(input.dataset.series) : set.delete(input.dataset.series);
    store.set("series", SERIES.map(s => s.id).filter(id => set.has(id)));
    const sel = view.querySelector(`select[data-show="${input.dataset.series}"]`);
    if (sel) sel.hidden = !input.checked;
  }));
  view.querySelectorAll("select[data-show]").forEach(sel => sel.addEventListener("change", () => setShowLevel(sel.dataset.show, sel.value)));
}

function showSelect(s, on) {
  const level = showLevel(s.id);
  return `<select class="show-sel" data-show="${s.id}" aria-label="What ${esc(s.name)} shows on Upcoming"${on ? "" : " hidden"}>${SHOW_LEVELS.map(([v, l]) =>
    `<option value="${v}"${v === level ? " selected" : ""}>${l}</option>`).join("")}</select>`;
}

function statusText(st) {
  if (!st) return "Not fetched yet";
  if (st.error) return `Source problem: showing last good data${st.last_ok ? ` from ${new Date(st.last_ok).toLocaleString()}` : ""}`;
  return `${st.events} event${st.events === 1 ? "" : "s"} · updated ${new Date(st.last_ok).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
}

// One race weekend: schedule with where to watch and weather, your drivers, entry list, results, news.
async function eventView(id, key) {
  setNav("series");
  const [e, feeds] = await Promise.all([fast(`/api/event/${id}/${encodeURIComponent(key)}`), fast("/api/live", LIVE_COPY).catch(() => [])]);
  const s = BY_ID[id];
  const today = localDay({ start_utc: new Date().toISOString() });
  const past = (e.end_day || e.start_day) < today;
  const mine = new Set(follows().map(f => normalise(f.name)));
  const isMine = d => [d.name, d.team, d.f1_team].some(n => n && mine.has(normalise(n)));
  const yours = e.drivers.filter(isMine);
  const live = feeds.some(f => f.series === id);
  const dates = `${shortDate(e.start_day)}${e.end_day && e.end_day !== e.start_day ? ` – ${shortDate(e.end_day)}` : ""}`;
  const buttons = [
    live && `<a class="btn primary" href="#/live/${id}">● Live timing</a>`,
    e.story && `<a class="btn" href="#/story/${id}/${encodeURIComponent(e.key)}">Race story →</a>`,
    !past && e.tickets && `<a class="btn" href="${esc(e.tickets.url)}" target="_blank" rel="noopener">Tickets ↗</a>`,
    e.url && `<a class="btn" href="${esc(e.url)}" target="_blank" rel="noopener">Event page ↗</a>`,
    s?.timing && `<a class="btn" href="${esc(s.timing)}" target="_blank" rel="noopener">Official timing ↗</a>`,
  ].filter(Boolean).join("");
  const driverRow = d => `<div class="srow${isMine(d) ? " followed" : ""}"><span class="pos">${esc(d.number ? `#${d.number}` : "")}</span>
    <span class="who"><a class="who-link" href="#/driver/${encodeURIComponent(d.name)}"><b>${esc(d.name)}</b></a><span class="muted">${esc(d.team || "")}${backerTag(d)}</span></span></div>`;
  view.innerHTML = `${backLink(`#/series/${id}`)}
    <h1>${chip(id)} ${esc(e.name)}</h1>
    <p class="muted event-meta">${placeLine(e.venue, e.name, e.place, e.country)} · ${esc(dates)} · Round ${e.round} of ${e.rounds}</p>
    ${buttons ? `<div class="series-links">${buttons}</div>` : ""}
    ${e.weekend.length ? `<p class="small">Also racing here: ${e.weekend.map(w =>
      `<a href="#/event/${w.series}/${encodeURIComponent(w.key)}">${chip(w.series)}</a>`).join(" ")}</p>` : ""}
    ${yours.length ? `<h2>Your drivers</h2><div class="card standings">${yours.map(driverRow).join("")}</div>` : ""}
    ${!past ? watchList(e.watch_list) : ""}
    ${!past ? oddsBlock(e.odds, n => isMine({ name: n })) : ""}
    ${e.results?.rows?.length ? `<h2>Results</h2>${standingsBody(id, "last", { last_results: e.results.rows, last_event: e.name, last_session: e.results.session })}` : ""}
    ${Object.keys(e.qualifying || {}).length ? `<h2>Qualifying</h2>${standingsBody(id, "quali", { qualifying: e.qualifying, quali_event: e.name })}` : ""}
    <h2>Schedule</h2>
    <div class="card">${e.sessions.length ? `${commonWatch(e.sessions) && e.sessions[0].watch
      ? `<div class="evc-head"><span class="muted small">Where to watch</span>${badge(e.sessions[0].watch)}</div>` : ""}${seriesRows(e.sessions)}`
      : `<div class="empty">No sessions published yet.</div>`}</div>
    ${e.news.length ? `<h2>News</h2><div class="card">${e.news.map(n => `<a class="item news-item" href="${esc(n.url)}" target="_blank" rel="noopener">
      <div><b>${esc(n.title)}</b><div class="muted small">${esc(n.source || "")} · ${esc(ago(n.time))}</div></div></a>`).join("")}</div>` : ""}
    ${e.drivers.length ? `<h2>${e.entry_list ? "Entry list" : "On the grid"} (${e.drivers.length})</h2>
      <details class="card entry-list"${e.drivers.length <= 30 ? " open" : ""}><summary class="small muted">${e.entry_list
        ? "Everyone entered for this event" : "The series' regular drivers; this event has no separate entry list"}</summary>
        <div class="standings">${e.drivers.map(driverRow).join("")}</div></details>` : ""}`;
  wireClassPicker(() => eventView(id, key));
}

// Betting odds (DraftKings, fetched from a home connection; see lightsout/odds.py). Shown as American odds plus the
// implied chance, which includes the bookmaker's margin, so a market's chances add up to more than 100%.
const oddsOn = () => SETTINGS?.odds !== false;
const pctText = p => p < 1 ? "<1%" : `${Math.round(p)}%`;
const MARKET_SHORT = { "Race Winner": "to win", "Podium Finish": "podium", "Top 3 Finish": "top 3", "Top 5 Finish": "top 5", "Top 10 Finish": "top 10" };
// One line, opened on tap: what +110 / −360 / 48% mean.
const oddsHelp = () => `<details class="odds-help"><summary>What do the odds mean?</summary><div class="small">
  <p><b>+110</b> means a $100 bet wins $110. The bigger the number after the +, the less likely.</p>
  <p><b>−360</b> marks a favourite: you'd bet $360 to win $100.</p>
  <p><b>48%</b> is the same price as a chance. The bookmaker adds a margin, so everyone's chances add up to more than 100%.</p>
  <p><b>+50000</b> is DraftKings' longest price: they rate that driver as having practically no chance.</p>
  <p><b>even</b> on a duel: DraftKings gives both drivers exactly the same price, so it's a coin flip by the odds.</p></div></details>`;
const oddsNote = o => `<p class="small muted">${esc(o.source)} odds, updated ${esc(ago(o.fetched))}. Chances include the bookmaker's margin. For fun, not advice.</p>`;
function oddsBlock(o, mine) {
  if (!o?.markets?.length || !oddsOn()) return "";
  const row = r => `<div class="orow${mine(r.name) ? " followed" : ""}"><a class="who-link" href="#/driver/${encodeURIComponent(r.name)}"><b>${esc(r.name)}</b></a>
    <span class="obar"><i style="width:${Math.min(100, r.pct)}%"></i></span><span class="num">${pctText(r.pct)}</span><span class="num muted">${esc(r.american)}</span></div>`;
  return `<h2>Odds</h2>${oddsHelp()}${o.markets.map((m, i) => `<details class="card odds"${i ? "" : " open"}><summary><b>${esc(m.name)}</b>
      <span class="muted small">${m.rows.length} drivers</span></summary>${m.rows.slice(0, 10).map(row).join("")}
      ${m.rows.length > 10 ? `<details class="more-odds"><summary class="small muted">Everyone else (${m.rows.length - 10})</summary>${m.rows.slice(10).map(row).join("")}</details>` : ""}</details>`).join("")}
    ${oddsNote(o)}`;
}

// F1 ACADEMY rounds: who to watch, for people who don't follow the series week to week.
function watchList(w) {
  if (!w) return "";
  const who = d => `<a class="who-link" href="#/driver/${encodeURIComponent(d.name)}"><b>${esc(d.name)}</b></a>${backerTag(d)}`;
  const lead = w.leaders?.[0]?.points ?? 0;
  const rows = [
    ...(w.leaders || []).map(d => [`P${d.pos} in the championship`, `${who(d)} <span class="muted small">${d.pos > 1 ? `−${pts(lead - d.points)}` : `${pts(d.points)} pts`}</span>`]),
    ...(w.home || []).map(d => ["Home race", who(d)]),
    ...(w.wildcards || []).map(d => ["Wild card entry", who(d)]),
  ];
  return `<h2>Who to watch</h2><div class="card">${rows.map(([k, v]) => `<div class="item watch-row"><div><div class="muted small">${esc(k)}</div>${v}</div></div>`).join("")}</div>`;
}

async function trackView(slug) {
  setNav("series");
  const t = await fast(`/api/track/${encodeURIComponent(slug)}`);
  const today = localDay({ start_utc: new Date().toISOString() });
  const ahead = t.events.filter(e => (e.end_day || e.start_day) >= today), done = t.events.filter(e => (e.end_day || e.start_day) < today);
  const dates = e => `${shortDate(e.start_day)}${e.end_day && e.end_day !== e.start_day ? ` – ${shortDate(e.end_day)}` : ""}`;
  const row = e => `<a class="item" href="#/event/${e.series}/${encodeURIComponent(e.key)}"><div>${chip(e.series)} <b>${esc(e.name)}</b>${e.tickets ? ` <span class="tag tix">Tickets</span>` : ""}
    <div class="muted small">${esc(BY_ID[e.series]?.name || e.series)}</div></div><div class="small muted" style="text-align:right">${esc(dates(e))}</div></a>`;
  const mi = milesTo(t);
  const q = encodeURIComponent(t.name);
  view.innerHTML = `${backLink("#/")}
    <h1>${esc(t.name)}</h1>
    <p class="muted event-meta">${esc(t.label)}${mi != null ? ` · ${mi < 15 ? "nearby" : `${distLabel(mi)} from ${esc(home().label)}`}` : ""}</p>
    <div class="series-links"><a class="btn" href="https://maps.apple.com/?ll=${t.lat},${t.lon}&q=${q}" target="_blank" rel="noopener">Apple Maps ↗</a>
      <a class="btn" href="https://www.google.com/maps/search/?api=1&query=${t.lat}%2C${t.lon}" target="_blank" rel="noopener">Google Maps ↗</a></div>
    ${t.sessions.length ? `<h2>Next two weeks</h2><div class="evcs">${eventCards(t.sessions, followedSeries())}</div>` : ""}
    <h2>Coming up here</h2>${ahead.length ? `<div class="card">${ahead.map(row).join("")}</div>`
      : `<div class="card"><div class="empty">Nothing else scheduled here yet.</div></div>`}
    ${done.length ? `<h2>Earlier this season</h2><div class="card past">${done.map(row).join("")}</div>` : ""}`;
}

// "7 of 24 rounds done": the season of the next round (or the last one, once it's over).
// A series page's sessions: day + time, name, and the watch badge only when it differs within the weekend.
const commonWatch = ss => ss.length && ss.every(x => JSON.stringify(x.watch || null) === JSON.stringify(ss[0].watch || null));
function seriesRows(sessions) {
  const same = commonWatch(sessions);
  return sessions.map(x => {
    const d = localDay(x);
    return `<div class="evc-row${RACE_KINDS.has(x.kind) ? " race" : ""}"><div class="when-cell"><div class="small muted">${esc(dayLabel(d))}</div>${timeCell(x)}</div>
      <div class="evc-ses">${esc(x.name)}${isLive(x) ? ' <span class="live">LIVE</span>' : ""}${weatherTag(x.weather)}</div>${same ? "" : badge(x.watch)}</div>`;
  }).join("");
}

function seasonProgress(events, today) {
  const end = e => e.end_day || e.start_day;
  const season = (events.find(e => end(e) >= today) || events[events.length - 1])?.season;
  const rounds = events.filter(e => e.season === season);
  if (rounds.length < 2) return "";
  const done = rounds.filter(e => end(e) < today).length;
  const years = [...new Set(rounds.map(e => e.start_day.slice(0, 4)))];
  const label = years.length > 1 ? `${years[0]}–${years[years.length - 1].slice(2)}` : years[0];
  const next = rounds[done];
  const text = done === rounds.length ? `Season complete · ${rounds.length} rounds`
    : done === 0 ? `${rounds.length} rounds · starts ${esc(shortDate(next.start_day))}`
    : `<b>${done}</b> of ${rounds.length} rounds done · next: ${esc(next.name)}`;
  return `<div class="season"><div class="season-text">${text}<span class="muted"> · ${esc(label)}</span></div>
    <div class="season-bar" role="img" aria-label="${done} of ${rounds.length} rounds done">${rounds.map((e, i) =>
      `<i class="${i < done ? "done" : i === done ? "next" : ""}" title="${esc(e.name)}"></i>`).join("")}</div></div>`;
}

const SERIES_TABS = [["schedule", "Schedule"], ["last", "Last race"], ["quali", "Qualifying"], ["drivers", "Drivers"], ["teams", "Teams"]];

async function seriesView(id, tab = "schedule") {
  setNav("series");
  const s = BY_ID[id];
  if (!s) { view.innerHTML = `<div class="empty">Unknown series.</div>`; return; }
  if (!s.has_standings) tab = "schedule";
  // Everything at once: standings, live status and (for the schedule tab) the events and race stories.
  const schedule = () => Promise.all([fast(`/api/series/${id}/events`), fast(`/api/stories/${id}`).catch(() => [])]);
  const early = tab === "schedule" ? schedule() : null;
  early?.catch(() => {});   // if standings fail first, the page shows that error; this one needn't be reported too
  const [st, feeds] = await Promise.all([s.has_standings ? fast(`/api/series/${id}/standings`) : {}, fast("/api/live", LIVE_COPY).catch(() => [])]);
  const shown = SERIES_TABS.filter(([t]) => hasTab(t, st));
  if (!shown.some(([t]) => t === tab)) tab = "schedule";
  const liveNow = feeds.some(f => f.series === id);
  const tabs = s.has_standings || liveNow ? `<div class="tabs" role="tablist">${liveNow ? `<a role="tab" href="#/live/${id}" class="live-tab">● Live</a>` : ""}${(s.has_standings ? shown : SERIES_TABS.slice(0, 1)).map(([t, label]) =>
    `<a role="tab" href="#/series/${id}/${t}" class="${t === tab ? "active" : ""}" aria-selected="${t === tab}">${label}</a>`).join("")}</div>` : "";
  const links = [s.site && `<a class="btn" href="${esc(s.site)}" target="_blank" rel="noopener">Official site ↗</a>`,
    s.timing && `<a class="btn" href="${esc(s.timing)}" target="_blank" rel="noopener">Official timing ↗</a>`].filter(Boolean).join("");
  const level = showLevel(id);
  const showBar = enabledSeries().includes(id) ? `<div class="show-bar"><span class="muted small">On Upcoming</span><div class="chips">${SHOW_LEVELS.map(([v, l]) =>
    `<button class="chip-btn${v === level ? " active" : ""}" data-level="${v}" aria-pressed="${v === level}">${l}</button>`).join("")}</div></div>` : "";
  const head = `${backLink("#/series")}<h1>${chip(id)} ${esc(s.name)}</h1>${links ? `<div class="series-links">${links}</div>` : ""}${showBar}${tabs}`;
  const bindShow = () => view.querySelectorAll("[data-level]").forEach(b => b.addEventListener("click", () => {
    setShowLevel(id, b.dataset.level);
    view.querySelectorAll("[data-level]").forEach(x => { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", x === b); });
  }));
  if (tab === "schedule") {
    const [events, storyKeys] = await (early || schedule());
    const hasStory = new Set(storyKeys);
    const today = localDay({ start_utc: new Date().toISOString() });
    const followed = followedSeries();
    view.innerHTML = `${head}${seasonProgress(events, today)}<p class="muted small">${statusText(s.status)}</p>
      <div class="list">${events.map(e => {
        const past = e.end_day && e.end_day < today;
        return `<div class="card${past ? " past" : ""}">
          <div class="event-head"><div class="ev"><a class="ev-link" href="#/event/${id}/${encodeURIComponent(e.key)}">${esc(e.name)} ›</a>${hasStory.has(e.key) ? ` <a class="tag story" href="#/story/${id}/${encodeURIComponent(e.key)}">Race story →</a>` : ""}${!past && e.tickets ? ` <a class="tag tix" href="${esc(e.tickets.url)}" target="_blank" rel="noopener" title="${esc(e.tickets.label)}">Tickets ↗</a>` : ""}</div>
          <div class="muted small">${placeLine(e.venue, e.name, e.place, e.country)} · ${esc(shortDate(e.start_day))}${e.end_day && e.end_day !== e.start_day ? ` – ${esc(shortDate(e.end_day))}` : ""}</div>${commonWatch(e.sessions) ? `<div class="evh-badge">${badge(e.sessions[0].watch)}</div>` : ""}</div>
          ${seriesRows(e.sessions)}
        </div>`;
      }).join("") || `<div class="empty">No events yet.</div>`}</div>`;
    bindShow();
    return;
  }
  view.innerHTML = head + standingsBody(id, tab, st);
  bindShow();
  wireClassPicker(() => seriesView(id, tab));
}

// Drivers / Teams / Last race for one series (shared by the series page and the Standings tab).
// Series whose official teams' table leaves out some entries people will look for.
const TEAMS_NOTE = {
  gtwce: "Only teams entered for the teams' championship are listed. Some cars, like Mercedes-AMG Team Verstappen Racing, score only in the drivers' standings.",
};

// Team colour on a standings row (from /api/series/<id>/standings; colors.yaml, else the series colour).
const tc = r => r.color ? ` style="--tc:${esc(r.color)}"` : "";
// A standings name opens its driver (or team) page; a crew ("A / B", sports cars) links each driver.
const whoLink = (name, kind = "driver", cls = "") => `<div>${String(name).split(" / ").map(n =>
  `<a class="who-link" href="#/${kind}/${encodeURIComponent(n)}"><b${cls ? ` class="${cls}"` : ""}>${esc(n)}</b></a>`).join(" / ")}</div>`;

// F1 ACADEMY: every car runs in a backer's colours, an F1 team (shown in that team's colour) or a sponsor; plus wild cards.
const backerTag = r => `${r.wildcard ? `<i class="btag wild">Wild card</i>` : ""}${r.backer ? `<i class="btag${r.f1_team ? " f1" : ""}"${r.f1_color
  ? ` style="--bc:${esc(r.f1_color)}"` : ""} title="Backed by ${esc(r.backer)}">${esc(r.f1_team || r.backer)}</i>` : ""}`;

function standingsBody(id, tab, st) {
  const s = BY_ID[id];
  const mine = new Set(follows().map(f => normalise(f.name)));
  const isMine = (...names) => names.some(n => n && mine.has(normalise(n)));
  const updated = st.updated ? `Updated ${new Date(st.updated).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : "";
  const resultRows = rows => `<div class="card standings">${rows.map(r => `<div class="srow${isMine(r.name, r.team, r.f1_team) ? " followed" : ""}"${tc(r)}>
        <span class="pos">${esc(r.pos)}</span><span class="who">${whoLink(r.name)}<span class="tteam">${esc(r.team)}${backerTag(r)}</span></span>
        <span class="num">${esc(r.time)}</span><span class="num pts">${r.points ? pts(r.points) : ""}</span></div>`).join("")}</div>`;
  if (tab === "last") {
    return st.last_results?.length ? `<p class="muted small">${[st.last_event, st.last_session, updated].filter(Boolean).map(esc).join(" · ")}</p>
      ${resultRows(st.last_results)}`
      : `<div class="empty">No race results yet.</div>`;
  }
  if (tab === "quali") {
    const sessions = Object.keys(st.qualifying || {});
    if (!sessions.length) return `<div class="empty">No qualifying results yet.</div>`;
    const pick = sessions.includes(seriesQuali[id]) ? seriesQuali[id] : sessions[0];
    const picker = sessions.length > 1 ? `<div class="chips">${sessions.map(q =>
      `<button class="chip-btn${q === pick ? " active" : ""}" data-quali="${esc(q)}" data-for="${id}">${esc(q)}</button>`).join("")}</div>` : "";
    return `${picker}<p class="muted small">${[st.quali_event, pick, updated].filter(Boolean).map(esc).join(" · ")}</p>
      ${resultRows(st.qualifying[pick])}${pick === "Combined" ? `<p class="muted small">The average of the crew's qualifying times sets the grid.</p>` : ""}`;
  }
  const classes = tab === "drivers" ? Object.keys(st.classes || {}) : [];
  const cls = classes.includes(seriesClass[id]) ? seriesClass[id] : null;
  const rows = tab === "drivers" ? (cls ? st.classes[cls] : st.drivers) : st.teams;
  const lead = rows?.[0]?.points ?? 0;
  const picker = classes.length ? `<div class="chips">${[s.short, ...classes].map((c, i) =>
    `<button class="chip-btn${(i === 0 ? !cls : c === cls) ? " active" : ""}" data-class="${i === 0 ? "" : esc(c)}" data-for="${id}">${esc(c)}</button>`).join("")}</div>` : "";
  return rows?.length ? `${picker}<p class="muted small">${esc(st.season_label)} · ${updated}</p>
    <div class="card standings">${rows.map(r => `<div class="srow${isMine(r.name, tab === "teams" ? r.name : null, r.f1_team) ? " followed" : ""}"${tc(r)}>
      <span class="pos">${r.pos}</span><span class="who">${tab === "teams" ? whoLink(r.name, "team", "tteam-name") : whoLink(r.name)}<span class="tteam">${esc(r.team)}${backerTag(r)}</span></span>
      <span class="num muted">${r.pos > 1 ? `−${pts(lead - r.points)}` : ""}</span><span class="num pts">${pts(r.points)}</span></div>`).join("")}</div>
    ${tab === "teams" && TEAMS_NOTE[id] ? `<p class="muted small">${esc(TEAMS_NOTE[id])}</p>` : ""}`
    : `<div class="empty">${tab === "teams" ? "This series doesn't publish a team championship we can read." : "No standings yet."}</div>`;
}

function wireClassPicker(rerender) {
  view.querySelectorAll("[data-class]").forEach(b => b.addEventListener("click", () => {
    seriesClass[b.dataset.for] = b.dataset.class || null; rerender();
  }));
  view.querySelectorAll("[data-quali]").forEach(b => b.addEventListener("click", () => {
    seriesQuali[b.dataset.for] = b.dataset.quali; rerender();
  }));
}

// Standings tab: every championship in one place. Series you have switched on come first; the last one picked is remembered.
const STANDINGS_TABS = [["drivers", "Drivers"], ["teams", "Teams"], ["last", "Last race"], ["quali", "Qualifying"]];
// Tabs that only show when the series has them: Teams (some publish no team table), Qualifying (GTWCE so far).
// SRO's other series have results but no championship table: Drivers hides there, and America (qualifying only) has
// no Last race either. A series with nothing yet keeps Drivers and Last race, which say so.
const hasTab = (t, st) => {
  const quali = Object.keys(st.qualifying || {}).length, last = st.last_results?.length;
  if (t === "teams") return st.teams?.length;
  if (t === "quali") return quali;
  if (t === "drivers") return st.drivers?.length || (!quali && !last);
  if (t === "last") return last || !quali;
  return true;
};

async function standingsView(id, tab) {
  setNav("standings");
  const on = new Set(enabledSeries());
  const all = SERIES.filter(x => x.has_standings);
  const list = [...all.filter(x => on.has(x.id)), ...all.filter(x => !on.has(x.id))];
  if (!list.length) { view.innerHTML = `<h1>Standings</h1><div class="empty">No standings yet.</div>`; return; }
  if (!BY_ID[id]?.has_standings) id = list.some(x => x.id === store.get("standingsSeries")) ? store.get("standingsSeries") : list[0].id;
  store.set("standingsSeries", id);
  const st = await fast(`/api/series/${id}/standings`);
  const tabs = STANDINGS_TABS.filter(([t]) => hasTab(t, st));
  if (!tabs.some(([t]) => t === tab)) tab = tabs.some(([t]) => t === "drivers") ? "drivers" : tabs[0][0];
  view.innerHTML = `<h1>Standings</h1>
    <div class="series-pick" role="tablist" aria-label="Series">${list.map(x =>
      `<a role="tab" href="#/standings/${x.id}/${["teams", "quali"].includes(tab) ? "drivers" : tab}" class="${x.id === id ? "active" : ""}${on.has(x.id) ? "" : " off"}" aria-selected="${x.id === id}">${esc(x.short)}</a>`).join("")}</div>
    <div class="standings-title">${chip(id)} <b>${esc(BY_ID[id].name)}</b> <a class="small" href="#/series/${id}">Schedule →</a></div>
    <div class="tabs" role="tablist">${tabs.map(([t, label]) =>
      `<a role="tab" href="#/standings/${id}/${t}" class="${t === tab ? "active" : ""}" aria-selected="${t === tab}">${label}</a>`).join("")}</div>
    ${standingsBody(id, tab, st)}`;
  view.querySelector(".series-pick a.active")?.scrollIntoView({ block: "nearest", inline: "center" });
  wireClassPicker(() => standingsView(id, tab));
}
const seriesClass = {};  // picked support championship per series (e.g. WRC2), for this page visit
const seriesQuali = {};  // picked qualifying session per series (Q1, Combined...)

async function settingsView() {
  setNav("settings");
  const entries = await fast("/api/entries").catch(() => []);
  const ids = enabledSeries();
  const kinds = store.get("kinds", KINDS.map(k => k[0]));
  const allKinds = kinds.length === KINDS.length;
  // Each series' "On Upcoming" level carries over to the calendar ("wec:qualifying,cup:races").
  const levels = ids.filter(id => showLevel(id) !== "all").map(id => `${id}:${showLevel(id)}`).join(",");
  const query = [!allKinds && `kinds=${kinds.join(",")}`, levels && `show=${levels}`].filter(Boolean).join("&");
  const https = `${location.origin}/cal/${CONFIG.cal_token}/${ids.join("+")}.ics${query ? `?${query}` : ""}`;
  const webcal = https.replace(/^https?:/, "webcal:");

  view.innerHTML = `<h1>Settings</h1>
    <a class="card whatsnew-link" href="#/new"><b>What's new</b>${unseenChanges().length ? ` <span class="pill">${unseenChanges().length} new</span>` : ""}<span class="muted small">Every update to the app →</span></a>
    <a class="card whatsnew-link" href="#/report" style="margin-top:8px"><b>Report a problem or suggest an idea</b><span class="muted small">Goes straight to the to-do list →</span></a>
    <div id="myreports"></div>
    <h2>Alerts</h2>
    <p class="small muted" style="margin:-2px 2px 8px">New here? <a href="#/app">Get the app and alerts on your phone</a>.</p>
    <div class="card" id="notif"><div class="empty">Checking this device…</div></div>
    <div class="card" style="margin-top:8px">${alertToggles()}</div>

    <h2>Weather</h2>
    <div class="card"><div class="item"><div>Temperatures in<div class="small muted">Forecasts at the track show up for sessions in the next 7 days.</div></div>
      <div class="chips" style="margin:0">${[["f", "°F"], ["c", "°C"]].map(([u, l]) =>
        `<button class="chip-btn${store.get("units", "f") === u ? " active" : ""}" data-units="${u}">${l}</button>`).join("")}</div></div></div>

    <h2 id="near">Near me</h2>
    <div class="card"><div class="form">
      <div class="small muted">Set home once: races show how far away they are, and Upcoming gets a Near me filter with every track in range. Only a rounded point (about 1 km) is saved, never your exact location.</div>
      ${home() ? `<div class="home-now"><span>Home: <b>${esc(home().label)}</b></span> <button class="link" id="home-clear">Clear</button></div>` : ""}
      <div class="home-set"><input type="text" id="zip" inputmode="numeric" maxlength="5" placeholder="US ZIP code" aria-label="US ZIP code">
        <button id="zip-go">Set</button><button id="geo">Use my location</button></div>
      <div class="small muted" id="home-msg" role="status"></div>
      <div><div class="small muted" style="margin-bottom:4px">Near means within</div><div class="chips" style="margin:0">${[100, 200, 300, 500, 1000].map(m =>
        `<button class="chip-btn${nearMiles() === m ? " active" : ""}" data-near="${m}">${distLabel(m)}</button>`).join("")}</div></div>
    </div></div>

    <h2>Calendar feed</h2>
    <div class="card"><div class="form">
      <div class="small muted">Subscribe once and your calendar app keeps it updated, including reschedules. It covers the ${ids.length} series switched on in <a href="#/series">Series</a>, each showing what you picked for it there (all sessions, quali + races, or races only); change those and subscribe again with the new link.</div>
      <div class="checks">${KINDS.map(([k, label]) => `<label><input type="checkbox" data-kind="${k}" ${kinds.includes(k) ? "checked" : ""}> ${label}</label>`).join("")}</div>
      <div><a class="btn primary" href="${esc(webcal)}">Subscribe in Calendar</a> <button id="copy">Copy link</button></div>
      <code class="url">${esc(https)}</code>
      <div class="small muted">Treat this link like a password: anyone with it can see the calendar.</div>
    </div></div>

    <h2>Following</h2>
    <div class="card" id="following"></div>

    <h2>Pick'em</h2>
    <div class="card"><label class="item"><span>Name friends see<span class="small muted" style="display:block">On the Pick'em tables. Empty = "${esc(ME.email.split("@")[0])}". Your email is never shown.</span></span>
      <input type="text" id="nickname" maxlength="24" autocomplete="nickname" placeholder="${esc(ME.email.split("@")[0])}" value="${esc(store.get("nickname", ""))}" style="max-width:11em"></label></div>

    <h2>Display</h2>
    <div class="card"><label class="item"><span>Animations between pages<span class="small muted" style="display:block">Quick slides between tabs and pages. Off for instant switching; also off when your phone's Reduce Motion is on.</span></span>
      <input type="checkbox" id="animations" ${SETTINGS?.animations !== false ? "checked" : ""}></label>
      <label class="item"><span>Betting odds<span class="small muted" style="display:block">DraftKings' race odds on event pages, driver pages and Pick'em duels. Off hides them everywhere.</span></span>
      <input type="checkbox" id="odds" ${oddsOn() ? "checked" : ""}></label></div>

    ${ME.admin ? `<a class="card whatsnew-link" href="#/admin" style="margin-top:22px"><b>Admin</b><span class="muted small">People, invites, sources →</span></a>` : ""}

    <h2>Data sources</h2>
    <div class="card">${SERIES.map(s => `<div class="item"><div>${chip(s.id)} ${esc(s.name)}</div><div class="small muted" style="text-align:right">${statusText(s.status)}</div></div>`).join("")}</div>
    <p class="small muted">Where-to-watch info is for the US, ${esc(CONFIG.watch_season)} season.</p>`;

  const msg = view.querySelector("#home-msg");
  const setHome = h => { store.set("home", h); for (const k of GET_CACHE.keys()) if (k === "/api/tracks") GET_CACHE.delete(k); settingsView(); };
  view.querySelector("#home-clear")?.addEventListener("click", () => setHome(null));
  view.querySelector("#zip-go").addEventListener("click", async () => {
    msg.textContent = "Looking it up…";
    try { setHome(await api(`/api/home?zip=${encodeURIComponent(view.querySelector("#zip").value.trim())}`)); }
    catch { msg.textContent = "That ZIP code wasn't found. Five digits, US only (or use your location)."; }
  });
  view.querySelector("#zip").addEventListener("keydown", e => { if (e.key === "Enter") view.querySelector("#zip-go").click(); });
  view.querySelector("#geo").addEventListener("click", () => {
    if (!navigator.geolocation) { msg.textContent = "This browser can't share a location."; return; }
    msg.textContent = "Asking your device…";
    navigator.geolocation.getCurrentPosition(
      pos => setHome({ label: "My location", lat: Math.round(pos.coords.latitude * 100) / 100, lon: Math.round(pos.coords.longitude * 100) / 100 }),
      () => { msg.textContent = "Location wasn't shared. You can type a ZIP code instead."; }, { maximumAge: 3600000, timeout: 15000 });
  });
  view.querySelectorAll("[data-near]").forEach(b => b.addEventListener("click", () => { store.set("nearMiles", Number(b.dataset.near)); settingsView(); }));
  if (location.hash === "#/settings/near") view.querySelector("#near")?.scrollIntoView();
  view.querySelector("#animations").addEventListener("change", e => store.set("animations", e.target.checked));
  view.querySelector("#odds").addEventListener("change", e => store.set("odds", e.target.checked));
  view.querySelector("#nickname").addEventListener("change", e => {
    store.set("nickname", e.target.value.trim().slice(0, 24));
    for (const k of GET_CACHE.keys()) if (k.startsWith("/api/pickem")) GET_CACHE.delete(k);
  });
  view.querySelectorAll("[data-units]").forEach(b => b.addEventListener("click", () => {
    store.set("units", b.dataset.units); settingsView();
  }));
  view.querySelectorAll("[data-kind]").forEach(cb => cb.addEventListener("change", () => {
    const chosen = [...view.querySelectorAll("[data-kind]:checked")].map(c => c.dataset.kind);
    store.set("kinds", chosen.length ? chosen : KINDS.map(k => k[0]));
    settingsView();
  }));
  view.querySelector("#copy").addEventListener("click", async e => {
    try { await navigator.clipboard.writeText(https); e.target.textContent = "Copied"; }
    catch { e.target.textContent = "Copy failed"; }
  });
  renderFollowing(view.querySelector("#following"), entries);
  showNotifications(view.querySelector("#notif"));
  bindAlertToggles();
  showMyReports("myreports");
}

// ---- Pick'em ----------------------------------------------------------------------------------
// Per race: five Duels (pick who finishes ahead; ★ = your lock, 2 points or nothing) and a Podium call (1 point per
// driver who makes the podium, 2 for the exact spot). Picks close at lights out, when friends' picks appear.
// One table per series. Server side: lightsout/picks.py.
const PICK_SERIES = ["f1", "cup", "indycar"];
const raceWhen = iso => new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const finishText = f => f == null ? "" : /^\d+$/.test(f) ? `Finished P${f}` : f === "R" ? "Retired" : esc(f);

async function pickemView(series, eventKey, quiet = false) {
  setNav("picks");
  const choices = PICK_SERIES.filter(id => BY_ID[id]);
  if (!choices.includes(series)) series = choices.includes(store.get("pickSeries")) ? store.get("pickSeries") : choices[0];
  store.set("pickSeries", series);
  const path = `/api/pickem?series=${series}${eventKey ? `&event=${encodeURIComponent(eventKey)}` : ""}`;
  if (!quiet) showLoading("Loading Pick'em…");
  const d = await fast(path);
  const r = d.race, sl = d.slate;
  const here = `#/picks/${series}`;
  const head = `<h1>Pick'em</h1>
    <div class="series-pick" role="tablist" aria-label="Series">${choices.map(id =>
      `<a role="tab" href="#/picks/${id}" class="${id === series ? "active" : ""}" aria-selected="${id === series}">${esc(BY_ID[id].short)}</a>`).join("")}</div>`;
  if (!r) { view.innerHTML = `${head}<div class="empty">No ${esc(BY_ID[series].name)} races on the schedule yet.</div>`; return; }

  const status = !sl ? "" : sl.graded ? `<span class="pill ok">Scored</span>` : sl.started ? `<span class="pill warn">Picks closed</span>`
    : `<span class="pill">Open until lights out</span>`;
  const racebar = `<div class="racebar">
      ${d.prev ? `<a href="${here}/${encodeURIComponent(d.prev)}" aria-label="Previous race">←</a>` : "<span></span>"}
      <div><b><a class="ev-link" href="#/event/${series}/${encodeURIComponent(r.event_key)}">${esc(r.name)}</a></b>
        <div class="small muted">Round ${r.round} of ${r.rounds} · ${esc(raceWhen(r.start))}</div></div>
      ${d.next ? `<a href="${here}/${encodeURIComponent(d.next)}" aria-label="Next race">→</a>` : "<span></span>"}
    </div>`;

  let body, bindPicks = null;
  if (!sl) {
    const future = Date.parse(r.start) > Date.now();
    body = `<div class="card"><div class="empty">${future
      ? `Duels for this race appear ${esc(new Date(r.opens).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" }))}, 10 days before it starts.`
      : "There was no Pick'em for this race."}</div></div>`;
  } else {
    const closed = sl.started;
    const myPts = [...sl.duels.map(x => x.points), ...sl.podium.points].filter(x => x != null);
    const duelRows = sl.duels.map(x => {
      const side = drv => {
        const on = x.mine === drv.name;
        const res = on && x.outcome ? ` ${x.outcome === "won" ? "won" : x.outcome === "lost" ? "lost" : "void"}` : "";
        const sub = drv.finish != null ? finishText(drv.finish) : `${drv.pos && drv.pos < 999 ? `P${drv.pos} · ` : ""}${esc(drv.team)}`;
        const line = !closed && drv.odds && oddsOn() ? `<span class="pk-odds">${esc(drv.odds.american)} ${MARKET_SHORT[drv.odds.market] || esc(drv.odds.market)} · ${pctText(drv.odds.pct)}</span>` : "";
        return `<button class="pk${on ? " on" : ""}${res}" data-duel="${x.slot}" data-name="${esc(drv.name)}" ${closed ? "disabled" : ""}>
          <span class="pk-nm">${esc(drv.name)}</span><span class="pk-sub">${sub}</span>${line}</button>`;
      };
      const friends = x.everyone || [];
      const others = x.count - (x.mine ? 1 : 0);
      return `<div class="pick-row">
        <div class="pick-line">${side(x.drivers[0])}<div class="pick-mid">vs${!closed && oddsOn() && x.drivers[0].odds?.even
          ? `<small class="even" title="DraftKings prices them exactly the same">even</small>` : ""}</div>${side(x.drivers[1])}
          <button class="lockbtn${x.lock ? " on" : ""}" data-lock="${x.slot}" ${closed ? "disabled" : ""} aria-pressed="${x.lock}" title="Lock: 2 points if right, nothing if wrong">★</button></div>
        ${x.outcome === "void" ? `<div class="pick-friends">No winner (one didn't start, or both retired): doesn't count.</div>` : ""}
        ${closed && friends.length ? `<div class="pick-friends">${friends.map(p => `${esc(p.name)}: ${esc(p.pick.split(" ").slice(-1)[0])}${p.lock ? " ★" : ""}`).join(" · ")}</div>`
          : !closed && others > 0 ? `<div class="pick-friends">${others} friend${others > 1 ? "s have" : " has"} picked</div>` : ""}
      </div>`;
    }).join("");
    const P = sl.podium;
    const podRows = [1, 2, 3].map(n => {
      const mine = P.mine[n - 1], pts = P.points[n - 1];
      const res = pts == null ? "" : `<span class="pill ${pts ? "ok" : ""}">${pts ? `+${pts}` : "0"}</span>`;
      return `<label class="podium-row"><span class="podium-pos">P${n}</span>
        <select data-podium="${n}" ${closed ? "disabled" : ""}><option value="">Pick a driver…</option>${sl.field.map(f =>
          `<option value="${esc(f.name)}" ${f.name === mine ? "selected" : ""}>${esc(f.name)}${f.team ? ` (${esc(f.team)})` : ""}</option>`).join("")}</select>${res}</label>`;
    }).join("");
    const podFriends = (P.everyone || []).filter(p => p.picks.some(Boolean));
    const anyOdds = !closed && oddsOn() && sl.duels.some(x => x.drivers.some(d => d.odds));
    body = `<h2>Duels</h2>${anyOdds ? `<div class="odds-intro small muted">Odds under each name: DraftKings' price for whichever bet best splits the pair (to win, podium or top 10).</div>${oddsHelp()}` : ""}
      <div class="card picks">${duelRows}</div>
      <h2>Podium</h2>
      <div class="card pad16">${podRows}
        ${P.actual ? `<p class="small" style="margin:10px 0 0"><b>Actual podium:</b> ${P.actual.map((n, i) => `P${i + 1} ${esc(n)}`).join(" · ")}</p>` : ""}
        ${closed && podFriends.length ? `<p class="small muted" style="margin:8px 0 0">${podFriends.map(p => `${esc(p.name)}: ${p.picks.map(n => n ? esc(n.split(" ").slice(-1)[0]) : "–").join(", ")}`).join("<br>")}</p>`
          : !closed && P.count ? `<p class="small muted" style="margin:8px 0 0">${P.count} friend${P.count > 1 ? "s have" : " has"} called the podium</p>` : ""}
      </div>
      ${sl.graded && myPts.length ? `<p class="small"><b>You scored ${myPts.reduce((a, b) => a + b, 0)}</b> this race.</p>` : ""}
      <p class="small muted" id="pickmsg" role="status"></p>`;
    bindPicks = () => {
      const msg = t => { const m = document.getElementById("pickmsg"); if (m) m.textContent = t; };
      const send = async pick => {
        try { await postJSON("/api/pickem", { series, event: r.event_key, ...pick }); }
        catch (e) { msg(e.detail || "Couldn't save that pick. Try again."); }
        await api(path).catch(() => {});
        pickemView(series, eventKey, true);
      };
      view.querySelectorAll("[data-duel]").forEach(b => b.addEventListener("click", () => {
        const x = sl.duels[+b.dataset.duel], name = x.mine === b.dataset.name ? null : b.dataset.name;  // tap again to clear
        b.closest(".pick-line").querySelectorAll(".pk").forEach(o => o.classList.toggle("on", o === b && !!name));
        send({ kind: "duel", slot: x.slot, name, lock: name ? x.lock : false });
      }));
      view.querySelectorAll("[data-lock]").forEach(b => b.addEventListener("click", () => {
        const x = sl.duels[+b.dataset.lock];
        if (!x.mine) { msg("Pick a driver in that duel first, then lock it."); return; }
        view.querySelectorAll(".lockbtn").forEach(o => o.classList.toggle("on", o === b && !x.lock));
        send({ kind: "duel", slot: x.slot, name: x.mine, lock: !x.lock });
      }));
      view.querySelectorAll("[data-podium]").forEach(sel => sel.addEventListener("change", () =>
        send({ kind: "podium", slot: +sel.dataset.podium, name: sel.value || null })));
    };
  }

  const table = list => list?.length ? `<div class="card"><table class="board"><tr><th></th><th>Name</th><th>Pts</th><th>Duels</th><th>Podium</th></tr>
    ${list.map((p, i) => `<tr class="${p.me ? "me" : ""}"><td class="muted">${i + 1}</td><td>${esc(p.name)}${p.me ? " (you)" : ""}</td><td><b>${p.pts}</b></td><td>${p.w}–${p.l}</td><td>${p.podium}</td></tr>`).join("")}</table></div>`
    : `<div class="card"><div class="empty">No scored races yet this season.</div></div>`;
  const b = d.board;
  view.innerHTML = `${head}${racebar}
    ${status ? `<div class="pick-status">${status}</div>` : ""}
    <p class="help small muted">Tap who finishes ahead in each duel: <b>1 point</b> each. Tap ★ on one duel to make it your lock, worth <b>2</b> (nothing if it's wrong). Call the podium too: <b>1 point</b> for each driver who makes it, <b>2</b> for the exact spot. Picks close at lights out.</p>
    ${body}
    ${b?.last ? `<h2>Last scored race · ${esc(b.last.race)}</h2>${table(b.last.table)}` : ""}
    <h2>${esc(BY_ID[series].short)} season ${esc(b?.season ?? "")}</h2>${table(b?.table)}
    <p class="help small muted">You show up as <b>${esc(d.name)}</b>. <a href="#/settings">Change your name in Settings</a>.</p>`;
  bindPicks?.();
}

// ---- following ----------------------------------------------------------------
// A follow is just a name. Typed names are matched against known drivers and teams (forgiving
// spelling), then looked up across every series; the review shows where each one turned up.
let pendingFollows = [];

function foundText(st) {
  if (!st || !st.found.length) return "Not on any current grid or entry list yet. We'll keep watching every series.";
  return st.found.map(f => f.kind === "grid" ? `${BY_ID[f.series]?.short} ${["wrc", "ara", "erx", "w2rc"].includes(f.series) ? "registered" : "grid"}`
    : `${BY_ID[f.series]?.short}: ${f.event_name}`)
    .filter((x, i, a) => a.indexOf(x) === i).join(" · ");
}

async function renderFollowing(box, entries) {
  const rerender = () => renderFollowing(box, entries);
  const current = await loadFollowStatus();
  const list = follows().map((f, i) => {
    const st = current.find(x => x.name === f.name);
    return `<div class="item"><div><a class="who-link" href="#/driver/${encodeURIComponent(f.name)}"><b>${esc(f.name)}</b> ›</a> ${(st?.series || []).map(chip).join(" ")}
      <div class="muted small">${esc(foundText(st))}</div></div><button data-unfollow="${i}">Remove</button></div>`;
  }).join("");

  const review = pendingFollows.length ? `<div class="review">
      <div class="small muted">Check the matches, then save. Each name is watched across every series.</div>
      ${pendingFollows.map((p, i) => `<div class="review-row${p.status?.found.length ? "" : " unmatched"}">
        <div class="review-head"><b>${esc(p.name)}</b>
          ${normalise(p.name) !== normalise(p.input) ? `<span class="small muted">matched from “${esc(p.input)}”</span>` : ""}
          <button class="link" data-drop="${i}" aria-label="Remove ${esc(p.name)}">✕</button></div>
        <div class="small">${(p.status?.series || []).map(chip).join(" ")} <span class="muted">${esc(foundText(p.status))}</span></div>
      </div>`).join("")}
      <div><button class="btn primary" id="save-follows">Save ${pendingFollows.length === 1 ? "follow" : `${pendingFollows.length} follows`}</button> <button id="cancel-follows">Cancel</button></div>
    </div>` : "";

  box.innerHTML = `${list}
    <form class="form" id="follow-form">
      <input type="text" name="names" placeholder="Drivers or teams, e.g. Max Verstappen, Lia Block" autocomplete="off" ${pendingFollows.length ? "disabled" : ""}>
      ${pendingFollows.length ? "" : `<div><button class="btn primary">Find</button> <span class="small muted">Separate names with commas. Spelling doesn't need to be perfect.</span></div>`}
    </form>${review}`;

  box.querySelector("#follow-form").addEventListener("submit", async e => {
    e.preventDefault();
    const names = splitNames(e.target.names.value);
    if (!names.length) return;
    const typed = names.map(input => ({ input, name: matchName(input, entries)?.name || input }));
    const status = await fetch("/api/follow-status", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names: typed.map(t => t.name) }) }).then(r => r.json()).catch(() => []);
    pendingFollows = typed.map((t, i) => ({ ...t, status: status[i] }));
    rerender();
  });
  box.querySelectorAll("[data-drop]").forEach(b => b.addEventListener("click", () => {
    pendingFollows.splice(Number(b.dataset.drop), 1); rerender();
  }));
  box.querySelector("#cancel-follows")?.addEventListener("click", () => { pendingFollows = []; rerender(); });
  box.querySelector("#save-follows")?.addEventListener("click", () => {
    const have = new Set(follows().map(f => normalise(f.name)));
    store.set("follows", [...follows(), ...pendingFollows.filter(p => !have.has(normalise(p.name))).map(p => ({ name: p.name }))]);
    pendingFollows = [];
    rerender();
  });
  box.querySelectorAll("[data-unfollow]").forEach(b => b.addEventListener("click", () => {
    const l = follows(); l.splice(Number(b.dataset.unfollow), 1); store.set("follows", l); rerender();
  }));
}

// ---- live timing ----------------------------------------------------------------
const TYRES = { S: "#e10600", M: "#f5c400", H: "#e8e8e8", I: "#2fb34a", W: "#2a7de1" };
const TRACK_CLASS = { "Green": "ok", "Yellow": "warn", "Safety car": "warn", "Virtual safety car": "warn",
  "VSC ending": "warn", "Red flag": "bad" };
let liveTimer = null;

async function liveView(id) {
  setNav("series");
  const s = BY_ID[id];
  loadMorph();
  // The first draw replaces the page; each 2 s refresh after it changes only what moved, so a scroll, a selection or a
  // tap in progress survives the update.
  let drawn = false;
  const draw = html => {
    if (drawn && morph) morph(view, html, MORPH);
    else view.innerHTML = html;
    drawn = true;
  };
  const render = async () => {
    if (!location.hash.startsWith(`#/live/${id}`)) {
      clearInterval(liveTimer); liveTimer = null; return;
    }
    const d = await api(`/api/live/${id}`).catch(() => ({}));
    if (!d.rows) {
      draw(`${backLink(`#/series/${id}`)}<h1>${chip(id)} ${esc(s?.name || id)}</h1><div class="empty">No live session right now. Timing connects 15 minutes before each session.</div>`);
      return;
    }
    const mine = new Set(follows().map(f => normalise(f.name)));
    const isMine = r => [r.name, r.team, r.f1_team].some(n => n && mine.has(normalise(n)));
    if (d.kind === "rally") { draw(rallyLive(id, d, mine)); return; }
    const race = d.kind === "race";
    const blank = v => (v === "." || v === "-" ? "" : v);
    d.rows.forEach(r => ["gap", "interval", "last", "best"].forEach(k => { r[k] = blank(r[k]); }));
    draw(`${backLink(`#/series/${id}`)}<h1>${chip(id)} ${esc(d.event)}</h1>
      <div class="live-bar">
        <span class="pill">${esc(d.session)}</span>
        ${d.lap ? `<span class="pill">Lap ${d.lap}${d.total_laps ? ` / ${d.total_laps}` : ""}</span>` : ""}
        ${d.clock && !race ? `<span class="pill">${esc(d.clock)} left</span>` : ""}
        ${d.track ? `<span class="pill ${TRACK_CLASS[d.track] || ""}">${esc(d.track)}</span>` : ""}
        <span class="${d.connected ? "live" : "muted small"}">${d.connected ? "LIVE" : esc(d.status || "Finished")}</span>
      </div>
      <div class="card standings timing">
        <div class="trow head muted"><span>Pos</span><span>Driver</span><span>${race ? "Gap" : "Gap"}</span><span>${race ? "Int" : "Best"}</span><span>Last</span><span>${d.rows.some(r => r.tyre) ? "Tyre" : "Pits"}</span></div>
        ${d.rows.map(r => {
          // In the pits only means something while the session runs; it sits by the name so the gap stays readable.
          const flag = r.status === "OUT" || (r.status === "PIT" && d.connected) ? r.status : "";
          const code = `<b>${esc(r.code)}</b>${flag ? ` <small class="tstat${flag === "OUT" ? " bad" : ""}">${flag}</small>` : ""}`;
          return `<div class="trow${isMine(r) ? " followed" : ""}${r.status === "OUT" ? " out" : ""}">
          <span class="pos">${r.pos < 99 ? r.pos : ""}</span>
          <span class="who"><i class="bar" style="background:${esc(r.colour || "var(--border)")}"></i>${r.name
            ? `<a class="who-link" href="#/driver/${encodeURIComponent(r.name)}">${code}</a>` : code}<span class="muted">${esc(r.team || r.name)}${backerTag(r)}</span></span>
          <span class="num">${esc(r.gap || (r.pos === 1 ? (race ? "Leader" : r.best) : ""))}</span>
          <span class="num muted">${esc(race ? (r.pos === 1 ? "" : r.interval) : r.best)}</span>
          <span class="num">${esc(r.last)}</span>
          <span class="tyre">${r.tyre ? `<i style="background:${TYRES[r.tyre] || "#888"}">${esc(r.tyre)}</i>` : ""}${r.pits ? `<small>${r.pits}</small>` : ""}</span>
        </div>`;
        }).join("")}
      </div>
      ${eventsPanel(id)}`);
  };
  const on = listen();   // once per visit: the 2 s redraws keep these
  view.addEventListener("change", e => {
    if (e.target.id !== "keyonly") return;
    try { localStorage.setItem("liveKeyOnly", JSON.stringify(e.target.checked)); } catch { /* fine */ }
    render();
  }, on);
  view.addEventListener("click", e => {
    const b = e.target.closest("[data-rclass]");
    if (b) { rallyFilter[id] = b.dataset.rclass; render(); }
  }, on);
  clearInterval(liveTimer);
  await render();
  liveTimer = setInterval(() => { if (document.visibilityState === "visible") render(); }, 2000);
}

// Race events for the live screen, refreshed every 10 s (the leaderboard refreshes every 2 s).
const LIVE_EVENTS = {};
function eventsPanel(id) {
  const cache = LIVE_EVENTS[id];
  if (!cache || Date.now() - cache.at > 10000) {
    LIVE_EVENTS[id] = { at: Date.now(), items: cache?.items || [] };
    api(`/api/events/${id}`).then(items => { LIVE_EVENTS[id] = { at: Date.now(), items }; }).catch(() => {});
  }
  const keyOnly = local("liveKeyOnly", false);
  const items = (LIVE_EVENTS[id]?.items || []).filter(e => !keyOnly || e.important).slice(0, 40);
  if (!LIVE_EVENTS[id]?.items?.length) return "";
  return `<h2>Events</h2><label class="small"><input type="checkbox" id="keyonly" ${keyOnly ? "checked" : ""}> Key moments only</label>
    <div class="card">${items.map(e => `<div class="sevent${e.important ? " key" : ""}">
      <span class="slap">${e.lap ? `L${esc(e.lap)}` : esc(new Date(e.time).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }))}</span>
      <span class="pill ${EVENT_CLASS[e.kind] || ""}">${esc(EVENT_LABEL[e.kind] || e.kind)}</span>
      <span class="stext">${esc(e.text)}</span></div>`).join("") || `<div class="empty">No key moments yet.</div>`}</div>`;
}

// A rally is ~20 timed stages over several days: show where we are, the latest stage, and the overall order.
const rallyFilter = {};
const CLASS_HELP = { Rally1: "top-class WRC cars", Rally2: "WRC2", Rally3: "WRC3 & Junior WRC", Rally4: "national championships",
  Rally5: "entry level" };

function rallyLive(id, d, mine) {
  const classes = [...new Set(d.rows.map(r => r.class).filter(Boolean))];
  const pick = classes.includes(rallyFilter[id]) ? rallyFilter[id] : "";
  const keep = r => !pick || r.class === pick;
  const followed = r => mine.has(normalise(r.name)) || mine.has(normalise(r.codriver)) || mine.has(normalise(r.team));
  const pct = d.total_laps ? Math.round(100 * d.lap / d.total_laps) : 0;
  const row = (r, value, sub) => `<div class="trow rally${followed(r) ? " followed" : ""}">
      <span class="pos">${r.pos < 99 ? r.pos : ""}</span>
      <span class="who"><b>${esc(r.name)}</b><span class="muted">#${esc(r.number)} · ${esc(r.class)} · ${esc(r.team)}</span></span>
      <span class="num">${esc(value)}</span><span class="num muted">${esc(sub)}</span></div>`;
  const stageRows = d.stage_rows.filter(keep).slice(0, 10);
  return `${backLink(`#/series/${id}`)}<h1>${chip(id)} ${esc(d.event)}</h1>
    <div class="live-bar"><span class="pill">Stage ${d.lap} of ${d.total_laps}</span>
      <span class="${d.connected ? "live" : "muted small"}">${d.connected ? "LIVE" : "Finished"}</span></div>
    <div class="progress" aria-label="${pct}% of stages complete"><i style="width:${pct}%"></i></div>
    ${d.next ? `<p class="small muted">Next: <b>${esc(d.next.code)} ${esc(d.next.name)}</b>${d.next.km ? ` · ${d.next.km} km` : ""}</p>` : ""}
    ${classes.length > 1 ? `<div class="chips"><button class="chip-btn${pick ? "" : " active"}" data-rclass="">All</button>${classes.map(c =>
      `<button class="chip-btn${c === pick ? " active" : ""}" data-rclass="${esc(c)}" title="${esc(CLASS_HELP[c] || "")}">${esc(c)}</button>`).join("")}</div>
      <p class="small muted">${classes.map(c => CLASS_HELP[c] ? `${esc(c)} = ${CLASS_HELP[c]}` : "").filter(Boolean).join(" · ")}</p>` : ""}
    <h2>${esc(d.stage.code || "Stage")} ${esc(d.stage.name || "")}${d.stage.km ? ` · ${d.stage.km} km` : ""} · ${esc(d.stage.status || "")}</h2>
    <div class="card standings timing">${stageRows.length ? stageRows.map(r => row(r, r.time, r.gap)).join("") : `<div class="empty">No stage times yet.</div>`}</div>
    <h2>Overall</h2>
    <div class="card standings timing">${d.rows.filter(keep).map(r => row(r, r.pos === 1 ? r.total : r.gap, r.penalty ? `pen ${r.penalty}` : r.interval)).join("")}</div>`;
}

// ---- the Wire: news + race events ------------------------------------------------------
let NAMES = null;
const loadNames = async () => (NAMES ||= await api("/api/names").catch(() => ({ drivers: [], teams: [] })));
const wirePath = st => {
  const qs = new URLSearchParams();
  if (st.kind) qs.set("kind", st.kind);
  qs.set("series", st.series || enabledSeries().join(","));
  if (st.driver) qs.set("driver", st.driver);
  if (st.team) qs.set("team", st.team);
  if (st.mine) qs.set("mine", "1");
  return `/api/wire?${qs}`;
};

const EVENT_LABEL = { caution: "Caution", red_flag: "Red flag", incident: "Incident", investigation: "Investigation",
  penalty: "Penalty", retired: "Out", lead: "Lead", stage: "Stage", result: "Result", start: "Start", restart: "Restart",
  note: "Note", pit: "Pit", track_limits: "Track limits", info: "Race control" };
const EVENT_CLASS = { caution: "warn", red_flag: "bad", penalty: "bad", retired: "bad", incident: "warn",
  investigation: "warn", result: "ok", lead: "ok", stage: "ok", start: "ok" };

const nameLinks = i => [...i.drivers.map(n => `<a class="tag" href="#/driver/${encodeURIComponent(n)}">${esc(n)}</a>`),
  ...i.teams.map(t => `<a class="tag team" href="#/team/${encodeURIComponent(t)}">${esc(t)}</a>`)].join("");

function isMine(i) {
  const f = follows().map(x => normalise(x.name));
  return [...i.drivers, ...i.teams].some(n => { const k = normalise(n); return f.some(x => x === k || (x.length >= 4 && (k.includes(x) || x.includes(k)))); });
}

function wireItem(i) {
  const chips = (i.series || []).filter(id => BY_ID[id]).map(chip).join(" ");
  const mine = isMine(i) ? " followed" : "";
  if (i.type === "news") {
    return `<div class="witem${mine}"><div class="wmeta">${chips}<b>${esc(i.source)}</b><span>${esc(ago(i.time))}</span></div>
      <a class="wtitle" href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a>
      ${i.text ? `<div class="wtext">${esc(i.text)}</div>` : ""}<div class="wtags">${nameLinks(i)}</div></div>`;
  }
  return `<div class="witem${mine}"><div class="wmeta">${chips}<span class="pill ${EVENT_CLASS[i.kind] || ""}">${esc(EVENT_LABEL[i.kind] || i.kind)}</span>
      <span>${esc(i.session || "")}</span><span>${esc(ago(i.time))}</span></div>
    <div class="wevent">${i.lap ? `<b>L${esc(i.lap)}</b> ` : ""}${esc(i.text)}</div>
    <div class="wtags">${nameLinks(i)}${i.event_key ? `<a class="tag story" href="#/story/${esc(i.series[0])}/${encodeURIComponent(i.event_key)}">Race story →</a>` : ""}</div></div>`;
}

async function wireView() {
  setNav("wire");
  const st = { kind: "", series: "", driver: "", team: "", mine: false, ...local("wire", {}) };
  const save = next => { try { localStorage.setItem("wire", JSON.stringify(next)); } catch { /* fine */ } wireView(); };
  const [items, dir] = await Promise.all([fast(wirePath(st)), loadNames()]);
  const shown = st.series ? [st.series] : enabledSeries();
  const inScope = x => x.series.some(id => shown.includes(id));
  view.innerHTML = `<h1>Wire</h1>
    <div class="tabs">${[["", "All"], ["news", "News"], ["events", "Race events"]].map(([k, l]) =>
      `<a href="javascript:void 0" data-kind="${k}" class="${st.kind === k ? "active" : ""}">${l}</a>`).join("")}</div>
    <div class="chips"><button class="chip-btn${st.series ? "" : " active"}" data-series="">My series</button>${enabledSeries().map(id =>
      `<button class="chip-btn${st.series === id ? " active" : ""}" data-series="${id}">${esc(BY_ID[id].short)}</button>`).join("")}</div>
    <div class="filters">
      <input list="dl-drivers" id="f-driver" placeholder="Driver" value="${esc(st.driver)}">
      <input list="dl-teams" id="f-team" placeholder="Team" value="${esc(st.team)}">
      <label class="small"><input type="checkbox" id="f-mine" ${st.mine ? "checked" : ""}> Only who I follow</label>
      ${st.driver || st.team ? `<button class="link" id="f-clear">Clear</button>` : ""}
    </div>
    <datalist id="dl-drivers">${dir.drivers.filter(inScope).map(d => `<option value="${esc(d.name)}">`).join("")}</datalist>
    <datalist id="dl-teams">${dir.teams.filter(inScope).map(t => `<option value="${esc(t.name)}">`).join("")}</datalist>
    ${items.length ? `<div class="card">${items.map(wireItem).join("")}</div>`
      : `<div class="empty">Nothing matches.${st.mine ? " Try turning off \"Only who I follow\"." : ""}</div>`}
    <p class="small muted">News from Motorsport.com, Autosport, RACER, The Race, DirtFish and the series' own sites. Race events come from each series' live timing.</p>`;
  view.querySelectorAll("[data-kind]").forEach(b => b.addEventListener("click", () => save({ ...st, kind: b.dataset.kind })));
  view.querySelectorAll("[data-series]").forEach(b => b.addEventListener("click", () => save({ ...st, series: b.dataset.series })));
  view.querySelector("#f-driver").addEventListener("change", e => save({ ...st, driver: e.target.value.trim() }));
  view.querySelector("#f-team").addEventListener("change", e => save({ ...st, team: e.target.value.trim() }));
  view.querySelector("#f-mine").addEventListener("change", e => save({ ...st, mine: e.target.checked }));
  view.querySelector("#f-clear")?.addEventListener("click", () => save({ ...st, driver: "", team: "" }));
}

// ---- driver and team pages ------------------------------------------------------------------
async function personView(type, name) {
  setNav("wire");
  if (!QUIET_ROUTE) showLoading(`Loading ${esc(name)}…`);
  // Wire items are tagged with the grid's spelling; a follow may be typed differently or be a team.
  const personPath = `/api/person/${encodeURIComponent(name)}`;
  const tagFor = p => {
    const asDriver = p?.rides?.find(r => normalise(r.name) === normalise(name))?.name;
    return { asDriver, tagged: type === "team" || (!asDriver && p?.rides?.length) ? "team" : "driver" };
  };
  const wirePath = p => {
    const { asDriver, tagged } = tagFor(p);
    return `/api/wire?${tagged}=${encodeURIComponent(asDriver || (tagged === "team" ? (p?.rides?.[0]?.team || name) : name))}`;
  };
  // Driver pages also get a profile (photo, Wikipedia lines, F1 career), asked for under the grid's spelling.
  const profilePath = p => type === "driver" ? `/api/profile/${encodeURIComponent(tagFor(p).asDriver || name)}` : null;
  // Seen before: the Wire and profile requests go out with the person's, from the phone's copy of who they are.
  const copy = GET_CACHE.get(personPath)?.data;
  const guess = copy ? wirePath(copy) : null, guessProfile = copy ? profilePath(copy) : null;
  const [p, dir, early, earlyProfile] = await Promise.all([fast(personPath).catch(() => null), loadNames(),
    guess && fast(guess).catch(() => []), guessProfile && fast(guessProfile).catch(() => null)]);
  const [items, prof] = await Promise.all([guess === wirePath(p) ? early : fast(wirePath(p)).catch(() => []),
    !profilePath(p) ? null : guessProfile === profilePath(p) ? earlyProfile : fast(profilePath(p)).catch(() => null)]);
  const { tagged } = tagFor(p);
  const entry = (type === "driver" ? dir.drivers : dir.teams).find(x => x.name === name);
  const followed = follows().some(f => normalise(f.name) === normalise(name));
  const seriesIds = p?.series?.length ? p.series : (entry?.series || []);
  // Where they race: one line per series with team and car number, plus events they've been entered in.
  const rides = Object.values((p?.rides || []).reduce((m, r) => (m[r.series] ||= r, m), {}));
  const entries = (p?.found || []).filter(f => f.kind === "entry");
  const where = rides.map(r => `<div class="item"><div>${chip(r.series)} <b>${esc(BY_ID[r.series]?.name || r.series)}</b>
      <div class="muted small">${esc([r.team, r.number ? `#${r.number}` : ""].filter(Boolean).join(" · "))}</div></div></div>`).join("")
    + entries.map(f => `<div class="item"><div>${chip(f.series)} Entered: <b>${esc(f.event_name)}</b>
      <div class="muted small">${esc(shortDate(f.start_day))}${(p?.new || []).some(n => n.event_key === f.event_key) ? " · NEW" : ""}</div></div></div>`).join("");
  const last = (p?.results || []).map(x => `<div class="item"><div>${chip(x.series)} <b>${/^\d+$/.test(x.pos) ? `P${esc(x.pos)}` : esc(x.pos)}</b> · ${esc(x.event)}
      <div class="muted small">${esc(x.session || "")}${x.time ? ` · ${esc(x.time)}` : ""}${x.points ? ` · ${x.points} pts` : ""}</div></div></div>`).join("");
  const upcoming = p?.upcoming || [];
  const w = prof?.wiki, a = prof?.f1a;   // a = the F1 ACADEMY profile: its photo and bio come first
  const photo = a?.photo || w?.photo;
  const about = a ? [a.wildcard ? "Wild card" : "", [a.team, a.number ? `#${a.number}` : ""].filter(Boolean).join(" "), a.nationality,
    a.born ? `${ageOn(a.born)} years old` : ""].filter(Boolean).join(" · ") : w?.description;
  const bio = a?.bio?.length ? `<p class="bio">${esc(a.bio[0])}</p>${a.quote ? `<blockquote class="quote">“${esc(a.quote)}”</blockquote>` : ""}
      ${a.bio.length > 1 ? `<details class="more-bio"><summary>More about ${esc(name.split(" ")[0])}</summary>${a.bio.slice(1).map(x => `<p class="bio">${esc(x)}</p>`).join("")}</details>` : ""}`
    : w?.extract ? `<p class="bio">${esc(w.extract)}</p>` : prof?.pending ? `<p class="small muted">Looking up their photo and career…</p>` : "";
  const outLinks = a ? [["F1 ACADEMY profile", a.url], ["Instagram", a.links?.instagram], ["YouTube", a.links?.youtube]].filter(([, u]) => u)
    .map(([k, u]) => `<a class="btn" href="${esc(u)}" target="_blank" rel="noopener">${k} ↗</a>`).join("") : "";
  const academy = p?.academy || [];
  paint(`${backLink("#/")}<div class="dhead">${photo ? `<img class="dphoto" src="${esc(photo)}" alt="" width="88" height="88">` : ""}
      <div><h1>${esc(name)}</h1>${about ? `<div class="muted small">${esc(about)}</div>` : ""}
      ${a?.backer ? `<div class="small backed">Races in ${a.f1_team ? "the colours of " : ""}${backerTag({ ...a, wildcard: false })}${a.f1_team ? "" : " colours"}</div>` : ""}</div></div>
    <div class="person-head">${seriesIds.map(chip).join(" ")} <span class="muted small">${tagged === "team" ? "Team" : "Driver"}</span>
      <button class="btn${followed ? "" : " primary"}" id="follow">${followed ? "Following ✓" : "Follow"}</button></div>
    ${bio}
    ${outLinks ? `<div class="series-links">${outLinks}</div>` : ""}
    ${academy.length ? `<h2>In F1 ACADEMY</h2><p class="small muted">The all-women series on F1 weekends. These drivers race in ${esc(name)}'s colours.</p>
      <div class="card standings">${academy.map(d => `<div class="srow"><span class="pos">${esc(d.number ? `#${d.number}` : "")}</span>
        <span class="who"><a class="who-link" href="#/driver/${encodeURIComponent(d.name)}"><b>${esc(d.name)}</b></a><span class="muted">${esc(d.team)}</span></span></div>`).join("")}</div>` : ""}
    ${seasonCards(p?.cards)}
    ${oddsOn() && p?.odds?.length ? `<h2>Odds</h2><div class="card">${p.odds.map(x => `<a class="item" href="#/event/${x.series}/${encodeURIComponent(x.event_key)}"><div>${chip(x.series)} <b>${esc(x.event)}</b>
      <div class="small">${x.lines.map(l => `${esc(l.market.replace(/ Finish$/, ""))} <b>${esc(l.american)}</b> <span class="muted">(${pctText(l.pct)})</span>`).join(" · ")}</div></div></a>`).join("")}</div>
      ${oddsHelp()}` : ""}
    ${where ? `<h2>Where they race</h2><div class="card">${where}</div>` : `<p class="small muted">Not on any current grid or published entry list. If you follow them, every series is still watched.</p>`}
    ${upcoming.length ? `<h2>Coming up</h2><div class="card">${upcoming.map(x => sessionRow({ ...x }, new Set())).join("")}</div>` : ""}
    ${last ? `<h2>Last race</h2><div class="card">${last}</div>` : ""}
    ${careerBlock(prof?.f1)}
    ${a?.articles?.length ? `<h2>From F1 ACADEMY</h2><div class="card">${a.articles.map(n => `<a class="item news-item" href="${esc(n.url)}" target="_blank" rel="noopener">
      <div><b>${esc(n.title)}</b><div class="muted small">${esc(n.time ? ago(n.time) : "")}</div></div></a>`).join("")}</div>` : ""}
    <h2>News and race events</h2>
    ${items.length ? `<div class="card">${items.map(wireItem).join("")}</div>` : `<div class="empty">Nothing in the last 30 days.</div>`}
    ${a ? `<p class="credit small muted">Photo and bio: <a href="${esc(a.url)}" target="_blank" rel="noopener">F1 ACADEMY</a>${w ? ` · More: <a href="${esc(w.url)}" target="_blank" rel="noopener">Wikipedia</a>` : ""}</p>`
      : w ? `<p class="credit small muted">${w.credit ? `Photo: <a href="${esc(w.credit.url)}" target="_blank" rel="noopener">${esc(w.credit.author || "Wikimedia Commons")}</a>${w.credit.license ? `, ${esc(w.credit.license)}` : ""} · ` : ""}About: <a href="${esc(w.url)}" target="_blank" rel="noopener">Wikipedia</a>${prof.f1 ? " · F1 stats: Jolpica" : ""}</p>` : ""}`);
  if (prof?.pending) waitForProfile(profilePath(p));
  view.addEventListener("click", e => {
    if (!e.target.closest("#follow")) return;
    const l = follows();
    store.set("follows", followed ? l.filter(f => normalise(f.name) !== normalise(name)) : [...l, { name }]);
    loadFollowStatus().then(() => personView(type, name));
  }, listen());
}

const ageOn = born => { const b = new Date(`${born}T12:00:00`), n = new Date();
  return n.getFullYear() - b.getFullYear() - (n < new Date(n.getFullYear(), b.getMonth(), b.getDate()) ? 1 : 0); };

// The profile is built in the background the first time anyone opens the page: ask again, redrawing as parts land.
function waitForProfile(path, tries = 0) {
  const at = location.hash, before = JSON.stringify(GET_CACHE.get(path)?.data);
  setTimeout(() => api(path).then(d => {
    if (location.hash !== at) return;
    if (!d.pending || JSON.stringify(d) !== before) route(true);   // the photo can land before the F1 career
    else if (tries < 30) waitForProfile(path, tries + 1);
  }).catch(() => {}), 2000);
}

// This season, one card per championship: points, position, wins and form, as on Home.
const seasonCards = cards => !cards?.length ? "" : `<h2>This season</h2><div class="heroes${cards.length % 2 ? " odd" : ""}">${cards.map(c =>
  `<div class="hero plain" style="--tc:${esc(c.color)}">
    <div class="hero-top">${chip(c.series)}${c.team ? `<span class="hero-team">${esc(c.team)}</span>` : ""}<span class="muted">${esc(c.season)}</span></div>
    <div class="hero-pts"><b>${pts(c.points)}</b><span>PTS</span></div>
    <div class="hero-stats">${heroStats(c)}</div>${formBars(c.form)}</div>`).join("")}</div>`;

// Formula 1 career (Jolpica): totals, then season by season.
function careerBlock(f) {
  if (!f?.races) return "";
  const place = x => /^\d+$/.test(x || "") ? `P${x}` : esc(x || "–");
  const tiles = [["Titles", f.titles.length], ["Wins", f.wins], ["Podiums", f.podiums], ["Poles", f.poles], ["Races", f.races],
    ["Points", pts(f.points)]].map(([k, v]) => `<div><b>${v}</b><span>${k}</span></div>`).join("");
  const facts = [f.titles.length ? `Champion ${[...f.titles].sort().join(", ")}` : "",
    f.first ? `First race: ${esc(f.first.race)} ${f.first.season}` : "",
    f.first_win ? `First win: ${esc(f.first_win.race)} ${f.first_win.season}` : f.best ? `Best finish: P${f.best}` : ""].filter(Boolean);
  const rows = f.seasons.map(s => `<tr><td>${s.season}</td><td>${esc(s.teams.join(" / "))}</td><td>${place(s.pos)}</td>
    <td>${pts(s.points)}</td><td>${s.wins}</td></tr>`).join("");
  return `<h2>F1 career</h2><div class="card"><div class="tiles">${tiles}</div>
    ${facts.length ? `<div class="facts small muted">${facts.join(" · ")}</div>` : ""}
    <details class="seasons"${f.seasons.length <= 4 ? " open" : ""}><summary>Season by season (${f.seasons.length})</summary>
      <table><thead><tr><th>Year</th><th>Team</th><th>Pos</th><th>Pts</th><th>Wins</th></tr></thead><tbody>${rows}</tbody></table></details></div>`;
}

// ---- race story: everything that happened at one event ----------------------------------------
async function storyView(series, eventKey) {
  setNav("series");
  const s = BY_ID[series];
  const [events, list] = await Promise.all([api(`/api/story/${series}/${encodeURIComponent(eventKey)}`),
    api(`/api/series/${series}/events`).catch(() => [])]);
  const ev = list.find(e => e.key === eventKey);
  const keyOnly = local("storyKeyOnly", false);
  // In lap order: lap notes carry no clock time, and the result belongs at the end.
  const lapOf = e => e.lap ?? (e.kind === "result" ? 1e6 : 0);
  const rows = events.filter(e => !keyOnly || e.important).sort((a, b) => lapOf(a) - lapOf(b) || a.time.localeCompare(b.time));
  const groups = new Map();
  for (const e of rows) { const k = e.session || "Race"; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); }
  view.innerHTML = `${backLink(`#/series/${series}`)}<h1>${chip(series)} ${esc(ev?.name || s?.name || series)}</h1>
    <p class="muted small">Race story · ${events.length} events</p>
    <label class="small"><input type="checkbox" id="keyonly" ${keyOnly ? "checked" : ""}> Key moments only</label>
    ${[...groups].map(([sess, list]) => `<h2>${esc(sess)}</h2><div class="card">${list.map(e => `<div class="sevent${e.important ? " key" : ""}">
      <span class="slap">${e.lap ? `L${esc(e.lap)}` : esc(new Date(e.time).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }))}</span>
      <span class="pill ${EVENT_CLASS[e.kind] || ""}">${esc(EVENT_LABEL[e.kind] || e.kind)}</span>
      <span class="stext">${esc(e.text)}</span></div>`).join("")}</div>`).join("") || `<div class="empty">Nothing recorded for this event.</div>`}`;
  view.querySelector("#keyonly").addEventListener("change", e => {
    try { localStorage.setItem("storyKeyOnly", JSON.stringify(e.target.checked)); } catch { /* fine */ }
    storyView(series, eventKey);
  });
}

// ---- getting the app + alerts (same approach as Shots on Goal) -------------------------------
let INSTALL_PROMPT = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); INSTALL_PROMPT = e; });
const isStandalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
function device() {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)) return /CriOS|FxiOS|EdgiOS/.test(ua) ? "ios-other" : "ios";
  return /Android/.test(ua) ? "android" : "desktop";
}
const SHARE_ICON = `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M8 7l4-4 4 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M6 11H5v10h14V11h-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
const DOTS_ICON = `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5" r="2" fill="currentColor"/><circle cx="12" cy="12" r="2" fill="currentColor"/><circle cx="12" cy="19" r="2" fill="currentColor"/></svg>`;
const b64ToBytes = b64 => Uint8Array.from(atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - b64.length % 4) % 4)), c => c.charCodeAt(0));

async function getAppBanner() {
  if (local("getappHidden", false)) return "";
  if (!isStandalone() && device() !== "desktop") return "<b>Put Lights Out on your home screen</b> and get alerts for the drivers you follow.";
  if ("PushManager" in window && Notification.permission === "default") {
    const reg = await Promise.race([navigator.serviceWorker?.ready, new Promise(r => setTimeout(() => r(null), 2000))]).catch(() => null);
    const sub = reg ? await reg.pushManager.getSubscription().catch(() => null) : null;
    if (!sub) return "<b>Turn on alerts</b>: race starts, cautions, and news about the drivers you follow.";
  }
  return "";
}

async function showNotifications(box) {
  if (!box) return;
  const standalone = isStandalone(), ios = device().startsWith("ios");
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    box.innerHTML = `<div class="form small">${ios && !standalone
      ? "On iPhone, alerts only work from the home-screen app. Add Lights Out to your home screen, open it from there, and come back here."
      : "This browser can't receive notifications."}${location.hash !== "#/app" ? ` <a href="#/app">Show me how</a>` : ""}</div>`;
    return;
  }
  const reg = await Promise.race([navigator.serviceWorker.ready, new Promise(r => setTimeout(() => r(null), 5000))]);
  if (!reg) { box.innerHTML = `<div class="form small">Couldn't check this device. Reload the page and try again.</div>`; return; }
  const sub = await reg.pushManager.getSubscription();
  const info = await api("/api/push").catch(() => null);
  const blocked = Notification.permission === "denied";
  box.innerHTML = `<div class="form"><div class="small">${blocked ? "Notifications are blocked for this site in your browser settings."
      : sub ? "On for this device." : "Off for this device."}${info?.devices ? ` <span class="muted">(${info.devices} device${info.devices > 1 ? "s" : ""} signed up)</span>` : ""}</div>
    <div>${blocked ? "" : sub ? `<button id="pushoff">Turn off here</button> <button id="pushtest">Send a test</button>`
      : `<button class="btn primary" id="pushon">Turn on for this device</button>`}</div><div class="small muted" id="pushmsg"></div></div>`;
  const msg = t => { box.querySelector("#pushmsg").textContent = t; };
  box.querySelector("#pushon")?.addEventListener("click", async e => {
    e.target.disabled = true;
    try {
      if (await Notification.requestPermission() !== "granted") { msg("Permission wasn't given."); e.target.disabled = false; return; }
      const s2 = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(info.key) });
      await fetch("/api/push/subscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subscription: s2.toJSON() }) });
      showNotifications(box);
    } catch (err) { msg(`Couldn't turn them on: ${err.message || err}`); e.target.disabled = false; }
  });
  box.querySelector("#pushoff")?.addEventListener("click", async () => {
    await fetch("/api/push/unsubscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint: sub.endpoint }) }).catch(() => {});
    await sub.unsubscribe();
    showNotifications(box);
  });
  box.querySelector("#pushtest")?.addEventListener("click", async e => {
    e.target.disabled = true;
    const r = await fetch("/api/push/test", { method: "POST" }).then(x => x.json()).catch(() => ({ sent: 0 }));
    msg(r.sent ? "Sent. It should pop up in a few seconds." : "Couldn't deliver it. Try turning alerts off and on again.");
    e.target.disabled = false;
  });
}

const ALERT_KINDS = [["start", "Race and sprint starts (with where to watch)"], ["cautions", "Cautions, safety cars and red flags"],
  ["incidents", "Your drivers: incidents, penalties, retirements, taking the lead"], ["results", "Results, with where your drivers finished"],
  ["news", "News about your drivers and teams"]];

function alertToggles() {
  const n = store.get("notify", {});
  return `<div class="form">${ALERT_KINDS.map(([k, l]) => `<label class="small"><input type="checkbox" data-notify="${k}" ${n[k] !== false ? "checked" : ""}> ${l}</label>`).join("")}
    <div class="small muted">Race-wide alerts are for series where you follow someone; the rest are for the drivers and teams you follow.</div></div>`;
}

function bindAlertToggles() {
  view.querySelectorAll("[data-notify]").forEach(cb => cb.addEventListener("change", () => {
    store.set("notify", Object.fromEntries(ALERT_KINDS.map(([k]) => [k, view.querySelector(`[data-notify="${k}"]`).checked])));
  }));
}

function appView() {
  setNav("settings");
  const d = device();
  let step1;
  if (isStandalone()) step1 = `<p class="small" style="color:var(--free-fg);font-weight:650">✓ You're using the home-screen app.</p>`;
  else if (d === "ios") step1 = `<ol class="steps"><li>Tap <b>Share</b> ${SHARE_ICON} at the bottom of Safari (top right on iPad).</li>
      <li>Scroll down and tap <b>Add to Home Screen</b>.</li><li>Tap <b>Add</b>, open <b>Lights Out</b> from your home screen, and come back here (Settings → Get the app).</li></ol>`;
  else if (d === "ios-other") step1 = `<p class="small">On iPhone this works best from <b>Safari</b>: open ${esc(location.origin)}/#/app there.</p>`;
  else if (INSTALL_PROMPT) step1 = `<button class="btn primary" id="install">Install Lights Out</button>`;
  else if (d === "android") step1 = `<ol class="steps"><li>In Chrome, tap the menu ${DOTS_ICON} at the top right.</li>
      <li>Tap <b>Add to Home screen</b> (or <b>Install app</b>), then <b>Install</b>.</li><li>Open <b>Lights Out</b> from your home screen.</li></ol>`;
  else step1 = `<p class="small">Optional on a computer; alerts work in the browser. In Chrome or Edge, use the install icon at the right of the address bar.</p>`;
  view.innerHTML = `${backLink("#/settings", "Settings")}<h1>Get the app</h1>
    <p class="muted">On your home screen, Lights Out opens like an app and can alert you about the drivers you follow.</p>
    <h2>1. Add it to your home screen</h2><div class="card"><div class="form">${step1}</div></div>
    <h2>2. Turn on alerts</h2><div class="card" id="notif"><div class="empty">Checking this device…</div></div>
    <h2>3. Pick what you hear about</h2><div class="card">${alertToggles()}</div>
    <p class="small muted">${follows().length ? "" : "Follow some drivers or teams first (Settings → Following), or alerts have no one to be about."}</p>`;
  showNotifications(view.querySelector("#notif"));
  bindAlertToggles();
  view.querySelector("#install")?.addEventListener("click", async () => { INSTALL_PROMPT.prompt(); await INSTALL_PROMPT.userChoice.catch(() => null); INSTALL_PROMPT = null; appView(); });
}

// ---- What's new ------------------------------------------------------------------------
let CHANGES = [];  // newest first, from /api/changes
// Unseen = newer than the last entry this person opened the page at; someone who never has sees the last week's.
function unseenChanges() {
  const seen = store.get("seenChanges", null);
  const i = seen ? CHANGES.findIndex(c => c.id === seen) : -1;
  if (i >= 0) return CHANGES.slice(0, i);
  const week = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
  return CHANGES.filter(c => c.date >= week);
}
function markChangesSeen() {
  if (CHANGES.length && store.get("seenChanges", null) !== CHANGES[0].id) store.set("seenChanges", CHANGES[0].id);
  updateNewDot();
}
function updateNewDot() {
  document.querySelector('[data-nav="settings"]')?.classList.toggle("has-new", unseenChanges().length > 0);
}
function newBanner() {
  const fresh = unseenChanges();
  if (!fresh.length) return "";
  const lead = fresh.find(c => c.link !== "#/new") || fresh[0];  // headline a feature, not this page itself
  const more = fresh.length > 1 ? ` and ${fresh.length - 1} more` : "";
  return `<div class="newbar"><span class="pill">New</span><span><b>${esc(lead.title)}</b>${more}</span>
    <a class="btn" href="#/new">See what's new</a><button class="link x" id="newbar-x" aria-label="Dismiss">✕</button></div>`;
}
const KIND_NAME = { new: "New", improved: "Improved", fixed: "Fixed" };
function whatsNewView() {
  setNav("settings");
  const fresh = new Set(unseenChanges().map(c => c.id));
  const days = CHANGES.reduce((m, c) => ((m[c.date] ||= []).push(c), m), {});
  view.innerHTML = `${backLink("#/settings", "Settings")}<h1>What's new</h1><p class="muted">Every update to Lights Out, newest first.</p>
    ${Object.entries(days).map(([d, list]) => `<h2>${esc(new Date(d + "T12:00").toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" }))}</h2>
      <div class="card">${list.map(c => `<div class="change${fresh.has(c.id) ? " fresh" : ""}">
        <div class="change-head"><span class="pill ${c.kind === "new" ? "ok" : ""}">${esc(KIND_NAME[c.kind] || c.kind)}</span>
          <b>${esc(c.title)}</b>${fresh.has(c.id) ? `<span class="dot" aria-label="unread"></span>` : ""}</div>
        <div class="change-text">${esc(c.text)}</div>
        ${c.link && c.link !== "#/new" ? `<a class="change-link" href="${esc(c.link)}">Try it →</a>` : ""}</div>`).join("")}</div>`).join("")}
    <p class="muted small source-note">Lights Out is free software (AGPL-3.0). <a href="${esc(document.querySelector('meta[name="source"]')?.content || "")}" target="_blank" rel="noopener">Source code ↗</a></p>`;
  markChangesSeen();
}

// ---- access gate --------------------------------------------------------------------
// Cloudflare signs everyone in with an email code; the app keeps its own approved list.
let ME = null;

function gateView() {
  document.getElementById("tabs").hidden = true;
  const st = ME.status;
  let body;
  if (st === "signed-out") {
    body = `<h1>Signed out</h1><p class="muted">Reload the page to sign in.</p>`;
  } else if (st === "pending") {
    body = `<h1>Request sent</h1>
      <p class="muted">You're on the list as <b>${esc(ME.email)}</b>. Once you're approved, just reload this page.</p>
      <button class="btn" id="reload">Reload</button>`;
  } else if (st === "denied" || st === "revoked") {
    body = `<h1>No access</h1><p class="muted">${esc(ME.email)} doesn't have access to Lights Out.</p>`;
  } else {
    body = `<h1>Lights Out</h1>
      <p class="muted">Racing schedules, live timing and where to watch, for a few friends.</p>
      <p>You're signed in as <b>${esc(ME.email)}</b>.</p>
      <button class="btn primary" id="ask">Request access</button>`;
  }
  view.innerHTML = `<div class="gate"><div class="card">${body}</div></div>`;
  document.getElementById("reload")?.addEventListener("click", () => location.reload());
  document.getElementById("ask")?.addEventListener("click", async e => {
    e.target.disabled = true;
    try {
      const r = await fetch("/api/request", { method: "POST" }).then(x => x.json());
      ME.status = r.status;
    } catch { ME.status = "pending"; }
    gateView();
  });
}

const ago = iso => {
  if (!iso) return "";
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

// Page views (anonymous, your own not counted): which pages friends actually open.
function usageBlock(u) {
  if (!u?.pages.length) return `<h2>Page views</h2><div class="card"><div class="empty">No views counted yet.</div></div>`;
  const month = u.pages.reduce((n, p) => n + p.month, 0);
  return `<h2>Page views</h2>
    <div class="card"><table class="board"><tr><th>Page</th><th>7 days</th><th>30 days</th><th>Since ${esc(u.first)}</th></tr>
      ${u.pages.map(p => `<tr><td>${esc(VIEW_NAME[p.route] || p.route)}</td><td>${p.week}</td><td>${p.month}</td><td>${p.all}</td></tr>`).join("")}
    </table></div>
    <p class="small muted">${month} views in the last 30 days. Counted per page and day, never who; your own views aren't counted.</p>`;
}

async function adminView() {
  setNav("admin");
  if (!ME.admin) { view.innerHTML = `<div class="empty">Admin only.</div>`; return; }
  const send = (path, method, body) => fetch(path, { method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined }).then(r => r.json());
  const d = await api("/api/admin");
  const people = st => d.users.filter(u => u.status === st);
  const row = (u, buttons) => `<div class="person"><span class="em">${esc(u.email)}</span><span class="when">${esc(ago(u.requested || u.decided))}</span>${buttons}</div>`;
  const btn = (email, status, label, primary) => `<button class="${primary ? "btn primary" : "btn"}" data-email="${esc(email)}" data-status="${status}">${label}</button>`;
  const pending = people("pending"), approved = people("approved"), other = d.users.filter(u => ["denied", "revoked"].includes(u.status));
  const openInvites = d.invites.filter(i => i.uses.length < i.max_uses);
  view.innerHTML = `${backLink("#/settings", "Settings")}<h1>Admin</h1>
    <h2>Waiting for approval</h2>
    <div class="card">${pending.length ? pending.map(u => row(u, btn(u.email, "approved", "Approve", true) + btn(u.email, "denied", "Deny"))).join("") : `<div class="empty">Nobody waiting.</div>`}</div>
    <h2>Invite a friend</h2>
    <div class="card"><div class="copyrow"><input type="text" id="note" placeholder="Who it's for (optional)"><input type="number" id="uses" min="1" max="50" value="1" inputmode="numeric" aria-label="How many people" title="How many people can use it" style="flex:none;width:4.5em"><button class="btn primary" id="mkinvite">Make link</button></div>
      <div id="newlink"></div>
      ${openInvites.map(i => `<div class="person"><span class="em">${esc(i.note || "Unnamed invite")}
          <div class="when">${i.max_uses > 1 ? `${i.uses.length} of ${i.max_uses} joined` : "For one person"}${i.uses.length ? `: ${i.uses.map(u => esc(u.email)).join(", ")}` : ""}</div></span>
        <span class="when">${esc(ago(i.created))}</span>
        <button class="btn" data-copy="${esc(i.link)}">Copy link</button><button class="btn" data-del="${esc(i.code)}">${i.uses.length ? "Close" : "Delete"}</button></div>`).join("")}</div>
    <p class="small muted">Set how many people can use a link (one for a single friend, more for a group chat). Whoever signs in with it is approved straight away. Close a group link to stop it.</p>
    <h2>Approved (${approved.length})</h2>
    <div class="card">${approved.map(u => row(u, ME.email === u.email ? `<span class="small muted">You</span>` : btn(u.email, "revoked", "Revoke"))).join("")}</div>
    ${other.length ? `<h2>Denied or revoked</h2><div class="card">${other.map(u => row(u, btn(u.email, "approved", "Approve"))).join("")}</div>` : ""}
    ${usageBlock(d.usage)}`;
  view.querySelectorAll("[data-status]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    await send("/api/admin/users", "POST", { email: b.dataset.email, status: b.dataset.status });
    adminView();
  }));
  const copy = async (text, b) => {
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch { prompt("Copy this link:", text); }
  };
  view.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", () => copy(b.dataset.copy, b)));
  view.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", async () => {
    await send(`/api/admin/invites/${encodeURIComponent(b.dataset.del)}`, "DELETE");
    adminView();
  }));
  document.getElementById("mkinvite").addEventListener("click", async () => {
    const r = await send("/api/admin/invites", "POST", { note: document.getElementById("note").value, uses: +document.getElementById("uses").value || 1 });
    await adminView();
    document.getElementById("newlink").innerHTML = `<div class="copyrow"><input type="text" readonly value="${esc(r.link)}"><button class="btn" id="copynew">Copy</button></div>`;
    document.getElementById("copynew").addEventListener("click", e => copy(r.link, e.target));
  });
}

// ---- router -------------------------------------------------------------------
// ---- Bug reports ------------------------------------------------------------------------------
// Filed as GitHub issues by the server, without anyone's name or email. "Your reports" shows their state.
let reportFrom = "#/";   // the page someone was on when they tapped "Report it"

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = { ios: /iPad/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ? "iPad" : "iPhone",
    "ios-other": "iPhone", android: "Android", desktop: /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "Computer" }[device()];
  const browser = /EdgiOS|Edg\//.test(ua) ? "Edge" : /CriOS|Chrome\//.test(ua) ? "Chrome" : /FxiOS|Firefox\//.test(ua) ? "Firefox"
    : /Safari\//.test(ua) ? "Safari" : "browser";
  return `${os} · ${browser}${isStandalone() ? " · home-screen app" : ""} · ${window.innerWidth}px wide`;
}

const REPORT_STATE = { open: ["Open", ""], fixed: ["Fixed", "ok"], closed: ["Closed", "warn"] };
async function postJSON(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
  if (PI_DOWN(r.status)) goOffline();
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const err = new Error(`${path}: ${r.status}`); err.detail = data.detail; throw err; }
  return data;
}
function reportList(reports) {
  if (!reports.length) return "";
  return `<div class="card">${reports.map(r => {
    const [label, cls] = REPORT_STATE[r.state] || REPORT_STATE.open;
    return `<a class="report-row" href="${esc(r.url || "#")}" target="_blank" rel="noopener">
      <span class="report-head"><span class="pill ${cls}">${label}</span><b>${esc(r.title)}</b></span>
      <span class="muted small">${r.kind === "idea" ? "Idea" : "Bug"} #${r.issue} · sent ${esc(new Date(r.created).toLocaleDateString(undefined, { month: "short", day: "numeric" }))}</span>
      ${r.reply ? `<span class="report-reply small"><b>Reply:</b> ${esc(r.reply.length > 280 ? r.reply.slice(0, 279) + "…" : r.reply)}</span>` : ""}
    </a>`;
  }).join("")}</div>`;
}

async function showMyReports(boxId) {
  const box = document.getElementById(boxId);
  if (!box) return;
  const d = await api("/api/bugs").catch(() => null);
  if (!d) { box.innerHTML = ""; return; }
  box.innerHTML = d.reports.length ? `<h2>Your reports</h2>${reportList(d.reports)}<p class="small muted help">Tap one to see it on GitHub. It says Fixed once it's been sorted out.</p>` : "";
}

function reportView(kind = "bug") {
  setNav("");
  const page = reportFrom;
  view.innerHTML = `
    ${backLink(reportFrom || "#/")}<h1>Report a problem</h1>
    <div class="tabs" role="group" aria-label="Kind">
      <a href="#/report" data-kind="bug" class="${kind === "bug" ? "active" : ""}">Something's wrong</a>
      <a href="#/report" data-kind="idea" class="${kind === "idea" ? "active" : ""}">I have an idea</a>
    </div>
    <div class="card pad16">
      <textarea id="bugtext" class="field bugtext" rows="7" maxlength="4000" placeholder="${kind === "idea"
        ? "What would make the app better?" : "What happened, and what did you expect instead? Which series, race or driver, if it's about one."}"></textarea>
      <p class="small muted help" style="margin-top:10px">Sent with it: the page you were on (<code>${esc(page)}</code>), ${esc(deviceLabel())}, and the app version. Not your name or email.</p>
      <p class="small muted help"><b>Reports are posted publicly on GitHub</b>, so leave out anything private.</p>
      <div style="display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap">
        <button class="btn primary" id="bugsend">Send</button><a href="${esc(page)}" class="small">Cancel</a>
      </div>
      <p class="small muted help" id="bugmsg" role="status"></p>
    </div>
    <div id="myreports"></div>`;
  view.querySelectorAll("[data-kind]").forEach(b => b.addEventListener("click", ev => {
    ev.preventDefault();
    const text = document.getElementById("bugtext").value;
    reportView(b.dataset.kind);
    document.getElementById("bugtext").value = text;
  }));
  const msg = t => { document.getElementById("bugmsg").textContent = t; };
  document.getElementById("bugsend").addEventListener("click", async e => {
    const text = document.getElementById("bugtext").value.trim();
    if (text.length < 10) { msg("Tell us a bit more (at least a sentence)."); return; }
    e.target.disabled = true;
    msg("Sending…");
    try {
      const r = await postJSON("/api/bug", { kind, text, page, device: deviceLabel() });
      if (r.link) {  // this install isn't connected to GitHub: hand over GitHub's own form, filled in
        window.open(r.link, "_blank", "noopener");
        msg("Opened GitHub's form in a new tab (you'll need a GitHub account there).");
        e.target.disabled = false;
        return;
      }
      view.innerHTML = `<h1>Thanks!</h1>
        <div class="card pad16"><p style="margin-top:0">It's in as <a href="${esc(r.url)}" target="_blank" rel="noopener">#${r.issue}: ${esc(r.title)}</a>.</p>
        <p class="small muted help">You can follow it under <a href="#/settings">Settings → Your reports</a>; it says Fixed once it's been sorted out.</p>
        <div style="margin-top:12px"><a class="btn primary" href="${esc(page)}">Back to where I was</a></div></div>`;
    } catch (err) {
      msg(err.detail || "Couldn't send it. Check your connection and try again.");
      e.target.disabled = false;
    }
  });
  showMyReports("myreports");
}

// ---- page transitions ------------------------------------------------------------------------
// "Slapshot": the old page goes at once and the new one flies in from the side already at speed, overshoots and
// snaps back. Upcoming -> Wire -> Standings -> Series in tab order; deeper pages from the right, back from the left.
// Plays once the new page's content is on screen; never for quiet redraws or the same page re-shown; off with
// Settings > Display or the phone's Reduce Motion. Whole-pixel moves only, so text doesn't shift when it stops.
const NAV_ORDER = { "": 0, wire: 1, picks: 2, standings: 3, series: 4, settings: 5, new: 6, app: 6, report: 6, admin: 6 };
const DEEP = new Set(["event", "driver", "team", "story", "live"]);
let QUIET_ROUTE = false, lastHash = null, lastPage = null, pendingShot = 0;
const motionOn = () => SETTINGS?.animations !== false && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function shotFrom(prev, next, prevHash, nextHash) {
  if (DEEP.has(next) || (next === "series" && nextHash.split("/").length > 2)) return 1;   // a series' own page is deeper too
  if (DEEP.has(prev) || (prev === "series" && prevHash.split("/").length > 2)) return -1;
  return (NAV_ORDER[next] ?? 0) >= (NAV_ORDER[prev] ?? 0) ? 1 : -1;
}
new MutationObserver(() => {
  if (!pendingShot || !view.firstElementChild) return;
  const d = pendingShot;
  pendingShot = 0;
  view.animate([
    { transform: `translate3d(${56 * d}px, 0, 0)`, opacity: 0, offset: 0 },
    { transform: `translate3d(${-7 * d}px, 0, 0)`, opacity: 1, offset: 0.55 },
    { transform: `translate3d(${2 * d}px, 0, 0)`, offset: 0.8 },
    { transform: "translate3d(0, 0, 0)", opacity: 1, offset: 1 },
  ], { duration: 280, easing: "cubic-bezier(.12, .8, .3, 1)" });
}).observe(view, { childList: true });

// After the app opens (and every 90 s while it's visible): load the other tabs in the background, so switching tabs
// draws straight from the phone's copy.
async function prefetch() {
  if (ME?.status !== "approved" || !SERIES.length) return;
  const ids = enabledSeries();
  const sid = store.get("standingsSeries") || SERIES.find(x => x.has_standings && ids.includes(x.id))?.id;
  const paths = [`/api/sessions?series=${ids.join(",")}`, wirePath({ kind: "", series: "", driver: "", team: "", mine: false, ...local("wire", {}) }),
    ...(sid ? [`/api/series/${sid}/standings`] : []), ...(follows().length ? ["/api/cards"] : []), "/api/entries"];
  for (const path of paths) {
    if (document.hidden) return;
    const hit = GET_CACHE.get(path);
    if (!hit || Date.now() - hit.at > 60000) await api(path).catch(() => {});
  }
  loadNames();
}
setInterval(() => { if (!document.hidden) prefetch(); }, 90000);

// Page views for /admin: counted per page pattern on the phone, sent in batches (sendBeacon: never in a page's way).
// Anonymous on the server; the owner's own views aren't counted. Patterns follow route()'s matches below.
const VIEWS = {};
const VIEW_PLAIN = new Set(["/report", "/wire", "/new", "/app", "/series", "/settings", "/settings/near", "/standings", "/picks"]);
const VIEW_DEEP = [[/^\/series\/[\w-]+(?:\/\w+)?$/, "series/:id"], [/^\/live\/[\w-]+$/, "live/:series"],
  [/^\/driver\/.+$/, "driver/:name"], [/^\/team\/.+$/, "team/:name"], [/^\/story\/[\w-]+\/.+$/, "story/:id"],
  [/^\/event\/[\w-]+\/.+$/, "event/:id"], [/^\/track\/[\w-]+$/, "track/:id"],
  [/^\/standings\/[\w-]+(?:\/\w+)?$/, "standings/:series"], [/^\/picks\/[\w-]+(?:\/.+)?$/, "picks/:sub"]];
const VIEW_NAME = { home: "Home", wire: "Race Wire", new: "What's new", app: "Get the app", series: "Series list",
  settings: "Settings", standings: "Standings", picks: "Pick'em", report: "Report a problem", "series/:id": "A series' page",
  "live/:series": "Live timing", "driver/:name": "Driver page", "team/:name": "Team page", "story/:id": "Race story",
  "event/:id": "Event page", "track/:id": "Track page", "standings/:series": "Standings, a series",
  "picks/:sub": "Pick'em, a race or table" };

function countView(hash) {
  if (ME?.status !== "approved" || ME.admin) return;
  const key = VIEW_PLAIN.has(hash) ? hash.split("/")[1] : VIEW_DEEP.find(([re]) => re.test(hash))?.[1] || "home";
  VIEWS[key] = (VIEWS[key] || 0) + 1;
}

// Leaving the app: sendBeacon (survives the page going away). Otherwise a fetch, and a batch that doesn't land
// (offline, an expired sign-in redirecting to the login page) goes back in the count for next time.
function sendViews(leaving = false) {
  if (!Object.keys(VIEWS).length) return;
  const batch = { ...VIEWS }, body = JSON.stringify({ views: batch });
  for (const k in VIEWS) delete VIEWS[k];
  const putBack = () => { for (const k in batch) VIEWS[k] = (VIEWS[k] || 0) + batch[k]; };
  if (leaving && navigator.sendBeacon?.("/api/views", new Blob([body], { type: "application/json" }))) return;
  fetch("/api/views", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true, redirect: "error" })
    .then(res => { if (!res.ok) putBack(); }, putBack);
}

async function route(quiet = false) {
  const hash = location.hash.replace(/^#/, "") || "/";
  const page = hash.split("/")[1] || "";
  QUIET_ROUTE = quiet;
  const navigated = !quiet && location.hash !== lastHash;   // a navigation, not a redraw of the same page
  if (!quiet) {
    const changed = lastHash !== null && location.hash !== lastHash;
    pendingShot = changed && motionOn() && ME?.status === "approved" ? shotFrom(lastPage, page, lastHash || "", location.hash) : 0;
    lastHash = location.hash; lastPage = page;
    setCompact(false);
    viewOff.abort();
  }
  try {
    let early = null;   // first load: ask for the series, config and What's new alongside /api/me, not after it
    if (!ME) {
      const me = api("/api/me");
      const lists = Promise.all([api("/api/series"), api("/api/config")]);
      lists.catch(() => {});   // someone not approved yet gets 403s here; the gate page below handles them
      early = [api("/api/changes").catch(() => []), lists];
      ME = await me;
    }
    if (ME.status !== "approved") return gateView();
    if (navigated) countView(hash);
    document.getElementById("tabs").hidden = false;
    if (!SETTINGS || !SERIES.length) {
      // First load: settings, What's new and the series list all at once.
      const [, changes, both] = await Promise.all([SETTINGS ? null : loadSettings(), early?.[0] || api("/api/changes").catch(() => []),
        SERIES.length ? null : early?.[1] || Promise.all([api("/api/series"), api("/api/config")])]);
      CHANGES = changes;
      if (both) {
        [SERIES, CONFIG] = both;
        BY_ID = Object.fromEntries(SERIES.map(s => [s.id, s]));
        switchOnNewSeries();
        FOLLOW_FIRST = loadFollowStatus();   // home waits for it alongside its own data; other pages wait below
        setTimeout(() => { prefetch(); loadMorph(); }, 2500);
      }
    }
    updateNewDot();
    if (FOLLOW_FIRST && hash !== "/") { await FOLLOW_FIRST; FOLLOW_FIRST = null; }
    const m = hash.match(/^\/series\/([\w-]+)(?:\/(\w+))?$/);
    const lv = hash.match(/^\/live\/([\w-]+)$/);
    const person = hash.match(/^\/(driver|team)\/(.+)$/);
    const story = hash.match(/^\/story\/([\w-]+)\/(.+)$/);
    const evp = hash.match(/^\/event\/([\w-]+)\/(.+)$/);
    const trk = hash.match(/^\/track\/([\w-]+)$/);
    const sd = hash.match(/^\/standings(?:\/([\w-]+)(?:\/(\w+))?)?$/);
    const pk = hash.match(/^\/picks(?:\/([\w-]+)(?:\/(.+))?)?$/);
    if (hash !== "/report") reportFrom = location.hash || "#/";
    document.getElementById("foot").hidden = hash === "/report";
    if (hash === "/report") reportView();
    else if (hash === "/admin") await adminView();
    else if (hash === "/wire") await wireView();
    else if (hash === "/new") whatsNewView();
    else if (hash === "/app") appView();
    else if (trk) await trackView(trk[1]);
    else if (person) await personView(person[1], decodeURIComponent(person[2]));
    else if (story) await storyView(story[1], decodeURIComponent(story[2]));
    else if (lv) await liveView(lv[1]);
    else if (m) await seriesView(m[1], m[2]);
    else if (sd) await standingsView(sd[1], sd[2]);
    else if (pk) await pickemView(pk[1], pk[2] && decodeURIComponent(pk[2]));
    else if (evp) await eventView(evp[1], decodeURIComponent(evp[2]));
    else if (hash === "/series") await seriesListView();
    else if (hash === "/settings" || hash === "/settings/near") await settingsView();
    else await homeView();
  } catch (err) {
    view.innerHTML = `<div class="empty">Couldn't load data. ${esc(err.message)}</div>`;
  } finally {
    QUIET_ROUTE = false;
  }
}

window.addEventListener("hashchange", () => { CAME_FROM_APP = true; window.scrollTo(0, 0); route(); });
route();
setInterval(sendViews, 60000);
// Coming back after a while counts as opening the app again; leaving sends what's been counted.
let hiddenAt = 0;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { hiddenAt = Date.now(); sendViews(true); return; }
  if (hiddenAt && Date.now() - hiddenAt > 5 * 60000) countView(location.hash.replace(/^#/, "") || "/");
});
// Keep live badges and "next up" fresh while the page is open (a quiet redraw: no animation).
setInterval(() => { if ((location.hash || "#/") === "#/" && document.visibilityState === "visible") route(true); }, 60000);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
