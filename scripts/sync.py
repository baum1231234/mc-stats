"""Holt die Spieler-Stats per FTP vom Minecraft-Server und schreibt data/stats.json.

Zugangsdaten kommen aus Umgebungsvariablen (GitHub Secrets), nie aus dem Code:
  FTP_HOST, FTP_USER, FTP_PASS   Pflicht
  FTP_PORT                       optional, Standard 21
  SERVER_DIR                     optional, Ordner mit server.properties (sonst automatische Suche)

Lokaler Test ohne FTP:  python scripts/sync.py --local <Serverordner>
"""
import gzip
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from ftplib import FTP, FTP_TLS, all_errors
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "stats.json"
PREFIX = "minecraft:"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def top(group, n=3):
    items = sorted(group.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [[k.removeprefix(PREFIX), v] for k, v in items]


def summarize(raw):
    stats = raw.get("stats", {})
    custom = stats.get(PREFIX + "custom", {})
    mined = stats.get(PREFIX + "mined", {})
    crafted = stats.get(PREFIX + "crafted", {})
    killed = stats.get(PREFIX + "killed", {})
    killed_by = stats.get(PREFIX + "killed_by", {})

    def c(key):
        return custom.get(PREFIX + key, 0)

    return {
        # vor 1.17 hieß die Spielzeit play_one_minute (trotzdem in Ticks)
        "playTicks": c("play_time") or c("play_one_minute"),
        "deaths": c("deaths"),
        "mobKills": c("mob_kills"),
        "playerKills": c("player_kills"),
        "killedByPlayers": killed_by.get(PREFIX + "player", 0),
        "damageDealt": c("damage_dealt"),
        "damageTaken": c("damage_taken"),
        "distanceCm": sum(v for k, v in custom.items() if k.endswith("_one_cm")),
        "walkCm": c("walk_one_cm") + c("sprint_one_cm") + c("crouch_one_cm"),
        "flyCm": c("aviate_one_cm") + c("fly_one_cm"),
        "jumps": c("jump"),
        "mined": sum(mined.values()),
        "topMined": top(mined),
        "crafted": sum(crafted.values()),
        "topCrafted": top(crafted),
        "topKilled": top(killed),
        # nur Tode durch Mobs und Spieler – Fallen, Lava usw. zählt Minecraft nicht einzeln
        "topKilledBy": top(killed_by, 5),
    }


ADV_RECENT = 8


def summarize_advancements(raw):
    """Experimentell: erledigte Advancements je Reiter und die zuletzt erreichten (ohne Rezepte)."""
    by_tab, done = {}, []
    for key, entry in raw.items():
        if not isinstance(entry, dict) or not entry.get("done"):
            continue
        ident = key.removeprefix(PREFIX)
        if "recipes/" in ident or "/" not in ident:
            continue
        tab = ident.split("/")[0].split(":")[-1]
        by_tab[tab] = by_tab.get(tab, 0) + 1
        when = max((str(v) for v in (entry.get("criteria") or {}).values()), default="")
        try:
            when = datetime.strptime(when, "%Y-%m-%d %H:%M:%S %z").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            when = None
        done.append([ident, when])
    done.sort(key=lambda d: d[1] or "", reverse=True)
    return {"count": len(done), "byTab": by_tab, "recent": done[:ADV_RECENT]}


class LocalSource:
    def __init__(self, root):
        self.root = Path(root)
        self.roots = [self.root]

    def read(self, path):
        return (self.root / path).read_bytes()

    def list(self, path):
        return [p.name for p in (self.root / path).iterdir()]

    def mtime(self, path):
        ts = (self.root / path).stat().st_mtime
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FtpSource:
    def __init__(self):
        host, user, pw = (os.environ[k] for k in ("FTP_HOST", "FTP_USER", "FTP_PASS"))
        port = int(os.environ.get("FTP_PORT") or 21)
        try:
            ftp = FTP_TLS(timeout=30)
            ftp.connect(host, port)
            ftp.login(user, pw)
            ftp.prot_p()
        except all_errors:
            print("FTPS nicht möglich, nutze normales FTP")
            ftp = FTP(timeout=30)
            ftp.connect(host, port)
            ftp.login(user, pw)
        self.ftp = ftp
        self.roots = self._find_server_dirs()
        self.root = self.roots[0]

    def _exists(self, path):
        try:
            self.ftp.voidcmd("TYPE I")  # SIZE geht bei vielen Servern nur im Binärmodus
            self.ftp.size(path)
            return True
        except all_errors:
            return False

    def _dirs(self, path):
        out = []
        try:
            for name, facts in self.ftp.mlsd(path):
                if facts.get("type") == "dir":
                    out.append(name)
        except all_errors:
            # Server ohne MLSD: alles ohne Punkt im Namen als Ordner probieren
            out = [n.rsplit("/", 1)[-1] for n in self.ftp.nlst(path) if "." not in n.rsplit("/", 1)[-1]]
        return out

    def _find_server_dirs(self):
        # Nitrado legt je Spielvariante einen eigenen Ordner an (minecraft, minecraftforge, ...)
        fixed = os.environ.get("SERVER_DIR")
        if fixed:
            return [fixed.strip("/")]
        level, seen, found = [""], [], []
        for _ in range(2):
            nxt = []
            for d in level:
                if self._exists(f"{d}/server.properties".lstrip("/")):
                    found.append(d)
                else:
                    nxt += [f"{d}/{s}".lstrip("/") for s in self._dirs(d or ".")]
            seen += nxt
            level = nxt
        if not found:
            print("Durchsuchte Ordner:", ", ".join(seen) or "keine")
            sys.exit("server.properties nicht gefunden – bitte SERVER_DIR als Secret setzen")
        return found

    def _p(self, path):
        return f"{self.root}/{path}".lstrip("/")

    def read(self, path):
        buf = io.BytesIO()
        self.ftp.retrbinary("RETR " + self._p(path), buf.write)
        return buf.getvalue()

    def list(self, path):
        return [n.rsplit("/", 1)[-1] for n in self.ftp.nlst(self._p(path) or ".")]

    def mtime(self, path):
        try:
            t = self.ftp.sendcmd("MDTM " + self._p(path)).split()[-1][:14]
            return datetime.strptime(t, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
        except (all_errors, ValueError):
            return None


def mojang_name(uuid):
    url = "https://sessionserver.mojang.com/session/minecraft/profile/" + uuid.replace("-", "")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r)["name"]
    except Exception:
        return None


def locate(src):
    """Findet den Stats-Ordner im aktuellen Serverordner."""
    world = "world"
    try:
        for line in src.read("server.properties").decode("utf-8", "replace").splitlines():
            if line.startswith("level-name="):
                world = line.split("=", 1)[1].strip() or world
    except Exception:
        return None, []
    # neuere Minecraft-Versionen legen die Stats unter players/stats ab
    for cand in (f"{world}/stats", f"{world}/players/stats"):
        try:
            return cand, src.list(cand)
        except Exception:
            continue
    return None, []


def read_nbt(data):
    """Liest eine (meist gzip-gepackte) NBT-Datei in verschachtelte dicts/listen."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    pos = 0

    def take(fmt):
        nonlocal pos
        size = struct.calcsize(fmt)
        val = struct.unpack_from(fmt, data, pos)
        pos += size
        return val[0] if len(val) == 1 else val

    def string():
        nonlocal pos
        n = take(">H")
        s = data[pos:pos + n].decode("utf-8", "replace")
        pos += n
        return s

    def payload(tag):
        nonlocal pos
        if tag in NBT_NUMBERS:
            return take(NBT_NUMBERS[tag])
        if tag == 8:
            return string()
        if tag in NBT_ARRAYS:
            fmt = f">{take('>i')}{NBT_ARRAYS[tag]}"
            vals = struct.unpack_from(fmt, data, pos)
            pos += struct.calcsize(fmt)
            return list(vals)
        if tag == 9:
            inner, n = take(">b"), take(">i")
            return [payload(inner) for _ in range(n)]
        if tag == 10:
            out = {}
            while (inner := take(">b")) != 0:
                key = string()
                out[key] = payload(inner)
            return out
        raise ValueError(f"unbekannter NBT-Typ {tag}")

    tag = take(">b")
    string()  # Name der Wurzel
    return payload(tag)


NBT_NUMBERS = {1: ">b", 2: ">h", 3: ">i", 4: ">q", 5: ">f", 6: ">d"}
NBT_ARRAYS = {7: "b", 11: "i", 12: "q"}
STORAGE = "kbk"  # Namensraum aus /base: data modify storage kbk:bases ...


def find_key(node, key):
    """Sucht einen Schlüssel beliebig tief – das Speicherformat ändert sich je nach Version."""
    if isinstance(node, dict):
        if isinstance(node.get(key), dict):
            return node[key]
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            hit = find_key(child, key)
            if hit is not None:
                return hit
    return None


ICONS = OUT.parent / "icons"
ICON_MAX = 1_000_000
# Dateianfang → Endung; nur echte Rasterbilder (kein SVG, kein HTML)
ICON_TYPES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif", b"RIFF": "webp"}


def fetch_icon(url):
    """Lädt das Bild hinter /base icon einmal herunter und gibt den Pfad der eigenen Kopie zurück.

    Die Seite zeigt nie den fremden Link selbst: sonst könnte jeder Spieler Besucher der Seite
    auf einen beliebigen Server schicken, und tote Links gäbe es auch.
    """
    if not url.startswith("https://"):
        return None
    stem = hashlib.sha1(url.encode()).hexdigest()[:16]
    for ext in ICON_TYPES.values():
        if (ICONS / f"{stem}.{ext}").exists():
            return f"data/icons/{stem}.{ext}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (mc-stats)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if not r.url.startswith("https://"):
                return None
            data = r.read(ICON_MAX + 1)
    except Exception as err:
        print("Icon nicht ladbar:", err)
        return None
    ext = next((e for magic, e in ICON_TYPES.items() if data.startswith(magic)), None)
    if ext == "webp" and data[8:12] != b"WEBP":
        ext = None
    if not ext or len(data) > ICON_MAX:
        print("Icon abgelehnt (kein Bild oder größer als 1 MB)")
        return None
    ICONS.mkdir(parents=True, exist_ok=True)
    (ICONS / f"{stem}.{ext}").write_bytes(data)
    return f"data/icons/{stem}.{ext}"


def read_bases(src, world, uuids, canon, old, now):
    """Bases, die Spieler im Spiel mit /base markiert haben (bis zu drei pro Spieler)."""
    raw = None
    # neuere Versionen: data/<Namensraum>/command_storage.dat, ältere: data/command_storage_<Namensraum>.dat
    for path in (f"{world}/data/{STORAGE}/command_storage.dat", f"{world}/data/command_storage_{STORAGE}.dat"):
        try:
            raw = read_nbt(src.read(path))
            break
        except Exception:
            continue
    if raw is None:
        print("Bases: keine lesbare Storage-Datei gefunden")
        return None  # Datei fehlt oder wird gerade geschrieben – alten Stand behalten
    # der Storage heißt kbk:bases und enthält selbst wieder den Schlüssel "bases" – bis zu den Einträgen absteigen
    entries = find_key(raw, "bases") or {}
    while isinstance(entries.get("bases"), dict) and "pos" not in entries["bases"]:
        entries = entries["bases"]
    bases = []
    for player, owned in entries.items():
        # je Spieler eine Liste von Bases; ganz am Anfang war es ein einzelner Eintrag
        for entry in owned if isinstance(owned, list) else [owned]:
            pos = entry.get("pos") if isinstance(entry, dict) else None
            if not pos or len(pos) != 3:
                continue
            base = {
                "player": player,
                "uuid": uuids.get(player.lower()),
                "name": str(entry.get("name") or "Base")[:32],
                "x": round(pos[0]), "y": round(pos[1]), "z": round(pos[2]),
                "dim": str(entry.get("dim") or "minecraft:overworld").removeprefix(PREFIX),
            }
            known = old.get((player, base["name"]), {})
            same = all(known.get(k) == base[k] for k in ("x", "y", "z", "dim"))
            base["since"] = known.get("since", now) if same else now
            icon = fetch_icon(str(entry.get("icon") or ""))
            if icon:
                base["icon"] = icon
            # Mitbesitzer aus /base teilen – nur Namen, die der Server wirklich kennt
            shared = []
            for mate in entry.get("mit") or []:
                name = canon.get(str(mate.get("n") if isinstance(mate, dict) else mate).lower())
                if name and name.lower() != player.lower() and name not in [s["name"] for s in shared]:
                    shared.append({"name": name, "uuid": uuids.get(name.lower())})
            if shared:
                base["shared"] = shared[:5]
            bases.append(base)
    # Bilder, die keine Base mehr benutzt, wieder wegräumen
    used = {b.get("icon") for b in bases}
    for file in ICONS.glob("*") if ICONS.exists() else []:
        if f"data/icons/{file.name}" not in used:
            file.unlink()
    # wie bei den Karten: ein bereits laufender Sync-Job merkt nur data/stats.json vor
    subprocess.run(["git", "add", "-A", "data/icons"], cwd=OUT.parent.parent, capture_output=True)
    return sorted(bases, key=lambda b: (b["player"].lower(), b["name"].lower()))


def main():
    src = LocalSource(sys.argv[2]) if sys.argv[1:2] == ["--local"] else FtpSource()

    # den Serverordner mit den meisten Spieler-Stats nehmen
    best = None
    for root in src.roots:
        src.root = root
        stats_dir, files = locate(src)
        count = len([f for f in files if f.endswith(".json")])
        print(f"Serverordner '{root or '/'}': {stats_dir or 'keine Stats'} ({count} Spieler)")
        if stats_dir and (best is None or count > best[3]):
            best = (root, stats_dir, files, count)
    if best is None:
        sys.exit("Kein Stats-Ordner gefunden")
    src.root, stats_dir, files, _ = best

    source = f"{src.root}/{stats_dir}"
    print("Quelle:", source)

    names = {}
    try:
        for entry in json.loads(src.read("usercache.json")):
            names[entry["uuid"]] = entry["name"]
    except Exception:
        pass

    old, old_bases, old_tiles = {}, [], {}
    if OUT.exists():
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        # nach einem Wechsel des Serverordners gilt der Lauf wieder als erster (niemand ist "neu")
        if not prev.get("demo") and prev.get("source") == source:
            old = {p["uuid"]: p for p in prev.get("players", [])}
            old_bases = prev.get("bases", [])
            old_tiles = prev.get("tiles", {})
    first_run = not old
    now = now_iso()

    players = []
    for fname in files:
        if not fname.endswith(".json"):
            continue
        uuid = fname[:-5]
        path = f"{stats_dir}/{fname}"
        try:
            raw = json.loads(src.read(path))
        except ValueError:
            continue  # Datei wird gerade geschrieben – nächster Lauf holt sie
        known = old.get(uuid, {})
        player = {
            "uuid": uuid,
            "name": names.get(uuid) or known.get("name") or mojang_name(uuid) or uuid[:8],
            # beim allerersten Lauf ist niemand "neu"
            "firstSeen": known["firstSeen"] if uuid in old else (None if first_run else now),
            "lastSeen": src.mtime(path) or known.get("lastSeen"),
        }
        player.update(summarize(raw))
        # Advancements ändern sich nur zusammen mit den Stats – sonst den alten Stand behalten
        if "adv" in known and known.get("lastSeen") == player["lastSeen"]:
            player["adv"] = known["adv"]
        else:
            try:
                adv_path = f"{stats_dir.rsplit('/', 1)[0]}/advancements/{fname}"
                player["adv"] = summarize_advancements(json.loads(src.read(adv_path)))
            except Exception:
                if "adv" in known:
                    player["adv"] = known["adv"]
        players.append(player)

    players.sort(key=lambda p: p["playTicks"], reverse=True)

    uuids = {p["name"].lower(): p["uuid"] for p in players}
    uuids.update({name.lower(): uuid for uuid, name in names.items()})
    world = stats_dir.split("/")[0]
    tiles = old_tiles
    canon = {p["name"].lower(): p["name"] for p in players}
    canon.update({name.lower(): name for name in names.values()})
    bases = read_bases(src, world, uuids, canon, {(b["player"], b["name"]): b for b in old_bases}, now)
    if bases is None:
        bases = old_bases
    else:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import maps
            cache = {}  # Region-Dateien, die Base-Bilder und Kacheln gemeinsam brauchen
            drawn = maps.update(src, world, bases, old_bases, now, read_nbt, cache)
            tiles, drawn_tiles = maps.update_tiles(src, world, bases, old_tiles, now, read_nbt, cache)
            print(f"Karten: {drawn} Base-Bilder, {drawn_tiles} Kacheln neu gezeichnet")
            # ein bereits laufender Sync-Job merkt nur data/stats.json vor – die Bilder hier selbst vormerken
            subprocess.run(["git", "add", "data/maps", "data/tiles"], cwd=OUT.parent.parent, capture_output=True)
        except Exception as err:
            print("Karten fehlgeschlagen:", err)  # die Stats sollen trotzdem geschrieben werden

    if OUT.exists() and players == list(old.values()) and bases == old_bases and tiles == old_tiles:
        print("Keine Änderung")
        return
    OUT.parent.mkdir(exist_ok=True)
    out = {"updated": now, "source": source, "players": players, "bases": bases, "tiles": tiles}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(players)} Spieler, {len(bases)} Bases geschrieben")


if __name__ == "__main__":
    main()
