"""Zeichnet die Welt von oben: ein Bild je Base und Kacheln für die Übersichtskarte.

Übersicht: eine Kachel pro Region unter data/tiles/overworld/r.<x>.<z>.png.
Pro Base und Tag entsteht ein Bild unter data/maps/<Spieler>_<x>_<z>/<Datum>.png.
Neu gezeichnet wird nur, wenn sich eine der Region-Dateien geändert hat, und am selben
Tag höchstens alle RENDER_HOURS Stunden. Ältere Tage bleiben als Verlauf liegen.
"""
import gzip
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

SIZE = 256  # Kantenlänge der Base-Bilder in Blöcken (Vielfaches von 16)
RENDER_HOURS = 3
TILE_STEP = 2  # Übersichtskacheln: Blöcke pro Pixel
TILE_HOURS = 12  # eine Kachel höchstens so oft neu zeichnen
TILES_PER_RUN = 4  # Rest folgt im nächsten Lauf (jede Minute), damit kein Sync minutenlang hängt
MAX_TILES = 400  # unveränderte Regionen werden nie neu geladen, teuer ist nur der erste Durchgang
VIEW_MIN, VIEW_PAD = 512, 1.3  # Ausschnitt der Übersicht – muss zu baseMap() in app.js passen
ROOT = Path(__file__).resolve().parent.parent
PAPER = (217, 197, 147)  # ungeladene Chunks: Farbe des Kartenpapiers
U64 = 0xFFFFFFFFFFFFFFFF

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Berlin")
except Exception:
    TZ = timezone.utc

