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
    ["Stirbt meist durch", (p) => (p.deaths ? topEntry(p.topKilledBy) : "–"), null],
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

const state = { players: [], bases: [], tiles: null, updated: null, demo: false, online: null, tab: TABS[0], sortCol: 0, dim: null, base: null, detail: false, date: null, view: null, profile: null };
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
  const name = el("button", { type: "button", className: "link", title: "Profil ansehen", onclick: () => { state.profile = p.uuid; render(); } }, p.name);
  const cell = el("div", { className: "player" }, img, name);
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

// Ausschnitt, der alle Bases mit Rand zeigt – dieselbe Rechnung wie view_regions() in scripts/maps.py
function mapFit(bases) {
  const xs = bases.map((b) => b.x), zs = bases.map((b) => b.z);
  const span = Math.max(512, Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs)) * 1.3;
  return { span, cx: (Math.max(...xs) + Math.min(...xs)) / 2, cz: (Math.max(...zs) + Math.min(...zs)) / 2 };
}

// state.view gibt es nur, solange jemand gezoomt oder verschoben hat – sonst folgt die Karte den Bases
function mapView(bases) {
  const fit = mapFit(bases), v = state.view;
  return v ? { span: fit.span / v.zoom, cx: v.cx, cz: v.cz, fit } : { ...fit, fit };
}

const MIN_SPAN = 64; // näher als 64 Blöcke Kantenlänge bringt bei 2 Blöcken pro Pixel nichts mehr
let overviewBases = null; // Bases der gerade gezeigten Übersicht; null, solange die Nahansicht offen ist

function zoomMap(factor, fx = 0.5, fy = 0.5) {
  if (!overviewBases) return;
  const { span, cx, cz, fit } = mapView(overviewBases);
  const zoom = Math.min(fit.span / MIN_SPAN, Math.max(0.5, (fit.span / span) * factor));
  const next = fit.span / zoom;
  // der Punkt unter dem Zeiger bleibt stehen
  state.view = { zoom, cx: cx + (fx - 0.5) * (span - next), cz: cz + (fy - 0.5) * (span - next) };
  renderBases();
}

function initMapGestures() {
  const box = $("base-map"), pointers = new Map();
  let last = null, moved = false, pinch = 0, frame = 0;
  const at = (x, y) => { const r = box.getBoundingClientRect(); return [(x - r.left) / r.width, (y - r.top) / r.height]; };
  box.addEventListener("wheel", (e) => {
    if (!overviewBases) return;
    e.preventDefault();
    zoomMap(e.deltaY < 0 ? 1.25 : 0.8, ...at(e.clientX, e.clientY));
  }, { passive: false });
  box.addEventListener("pointerdown", (e) => {
    if (!overviewBases || e.target.closest(".map-zoom")) return;
    pointers.set(e.pointerId, e);
    last = e; moved = false; pinch = 0;
  });
  box.addEventListener("pointermove", (e) => {
    if (!overviewBases || !pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, e);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      if (pinch) zoomMap(dist / pinch, ...at((a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2));
      pinch = dist; moved = true;
      return;
    }
    const dx = e.clientX - last.clientX, dy = e.clientY - last.clientY;
    // ein paar Pixel Zittern sind noch ein Klick auf einen Marker, kein Verschieben
    if (!moved && Math.hypot(dx, dy) < 5) return;
    // festhalten, damit das Ziehen auch außerhalb der Karte weiterläuft (und kein Marker-Klick nachkommt)
    if (!moved) try { box.setPointerCapture(e.pointerId); } catch {}
    moved = true; last = e;
    const { span, cx, cz, fit } = mapView(overviewBases), r = box.getBoundingClientRect();
    state.view = { zoom: fit.span / span, cx: cx - (dx / r.width) * span, cz: cz - (dy / r.height) * span };
    frame ||= requestAnimationFrame(() => { frame = 0; renderBases(); });
  });
  const end = (e) => { pointers.delete(e.pointerId); pinch = 0; };
  box.addEventListener("pointerup", end);
  box.addEventListener("pointercancel", end);
}

