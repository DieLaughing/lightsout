// Lights out: the start gantry, full screen. Five red lights come on a second apart, hold for a random 0.2-3 s (as the
// starter does), then all go out. Two ways in (web/app.js):
//   play({ label })               a race you follow is starting now: "LIGHTS OUT … AND AWAY WE GO", then it closes
//   play({ label, react: true })  the reaction test: tap the moment they go out; your time, your best, and the
//                                 leaderboard (POST /api/reaction keeps everyone's best; names as on Pick'em)
// Loaded on demand; nothing here runs until one of those happens.
const STEP_MS = 1000, HOLD_MIN = 200, HOLD_MAX = 3000, CLOSE_MS = 2600;
const bestKey = "reactionBest";
const best = () => { try { return Number(localStorage.getItem(bestKey)) || 0; } catch { return 0; } };

let open = null;
export function play({ label = "", react = false } = {}) {
  if (open) return open;
  const root = Object.assign(document.createElement("div"), { className: "gantry", tabIndex: -1 });
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-label", react ? "Reaction test" : "Lights out");
  root.innerHTML = `<button class="gt-x" type="button" aria-label="Close">✕</button>
    <div class="gt-label">${esc(label)}</div>
    <div class="gt-lights" aria-hidden="true">${"<span><i></i><i></i></span>".repeat(5)}</div>
    <div class="gt-say" aria-live="assertive">${react ? "Tap the moment the lights go out" : ""}</div>
    <div class="gt-sub"></div>
    <div class="gt-board"></div>
    <div class="gt-acts" hidden><button type="button" class="gt-again">Again</button><button type="button" class="gt-close">Close</button></div>`;
  document.body.append(root);
  document.documentElement.classList.add("gantry-open");
  root.focus({ preventScroll: true });
  const pods = [...root.querySelectorAll(".gt-lights span")], say = root.querySelector(".gt-say"), sub = root.querySelector(".gt-sub");
  const acts = root.querySelector(".gt-acts"), boardBox = root.querySelector(".gt-board");
  const timers = [];
  let out = 0, done = false;
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));
  const close = () => {
    timers.forEach(clearTimeout);
    root.classList.add("closing");
    setTimeout(() => { root.remove(); document.documentElement.classList.remove("gantry-open"); open = null; }, 220);
    document.removeEventListener("keydown", key);
  };
  const key = e => {
    if (e.key === "Escape") close();
    else if ((e.key === " " || e.key === "Enter") && !(done && e.target.closest?.("button"))) { e.preventDefault(); done ? acts.hidden || root.querySelector(".gt-again").click() : tap(); }
  };
  document.addEventListener("keydown", key);
  pods.forEach((p, i) => later(() => p.classList.add("on"), STEP_MS * (i + 1)));
  const hold = HOLD_MIN + Math.random() * (HOLD_MAX - HOLD_MIN);
  later(() => {
    pods.forEach(p => p.classList.remove("on"));
    out = performance.now();
    root.classList.add("out");
    if (!react) {
      say.innerHTML = "<b>Lights out</b>";
      later(() => { sub.textContent = "and away we go"; }, 450);
      later(close, CLOSE_MS);
    }
  }, STEP_MS * 5 + hold);
  function tap() {
    if (!react) return close();
    if (done) return;   // the result stays up: Again or Close
    if (!out) {   // jumped the start
      done = true;
      timers.forEach(clearTimeout);
      root.classList.add("jump");
      say.innerHTML = "<b>Jump start</b>";
      sub.textContent = "Wait for the lights to go out.";
      acts.hidden = false;
      return;
    }
    done = true;
    const ms = Math.round(performance.now() - out), was = best(), isBest = !was || ms < was;
    if (isBest) { try { localStorage.setItem(bestKey, String(ms)); } catch { /* this visit only */ } }
    say.innerHTML = `<b>${(ms / 1000).toFixed(3)}</b><small>s</small>`;
    sub.textContent = isBest ? (was ? "A new best on this device" : "Your first start: that's the one to beat") : `Your best: ${(was / 1000).toFixed(3)} s`;
    acts.hidden = false;
    fetch("/api/reaction", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ms }) })
      .then(r => r.ok ? r.json() : null).then(b => { if (b) board(b); }).catch(() => {});   // offline: just your time
  }
  function board(b) {
    if (b.me) { try { localStorage.setItem("reactionRank", JSON.stringify(`#${b.me.rank} of ${b.players}`)); } catch { /* fine */ } }
    const row = r => `<li class="${r.me ? "me" : ""}"><span>${r.rank}</span><b>${esc(r.name)}</b><em>${(r.ms / 1000).toFixed(3)}</em></li>`;
    const extra = b.me && !b.top.some(r => r.me) ? `<li class="gap">…</li>${row(b.me)}` : "";
    boardBox.innerHTML = `<div class="gt-bh">Leaderboard <small>${b.players} ${b.players === 1 ? "player" : "players"} · best times</small></div><ol>${b.top.map(row).join("")}${extra}</ol>`;
  }
  root.addEventListener("pointerdown", e => { if (e.target.closest("button")) return; e.preventDefault(); tap(); });
  root.querySelector(".gt-x").addEventListener("click", close);
  root.querySelector(".gt-close").addEventListener("click", close);
  root.querySelector(".gt-again").addEventListener("click", () => { close(); setTimeout(() => play({ label, react }), 240); });
  open = { close };
  return open;
}
const esc = t => String(t).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
