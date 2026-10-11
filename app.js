const CONFIG = window.CONFIG || {};
const REFRESH_MS = 60_000;
const NEW_PLAYER_DAYS = 7;

const nf = new Intl.NumberFormat("de-DE");
const num = (v) => nf.format(v || 0);
const km = (cm) => new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format((cm || 0) / 100000) + " km";
// Schaden wird in Zehntel-Lebenspunkten gespeichert, 2 Lebenspunkte = 1 Herz
const hearts = (v) => num(Math.round((v || 0) / 20)) + " ♥";
const pretty = (id) => id.replace(/^.*:/, "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

function playtime(ticks) {
  const min = Math.floor((ticks || 0) / 1200);
  const h = Math.floor(min / 60);
  return h ? `${num(h)} h ${min % 60} min` : `${min} min`;
}

function ago(iso) {
  if (!iso) return "–";
  const min = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (min < 1) return "gerade eben";
  if (min < 60) return `vor ${min} min`;
  if (min < 48 * 60) return `vor ${Math.round(min / 60)} h`;
  return `vor ${Math.round(min / 1440)} Tagen`;
}

const topEntry = (list) => (list && list[0] ? `${pretty(list[0][0])} (${num(list[0][1])})` : "–");

// Spalte: [Titel, Anzeige, Sortierwert]
const TABS = [
  { id: "zeit", label: "Spielzeit", cols: [
    ["Spielzeit", (p, max) => timeCell(p, max), (p) => p.playTicks],
    ["Zuletzt gesehen", (p) => (p.online ? "jetzt online" : ago(p.lastSeen)), (p) => (p.online ? Infinity : Date.parse(p.lastSeen) || 0)],
  ] },
  { id: "kampf", label: "Kampf", cols: [
    ["Mob-Kills", (p) => num(p.mobKills), (p) => p.mobKills],
    ["Spieler-Kills", (p) => num(p.playerKills), (p) => p.playerKills],
    ["Tode", (p) => num(p.deaths), (p) => p.deaths],
    ["Schaden ausgeteilt", (p) => hearts(p.damageDealt), (p) => p.damageDealt],
    ["Schaden kassiert", (p) => hearts(p.damageTaken), (p) => p.damageTaken],
    ["Meistgetötet", (p) => topEntry(p.topKilled), null],
  ] },
  { id: "wanted", label: "Most Wanted", posters: true, cols: [
    ["Spieler-Kills", (p) => num(p.playerKills), (p) => p.playerKills || 0],
    ["Von Spielern getötet", (p) => num(p.killedByPlayers), (p) => p.killedByPlayers || 0],
    ["Tode gesamt", (p) => num(p.deaths), (p) => p.deaths || 0],
  ] },
  { id: "bewegung", label: "Bewegung", cols: [
    ["Strecke gesamt", (p) => km(p.distanceCm), (p) => p.distanceCm],
    ["Zu Fuß", (p) => km(p.walkCm), (p) => p.walkCm],
    ["Sprünge", (p) => num(p.jumps), (p) => p.jumps],
  ] },
  { id: "bloecke", label: "Blöcke & Items", cols: [
    ["Abgebaut", (p) => num(p.mined), (p) => p.mined],
    ["Top-Block", (p) => topEntry(p.topMined), null],
    ["Gecraftet", (p) => num(p.crafted), (p) => p.crafted],
    ["Top-Item", (p) => topEntry(p.topCrafted), null],
  ] },
  { id: "bases", label: "Bases", bases: true, cols: [] },
];

const DIMS = { overworld: "Overworld", the_nether: "Nether", the_end: "End" };
const dimName = (d) => DIMS[d] || pretty(d);

const state = { players: [], bases: [], tiles: null, updated: null, demo: false, online: null, tab: TABS[0], sortCol: 0, dim: null, base: null, detail: false, date: null };
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function timeCell(p, max) {
  const fill = el("i");
  fill.style.width = (max ? (p.playTicks / max) * 100 : 0) + "%";
  return el("div", {}, playtime(p.playTicks), el("div", { className: "bar" }, fill));
}

function playerCell(p) {
  const img = el("img", { alt: "", loading: "lazy", src: `https://mc-heads.net/avatar/${encodeURIComponent(p.uuid)}/32` });
  const cell = el("div", { className: "player" }, img, p.name);
  if (p.online) cell.append(el("span", { className: "dot", title: "online" }));
  const isNew = p.firstSeen && Date.now() - new Date(p.firstSeen) < NEW_PLAYER_DAYS * 864e5;
  if (isNew) cell.append(el("span", { className: "badge" }, "NEU"));
  return cell;
}

function poster(p, rank) {
  const img = el("img", { alt: "", src: `https://mc-heads.net/avatar/${encodeURIComponent(p.uuid)}/96` });
  return el("div", { className: "poster" },
    el("div", { className: "poster-title" }, "WANTED"),
    img,
    el("div", { className: "poster-name" }, p.name),
    el("div", {}, `${num(p.playerKills)} Spieler-Kills`),
    el("div", { className: "poster-rank" }, `Nr. ${rank}`),
  );
}

const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

// eigenes Bild aus /base icon (vom Sync nach data/icons kopiert), sonst der Kopf des Besitzers
const head = (b, size) => b.icon || `https://mc-heads.net/avatar/${encodeURIComponent(b.uuid || b.player)}/${size}`;
const iconClass = (b) => (b.icon ? "custom" : "");
const baseKey = (b) => `${b.dim}/${b.player}/${b.name}`;

// Karte: Norden oben, Osten rechts – wie eine Minecraft-Karte (X nach rechts, Z nach unten)
function baseMap(bases, selected) {
  const SIZE = 600;
  // Ausschnitt: alle Bases mit Rand – dieselbe Rechnung wie view_regions() in scripts/maps.py
  const xs = bases.map((b) => b.x), zs = bases.map((b) => b.z);
  const span = Math.max(512, Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs)) * 1.3;
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cz = (Math.max(...zs) + Math.min(...zs)) / 2;
  const scale = SIZE / span;
  const px = (x) => SIZE / 2 + (x - cx) * scale, pz = (z) => SIZE / 2 + (z - cz) * scale;

  const step = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000].find((s) => span / s <= 7) || 50000;
  const root = svg("svg", { viewBox: `0 0 ${SIZE} ${SIZE}`, role: "img", "aria-label": "Karte der markierten Bases" });
  root.append(svg("rect", { width: SIZE, height: SIZE, class: "map-bg" }));

  // echtes Gelände: eine Kachel pro Region (512 × 512 Blöcke); wo noch keine da ist, bleibt Papier
  const tiles = bases[0].dim === "overworld" ? state.tiles : null;
  for (const [key, info] of Object.entries(tiles?.regions || {})) {
    const [rx, rz] = key.split(".").map(Number);
    // einen Hauch größer zeichnen, damit zwischen den Kacheln keine Haarlinien bleiben
    root.append(svg("image", {
      href: `${tiles.dir}/r.${key}.png?t=${encodeURIComponent(info.rendered)}`, class: "map-patch",
      x: px(rx * 512), y: pz(rz * 512), width: 512 * scale + 0.5, height: 512 * scale + 0.5,
    }));
  }
  for (let v = Math.ceil((cx - span / 2) / step) * step; v <= cx + span / 2; v += step) {
    root.append(svg("line", { x1: px(v), x2: px(v), y1: 0, y2: SIZE, class: v ? "map-grid" : "map-axis" }));
    root.append(svg("text", { x: px(v) + 4, y: 16, class: "map-tick" }, String(v)));
  }
  for (let v = Math.ceil((cz - span / 2) / step) * step; v <= cz + span / 2; v += step) {
    root.append(svg("line", { y1: pz(v), y2: pz(v), x1: 0, x2: SIZE, class: v ? "map-grid" : "map-axis" }));
    root.append(svg("text", { x: 4, y: pz(v) - 4, class: "map-tick" }, String(v)));
  }
  root.append(svg("text", { x: SIZE - 10, y: SIZE - 10, class: "map-tick", "text-anchor": "end" }, "N ▲ · X → · Z ▼"));

  // ausgewählte Base zuletzt zeichnen, damit sie oben liegt
  for (const b of [...bases].sort((a, c) => (a === selected) - (c === selected))) {
    const g = svg("g", { class: "map-base" + (b === selected ? " sel" : ""), tabindex: 0, role: "button", "aria-label": `${b.name} von ${b.player}` });
    g.append(
      svg("rect", { x: px(b.x) - 15, y: pz(b.z) - 15, width: 30, height: 30 }),
      svg("image", { href: head(b, 48), x: px(b.x) - 12, y: pz(b.z) - 12, width: 24, height: 24, class: iconClass(b), preserveAspectRatio: "xMidYMid slice" }),
      svg("text", { x: px(b.x), y: pz(b.z) + 32, "text-anchor": "middle" }, b.name),
    );
    // zweiter Klick auf die gewählte Base öffnet die Nahansicht
    g.onclick = () => { state.detail = b === selected && !!b.map; state.base = baseKey(b); state.date = null; render(); };
    g.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); g.onclick(); } };
    root.append(g);
  }
  return root;
}

