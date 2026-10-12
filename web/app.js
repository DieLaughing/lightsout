// Lights Out web app. No build step: plain modules, hash routing.
// Settings (series, follows, calendar kinds, alerts) live on your account so alerts know who you follow;
// the first visit after that change copies this browser's old settings up.
import { matchName, normalise, splitNames } from "./match.js?v=2";

const view = document.getElementById("view");

const SYNCED = ["series", "follows", "kinds", "dismissed", "notify", "seenSeries", "seenChanges", "units"];
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
      SETTINGS[key] = value;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => saveSettings().catch(() => {}), 400);
    }
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

const saveSettings = () => fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(SETTINGS) }).then(r => r.json());

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
const followedSeries = () => new Set(FOLLOW_STATUS.flatMap(f => f.series));
const dismissed = () => new Set(store.get("dismissed", []));
const newKey = (name, f) => `${name}|${f.series}|${f.event_key || "grid"}`;

// ---- helpers ---------------------------------------------------------------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Cloudflare's codes for "the Pi can't be reached" (a bug in the app is a plain 500): show the offline page.
const PI_DOWN = status => [502, 503, 504, 520, 521, 522, 523, 524, 530].includes(status);
const goOffline = () => location.replace(`/offline.html?back=${encodeURIComponent(location.href)}`);
const api = path => fetch(path, { headers: { Accept: "application/json" } }).then(r => {
  if (PI_DOWN(r.status)) goOffline();
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
});

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
      <div class="ev">${esc(s.event_name)}</div>
      <div class="ses">${chip(s.series)} ${esc(s.name)}${extra}${isLive(s) ? ' <span class="live">LIVE</span>' : ""}${weatherTag(s.weather)}${s.tickets && !isLive(s)
        ? ` <a class="tag tix" href="${esc(s.tickets.url)}" target="_blank" rel="noopener" title="${esc(s.tickets.label)}">Tickets ↗</a>` : ""}</div>
    </div>
    ${badge(s.watch)}
  </div>`;
}

// "← Back" on pages you reach from somewhere else: goes back if you came from inside the app, else to `fallback`.
let CAME_FROM_APP = false;
const backLink = fallback => `<a class="back" href="${fallback}" data-back>← Back</a>`;
document.addEventListener("click", e => {
  const a = e.target.closest("a[data-back]");
  if (a && CAME_FROM_APP) { e.preventDefault(); history.back(); }
});

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active", a.dataset.nav === name));
}

// ---- views ------------------------------------------------------------------
async function homeView() {
  setNav("home");
  const ids = enabledSeries();
  if (!ids.length) {
    view.innerHTML = `<div class="empty">No series switched on. <a href="#/settings">Pick some in Settings</a>.</div>`;
    return;
  }
  const [sessions] = await Promise.all([api(`/api/sessions?series=${ids.join(",")}`), loadFollowStatus()]);
  const followed = followedSeries();
  const live = sessions.filter(isLive);
  let html = "";
  const feeds = (await api("/api/live").catch(() => [])).filter(f => ids.includes(f.series));
  if (feeds.length) {
    html += `<h2>Live timing</h2><div class="card">${feeds.map(f => `<a class="item live-card" href="#/live/${f.series}">
      <div>${chip(f.series)} <b>${esc(f.event)}</b> · ${esc(f.session)}
      <div class="muted small">${f.lap ? `Lap ${f.lap}${f.total_laps ? `/${f.total_laps}` : ""} · ` : ""}${esc(f.track || f.status)}${f.connected ? "" : " · finished"}</div></div>
      <span class="${f.connected ? "live" : "muted small"}">${f.connected ? "LIVE" : "Results"}</span></a>`).join("")}</div>`;
  }

  if (live.length) {
    html += `<h2>Live now</h2><div class="card">${live.map(s => sessionRow(s, followed)).join("")}</div>`;
  }

  if (follows().length) {
    const hidden = dismissed();
    const rows = FOLLOW_STATUS.map(f => {
      const news = f.new.filter(n => !hidden.has(newKey(f.name, n)));
      const pend = f.pending?.[0];
      const next = f.next ? `Next: ${esc(f.next.event_name)} · ${esc(f.next.name)} · ${esc(dayLabel(localDay(f.next)))}`
        : pend ? `Registered for ${esc(BY_ID[pend.series]?.short)} · next rally ${esc(pend.event_name)}, ${esc(dayLabel(pend.day))} (entry list not out yet)`
        : f.found.length ? "Nothing scheduled yet" : "Not on any current grid or published entry list. Still watching every series.";
      return `<div class="item"><div style="flex:1"><div class="ev"><a class="who-link" href="#/driver/${encodeURIComponent(f.name)}"><b>${esc(f.name)}</b> ›</a> ${f.series.map(chip).join(" ")}</div>
        ${news.map(n => `<div class="news">NEW · ${n.kind === "entry" ? `Entered ${esc(n.event_name)}` : `Joined the ${esc(BY_ID[n.series]?.name || n.series)} grid`}
          <button class="link" data-dismiss="${esc(newKey(f.name, n))}" aria-label="Dismiss">✕</button></div>`).join("")}
        <div class="muted small">${next}</div></div></div>`;
    }).join("");
    html += `<h2>Following</h2><div class="card">${rows}</div>`;
  }

  const today = localDay({ start_utc: new Date().toISOString() });
  const upcoming = sessions.filter(s => !isLive(s) && (s.start_utc ? Date.parse(s.end_utc) > Date.now() : (s.end_day || s.day) >= today));
  if (upcoming.length) html += filterBar(upcoming);
  html += `<div id="upcoming">${upcomingList(upcoming, followed, today)}</div>`;
  const banner = await getAppBanner().catch(() => "");
  html = newBanner() + html;
  if (banner) html = `<div class="getapp"><div>${banner}</div><a class="btn primary" href="#/app">Show me how</a><button class="link x" id="hidebanner" aria-label="Hide">✕</button></div>` + html;
  view.innerHTML = html;
  view.querySelector("#newbar-x")?.addEventListener("click", e => { e.target.closest(".newbar")?.remove(); markChangesSeen(); });
  view.querySelector("#hidebanner")?.addEventListener("click", () => {
    try { localStorage.setItem("getappHidden", "true"); } catch { /* fine */ }
    view.querySelector(".getapp")?.remove();
  });
  view.querySelectorAll("[data-dismiss]").forEach(b => b.addEventListener("click", () => {
    store.set("dismissed", [...dismissed(), b.dataset.dismiss]); homeView();
  }));
  wireFilters(upcoming, followed, today);
}

// ---- Upcoming filters (remembered per device) ------------------------------------
const WHEN = [["all", "Any day"], ["today", "Today"], ["weekend", "This weekend"]];
const SESSION_FILTERS = [["races", "Races"], ["qualifying", "Qualifying"], ["practice", "Practice"]];
const TYPES = [["single", "Single seaters"], ["sportscar", "Sportscars & GT"], ["stock", "Stock cars"], ["rally", "Rally & off-road"]];
const RACE_KINDS = new Set(["race", "sprint", "rally_day", "event"]);
const NO_FILTERS = { when: "all", sessions: [], types: [], free: false };

function upFilters() {
  try { return { ...NO_FILTERS, ...JSON.parse(localStorage.getItem("upFilters") || "{}") }; } catch { return { ...NO_FILTERS }; }
}
function saveUpFilters(f) {
  try { localStorage.setItem("upFilters", JSON.stringify(f)); } catch { /* fine */ }
}
const filtersOn = f => f.when !== "all" || f.sessions.length || f.types.length || f.free;

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
  return true;
}

function filterBar(upcoming) {
  const f = upFilters();
  const present = new Set(upcoming.map(s => BY_ID[s.series]?.category));
  const btn = (group, value, label, on) =>
    `<button class="chip-btn${on ? " active" : ""}" data-f="${group}" data-v="${value}" aria-pressed="${on}">${label}</button>`;
  return `<div class="filters" id="filters">
    <div class="chips">${WHEN.map(([v, l]) => btn("when", v, l, f.when === v)).join("")}${btn("free", "1", "Free to watch", f.free)}</div>
    <div class="chips">${SESSION_FILTERS.map(([v, l]) => btn("sessions", v, l, f.sessions.includes(v))).join("")}</div>
    ${present.size > 1 ? `<div class="chips">${TYPES.filter(([v]) => present.has(v)).map(([v, l]) => btn("types", v, l, f.types.includes(v))).join("")}</div>` : ""}
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
  let html = filtersOn(f) ? `<p class="muted small filter-count">Showing ${shown.length} of ${upcoming.length} sessions ·
    <button class="link" id="f-clear">Clear filters</button></p>` : "";
  if (!upcoming.length) html += `<div class="empty">Nothing scheduled in the next 60 days for your series.</div>`;
  else if (!shown.length) html += `<div class="empty">Nothing matches these filters in the next 60 days.</div>`;
  for (const [d, rows] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
    html += `<h2>${esc(dayLabel(d))}</h2><div class="card">${rows.map(s => sessionRow(s, followed)).join("")}</div>`;
  }
  return html;
}