// Karte: Norden oben, Osten rechts – wie eine Minecraft-Karte (X nach rechts, Z nach unten)
function baseMap(bases, selected) {
  const SIZE = 600;
  const { span, cx, cz } = mapView(bases);
  const scale = SIZE / span;
  const px = (x) => SIZE / 2 + (x - cx) * scale, pz = (z) => SIZE / 2 + (z - cz) * scale;

  const step = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000].find((s) => span / s <= 7) || 50000;
  const root = svg("svg", { viewBox: `0 0 ${SIZE} ${SIZE}`, role: "img", "aria-label": "Karte der markierten Bases" });
  root.append(svg("rect", { width: SIZE, height: SIZE, class: "map-bg" }));

  // echtes Gelände: eine Kachel pro Region (512 × 512 Blöcke); wo noch keine da ist, bleibt Papier
  const tiles = bases[0].dim === "overworld" ? state.tiles : null;
  for (const [key, info] of Object.entries(tiles?.regions || {})) {
    const [rx, rz] = key.split(".").map(Number);
    if (px(rx * 512 + 512) < 0 || px(rx * 512) > SIZE || pz(rz * 512 + 512) < 0 || pz(rz * 512) > SIZE) continue;
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
    type: "button", ariaPressed: String(d === state.dim), onclick: () => { state.dim = d; state.base = null; state.detail = false; state.view = null; render(); },
  }, dimName(d))));

  const detail = state.detail && !!selected.map;
  $("base-time").hidden = !detail;
  overviewBases = detail ? null : bases;
  $("base-map").classList.toggle("pannable", !detail);
  $("base-map").replaceChildren(detail ? baseDetail(selected) : baseMap(bases, selected));
  if (!detail) {
    const zoom = el("div", { className: "map-zoom" },
      el("button", { type: "button", title: "Näher ran", onclick: () => zoomMap(1.6) }, "+"),
      el("button", { type: "button", title: "Weiter weg", onclick: () => zoomMap(1 / 1.6) }, "−"));
    if (state.view) zoom.append(el("button", { type: "button", title: "Alle Bases zeigen", onclick: () => { state.view = null; renderBases(); } }, "⟲"));
    $("base-map").append(zoom);
  }
  const mates = (selected.shared || []).map((s) => s.name);
  $("base-info").replaceChildren(
    el("img", { alt: "", src: head(selected, 96), className: iconClass(selected) }),
    el("div", { className: "base-name" }, selected.name),
    el("div", {}, `von ${[selected.player, ...mates].join(" & ")}`),
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
    // liegt die Base außerhalb des gezoomten Ausschnitts, springt die Karte hin
    type: "button", ariaPressed: String(b === selected), onclick: () => { state.base = baseKey(b); state.date = null; if (state.view) state.view = { ...state.view, cx: b.x, cz: b.z }; render(); },
  }, el("img", { alt: "", loading: "lazy", src: head(b, 32), className: iconClass(b) }), el("span", {}, b.name), el("small", {}, b.player + (b.shared ? ` +${b.shared.length}` : "")))));
}

const ADV_TABS = { story: "Minecraft", nether: "Nether", end: "Das Ende", adventure: "Abenteuer", husbandry: "Landwirtschaft" };

function card(title, rows, empty) {
  const body = rows.length
    ? el("ul", {}, ...rows.map(([label, value]) => el("li", {}, el("span", {}, label), el("b", {}, value))))
    : el("p", {}, empty);
  return el("section", { className: "card" }, el("h3", {}, title), body);
}