const mapUrl = (b, date) => `${b.map.dir}/${date || b.map.dates.at(-1)}.png?t=${encodeURIComponent(b.map.rendered)}`;
const dayLabel = (d) => new Date(d + "T12:00").toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });

// Nahansicht einer Base: ein Pixel pro Block, mit Zeitleiste über die gespeicherten Tage
function baseDetail(b) {
  const { size, origin, dates } = b.map;
  const date = dates.includes(state.date) ? state.date : dates.at(-1);
  const img = svg("image", { href: mapUrl(b, date), width: size, height: size, class: "map-patch" });
  const view = svg("svg", { viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": `${b.name} von oben` },
    svg("rect", { width: size, height: size, class: "map-bg" }), img,
    svg("rect", { x: b.x - origin[0] - 3, y: b.z - origin[1] - 3, width: 7, height: 7, class: "map-pin map-pin-edge" }),
    svg("rect", { x: b.x - origin[0] - 3, y: b.z - origin[1] - 3, width: 7, height: 7, class: "map-pin" }),
  );

  const label = el("span", {}, dayLabel(date));
  const time = [el("button", { type: "button", onclick: () => { state.detail = false; render(); } }, "◀ Übersicht")];
  if (dates.length > 1) {
    const slider = el("input", { type: "range", min: 0, max: dates.length - 1, value: dates.indexOf(date), ariaLabel: "Tag wählen" });
    // nur Bild und Datum tauschen – ein kompletter Neuaufbau würde das Ziehen abbrechen
    slider.oninput = () => {
      state.date = dates[slider.value];
      img.setAttribute("href", mapUrl(b, state.date));
      label.textContent = dayLabel(state.date);
    };
    time.push(slider);
  }
  time.push(label);
  $("base-time").replaceChildren(...time);
  return view;
}

function renderBases() {
  const dims = [...new Set(state.bases.map((b) => b.dim))].sort((a, b) => Object.keys(DIMS).indexOf(a) - Object.keys(DIMS).indexOf(b));
  if (!dims.includes(state.dim)) state.dim = dims[0] || null;
  const bases = state.bases.filter((b) => b.dim === state.dim);
  const selected = bases.find((b) => baseKey(b) === state.base) || bases[0];

  $("no-bases").hidden = state.bases.length > 0;
  for (const id of ["base-dims", "base-map", "base-time", "base-info", "base-list"]) $(id).hidden = !state.bases.length;
  if (!state.bases.length) return;

  $("base-dims").hidden = dims.length < 2;
  $("base-dims").replaceChildren(...dims.map((d) => el("button", {
    type: "button", ariaPressed: String(d === state.dim), onclick: () => { state.dim = d; state.base = null; state.detail = false; render(); },
  }, dimName(d))));

  const detail = state.detail && !!selected.map;
  $("base-time").hidden = !detail;
  $("base-map").replaceChildren(detail ? baseDetail(selected) : baseMap(bases, selected));
  $("base-info").replaceChildren(
    el("img", { alt: "", src: head(selected, 96), className: iconClass(selected) }),
    el("div", { className: "base-name" }, selected.name),
    el("div", {}, `von ${selected.player}`),
    el("div", { className: "base-coords" }, `X ${num(selected.x)} · Y ${num(selected.y)} · Z ${num(selected.z)}`),
    el("div", {}, dimName(selected.dim)),
    el("div", { className: "base-since" }, `markiert ${ago(selected.since)}`),
  );
  if (selected.map && !detail) {
    $("base-info").append(el("button", { type: "button", className: "base-open", onclick: () => { state.detail = true; render(); } }, "Base ansehen ▶"));
  } else if (!selected.map) {
    $("base-info").append(el("div", { className: "base-since" }, selected.dim === "overworld" ? "Kartenbild folgt in ein paar Minuten" : "Kartenbild nur in der Overworld"));
  }
  $("base-list").replaceChildren(...bases.map((b) => el("button", {
    type: "button", ariaPressed: String(b === selected), onclick: () => { state.base = baseKey(b); state.date = null; render(); },
  }, el("img", { alt: "", loading: "lazy", src: head(b, 32), className: iconClass(b) }), el("span", {}, b.name), el("small", {}, b.player))));
}

function render() {
  const onlineNames = new Set((state.online?.names || []).map((n) => n.toLowerCase()));
  const players = state.players.map((p) => ({ ...p, online: onlineNames.has(p.name.toLowerCase()) }));
  const { cols } = state.tab;
  const showBases = !!state.tab.bases;
  const sortBy = cols[state.sortCol]?.[2] || ((p) => p.playTicks);
  players.sort((a, b) => sortBy(b) - sortBy(a));
  const maxTicks = Math.max(0, ...players.map((p) => p.playTicks));

  $("tabs").replaceChildren(...TABS.map((t) => el("button", {
    type: "button", role: "tab", ariaSelected: String(t === state.tab),
    onclick: () => { state.tab = t; state.sortCol = 0; render(); },
  }, t.label)));

  $("bases").hidden = !showBases;
  $("table").hidden = showBases;
  if (showBases) renderBases();

  $("thead").replaceChildren(el("th", {}, "#"), el("th", {}, "Spieler"), ...cols.map(([title, , sort], i) => {
    if (!sort) return el("th", {}, title);
    const btn = el("button", { type: "button", onclick: () => { state.sortCol = i; render(); } }, title + (i === state.sortCol ? " ▼" : ""));
    if (i === state.sortCol) btn.dataset.active = "";
    return el("th", {}, btn);
  }));

  $("tbody").replaceChildren(...players.map((p, i) => el("tr", {},
    el("td", {}, String(i + 1)),
    el("td", {}, playerCell(p)),
    ...cols.map(([, show]) => el("td", {}, show(p, maxTicks))),
  )));
  $("empty").hidden = showBases || players.length > 0;

  const wanted = [...players].filter((p) => p.playerKills > 0).sort((a, b) => b.playerKills - a.playerKills).slice(0, 3);
  $("posters").hidden = !state.tab.posters || !wanted.length;
  $("posters").replaceChildren(...wanted.map((p, i) => poster(p, i + 1)));
  $("no-wanted").hidden = !state.tab.posters || wanted.length > 0 || !players.length;

  $("sum-players").textContent = num(players.length);
  $("sum-time").textContent = playtime(players.reduce((s, p) => s + p.playTicks, 0));
  $("sum-updated").textContent = ago(state.updated);
  $("demo-note").hidden = !state.demo;

  const o = state.online;
  $("sum-online").textContent = o?.up ? `${o.count} / ${o.max}` : "–";
  $("status").hidden = !o;
  $("status").classList.toggle("on", !!o?.up);
  $("status-text").textContent = o?.up ? "Server online" : "Server offline";
}

async function loadStats() {
  try {
    const res = await fetch(`data/stats.json?t=${Date.now()}`, { cache: "no-store" });
    const data = await res.json();
    Object.assign(state, { players: data.players || [], bases: data.bases || [], tiles: data.tiles || null, updated: data.updated, demo: !!data.demo });
  } catch (err) {
    console.error("Stats konnten nicht geladen werden", err);
  }
}

async function loadOnline() {
  if (!CONFIG.serverAddress) return;
  try {
    const res = await fetch(`https://api.mcsrvstat.us/3/${encodeURIComponent(CONFIG.serverAddress)}`);
    const data = await res.json();
    state.online = {
      up: !!data.online,
      count: data.players?.online ?? 0,
      max: data.players?.max ?? 0,
      names: (data.players?.list || []).map((p) => p.name),
    };
  } catch (err) {
    console.error("Online-Status konnte nicht geladen werden", err);
  }
}

async function refresh() {
  await Promise.all([loadStats(), loadOnline()]);
  render();
}

$("server-name").textContent = document.title = CONFIG.serverName || "Server-Stats";
refresh();
setInterval(refresh, REFRESH_MS);
