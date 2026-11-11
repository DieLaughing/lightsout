// First time in: two quick steps so Home isn't empty. 1) the series you watch (the usual ones already picked), 2) the
// drivers you follow (each series' current top three as suggestions, or search any name). Skipping keeps the defaults
// and saves them, so it never comes back. Loaded on demand (web/app.js) only for someone with no saved settings.
const CSS = `
.wl { position: fixed; inset: 0; z-index: 30; display: flex; flex-direction: column; background: var(--bg); color: var(--text); outline: 0; }
.wl-head { flex: none; padding: calc(16px + env(safe-area-inset-top, 0px)) calc(16px + var(--sr, 0px)) 14px calc(16px + var(--sl, 0px));
  background: var(--surface); border-bottom: 1px solid var(--border); }
.wl-steps { display: flex; gap: 6px; margin-bottom: 14px; } .wl-steps i { flex: 1; height: 4px; border-radius: 2px; background: var(--border); }
.wl-steps i.on { background: var(--accent); }
.wl-head h1 { margin: 0 0 4px; font-size: 26px; font-weight: 900; font-style: italic; text-transform: uppercase; letter-spacing: -.01em; }
.wl-head p { margin: 0; color: var(--muted); font-size: 14px; }
.wl-body { flex: 1; min-height: 0; overflow-y: auto; padding: 12px calc(16px + var(--sr, 0px)) 16px calc(16px + var(--sl, 0px)); }
.wl-body h2 { margin: 18px 0 8px; }
.wl-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.wl-pick { display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px 8px 8px; border-radius: 12px; border: 1.5px solid var(--border);
  background: var(--surface); color: var(--text); font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; text-align: left; }
.wl-pick[aria-pressed="true"] { border-color: var(--text); box-shadow: inset 0 0 0 1px var(--text); }
.wl-pick[data-s][aria-pressed="false"] { color: var(--muted); } .wl-pick[data-s][aria-pressed="false"] .chip { opacity: .45; }
.wl-pick[data-p] { padding: 8px 12px; }
.wl-pick small { color: var(--muted); font-weight: 500; }
.wl-find { width: 100%; font: inherit; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border); background: var(--surface); color: var(--text); }
.wl-foot { flex: none; display: flex; align-items: center; gap: 10px; padding: 10px calc(16px + var(--sr, 0px)) calc(10px + env(safe-area-inset-bottom, 0px)) calc(16px + var(--sl, 0px));
  background: var(--surface); border-top: 1px solid var(--border); }
.wl-count { font-size: 13px; color: var(--muted); }
.wl-foot .skip { border: 0; background: none; color: var(--muted); font: inherit; font-weight: 600; padding: 0 6px; cursor: pointer; }
.wl-foot .go { margin-left: auto; min-height: 44px; min-width: 120px; padding: 0 16px; border-radius: 12px; border: 0; background: var(--text); color: var(--bg);
  font: inherit; font-weight: 750; cursor: pointer; }`;

const CATS = [["single", "Single seaters"], ["sportscar", "Sportscars & GT"], ["stock", "Stock cars"], ["rally", "Rally & off-road"], ["bikes", "Bikes"], ["drift", "Drifting"]];