function wireFilters(upcoming, followed, today) {
  const bar = view.querySelector("#filters"), list = view.querySelector("#upcoming");
  const redraw = () => {
    const f = upFilters();
    bar.querySelectorAll("[data-f]").forEach(b => {
      const on = b.dataset.f === "when" ? f.when === b.dataset.v : b.dataset.f === "free" ? f.free : f[b.dataset.f].includes(b.dataset.v);
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on);
    });
    list.innerHTML = upcomingList(upcoming, followed, today);
    list.querySelector("#f-clear")?.addEventListener("click", () => { saveUpFilters({ ...NO_FILTERS }); redraw(); });
  };
  bar?.addEventListener("click", e => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    const f = upFilters(), g = b.dataset.f, v = b.dataset.v;
    if (g === "when") f.when = v;
    else if (g === "free") f.free = !f.free;
    else f[g] = f[g].includes(v) ? f[g].filter(x => x !== v) : [...f[g], v];
    saveUpFilters(f);
    redraw();
  });
  list.querySelector("#f-clear")?.addEventListener("click", () => { saveUpFilters({ ...NO_FILTERS }); redraw(); });
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
        <label class="switch" aria-label="Show ${esc(s.name)}"><input type="checkbox" data-series="${s.id}" ${on.has(s.id) ? "checked" : ""}><span></span></label>
      </div>`).join("")}</div>`;
  view.querySelectorAll("input[data-series]").forEach(input => input.addEventListener("change", () => {
    const set = new Set(enabledSeries());
    input.checked ? set.add(input.dataset.series) : set.delete(input.dataset.series);
    store.set("series", SERIES.map(s => s.id).filter(id => set.has(id)));
  }));
}

