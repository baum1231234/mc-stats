"""Zeichnet für jede markierte Base die Umgebung von oben (ein Pixel pro Block).

Pro Base und Tag entsteht ein Bild unter data/maps/<Spieler>_<x>_<z>/<Datum>.png.
Neu gezeichnet wird nur, wenn sich eine der Region-Dateien geändert hat, und am selben
Tag höchstens alle RENDER_HOURS Stunden. Ältere Tage bleiben als Verlauf liegen.
"""
import gzip
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

SIZE = 256  # Kantenlänge in Blöcken (Vielfaches von 16)
RENDER_HOURS = 3
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


def draw(src, folder, base, origin, cache, read_nbt):
    """Zeichnet das Bild einer Base. origin = Block oben links (Nordwest)."""
    n = SIZE // 16
    cx0, cz0 = origin[0] >> 4, origin[1] >> 4
    heights = [[None] * SIZE for _ in range(SIZE)]
    colors = [[None] * SIZE for _ in range(SIZE)]
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
            for i in range(256):
                x, z = (cx - cx0) * 16 + (i & 15), (cz - cz0) * 16 + (i >> 4)
                heights[z][x], colors[z][x] = cols[0][i], cols[1][i]

    if not any(rgb for row in colors for rgb in row):
        return None  # dort ist noch nichts erkundet
    pixels = bytearray()
    for z in range(SIZE):
        for x in range(SIZE):
            rgb = colors[z][x]
            if rgb is None:
                pixels += bytes(PAPER)
                continue
            # wie auf Minecraft-Karten: Hänge nach Norden heller, nach Süden dunkler
            north = heights[z - 1][x] if z and heights[z - 1][x] is not None else heights[z][x]
            shade = 255 if heights[z][x] > north else 180 if heights[z][x] < north else 220
            pixels += bytes(c * shade // 255 for c in rgb)
    return png(pixels, SIZE)


def update(src, world, bases, old_bases, now, read_nbt):
    """Ergänzt jede Overworld-Base um base["map"] und schreibt neue Bilder. Gibt die Zahl neuer Bilder zurück."""
    stamp = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    today = stamp.astimezone(TZ).strftime("%Y-%m-%d")
    old = {(b["player"], b["x"], b["z"], b["dim"]): b.get("map") for b in old_bases}
    folders, cache, drawn = {}, {}, 0
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
        fresh = last and stamp - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) < timedelta(hours=RENDER_HOURS)
        # ohne Änderungszeit vom Server lässt sich "unverändert" nicht erkennen
        unchanged = all(mtimes.values()) and mtimes == info.get("regions")
        if info.get("dates") and (unchanged or (fresh and today in info["dates"])):
            continue
        try:
            image = draw(src, folder, base, origin, cache, read_nbt)
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
