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
];

const state = { players: [], updated: null, demo: false, online: null, tab: TABS[0], sortCol: 0 };
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

function render() {
  const onlineNames = new Set((state.online?.names || []).map((n) => n.toLowerCase()));
  const players = state.players.map((p) => ({ ...p, online: onlineNames.has(p.name.toLowerCase()) }));
  const { cols } = state.tab;
  const sortBy = cols[state.sortCol][2];
  players.sort((a, b) => sortBy(b) - sortBy(a));
  const maxTicks = Math.max(0, ...players.map((p) => p.playTicks));

  $("tabs").replaceChildren(...TABS.map((t) => el("button", {
    type: "button", role: "tab", ariaSelected: String(t === state.tab),
    onclick: () => { state.tab = t; state.sortCol = 0; render(); },
  }, t.label)));

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
  $("empty").hidden = players.length > 0;

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
    Object.assign(state, { players: data.players || [], updated: data.updated, demo: !!data.demo });
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