STONE, WOOD, PLANT, DIRT, WHITE = (112, 112, 112), (143, 119, 72), (0, 124, 0), (151, 109, 77), (255, 255, 255)
DYES = {
    "white": WHITE, "orange": (216, 127, 51), "magenta": (178, 76, 216), "light_blue": (102, 153, 216),
    "yellow": (229, 229, 51), "lime": (127, 204, 25), "pink": (242, 127, 165), "light_gray": (153, 153, 153),
    "gray": (76, 76, 76), "cyan": (76, 127, 153), "purple": (127, 63, 178), "blue": (51, 76, 178),
    "brown": (102, 76, 51), "green": (102, 127, 51), "red": (153, 51, 51), "black": (25, 25, 25),
}
WOODS = {
    "dark_oak": (102, 76, 51), "pale_oak": (235, 230, 225), "oak": WOOD, "spruce": (129, 86, 49),
    "birch": (247, 233, 163), "jungle": DIRT, "acacia": (216, 127, 51), "mangrove": (153, 51, 51),
    "cherry": (209, 177, 161), "bamboo": (229, 229, 51), "crimson": (148, 63, 97), "warped": (58, 142, 140),
}
EXACT = {
    "grass_block": (127, 178, 56), "dirt": DIRT, "coarse_dirt": DIRT, "rooted_dirt": DIRT, "farmland": DIRT,
    "dirt_path": (148, 121, 65), "podzol": (129, 86, 49), "mycelium": (127, 63, 178), "mud": (87, 92, 92),
    "water": (64, 64, 255), "bubble_column": (64, 64, 255), "lava": (255, 90, 0),
    "sand": (247, 233, 163), "sandstone": (247, 233, 163), "red_sand": (216, 127, 51), "gravel": (136, 126, 126),
    "snow": WHITE, "snow_block": WHITE, "powder_snow": WHITE, "clay": (164, 168, 184),
    "ice": (160, 160, 255), "packed_ice": (160, 160, 255), "blue_ice": (160, 160, 255), "frosted_ice": (160, 160, 255),
    "netherrack": (112, 2, 0), "soul_sand": (102, 76, 51), "soul_soil": (102, 76, 51), "glowstone": (247, 233, 163),
    "obsidian": (21, 20, 31), "crying_obsidian": (21, 20, 31), "bedrock": (60, 60, 60), "end_stone": (247, 233, 163),
    "calcite": (255, 252, 245), "diorite": (255, 252, 245), "granite": DIRT, "tuff": (87, 92, 92),
    "moss_block": (102, 127, 51), "moss_carpet": (102, 127, 51), "hay_block": (250, 238, 77),
    "pumpkin": (216, 127, 51), "carved_pumpkin": (216, 127, 51), "jack_o_lantern": (216, 127, 51),
    "melon": (127, 204, 25), "cactus": PLANT, "lily_pad": PLANT, "sugar_cane": PLANT, "tnt": (255, 0, 0),
    "bricks": (153, 51, 51), "terracotta": (216, 127, 51), "bookshelf": WOOD, "crafting_table": WOOD,
}
# Reihenfolge zählt: der erste Treffer im Blocknamen gewinnt
KEYWORDS = [
    ("cherry_leaves", (242, 127, 165)), ("leaves", PLANT),
    ("wheat", PLANT), ("carrots", PLANT), ("potatoes", PLANT), ("beetroots", PLANT), ("stem", PLANT),
    ("deepslate", (100, 100, 100)), ("blackstone", (25, 25, 25)), ("basalt", (80, 80, 80)),
    ("nether_brick", (112, 2, 0)), ("quartz", (255, 252, 245)), ("prismarine", (76, 127, 153)),
    ("purpur", (153, 51, 178)), ("amethyst", (127, 63, 178)), ("sculk", (13, 18, 23)),
    ("oxidized", (58, 142, 140)), ("weathered", (58, 142, 140)), ("copper", (216, 127, 51)),
    ("gold", (250, 238, 77)), ("diamond", (92, 219, 213)), ("emerald", (0, 217, 58)), ("lapis", (74, 128, 255)),
    ("redstone_block", (255, 0, 0)), ("iron", (167, 167, 167)),
    ("chest", WOOD), ("barrel", WOOD), ("composter", WOOD), ("campfire", WOOD), ("lectern", WOOD),
    ("loom", WOOD), ("table", WOOD), ("beehive", WOOD), ("bee_nest", (250, 238, 77)),
]
# Kleinkram und Durchsichtiges: stattdessen zählt der Block darunter
INVISIBLE = {"", "air", "cave_air", "void_air", "light", "barrier", "structure_void", "fire", "soul_fire"}
SEE_THROUGH = (
    "glass", "torch", "lantern", "rail", "lever", "button", "pressure_plate", "redstone_wire", "repeater",
    "comparator", "tripwire", "string", "cobweb", "chain", "_bars", "flower", "_pot", "candle", "_head", "skull",
    "banner", "sign", "ladder", "vine", "grass", "fern", "bush", "sapling", "tulip", "dandelion", "poppy",
    "orchid", "allium", "bluet", "daisy", "cornflower", "lily_of_the_valley", "sprouts", "petals", "dripleaf",
    "_rod", "bell",
)


def color(name):
    """Kartenfarbe eines Blocks; None heißt: durchsichtig, Block darunter nehmen."""
    if name in INVISIBLE:
        return None
    if name in EXACT:
        return EXACT[name]
    for key, rgb in KEYWORDS:
        if key in name:
            return rgb
    if any(part in name for part in SEE_THROUGH) or name.endswith("_mushroom"):
        return None
    for dye, rgb in DYES.items():
        if name.startswith(dye + "_"):
            return rgb
    for wood, rgb in WOODS.items():
        if name.startswith(wood + "_") or name.startswith("stripped_" + wood + "_"):
            return rgb
    return STONE


def block_name(entry):
    # 26.x speichert Paletteneinträge als reinen Text, als {"": Name} oder als {"id": Name, "properties": ...}
    if isinstance(entry, dict):
        entry = entry.get("") or entry.get("id") or entry.get("Name") or ""
    return str(entry).removeprefix("minecraft:")


