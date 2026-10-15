// Forgiving name matching for the follow box: accents, case and small misspellings
// ("Kimi Anatoli" -> "Andrea Kimi Antonelli") all still find the driver.

export const normalise = s => String(s || "")
  .normalize("NFD").replace(/[̀-ͯ]/g, "")
  .toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

// Jaro-Winkler similarity, 0..1.
export function similarity(a, b) {
  if (a === b) return 1;
  if (!a.length || !b.length) return 0;
  const range = Math.max(0, Math.floor(Math.max(a.length, b.length) / 2) - 1);
  const am = new Array(a.length).fill(false), bm = new Array(b.length).fill(false);
  let matches = 0;
  for (let i = 0; i < a.length; i++) {
    for (let j = Math.max(0, i - range); j < Math.min(b.length, i + range + 1); j++) {
      if (!bm[j] && a[i] === b[j]) { am[i] = bm[j] = true; matches++; break; }
    }
  }
  if (!matches) return 0;
  let t = 0, k = 0;
  for (let i = 0; i < a.length; i++) {
    if (!am[i]) continue;
    while (!bm[k]) k++;
    if (a[i] !== b[k]) t++;
    k++;
  }
  const jaro = (matches / a.length + matches / b.length + (matches - t / 2) / matches) / 3;
  let prefix = 0;
  while (prefix < 4 && a[prefix] === b[prefix]) prefix++;
  return jaro + prefix * 0.1 * (1 - jaro);
}

// Every word typed has to resemble some word of the candidate; the score is their average.
// So "Verstappen" finds "Max Verstappen", and "Kimi Anatoli" finds "Andrea Kimi Antonelli".
function score(query, candidate) {
  const q = query.split(" "), c = candidate.split(" ");
  if (query === candidate) return 1;
  // Each candidate word can stand in for one typed word only, best pairs first: otherwise "Marc Marquez" would
  // match "Marcus Ericsson" by pairing both words with "Marcus". Mirrors follow.matches.
  const pairs = q.flatMap((w, i) => c.map((x, j) => [similarity(w, x), i, j])).sort((a, b) => b[0] - a[0]);
  const best = new Map(), used = new Set();
  for (const [sim, i, j] of pairs) if (!best.has(i) && !used.has(j)) { best.set(i, sim); used.add(j); }
  if (best.size < q.length || Math.min(...best.values()) < 0.8) return 0;
  const total = [...best.values()].reduce((a, b) => a + b, 0);
  // Prefer candidates that don't have lots of extra words (team names, middle names).
  return total / q.length - 0.01 * Math.max(0, c.length - q.length);
}

const THRESHOLD = 0.86;

/**
 * Match one typed name against driver and team entries ({series, name, team}).
 * Returns {name, kind, series: [...], score} or null when nothing is close enough.
 * The same person or team in several series (e.g. PREMA) comes back with all of them.
 */
export function matchName(input, entries) {
  const query = normalise(input);
  if (!query) return null;
  const candidates = new Map();  // normalised name -> {name, kind, series:Set}
  for (const e of entries) {
    for (const [label, kind] of [[e.name, "driver"], [e.team, "team"]]) {
      if (!label) continue;
      const key = normalise(label);
      if (!candidates.has(key)) candidates.set(key, { name: label, kind, series: new Set() });
      candidates.get(key).series.add(e.series);
    }
  }
  let best = null;
  for (const [key, c] of candidates) {
    const s = score(query, key);
    if (s >= THRESHOLD && (!best || s > best.score)) best = { ...c, score: s };
  }
  return best && { name: best.name, kind: best.kind, series: [...best.series], score: best.score };
}

/** "Max Verstappen, Lia Block; Kimi Antonelli" -> ["Max Verstappen", "Lia Block", "Kimi Antonelli"] */
export const splitNames = text => String(text || "").split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