function renderProfile(p, rank) {
  const facts = [
    ["Spielzeit", playtime(p.playTicks)], ["Tode", num(p.deaths)], ["Mob-Kills", num(p.mobKills)], ["Spieler-Kills", num(p.playerKills)],
    ["Schaden ausgeteilt", hearts(p.damageDealt)], ["Schaden kassiert", hearts(p.damageTaken)], ["Strecke", km(p.distanceCm)], ["Sprünge", num(p.jumps)],
    ["Abgebaut", num(p.mined)], ["Gecraftet", num(p.crafted)],
  ];
  const entries = (list) => (list || []).map(([id, n]) => [pretty(id), num(n)]);

  // Minecraft zählt nur Tode durch Mobs und Spieler einzeln – der Rest ist Fallen, Lava, Ertrinken usw.
  const causes = p.deaths ? entries(p.topKilledBy).map(([who, n]) => [who === "Player" ? "Spieler" : who, n]) : [];
  const other = (p.deaths || 0) - (p.topKilledBy || []).reduce((s, [, n]) => s + n, 0);
  if (other > 0) causes.push(["Anderes (Fall, Lava, Ertrinken …)", num(other)]);

  const adv = p.adv;
  const advRows = adv ? Object.entries(adv.byTab).sort((a, b) => b[1] - a[1]).map(([tab, n]) => [ADV_TABS[tab] || pretty(tab), num(n)]) : [];
  // jeder Reiter beginnt mit einem Advancement namens "root" – das bekommt den Namen des Reiters
  const advName = (id) => {
    const [tab, ...rest] = id.replace(/^.*:/, "").split("/");
    return rest.at(-1) === "root" ? `${ADV_TABS[tab] || pretty(tab)} (Start)` : pretty(rest.at(-1));
  };
  const recent = adv ? adv.recent.map(([id, when]) => [advName(id), when ? ago(when) : "–"]) : [];

  const bases = state.bases.filter((b) => b.player === p.name || (b.shared || []).some((s) => s.name === p.name));
  const baseButtons = bases.map((b) => el("button", {
    type: "button", onclick: () => { Object.assign(state, { profile: null, tab: TABS.find((t) => t.bases), dim: b.dim, base: baseKey(b), detail: false, view: null }); render(); },
  }, el("img", { alt: "", src: head(b, 32), className: iconClass(b) }), el("span", {}, b.name)));

  $("profile").replaceChildren(
    el("button", { type: "button", className: "back", onclick: () => { state.profile = null; render(); } }, "◀ Zurück"),
    el("div", { className: "profile-head" },
      el("img", { alt: "", src: `https://mc-heads.net/avatar/${encodeURIComponent(p.uuid)}/96` }),
      el("div", {},
        el("h2", {}, p.name),
        el("div", {}, p.online ? "jetzt online" : `zuletzt gesehen ${ago(p.lastSeen)}`),
        el("div", { className: "muted" }, `Platz ${rank} nach Spielzeit`))),
    el("div", { className: "slots profile-facts" }, ...facts.map(([label, value]) =>
      el("div", { className: "slot" }, el("span", { className: "slot-label" }, label), el("span", { className: "slot-value" }, value)))),
    el("div", { className: "cards" },
      card("Todesursachen", causes, "Noch nie gestorben."),
      card("Meistgetötet", entries(p.topKilled), "Noch nichts getötet."),
      card("Top-Blöcke", entries(p.topMined), "Noch nichts abgebaut."),
      card("Top-Items", entries(p.topCrafted), "Noch nichts gecraftet."),
      card(`Advancements: ${adv ? num(adv.count) : "–"} (experimentell)`, advRows, "Noch keine Daten."),
      card("Zuletzt erreicht", recent, "Noch keine Daten.")),
    el("section", { className: "card" }, el("h3", {}, "Bases"),
      bases.length ? el("div", { className: "base-list" }, ...baseButtons) : el("p", {}, "Keine Base markiert.")),
  );
}

function render() {
  const onlineNames = new Set((state.online?.names || []).map((n) => n.toLowerCase()));
  const players = state.players.map((p) => ({ ...p, online: onlineNames.has(p.name.toLowerCase()) }));
  const { cols } = state.tab;
  const sortBy = cols[state.sortCol]?.[2] || ((p) => p.playTicks);
  players.sort((a, b) => sortBy(b) - sortBy(a));
  const maxTicks = Math.max(0, ...players.map((p) => p.playTicks));

  $("tabs").replaceChildren(...TABS.map((t) => el("button", {
    type: "button", role: "tab", ariaSelected: String(t === state.tab),
    onclick: () => { state.tab = t; state.sortCol = 0; state.profile = null; render(); },
  }, t.label)));

  // das Profil nach Spielzeit einordnen, nicht nach der gerade gewählten Spalte
  const byTime = [...players].sort((a, b) => b.playTicks - a.playTicks);
  const profile = byTime.find((p) => p.uuid === state.profile);
  $("profile").hidden = !profile;
  if (profile) renderProfile(profile, byTime.indexOf(profile) + 1);

  const showBases = !!state.tab.bases && !profile;
  if (!showBases) overviewBases = null;
  $("bases").hidden = !showBases;
  $("table").hidden = showBases || !!profile;
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
  $("empty").hidden = showBases || !!profile || players.length > 0;

  const wanted = [...players].filter((p) => p.playerKills > 0).sort((a, b) => b.playerKills - a.playerKills).slice(0, 3);
  $("posters").hidden = !state.tab.posters || !wanted.length || !!profile;
  $("posters").replaceChildren(...wanted.map((p, i) => poster(p, i + 1)));
  $("no-wanted").hidden = !state.tab.posters || wanted.length > 0 || !players.length || !!profile;

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
initMapGestures();
refresh();
setInterval(refresh, REFRESH_MS);
