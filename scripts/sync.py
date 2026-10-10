"""Holt die Spieler-Stats per FTP vom Minecraft-Server und schreibt data/stats.json.

Zugangsdaten kommen aus Umgebungsvariablen (GitHub Secrets), nie aus dem Code:
  FTP_HOST, FTP_USER, FTP_PASS   Pflicht
  FTP_PORT                       optional, Standard 21
  SERVER_DIR                     optional, Ordner mit server.properties (sonst automatische Suche)

Lokaler Test ohne FTP:  python scripts/sync.py --local <Serverordner>
"""
import io
import json
import os
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

    def c(key):
        return custom.get(PREFIX + key, 0)

    return {
        # vor 1.17 hieß die Spielzeit play_one_minute (trotzdem in Ticks)
        "playTicks": c("play_time") or c("play_one_minute"),
        "deaths": c("deaths"),
        "mobKills": c("mob_kills"),
        "playerKills": c("player_kills"),
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
        "topKilled": top(killed, 1),
    }


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

    old = {}
    if OUT.exists():
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        # nach einem Wechsel des Serverordners gilt der Lauf wieder als erster (niemand ist "neu")
        if not prev.get("demo") and prev.get("source") == source:
            old = {p["uuid"]: p for p in prev.get("players", [])}
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
        players.append(player)

    players.sort(key=lambda p: p["playTicks"], reverse=True)
    if OUT.exists() and players == list(old.values()):
        print("Keine Änderung")
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updated": now, "source": source, "players": players}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(players)} Spieler geschrieben")


if __name__ == "__main__":
    main()
