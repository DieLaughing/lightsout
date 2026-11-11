// The live dock (from Shots on Goal): while a session with live timing is on, a strip at the bottom of every page says
// where your drivers are: "F1 Qualifying · L12/56 · P3 VER ▲1". Tap it for live timing. It lives outside #view, so
// nothing a page draws touches it, and it steps aside on that session's own live page.
// Loaded on demand once the first page is up (web/app.js startDock); it costs a page nothing.
const POLL_S = 10, QUIET_S = 30;        // with something live / with nothing live
const ARROW_MS = 30000;                 // how long a place gained or lost keeps its arrow

let ctx, box, feeds = [], snaps = new Map(), last = new Map(), moved = new Map(), timer = 0;

export function start(context) {
  ctx = context;
  box = Object.assign(document.createElement("nav"), { className: "dock", hidden: true });
  box.setAttribute("aria-label", "Live now");
  document.body.append(box);
  addEventListener("hashchange", draw);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  tick();
}
export const refresh = () => draw();   // Settings switched it on or off

async function tick() {
  clearTimeout(timer);
  if (!document.hidden) {
    try {
      const on = new Set(ctx.enabled());
      feeds = (await ctx.api("/api/live")).filter(f => f.connected && on.has(f.series));
      await Promise.all(feeds.map(f => ctx.api(`/api/live/${f.series}`).then(d => { note(f.series, d); snaps.set(f.series, d); }, () => {})));
    } catch { feeds = []; }
    draw();
  }
  timer = setTimeout(tick, (feeds.length ? POLL_S : QUIET_S) * 1000);
}

// Places gained or lost since the last look, per driver, kept for ARROW_MS.
function note(series, d) {
  for (const r of d?.rows || []) {
    const k = `${series}|${r.name}`, was = last.get(k);
    if (was && was !== r.pos && r.pos < 90) moved.set(k, { by: was - r.pos, at: Date.now() });
    last.set(k, r.pos);
  }
}

const FLAG = { "Yellow": "yellow", "Safety car": "yellow", "Virtual safety car": "yellow", "Red flag": "red", "Caution": "yellow", "Red": "red" };
function item(f) {
  const d = snaps.get(f.series) || {}, rows = (d.rows || []).filter(r => r.pos < 90);
  const mine = rows.filter(ctx.isMine).slice(0, 2), who = mine.length ? mine : rows.slice(0, 1);
  const people = who.map(r => {
    const m = moved.get(`${f.series}|${r.name}`), fresh = m && Date.now() - m.at < ARROW_MS;
    const arrow = fresh ? `<i class="${m.by > 0 ? "up" : "down"}">${m.by > 0 ? "▲" : "▼"}${Math.abs(m.by)}</i>` : "";
    const label = r.code && !String(r.code).startsWith("#") ? r.code : String(r.name).split(" ").pop();
    return `<span class="dk-who${mine.length ? "" : " lead"}"${r.colour ? ` style="--tc:${ctx.esc(r.colour)}"` : ""}><b>P${ctx.esc(r.pos)}</b> ${ctx.esc(label)}${r.status === "PIT" ? " <small>PIT</small>" : ""}${arrow}</span>`;
  }).join("");
  const flag = FLAG[f.track] ? `<span class="dk-flag ${FLAG[f.track]}">${ctx.esc(f.track)}</span>` : "";
  const lap = f.lap ? `L${f.lap}${f.total_laps ? `/${f.total_laps}` : ""}` : d.clock ? String(d.clock).replace(/^0(?=\d:)/, "") : "";
  return `<a class="dock-item" href="#/live/${ctx.esc(f.series)}" aria-label="${ctx.esc(`${f.session} live. Open live timing`)}">
    <span class="dk-top">${ctx.chip(f.series)}<span class="dk-ses"><span class="live-dot"></span>${ctx.esc(f.session)}</span>${lap ? `<span class="dk-lap">${ctx.esc(lap)}</span>` : ""}${flag}</span>
    <span class="dk-people">${people}</span></a>`;
}

let shown = "";
function draw() {
  const list = ctx.dockOn() ? feeds.filter(f => location.hash !== `#/live/${f.series}`) : [];
  document.body.classList.toggle("has-dock", !!list.length);
  box.hidden = !list.length;
  box.classList.toggle("many", list.length > 1);
  const html = list.map(item).join("");
  if (html !== shown) {
    const at = box.scrollLeft;
    box.innerHTML = html;
    box.scrollLeft = at;   // a swipe between two sessions survives the update
    shown = html;
  }
}
