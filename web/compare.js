// Compare: two drivers side by side (#/compare/<a>/<b>), for every series they both race in this season: the
// championship, the head-to-head (who finished ahead when both were classified), wins, podiums, top 10s, average finish,
// DNFs, and a strip of every round's pair of finishes. One side given (#/compare/<a>): pick the other, teammates first.
// Loaded on demand (web/app.js compareView); the numbers come from /api/compare, worked out from the saved race results.

// [label, key, lower is better, how to show it]
const STATS = [["Championship", "pos", true, v => `P${v}`], ["Points", "points", false, v => fmt(v)], ["Wins", "wins", false],
  ["Podiums", "podiums", false], ["Top 10s", "top10", false], ["Average finish", "avg", true, v => v.toFixed(1)], ["Retirements", "dnf", true]];
const fmt = v => Number.isInteger(v) ? String(v) : String(Number(v).toFixed(1)).replace(/\.0$/, "");
// Teammates share a colour: the second side goes darker so the split still reads.
const shade = (a, b) => a.toLowerCase() === b.toLowerCase() ? `color-mix(in srgb, ${b} 55%, #000)` : b;
const surname = n => String(n || "").split(" / ")[0].split(" ").pop();

export async function render(ctx, a, b) {
  const { esc, view } = ctx;
  if (!b) return picker(ctx, a);
  const d = await ctx.api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  const first = d.series[0];
  const A = first?.a?.name || a, B = first?.b?.name || b;
  const ac = first?.a?.color || "#666", bc = shade(ac, first?.b?.color || "#999");
  const head = `<div class="cp-head" style="--a:${esc(ac)};--b:${esc(bc)}">
      <a class="cp-side" href="#/driver/${encodeURIComponent(A)}"><span class="cp-name">${esc(A)}</span><span class="cp-sub">${esc(first?.a?.team || "")}</span></a>
      <span class="cp-vs">vs</span>
      <a class="cp-side" href="#/driver/${encodeURIComponent(B)}"><span class="cp-name">${esc(B)}</span><span class="cp-sub">${esc(first?.b?.team || "")}</span></a></div>
    <a class="cp-swap link" href="#/compare/${encodeURIComponent(B)}/${encodeURIComponent(A)}">⇄ Swap sides</a>`;
  const sections = d.series.map(x => section(ctx, x, A, B)).join("");
  const elsewhere = [[A, d.only_a], [B, d.only_b]].filter(([, ids]) => ids.length)
    .map(([n, ids]) => `<p class="small muted">${esc(n)} also races in ${ids.map(ctx.chip).join(" ")}</p>`).join("");
  ctx.paint(`${ctx.backLink("#/")}<h1 class="cp-title">Compare</h1>${head}
    ${sections || `<div class="empty">${esc(A)} and ${esc(B)} haven't raced in the same series this season, so there's no head-to-head.</div>`}
    ${elsewhere}<p class="cp-other"><a class="link" href="#/compare/${encodeURIComponent(A)}">Compare ${esc(surname(A))} with someone else</a></p>`);
}

function section(ctx, x, A, B) {
  const { esc } = ctx, [ha, hb] = x.h2h, both = ha + hb;
  const rows = STATS.filter(([, k]) => x.a[k] != null || x.b[k] != null).map(([label, k, low, show = fmt]) => {
    const va = x.a[k], vb = x.b[k], has = va != null && vb != null && va !== vb;
    const aBest = has && (low ? va < vb : va > vb), bBest = has && !aBest;
    return `<div class="cp-row"><span class="v${aBest ? " best" : ""}">${va == null ? "–" : esc(show(va))}</span><span class="k">${esc(label)}</span><span class="v${bBest ? " best" : ""}">${vb == null ? "–" : esc(show(vb))}</span></div>`;
  }).join("");
  const strip = x.duel.length > 1 ? `<div class="cp-duel"><div class="rb-head"><span>Every round</span><span>${esc(surname(A))} · ${esc(surname(B))}</span></div>
    <ol class="cp-tiles">${x.duel.map((r, i) => {
      const pa = /^\d+$/.test(r.a || "") ? +r.a : null, pb = /^\d+$/.test(r.b || "") ? +r.b : null;
      const win = pa && pb ? (pa < pb ? "a" : "b") : pa ? "a" : pb ? "b" : "";
      return `<li class="${win}" title="${esc(`${r.event}: ${surname(A)} ${r.a ?? "–"}, ${surname(B)} ${r.b ?? "–"}`)}"><span class="rb-n">${i + 1}</span><b class="pa">${esc(r.a ?? "–")}</b><b class="pb">${esc(r.b ?? "–")}</b></li>`;
    }).join("")}</ol></div>` : "";
  return `<section class="cp-sec" style="--a:${esc(x.a.color || "#666")};--b:${esc(shade(x.a.color || "#666", x.b.color || "#999"))}"><h2>${ctx.chip(x.series)} ${esc(x.season || "")}</h2>
    <div class="card cp-card">
      ${both ? `<div class="cp-h2h"><b class="${ha > hb ? "lead" : ""}">${ha}</b><span>Head to head<small>ahead at the finish, in ${both} race${both === 1 ? "" : "s"} they both finished</small></span><b class="${hb > ha ? "lead" : ""}">${hb}</b></div>
        <div class="cp-bar" role="img" aria-label="${esc(`${surname(A)} ${ha}, ${surname(B)} ${hb}`)}"><i style="flex:${ha || 0.0001}"></i><i style="flex:${hb || 0.0001}"></i></div>` : ""}
      ${rows}</div>${strip}</section>`;
}

async function picker(ctx, a) {
  const { esc } = ctx;
  const [dir, entries] = await Promise.all([ctx.loadNames(), ctx.entries()]);
  const me = entries.filter(e => ctx.same(e.name, a));
  const mates = entries.filter(e => me.some(m => m.series === e.series && m.team && m.team === e.team) && !ctx.same(e.name, a));
  const people = dir.drivers.filter(x => !ctx.same(x.name, a));
  const chips = list => list.map(n => `<a class="chip-btn" href="#/compare/${encodeURIComponent(a)}/${encodeURIComponent(n)}">${esc(n)}</a>`).join("");
  ctx.paint(`${ctx.backLink(`#/driver/${encodeURIComponent(a)}`)}<h1 class="cp-title">Compare ${esc(a)} with…</h1>
    ${mates.length ? `<h2>Teammates</h2>${[...new Set(mates.map(m => m.series))].map(sid => `<div class="chips cp-mates">${ctx.chip(sid)}${chips([...new Set(mates.filter(m => m.series === sid).map(m => m.name))])}</div>`).join("")}` : ""}
    <h2>Anyone</h2><input class="cp-find" type="search" placeholder="Type a driver's name" autocomplete="off" aria-label="Driver to compare with">
    <div class="chips" id="cp-found"></div>`);
  const input = ctx.view.querySelector(".cp-find"), found = ctx.view.querySelector("#cp-found");
  input.addEventListener("input", () => {
    const q = ctx.normalise(input.value);
    found.innerHTML = q.length < 2 ? "" : chips(people.filter(x => ctx.normalise(x.name).includes(q)).slice(0, 12).map(x => x.name));
  }, ctx.listen());
}