export function open(ctx) {
  if (!document.getElementById("wl-css")) document.head.append(Object.assign(document.createElement("style"), { id: "wl-css", textContent: CSS }));
  const { esc, chip, SERIES } = ctx;
  let step = 0;
  const series = new Set(ctx.enabled()), people = new Map();   // normalised -> name
  const root = Object.assign(document.createElement("div"), { className: "wl", tabIndex: -1 });
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-label", "Set up Lights Out");
  document.body.append(root);
  document.documentElement.classList.add("gantry-open");   // the page behind doesn't scroll
  let suggestions = null;

  const head = (title, line) => `<div class="wl-head"><div class="wl-steps">${[0, 1].map(i => `<i class="${i <= step ? "on" : ""}"></i>`).join("")}</div>
    <h1>${esc(title)}</h1><p>${esc(line)}</p></div>`;
  const pick = (on, attrs, inner) => `<button type="button" class="wl-pick" aria-pressed="${on}" ${attrs}>${inner}</button>`;

  function draw() {
    if (step === 0) {
      root.innerHTML = head("What do you watch?", "The usual ones are already on. Tap to add or drop a series; you can change it any time in Series.")
        + `<div class="wl-body">${CATS.map(([cat, label]) => {
          const list = SERIES.filter(s => s.category === cat);
          return list.length ? `<h2>${esc(label)}</h2><div class="wl-grid">${list.map(s => pick(series.has(s.id), `data-s="${esc(s.id)}"`, `${chip(s.id)}${esc(s.name)}`)).join("")}</div>` : "";
        }).join("")}</div>`
        + `<div class="wl-foot"><button type="button" class="skip">Skip for now</button><span class="wl-count">${series.size} on</span><button type="button" class="go">Next</button></div>`;
    } else {
      root.innerHTML = head("Who do you follow?", "Their races, results and news come first everywhere. Pick a few, or search for anyone.")
        + `<div class="wl-body"><input class="wl-find" type="search" placeholder="Search a driver or team" autocomplete="off" aria-label="Search a driver or team">
          <div class="wl-grid" id="wl-found" style="margin-top:10px"></div>
          ${suggestions === null ? `<p class="muted small">Loading the current leaders…</p>` : suggestions.map(([sid, names]) =>
            `<h2>${chip(sid)} ${esc(ctx.BY_ID[sid]?.name || sid)}</h2><div class="wl-grid">${names.map(n => person(n)).join("")}</div>`).join("")}</div>`
        + `<div class="wl-foot"><button type="button" class="skip">Back</button><span class="wl-count">${people.size ? `${people.size} picked` : "Nobody yet"}</span><button type="button" class="go">${people.size ? "Done" : "Skip"}</button></div>`;
    }
  }
  const person = n => pick(people.has(ctx.normalise(n)), `data-p="${esc(n)}"`, esc(n));

  async function loadSuggestions() {
    const ids = ctx.SERIES.map(s => s.id).filter(id => series.has(id) && ctx.BY_ID[id]?.has_standings).slice(0, 6);
    const tables = await Promise.all(ids.map(id => ctx.api(`/api/series/${id}/standings`).catch(() => ({}))));
    suggestions = ids.map((id, i) => [id, (tables[i].drivers || []).slice(0, 3).map(r => String(r.name).split(" / ")[0])]).filter(([, n]) => n.length);
    if (step === 1) { const q = root.querySelector(".wl-find")?.value || ""; draw(); restore(q); }
  }
  function restore(q) {
    const input = root.querySelector(".wl-find");
    if (input && q) { input.value = q; find(q); }
  }
  async function find(q) {
    const box = root.querySelector("#wl-found"), n = ctx.normalise(q);
    if (!box) return;
    if (n.length < 2) { box.innerHTML = ""; return; }
    const dir = await ctx.loadNames();
    const hits = [...dir.drivers, ...dir.teams].filter(x => ctx.normalise(x.name).includes(n)).slice(0, 10);
    box.innerHTML = hits.length ? hits.map(x => person(x.name)).join("") : `<span class="muted small">No one called that on a grid yet. Add them anyway in Settings: every series is watched.</span>`;
  }
  function finish(save) {
    ctx.store.set("series", ctx.SERIES.map(s => s.id).filter(id => series.has(id)));
    if (save && people.size) ctx.store.set("follows", [...ctx.follows(), ...[...people.values()].map(name => ({ name }))]);
    try { localStorage.setItem("welcomed", "true"); } catch { /* the saved series is enough */ }
    root.remove();
    document.documentElement.classList.remove("gantry-open");
    ctx.done();
  }

  root.addEventListener("click", e => {
    const s = e.target.closest("[data-s]"), p = e.target.closest("[data-p]");
    if (s) {
      const id = s.dataset.s;
      series.has(id) ? series.delete(id) : series.add(id);
      s.setAttribute("aria-pressed", series.has(id));
      root.querySelector(".wl-count").textContent = `${series.size} on`;
    } else if (p) {
      const name = p.dataset.p, k = ctx.normalise(name);
      people.has(k) ? people.delete(k) : people.set(k, name);
      root.querySelectorAll(`[data-p]`).forEach(b => { if (ctx.normalise(b.dataset.p) === k) b.setAttribute("aria-pressed", people.has(k)); });
      root.querySelector(".wl-count").textContent = people.size ? `${people.size} picked` : "Nobody yet";
      root.querySelector(".go").textContent = people.size ? "Done" : "Skip";
    } else if (e.target.closest(".go")) {
      if (step === 0) { step = 1; draw(); root.scrollTop = 0; if (suggestions === null) loadSuggestions(); }
      else finish(true);
    } else if (e.target.closest(".skip")) {
      if (step === 1) { step = 0; draw(); } else finish(false);
    }
  });
  root.addEventListener("input", e => { if (e.target.matches(".wl-find")) find(e.target.value); });
  draw();
  root.focus({ preventScroll: true });
}