function statusText(st) {
  if (!st) return "Not fetched yet";
  if (st.error) return `Source problem: showing last good data${st.last_ok ? ` from ${new Date(st.last_ok).toLocaleString()}` : ""}`;
  return `${st.events} event${st.events === 1 ? "" : "s"} · updated ${new Date(st.last_ok).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
}

// "7 of 24 rounds done": the season of the next round (or the last one, once it's over).
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

const SERIES_TABS = [["schedule", "Schedule"], ["last", "Last race"], ["drivers", "Drivers"], ["teams", "Teams"]];

async function seriesView(id, tab = "schedule") {
  setNav("series");
  const s = BY_ID[id];
  if (!s) { view.innerHTML = `<div class="empty">Unknown series.</div>`; return; }
  if (!s.has_standings) tab = "schedule";
  const st = s.has_standings ? await api(`/api/series/${id}/standings`) : {};
  const shown = SERIES_TABS.filter(([t]) => t !== "teams" || st.teams?.length);
  const liveNow = (await api("/api/live").catch(() => [])).some(f => f.series === id);
  const tabs = s.has_standings || liveNow ? `<div class="tabs" role="tablist">${liveNow ? `<a role="tab" href="#/live/${id}" class="live-tab">● Live</a>` : ""}${(s.has_standings ? shown : SERIES_TABS.slice(0, 1)).map(([t, label]) =>
    `<a role="tab" href="#/series/${id}/${t}" class="${t === tab ? "active" : ""}" aria-selected="${t === tab}">${label}</a>`).join("")}</div>` : "";
  const links = [s.site && `<a class="btn" href="${esc(s.site)}" target="_blank" rel="noopener">Official site ↗</a>`,
    s.timing && `<a class="btn" href="${esc(s.timing)}" target="_blank" rel="noopener">Official timing ↗</a>`].filter(Boolean).join("");
  const head = `${backLink("#/series")}<h1>${chip(id)} ${esc(s.name)}</h1>${links ? `<div class="series-links">${links}</div>` : ""}${tabs}`;
  if (tab === "schedule") {
    const [events, storyKeys] = await Promise.all([api(`/api/series/${id}/events`), api(`/api/stories/${id}`).catch(() => [])]);
    const hasStory = new Set(storyKeys);
    const today = localDay({ start_utc: new Date().toISOString() });
    const followed = followedSeries();
    view.innerHTML = `${head}${seasonProgress(events, today)}<p class="muted small">${statusText(s.status)}</p>
      <div class="list">${events.map(e => {
        const past = e.end_day && e.end_day < today;
        return `<div class="card${past ? " past" : ""}">
          <div class="event-head"><div class="ev">${esc(e.name)}${hasStory.has(e.key) ? ` <a class="tag story" href="#/story/${id}/${encodeURIComponent(e.key)}">Race story →</a>` : ""}${!past && e.tickets ? ` <a class="tag tix" href="${esc(e.tickets.url)}" target="_blank" rel="noopener" title="${esc(e.tickets.label)}">Tickets ↗</a>` : ""}</div>
          <div class="muted small">${esc([e.venue, e.country].filter(Boolean).join(", "))} · ${esc(shortDate(e.start_day))}${e.end_day && e.end_day !== e.start_day ? ` – ${esc(shortDate(e.end_day))}` : ""}</div></div>
          ${e.sessions.map(x => sessionRow({ ...x, series: id, event_name: dayLabel(localDay(x)) }, followed)).join("")}
        </div>`;
      }).join("") || `<div class="empty">No events yet.</div>`}</div>`;
    return;
  }
  view.innerHTML = head + standingsBody(id, tab, st);
  wireClassPicker(() => seriesView(id, tab));
}

// Drivers / Teams / Last race for one series (shared by the series page and the Standings tab).
function standingsBody(id, tab, st) {
  const s = BY_ID[id];
  const mine = new Set(follows().map(f => normalise(f.name)));
  const isMine = (...names) => names.some(n => n && mine.has(normalise(n)));
  const pts = p => Number.isInteger(p) ? p : p.toFixed(1);
  const updated = st.updated ? `Updated ${new Date(st.updated).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : "";
  if (tab === "last") {
    return st.last_results?.length ? `<p class="muted small">${esc(st.last_event)} · ${esc(st.last_session)} · ${updated}</p>
      <div class="card standings">${st.last_results.map(r => `<div class="srow${isMine(r.name, r.team) ? " followed" : ""}">
        <span class="pos">${esc(r.pos)}</span><span class="who"><b>${esc(r.name)}</b><span class="muted">${esc(r.team)}</span></span>
        <span class="num">${esc(r.time)}</span><span class="num pts">${r.points ? pts(r.points) : ""}</span></div>`).join("")}</div>`
      : `<div class="empty">No race results yet.</div>`;
  }
  const classes = tab === "drivers" ? Object.keys(st.classes || {}) : [];
  const cls = classes.includes(seriesClass[id]) ? seriesClass[id] : null;
  const rows = tab === "drivers" ? (cls ? st.classes[cls] : st.drivers) : st.teams;
  const lead = rows?.[0]?.points ?? 0;
  const picker = classes.length ? `<div class="chips">${[s.short, ...classes].map((c, i) =>
    `<button class="chip-btn${(i === 0 ? !cls : c === cls) ? " active" : ""}" data-class="${i === 0 ? "" : esc(c)}" data-for="${id}">${esc(c)}</button>`).join("")}</div>` : "";
  return rows?.length ? `${picker}<p class="muted small">${esc(st.season_label)} · ${updated}</p>
    <div class="card standings">${rows.map(r => `<div class="srow${isMine(r.name, tab === "teams" ? r.name : null) ? " followed" : ""}">
      <span class="pos">${r.pos}</span><span class="who"><b>${esc(r.name)}</b><span class="muted">${esc(r.team)}</span></span>
      <span class="num muted">${r.pos > 1 ? `−${pts(lead - r.points)}` : ""}</span><span class="num pts">${pts(r.points)}</span></div>`).join("")}</div>`
    : `<div class="empty">${tab === "teams" ? "This series doesn't publish a team championship we can read." : "No standings yet."}</div>`;
}

function wireClassPicker(rerender) {
  view.querySelectorAll("[data-class]").forEach(b => b.addEventListener("click", () => {
    seriesClass[b.dataset.for] = b.dataset.class || null; rerender();
  }));
}

// Standings tab: every championship in one place. Series you have switched on come first; the last one picked is remembered.
const STANDINGS_TABS = [["drivers", "Drivers"], ["teams", "Teams"], ["last", "Last race"]];

async function standingsView(id, tab) {
  setNav("standings");
  const on = new Set(enabledSeries());
  const all = SERIES.filter(x => x.has_standings);
  const list = [...all.filter(x => on.has(x.id)), ...all.filter(x => !on.has(x.id))];
  if (!list.length) { view.innerHTML = `<h1>Standings</h1><div class="empty">No standings yet.</div>`; return; }
  if (!BY_ID[id]?.has_standings) id = list.some(x => x.id === store.get("standingsSeries")) ? store.get("standingsSeries") : list[0].id;
  store.set("standingsSeries", id);
  const st = await api(`/api/series/${id}/standings`);
  const tabs = STANDINGS_TABS.filter(([t]) => t !== "teams" || st.teams?.length);
  if (!tabs.some(([t]) => t === tab)) tab = "drivers";
  view.innerHTML = `<h1>Standings</h1>
    <div class="series-pick" role="tablist" aria-label="Series">${list.map(x =>
      `<a role="tab" href="#/standings/${x.id}/${tab === "teams" ? "drivers" : tab}" class="${x.id === id ? "active" : ""}${on.has(x.id) ? "" : " off"}" aria-selected="${x.id === id}">${esc(x.short)}</a>`).join("")}</div>
    <div class="standings-title">${chip(id)} <b>${esc(BY_ID[id].name)}</b> <a class="small" href="#/series/${id}">Schedule →</a></div>
    <div class="tabs" role="tablist">${tabs.map(([t, label]) =>
      `<a role="tab" href="#/standings/${id}/${t}" class="${t === tab ? "active" : ""}" aria-selected="${t === tab}">${label}</a>`).join("")}</div>
    ${standingsBody(id, tab, st)}`;
  view.querySelector(".series-pick a.active")?.scrollIntoView({ block: "nearest", inline: "center" });
  wireClassPicker(() => standingsView(id, tab));
}
const seriesClass = {};  // picked support championship per series (e.g. WRC2), for this page visit

async function settingsView() {
  setNav("settings");
  const entries = await api("/api/entries").catch(() => []);
  const ids = enabledSeries();
  const kinds = store.get("kinds", KINDS.map(k => k[0]));
  const allKinds = kinds.length === KINDS.length;
  const https = `${location.origin}/cal/${CONFIG.cal_token}/${ids.join("+")}.ics${allKinds ? "" : `?kinds=${kinds.join(",")}`}`;
  const webcal = https.replace(/^https?:/, "webcal:");

  view.innerHTML = `<h1>Settings</h1>
    <a class="card whatsnew-link" href="#/new"><b>What's new</b>${unseenChanges().length ? ` <span class="pill">${unseenChanges().length} new</span>` : ""}<span class="muted small">Every update to the app →</span></a>
    <h2>Alerts</h2>
    <p class="small muted" style="margin:-2px 2px 8px">New here? <a href="#/app">Get the app and alerts on your phone</a>.</p>
    <div class="card" id="notif"><div class="empty">Checking this device…</div></div>
    <div class="card" style="margin-top:8px">${alertToggles()}</div>

    <h2>Weather</h2>
    <div class="card"><div class="item"><div>Temperatures in<div class="small muted">Forecasts at the track show up for sessions in the next 7 days.</div></div>
      <div class="chips" style="margin:0">${[["f", "°F"], ["c", "°C"]].map(([u, l]) =>
        `<button class="chip-btn${store.get("units", "f") === u ? " active" : ""}" data-units="${u}">${l}</button>`).join("")}</div></div></div>

    <h2>Calendar feed</h2>
    <div class="card"><div class="form">
      <div class="small muted">Subscribe once and your calendar app keeps it updated, including reschedules. It covers the ${ids.length} series switched on in <a href="#/series">Series</a>; change those and subscribe again with the new link.</div>
      <div class="checks">${KINDS.map(([k, label]) => `<label><input type="checkbox" data-kind="${k}" ${kinds.includes(k) ? "checked" : ""}> ${label}</label>`).join("")}</div>
      <div><a class="btn primary" href="${esc(webcal)}">Subscribe in Calendar</a> <button id="copy">Copy link</button></div>
      <code class="url">${esc(https)}</code>
      <div class="small muted">Treat this link like a password: anyone with it can see the calendar.</div>
    </div></div>

    <h2>Following</h2>
    <div class="card" id="following"></div>

    <h2>Data sources</h2>
    <div class="card">${SERIES.map(s => `<div class="item"><div>${chip(s.id)} ${esc(s.name)}</div><div class="small muted" style="text-align:right">${statusText(s.status)}</div></div>`).join("")}</div>
    <p class="small muted">Where-to-watch info is for the US, ${esc(CONFIG.watch_season)} season.</p>`;

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
  const render = async () => {
    if (!location.hash.startsWith(`#/live/${id}`)) { clearInterval(liveTimer); liveTimer = null; return; }
    const d = await api(`/api/live/${id}`).catch(() => ({}));
    if (!d.rows) {
      view.innerHTML = `${backLink(`#/series/${id}`)}<h1>${chip(id)} ${esc(s?.name || id)}</h1><div class="empty">No live session right now. Timing connects 15 minutes before each session.</div>`;
      return;
    }
    const mine = new Set(follows().map(f => normalise(f.name)));
    if (d.kind === "rally") { view.innerHTML = rallyLive(id, d, mine); bindRally(id); return; }
    const race = d.kind === "race";
    const blank = v => (v === "." || v === "-" ? "" : v);
    d.rows.forEach(r => ["gap", "interval", "last", "best"].forEach(k => { r[k] = blank(r[k]); }));
    view.innerHTML = `${backLink(`#/series/${id}`)}<h1>${chip(id)} ${esc(d.event)}</h1>
      <div class="live-bar">
        <span class="pill">${esc(d.session)}</span>
        ${d.lap ? `<span class="pill">Lap ${d.lap}${d.total_laps ? ` / ${d.total_laps}` : ""}</span>` : ""}
        ${d.clock && !race ? `<span class="pill">${esc(d.clock)} left</span>` : ""}
        ${d.track ? `<span class="pill ${TRACK_CLASS[d.track] || ""}">${esc(d.track)}</span>` : ""}
        <span class="${d.connected ? "live" : "muted small"}">${d.connected ? "LIVE" : esc(d.status || "Finished")}</span>
      </div>
      <div class="card standings timing">
        <div class="trow head muted"><span>Pos</span><span>Driver</span><span>${race ? "Gap" : "Gap"}</span><span>${race ? "Int" : "Best"}</span><span>Last</span><span>${d.rows.some(r => r.tyre) ? "Tyre" : "Pits"}</span></div>
        ${d.rows.map(r => `<div class="trow${mine.has(normalise(r.name)) || mine.has(normalise(r.team)) ? " followed" : ""}${r.status === "OUT" ? " out" : ""}">
          <span class="pos">${r.pos < 99 ? r.pos : ""}</span>
          <span class="who"><i class="bar" style="background:${esc(r.colour || "var(--border)")}"></i><b>${esc(r.code)}</b><span class="muted">${esc(r.team || r.name)}</span></span>
          <span class="num">${esc(r.status || r.gap || (r.pos === 1 ? (race ? "Leader" : r.best) : ""))}</span>
          <span class="num muted">${esc(race ? (r.pos === 1 ? "" : r.interval) : r.best)}</span>
          <span class="num">${esc(r.last)}</span>
          <span class="tyre">${r.tyre ? `<i style="background:${TYRES[r.tyre] || "#888"}">${esc(r.tyre)}</i>` : ""}${r.pits ? `<small>${r.pits}</small>` : ""}</span>
        </div>`).join("")}
      </div>
      ${eventsPanel(id)}`;
    bindEventsPanel(id);
  };
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
function bindEventsPanel(id) {
  view.querySelector("#keyonly")?.addEventListener("change", e => {
    try { localStorage.setItem("liveKeyOnly", JSON.stringify(e.target.checked)); } catch { /* fine */ }
    liveView(id);
  });
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

function bindRally(id) {
  view.querySelectorAll("[data-rclass]").forEach(b => b.addEventListener("click", () => {
    rallyFilter[id] = b.dataset.rclass; liveView(id);
  }));
}

// ---- the Wire: news + race events ------------------------------------------------------
let NAMES = null;
const loadNames = async () => (NAMES ||= await api("/api/names").catch(() => ({ drivers: [], teams: [] })));

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
  const qs = new URLSearchParams();
  if (st.kind) qs.set("kind", st.kind);
  qs.set("series", st.series || enabledSeries().join(","));
  if (st.driver) qs.set("driver", st.driver);
  if (st.team) qs.set("team", st.team);
  if (st.mine) qs.set("mine", "1");
  const [items, dir] = await Promise.all([api(`/api/wire?${qs}`), loadNames()]);
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
    <p class="small muted">News from Motorsport.com, Autosport, RACER, The Race and DirtFish. Race events come from each series' live timing.</p>`;
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
  view.innerHTML = `<div class="empty">Loading ${esc(name)}…</div>`;
  // Wire items are tagged with the grid's spelling; a follow may be typed differently or be a team.
  const [p, dir] = await Promise.all([api(`/api/person/${encodeURIComponent(name)}`).catch(() => null), loadNames()]);
  const asDriver = p?.rides?.find(r => normalise(r.name) === normalise(name))?.name;
  const tagged = type === "team" || (!asDriver && p?.rides?.length) ? "team" : "driver";
  const items = await api(`/api/wire?${tagged}=${encodeURIComponent(asDriver || (tagged === "team" ? (p?.rides?.[0]?.team || name) : name))}`).catch(() => []);
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
  const stand = (p?.standings || []).map(x => `<div class="item"><div>${chip(x.series)} <b>${esc(ordinal(x.pos))}</b> in the championship
      <div class="muted small">${x.points} pts${x.behind > 0 ? ` · ${x.behind} behind the leader` : x.pos === 1 ? " · leading" : ""}${x.season ? ` · ${esc(x.season)}` : ""}</div></div></div>`).join("");
  const last = (p?.results || []).map(x => `<div class="item"><div>${chip(x.series)} <b>${/^\d+$/.test(x.pos) ? `P${esc(x.pos)}` : esc(x.pos)}</b> · ${esc(x.event)}
      <div class="muted small">${esc(x.session || "")}${x.time ? ` · ${esc(x.time)}` : ""}${x.points ? ` · ${x.points} pts` : ""}</div></div></div>`).join("");
  const upcoming = p?.upcoming || [];
  view.innerHTML = `${backLink("#/")}<h1>${esc(name)}</h1>
    <div class="person-head">${seriesIds.map(chip).join(" ")} <span class="muted small">${tagged === "team" ? "Team" : "Driver"}</span>
      <button class="btn${followed ? "" : " primary"}" id="follow">${followed ? "Following ✓" : "Follow"}</button></div>
    ${where ? `<h2>Where they race</h2><div class="card">${where}</div>` : `<p class="small muted">Not on any current grid or published entry list. If you follow them, every series is still watched.</p>`}
    ${upcoming.length ? `<h2>Coming up</h2><div class="card">${upcoming.map(x => sessionRow({ ...x }, new Set())).join("")}</div>` : ""}
    ${stand ? `<h2>Championship</h2><div class="card">${stand}</div>` : ""}
    ${last ? `<h2>Last race</h2><div class="card">${last}</div>` : ""}
    <h2>News and race events</h2>
    ${items.length ? `<div class="card">${items.map(wireItem).join("")}</div>` : `<div class="empty">Nothing in the last 30 days.</div>`}`;
  view.querySelector("#follow").addEventListener("click", () => {
    const l = follows();
    store.set("follows", followed ? l.filter(f => normalise(f.name) !== normalise(name)) : [...l, { name }]);
    loadFollowStatus().then(() => personView(type, name));
  });
}

function ordinal(n) {
  n = Number(n);
  if (!n) return "";
  const teen = n % 100 >= 11 && n % 100 <= 13;
  return `${n}${teen ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th")}`;
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
  view.innerHTML = `<h1>Get the app</h1>
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
  view.innerHTML = `<h1>What's new</h1><p class="muted">Every update to Lights Out, newest first.</p>
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
  document.getElementById("nav").hidden = true;
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
  const unused = d.invites.filter(i => !i.used_by);
  view.innerHTML = `<h1>Admin</h1>
    <h2>Waiting for approval</h2>
    <div class="card">${pending.length ? pending.map(u => row(u, btn(u.email, "approved", "Approve", true) + btn(u.email, "denied", "Deny"))).join("") : `<div class="empty">Nobody waiting.</div>`}</div>
    <h2>Invite a friend</h2>
    <div class="card"><div class="copyrow"><input type="text" id="note" placeholder="Who it's for (optional)"><button class="btn primary" id="mkinvite">Make link</button></div>
      <div id="newlink"></div>
      ${unused.map(i => `<div class="person"><span class="em">${esc(i.note || "Unnamed invite")}</span><span class="when">${esc(ago(i.created))}</span>
        <button class="btn" data-copy="${esc(i.link)}">Copy link</button><button class="btn" data-del="${esc(i.code)}">Delete</button></div>`).join("")}</div>
    <p class="small muted">Each link works once. Whoever signs in with it is approved straight away.</p>
    <h2>Approved (${approved.length})</h2>
    <div class="card">${approved.map(u => row(u, ME.email === u.email ? `<span class="small muted">You</span>` : btn(u.email, "revoked", "Revoke"))).join("")}</div>
    ${other.length ? `<h2>Denied or revoked</h2><div class="card">${other.map(u => row(u, btn(u.email, "approved", "Approve"))).join("")}</div>` : ""}`;
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
    const r = await send("/api/admin/invites", "POST", { note: document.getElementById("note").value });
    await adminView();
    document.getElementById("newlink").innerHTML = `<div class="copyrow"><input type="text" readonly value="${esc(r.link)}"><button class="btn" id="copynew">Copy</button></div>`;
    document.getElementById("copynew").addEventListener("click", e => copy(r.link, e.target));
  });
}

// ---- router -------------------------------------------------------------------
async function route() {
  const hash = location.hash.replace(/^#/, "") || "/";
  try {
    if (!ME) ME = await api("/api/me");
    if (ME.status !== "approved") return gateView();
    document.getElementById("nav").hidden = false;
    document.getElementById("admin-tab").hidden = !ME.admin;
    if (!SETTINGS) {
      await loadSettings();
      CHANGES = await api("/api/changes").catch(() => []);
    }
    updateNewDot();
    if (!SERIES.length) {
      [SERIES, CONFIG] = await Promise.all([api("/api/series"), api("/api/config")]);
      BY_ID = Object.fromEntries(SERIES.map(s => [s.id, s]));
      switchOnNewSeries();
      await loadFollowStatus();
    }
    const m = hash.match(/^\/series\/([\w-]+)(?:\/(\w+))?$/);
    const lv = hash.match(/^\/live\/([\w-]+)$/);
    const person = hash.match(/^\/(driver|team)\/(.+)$/);
    const story = hash.match(/^\/story\/([\w-]+)\/(.+)$/);
    const sd = hash.match(/^\/standings(?:\/([\w-]+)(?:\/(\w+))?)?$/);
    if (hash === "/admin") await adminView();
    else if (hash === "/wire") await wireView();
    else if (hash === "/new") whatsNewView();
    else if (hash === "/app") appView();
    else if (person) await personView(person[1], decodeURIComponent(person[2]));
    else if (story) await storyView(story[1], decodeURIComponent(story[2]));
    else if (lv) await liveView(lv[1]);
    else if (m) await seriesView(m[1], m[2]);
    else if (sd) await standingsView(sd[1], sd[2]);
    else if (hash === "/series") await seriesListView();
    else if (hash === "/settings") await settingsView();
    else await homeView();
  } catch (err) {
    view.innerHTML = `<div class="empty">Couldn't load data. ${esc(err.message)}</div>`;
  }
}

window.addEventListener("hashchange", () => { CAME_FROM_APP = true; window.scrollTo(0, 0); route(); });
route();
// Keep live badges and "next up" fresh while the page is open.
setInterval(() => { if ((location.hash || "#/") === "#/" && document.visibilityState === "visible") homeView(); }, 60000);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