def chunk_columns(chunk):
    """Liefert für die 256 Spalten eines Chunks (Index z*16+x) Höhe und Farbe des obersten Blocks."""
    if str(chunk.get("Status", "")).removeprefix("minecraft:") != "full":
        return None
    sections = {s["Y"]: s.get("block_states") for s in chunk.get("sections", []) if isinstance(s, dict) and "Y" in s}
    surface = (chunk.get("Heightmaps") or {}).get("WORLD_SURFACE")
    if not sections or not surface:
        return None
    min_y = min(sections) * 16
    bits = max(1, (len(sections) * 16).bit_length())
    per = 64 // bits
    palettes = {}  # Sektion -> (Farben je Paletteneintrag, Daten, Bits)

    def block_color(x, y, z):
        sy = y >> 4
        if sy not in palettes:
            states = sections.get(sy) or {}
            pal = [color(block_name(e)) for e in states.get("palette", [])]
            palettes[sy] = (pal, states.get("data"), max(4, (len(pal) - 1).bit_length()))
        pal, data, width = palettes[sy]
        if not pal:
            return None
        if not data:
            return pal[0]
        idx = ((y & 15) << 8) | (z << 4) | x
        n = 64 // width
        return pal[((data[idx // n] & U64) >> (width * (idx % n))) & ((1 << width) - 1)]

    heights, colors = [], []
    for i in range(256):
        top = min_y + (((surface[i // per] & U64) >> (bits * (i % per))) & ((1 << bits) - 1)) - 1
        rgb, y = None, top
        while rgb is None and y >= min_y and y > top - 6:
            rgb = block_color(i & 15, y, i >> 4)
            y -= 1
        heights.append(y + 1)
        colors.append(rgb)
    return heights, colors


def region_chunk(region, lx, lz, read_nbt):
    loc = struct.unpack_from(">I", region, 4 * (lx + lz * 32))[0]
    offset = (loc >> 8) * 4096
    if not offset or offset + 5 > len(region):
        return None
    length, kind = struct.unpack_from(">IB", region, offset)
    raw = region[offset + 5:offset + 4 + length]
    if kind == 2:
        raw = zlib.decompress(raw)
    elif kind == 1:
        raw = gzip.decompress(raw)
    elif kind != 3:
        return None  # z. B. LZ4 – kann ohne Zusatzpaket nicht gelesen werden
    return read_nbt(raw)


def png(pixels, size):
    def part(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes(pixels[y * size * 3:(y + 1) * size * 3]) for y in range(size))
    head = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + part(b"IHDR", head) + part(b"IDAT", zlib.compress(rows, 9)) + part(b"IEND", b"")


def region_dir(src, world, dim):
    for cand in (f"{world}/dimensions/minecraft/{dim}/region", f"{world}/region"):
        try:
            if any(n.endswith(".mca") for n in src.list(cand)):
                return cand
        except Exception:
            continue
    return None


def draw(src, folder, cx0, cz0, n, step, cache, read_nbt):
    """Zeichnet n × n Chunks ab Chunk (cx0, cz0) von oben; step = Blöcke pro Pixel."""
    size = n * 16 // step
    picked = [i for i in range(256) if not (i & 15) % step and not (i >> 4) % step]
    heights = [[None] * size for _ in range(size)]
    colors = [[None] * size for _ in range(size)]
    for cz in range(cz0, cz0 + n):
        for cx in range(cx0, cx0 + n):
            name = f"r.{cx >> 5}.{cz >> 5}.mca"
            if name not in cache:
                try:
                    cache[name] = src.read(f"{folder}/{name}")
                except Exception:
                    cache[name] = b""
            try:
                cols = chunk_columns(region_chunk(cache[name], cx & 31, cz & 31, read_nbt) or {})
            except Exception:
                cols = None  # Chunk wird gerade geschrieben oder ist beschädigt
            if not cols:
                continue
            for i in picked:
                x, z = ((cx - cx0) * 16 + (i & 15)) // step, ((cz - cz0) * 16 + (i >> 4)) // step
                heights[z][x], colors[z][x] = cols[0][i], cols[1][i]

    if not any(rgb for row in colors for rgb in row):
        return None  # dort ist noch nichts erkundet
    pixels = bytearray()
    for z in range(size):
        for x in range(size):
            rgb = colors[z][x]
            if rgb is None:
                pixels += bytes(PAPER)
                continue
            # wie auf Minecraft-Karten: Hänge nach Norden heller, nach Süden dunkler
            north = heights[z - 1][x] if z and heights[z - 1][x] is not None else heights[z][x]
            shade = 255 if heights[z][x] > north else 180 if heights[z][x] < north else 220
            pixels += bytes(c * shade // 255 for c in rgb)
    return png(pixels, size)


def parse(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def view_regions(bases):
    """Regionen, die die Übersichtskarte zeigt – dieselbe Rechnung wie baseMap() in app.js."""
    xs, zs = [b["x"] for b in bases], [b["z"] for b in bases]
    span = max(VIEW_MIN, max(xs) - min(xs), max(zs) - min(zs)) * VIEW_PAD
    x0, z0 = (max(xs) + min(xs)) / 2 - span / 2, (max(zs) + min(zs)) / 2 - span / 2
    regions = [(rx, rz) for rx in range(int(x0 // 512), int((x0 + span) // 512) + 1)
               for rz in range(int(z0 // 512), int((z0 + span) // 512) + 1)]

    def distance(region):
        mx, mz = region[0] * 512 + 256, region[1] * 512 + 256
        return min(abs(b["x"] - mx) + abs(b["z"] - mz) for b in bases)

    # liegen die Bases sehr weit auseinander, gibt es nur rund um die Bases Gelände
    return sorted(regions, key=distance)[:MAX_TILES]


def explored_regions(src, folder, bases):
    """Alle Regionen, die es auf dem Server gibt – also alles, was schon jemand erkundet hat.

    Gibt {(rx, rz): Änderungszeit} zurück, die nächsten an den Bases zuerst, höchstens MAX_TILES.
    """
    found = {}
    for name, mtime in src.mtimes(folder).items():
        parts = name.split(".")
        if len(parts) == 4 and parts[0] == "r" and parts[3] == "mca":
            try:
                found[(int(parts[1]), int(parts[2]))] = mtime
            except ValueError:
                continue

    def distance(region):
        mx, mz = region[0] * 512 + 256, region[1] * 512 + 256
        return min(abs(b["x"] - mx) + abs(b["z"] - mz) for b in bases)

    return {region: found[region] for region in sorted(found, key=distance)[:MAX_TILES]}


def update_tiles(src, world, bases, old, now, read_nbt, cache):
    """Kacheln der Übersichtskarte: ein Bild pro Region (512 × 512 Blöcke, 2 Blöcke pro Pixel).

    Gibt (Zustand für stats.json, Zahl neu gezeichneter Kacheln) zurück.
    """
    bases = [b for b in bases if b["dim"] == "overworld"]
    folder = region_dir(src, world, "overworld") if bases else None
    if not folder:
        return old or {}, 0
    stamp, out = parse(now), "data/tiles/overworld"
    known = (old or {}).get("regions", {})
    regions, drawn = {}, 0
    try:
        candidates = explored_regions(src, folder, bases)
    except Exception as err:
        print("Regionen nicht auflistbar, zeichne nur rund um die Bases:", err)
        candidates = {}
    if not candidates:
        for region in view_regions(bases):
            try:
                candidates[region] = src.mtime(f"{folder}/r.{region[0]}.{region[1]}.mca")
            except Exception:
                candidates[region] = None
    for (rx, rz), mtime in candidates.items():
        key, name = f"{rx}.{rz}", f"r.{rx}.{rz}.mca"
        info = known.get(key)
        if info:
            regions[key] = info
        if not mtime or drawn >= TILES_PER_RUN:
            continue  # Region gibt es (noch) nicht, oder der Rest folgt im nächsten Lauf
        if info and (info["mtime"] == mtime or stamp - parse(info["rendered"]) < timedelta(hours=TILE_HOURS)):
            continue
        try:
            image = draw(src, folder, rx * 32, rz * 32, 32, TILE_STEP, cache, read_nbt)
        except Exception as err:
            print(f"Kachel {key} fehlgeschlagen: {err}")
            image = False
        if image is None and not cache.get(name):
            image = False  # Datei kam nicht an (draw() schluckt Lesefehler) – nicht als leer abstempeln
        cache.pop(name, None)  # jede Region ist rund 10 MB groß – nicht alle im Speicher halten
        drawn += 1
        if image is False:
            # erst nach TILE_HOURS wieder versuchen – sonst hängt jeder Lauf an denselben kaputten Regionen
            regions[key] = {**info, "rendered": now} if info else {"mtime": "", "rendered": now, "empty": True}
            continue
        if image is None:
            # leere Region merken, sonst lädt jeder Lauf sie erneut herunter; die Seite überspringt sie
            regions[key] = {"mtime": mtime, "rendered": now, "empty": True}
            continue
        path = ROOT / out / f"r.{key}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != image:
            path.write_bytes(image)
        regions[key] = {"mtime": mtime, "rendered": now}
    return {"dir": out, "regions": regions}, drawn


def update(src, world, bases, old_bases, now, read_nbt, cache):
    """Ergänzt jede Overworld-Base um base["map"] und schreibt neue Bilder. Gibt die Zahl neuer Bilder zurück."""
    stamp = parse(now)
    today = stamp.astimezone(TZ).strftime("%Y-%m-%d")
    old = {(b["player"], b["x"], b["z"], b["dim"]): b.get("map") for b in old_bases}
    folders, drawn = {}, 0
    for base in bases:
        info = dict(old.get((base["player"], base["x"], base["z"], base["dim"])) or {})
        if info:
            base["map"] = info
        if base["dim"] != "overworld":
            continue  # Nether hat eine Decke, das End kaum Gelände – dort bleibt es beim Raster
        if base["dim"] not in folders:
            folders[base["dim"]] = region_dir(src, world, base["dim"])
        folder = folders[base["dim"]]
        if not folder:
            continue
        origin = [((base["x"] >> 4) - SIZE // 32) * 16, ((base["z"] >> 4) - SIZE // 32) * 16]
        names = sorted({f"r.{(origin[0] + dx) >> 9}.{(origin[1] + dz) >> 9}.mca" for dx in (0, SIZE - 1) for dz in (0, SIZE - 1)})
        mtimes = {}
        for name in names:
            try:
                mtimes[name] = src.mtime(f"{folder}/{name}")
            except Exception:
                mtimes[name] = None  # Region gibt es noch nicht

        last = info.get("rendered")
        fresh = last and stamp - parse(last) < timedelta(hours=RENDER_HOURS)
        # ohne Änderungszeit vom Server lässt sich "unverändert" nicht erkennen
        unchanged = all(mtimes.values()) and mtimes == info.get("regions")
        if info.get("dates") and (unchanged or (fresh and today in info["dates"])):
            continue
        try:
            image = draw(src, folder, origin[0] >> 4, origin[1] >> 4, SIZE // 16, 1, cache, read_nbt)
        except Exception as err:
            print(f"Karte für {base['player']} fehlgeschlagen: {err}")
            continue
        if image is None:
            continue
        out = f"data/maps/{base['player']}_{base['x']}_{base['z']}"
        path = ROOT / out / f"{today}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != image:
            path.write_bytes(image)
            drawn += 1
        base["map"] = {
            "dir": out, "size": SIZE, "origin": origin,
            "dates": sorted(set(info.get("dates", [])) | {today}),
            "rendered": now, "regions": mtimes,
        }
    return drawn
