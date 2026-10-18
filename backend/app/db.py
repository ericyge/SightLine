"""SQLite schema and helpers (items, rooms, sightings, settings).

Owner: A. A fresh connection per call keeps this simple and thread-safe; paths come from env
at call time so tests can point DB_PATH / THUMBS_DIR at a temp dir.
"""
import io
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

log = logging.getLogger("sightline.db")

THUMB_URL_PREFIX = "/api/thumbs"
THUMB_MAX_PX = 96

DEFAULT_ITEMS = [
    ("keys", ["key"], "keys on a keychain", "kitchen counter"),
    ("wallet", [], "a wallet", "bedroom desk"),
    ("phone", ["cell phone", "mobile"], "a smartphone", "living room table"),
    ("glasses", ["eyeglasses", "specs"], "a pair of glasses", "bedroom nightstand"),
    ("medication", ["meds", "pills", "medicine"], "a pill bottle", "kitchen counter"),
]
DEFAULT_ROOMS = ["bedroom", "kitchen", "living room"]

COMMON_SYNONYMS: dict[str, list[str]] = {
    "keys": ["key", "keychain", "house keys", "car keys", "key ring", "keyring"],
    "wallet": ["billfold", "cardholder", "card holder", "pocketbook"],
    "phone": ["cell phone", "mobile", "smartphone", "iphone", "cellphone", "mobile phone"],
    "glasses": ["eyeglasses", "specs", "spectacles", "reading glasses", "sunglasses"],
    "medication": ["meds", "pills", "medicine", "pill bottle", "prescription", "vitamins"],
    "water bottle": ["bottle", "waterbottle", "drink bottle", "thermos", "flask", "hydroflask", "sports bottle"],
    "cup": ["mug", "coffee cup", "coffee mug", "teacup", "tea cup", "tumbler", "drinking glass", "glass"],
    "headphones": ["earbuds", "earphones", "airpods", "headset", "earbud", "earphone"],
    "remote": ["remote control", "tv remote", "controller"],
    "bag": ["backpack", "purse", "handbag", "tote", "tote bag", "satchel", "duffel bag"],
    "cane": ["white cane", "walking stick", "walking cane", "mobility cane"],
    "laptop": ["computer", "notebook computer", "macbook", "chromebook"],
    "charger": ["charging cable", "power cord", "adapter", "phone charger", "usb cable"],
    "book": ["notebook", "journal", "textbook", "paperback"],
    "watch": ["wristwatch", "smartwatch", "apple watch"],
    "hat": ["cap", "beanie", "baseball cap"],
    "shoes": ["shoe", "sneakers", "sneaker", "slippers", "slipper", "boots", "sandals"],
    "umbrella": ["parasol"],
    "inhaler": ["asthma inhaler"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    name TEXT PRIMARY KEY, aliases TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '', home_spot TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS rooms (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL, room TEXT NOT NULL,
    spot TEXT NOT NULL, x1 REAL, y1 REAL, x2 REAL, y2 REAL,
    confidence REAL, ts TEXT NOT NULL, thumb TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_sightings_item_ts ON sightings (item, ts);
CREATE TABLE IF NOT EXISTS places (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, room TEXT NOT NULL,
    description TEXT NOT NULL, landmarks TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS place_connections (
    from_place_id TEXT NOT NULL REFERENCES places(id),
    to_place_id TEXT NOT NULL REFERENCES places(id),
    departure_view TEXT NOT NULL, instruction TEXT NOT NULL, arrival_view TEXT NOT NULL,
    PRIMARY KEY (from_place_id, to_place_id), CHECK (from_place_id != to_place_id));
"""

_initialized: set[str] = set()
_schema_lock = threading.RLock()


def db_path() -> str:
    return os.getenv("DB_PATH", "sightline.db")


def thumbs_dir() -> Path:
    return Path(os.getenv("THUMBS_DIR", "thumbs"))


@contextmanager
def connect():
    """Open a connection (creating schema + default profile on first use), commit, close."""
    path = db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with _schema_lock:
            if path not in _initialized:
                conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                _migrate_confidence(conn)
                _migrate_memory(conn)
                _seed_defaults(conn)
                conn.commit()
                _initialized.add(path)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_confidence(conn: sqlite3.Connection) -> None:
    """Preserve existing sightings while allowing an unknown provider confidence."""
    columns = conn.execute("PRAGMA table_info(sightings)").fetchall()
    if not any(c["name"] == "confidence" and c["notnull"] for c in columns):
        return
    conn.execute("""CREATE TABLE sightings_nullable (
        id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL, room TEXT NOT NULL,
        spot TEXT NOT NULL, x1 REAL, y1 REAL, x2 REAL, y2 REAL,
        confidence REAL, ts TEXT NOT NULL, thumb TEXT NOT NULL DEFAULT '')""")
    conn.execute("INSERT INTO sightings_nullable SELECT * FROM sightings")
    conn.execute("DROP TABLE sightings")
    conn.execute("ALTER TABLE sightings_nullable RENAME TO sightings")
    conn.execute("CREATE INDEX idx_sightings_item_ts ON sightings (item, ts)")


def _migrate_memory(conn: sqlite3.Connection) -> None:
    """Old observations retain unknown place links rather than guessed destinations."""
    columns = {c["name"] for c in conn.execute("PRAGMA table_info(sightings)")}
    if "place_id" not in columns:
        conn.execute("ALTER TABLE sightings ADD COLUMN place_id TEXT REFERENCES places(id)")
    if "place_source" not in columns:
        conn.execute("ALTER TABLE sightings ADD COLUMN place_source TEXT NOT NULL DEFAULT 'none'")
        conn.execute("UPDATE sightings SET place_source='manual' WHERE place_id IS NOT NULL")
    if "place_reason" not in columns:
        conn.execute("ALTER TABLE sightings ADD COLUMN place_reason TEXT NOT NULL DEFAULT ''")
    if "session_id" not in columns:
        conn.execute("ALTER TABLE sightings ADD COLUMN session_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sightings_place ON sightings (place_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sightings_session ON sightings (session_id, ts)")


def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert the default watchlist/rooms once (a flag stops re-seeding after an empty profile)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
        return
    for name, aliases, desc, spot in DEFAULT_ITEMS:
        conn.execute("INSERT OR IGNORE INTO items VALUES (?,?,?,?)",
                     (name, json.dumps(aliases), desc, spot))
    conn.executemany("INSERT OR IGNORE INTO rooms VALUES (?)", [(r,) for r in DEFAULT_ROOMS])
    conn.execute("INSERT INTO settings VALUES ('guidance_style','clock_face')")
    conn.execute("INSERT INTO settings VALUES ('seeded','1')")


# ---- profile ----------------------------------------------------------------------------

def list_items() -> list[dict]:
    """All watched items as {name, aliases, description, home_spot}."""
    with connect() as c:
        rows = c.execute("SELECT * FROM items ORDER BY rowid").fetchall()
    return [{"name": r["name"], "aliases": json.loads(r["aliases"]),
             "description": r["description"], "home_spot": r["home_spot"]} for r in rows]


def get_item(name: str) -> dict | None:
    return next((i for i in list_items() if i["name"] == name), None)


def get_profile() -> dict:
    """Current profile in the POST /api/profile shape."""
    with connect() as c:
        rooms = [r["name"] for r in c.execute("SELECT name FROM rooms ORDER BY rowid")]
        style = c.execute("SELECT value FROM settings WHERE key='guidance_style'").fetchone()
    return {"watchlist": [{"name": i["name"], "description": i["description"],
                           "home_spot": i["home_spot"]} for i in list_items()],
            "rooms": rooms, "guidance_style": style["value"] if style else "clock_face"}


def set_profile(profile: dict) -> None:
    """Replace watchlist, rooms and style. Existing aliases are kept for items that stay."""
    old_aliases = {i["name"]: i["aliases"] for i in list_items()}
    with connect() as c:
        c.execute("DELETE FROM items")
        for w in profile["watchlist"]:
            name = w["name"].strip().lower()
            c.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?)",
                      (name, json.dumps(old_aliases.get(name, [])), w["description"], w["home_spot"]))
        c.execute("DELETE FROM rooms")
        c.executemany("INSERT OR IGNORE INTO rooms VALUES (?)",
                      [(r.strip().lower(),) for r in profile["rooms"]])
        c.execute("INSERT OR REPLACE INTO settings VALUES ('guidance_style', ?)",
                  (profile["guidance_style"],))


def guidance_style() -> str:
    return get_profile()["guidance_style"]


def _clean_item_name(text: str | None) -> str:
    """Normalize a spoken or model-supplied object name."""
    if not text:
        return ""
    t = " ".join(text.strip(" \t\r\n?.!,;:\"'()[]{}").lower().split())
    for prefix in ("my ", "the ", "a ", "an ", "some ", "pair of "):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    if not t or len(t) > 80 or not any(ch.isalnum() for ch in t):
        return ""
    return t


def _match_watchlist(cleaned: str, items: list[dict] | None = None) -> str | None:
    if not cleaned:
        return None
    forms = {cleaned, cleaned.rstrip("s")} - {""}
    for item in (items if items is not None else list_items()):
        names = {item["name"].lower(), *[a.lower() for a in item.get("aliases", [])]}
        singular_names = {n.rstrip("s") for n in names} - {""}
        if forms & (names | singular_names):
            return item["name"]
    return None


def is_watched(text: str) -> bool:
    """True when text matches an item in the active profile watchlist."""
    return _match_watchlist(_clean_item_name(text)) is not None


def canonicalize_item(text: str, watchlist: list[dict] | None = None) -> str | None:
    """Map an item label or query to a watchlist name, common canonical name, or normalized name."""
    t = _clean_item_name(text)
    if not t:
        return None
    watched = _match_watchlist(t, watchlist)
    if watched is not None:
        return watched
    forms = {t, t.rstrip("s")} - {""}
    for canonical, synonyms in COMMON_SYNONYMS.items():
        group = {canonical, *synonyms}
        singular_group = {g.rstrip("s") for g in group} - {""}
        if forms & (group | singular_group):
            return canonical
    return t


def seen_items() -> list[str]:
    """Distinct item names observed in sightings, most recent first."""
    with connect() as c:
        rows = c.execute(
            "SELECT item FROM sightings GROUP BY item ORDER BY MAX(julianday(ts)) DESC, MAX(id) DESC"
        ).fetchall()
    return [r["item"] for r in rows]


def resolve_item(text: str) -> str | None:
    """Map spoken text ("my cell phone", "water bottle", "mug") to a canonical or seen item name."""
    t = _clean_item_name(text)
    if not t:
        return None
    watched = _match_watchlist(t)
    if watched is not None:
        return watched
    canon = canonicalize_item(t) or t
    observed = seen_items()
    for seen in observed:
        if seen.lower() in {t, canon} or (canonicalize_item(seen) or seen.lower()) == canon:
            return canon if canon in COMMON_SYNONYMS else seen
    forms = {t, t.rstrip("s")} - {""}
    for seen in observed:
        tokens = set(seen.lower().split()) | {w.rstrip("s") for w in seen.lower().split()}
        if forms & tokens:
            return seen
    return canon


def target_item(text: str) -> dict | None:
    """Build a detection target dict for any item (watchlist or open-vocabulary on-demand)."""
    name = resolve_item(text)
    if not name:
        return None
    watched = get_item(name)
    if watched:
        return {**watched, "watched": True}
    synonyms = COMMON_SYNONYMS.get(name, [])
    desc = f"a {name}" + (f" (or {', '.join(synonyms[:6])})" if synonyms else "")
    return {"name": name, "aliases": synonyms, "description": desc, "home_spot": "", "watched": False}


def resolve_room(text: str) -> str | None:
    """Map spoken text ("the kitchen", "living room") to a stored room name, or None."""
    t = text.strip().lower()
    for prefix in ("the ", "my "):
        if t.startswith(prefix):
            t = t[len(prefix):]
    squash = t.replace(" ", "")
    return next((r for r in get_profile()["rooms"] if r == t or r.replace(" ", "") == squash), None)


# ---- sightings --------------------------------------------------------------------------

def save_thumb(frame: bytes, box: list[float]) -> str:
    """Save a small crop around box; return its URL path, or "" if the frame can't be decoded.

    Only this crop is stored. The frame itself is never written to disk.
    """
    try:
        img = Image.open(io.BytesIO(frame)).convert("RGB")
    except Exception:  # noqa: BLE001 - bad frame must not break observe
        log.warning("thumbnail skipped: undecodable frame")
        return ""
    w, h = img.size
    pad_x, pad_y = (box[2] - box[0]) * 0.2, (box[3] - box[1]) * 0.2
    crop = img.crop((max(0, (box[0] - pad_x)) * w, max(0, (box[1] - pad_y)) * h,
                     min(1, (box[2] + pad_x)) * w, min(1, (box[3] + pad_y)) * h))
    if crop.width < 2 or crop.height < 2:
        return ""
    crop.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
    thumbs_dir().mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.jpg"
    crop.save(thumbs_dir() / fname, "JPEG", quality=70)
    return f"{THUMB_URL_PREFIX}/{fname}"


def add_sighting(item: str, room: str, spot: str, box: list[float], confidence: float | None,
                 ts: str, thumb: str, place_id: str | None = None) -> dict:
    """Insert a sighting and return it in contract shape."""
    with connect() as c:
        return insert_sighting(c, item, room, spot, box, confidence, ts, thumb, place_id)


def insert_sighting(c, item, room, spot, box, confidence, ts, thumb, place_id=None,
                    place_source=None, place_reason="", session_id=None) -> dict:
    """Insert within a caller's transaction so a complete observed frame is atomic."""
    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    ts = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    if place_id is not None:
        place = c.execute("SELECT room FROM places WHERE id=?", (place_id,)).fetchone()
        if place is None or place["room"].casefold() != room.strip().casefold():
            raise ValueError("Sighting place must exist and belong to the supplied room.")
    source = place_source or ("manual" if place_id else "none")
    cursor = c.execute("""INSERT INTO sightings
        (item,room,spot,x1,y1,x2,y2,confidence,ts,thumb,place_id,place_source,place_reason,session_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item, room, spot, *box, confidence, ts, thumb, place_id, source, place_reason, session_id))
    row = c.execute("SELECT * FROM sightings WHERE id=?", (cursor.lastrowid,)).fetchone()
    return sighting_record(row)


def sighting_record(row: sqlite3.Row) -> dict:
    """Public observation including a stable ID and optional taught destination."""
    return {"id": row["id"], "item": row["item"], "room": row["room"], "spot": row["spot"],
            "box": [row["x1"], row["y1"], row["x2"], row["y2"]], "confidence": row["confidence"],
            "ts": row["ts"], "thumb": row["thumb"], "place_id": row["place_id"],
            "place_source": row["place_source"], "place_reason": row["place_reason"]}


def recent_sightings(item: str, limit: int = 5) -> list[dict]:
    """Newest sightings of item (including common synonyms) first, in contract shape."""
    canon = canonicalize_item(item) or item.strip().lower()
    candidates = sorted({item.strip().lower(), canon, *COMMON_SYNONYMS.get(canon, [])} - {""})
    placeholders = ",".join("?" for _ in candidates)
    with connect() as c:
        rows = c.execute(
            f"SELECT * FROM sightings WHERE lower(item) IN ({placeholders}) "
            "ORDER BY julianday(ts) DESC, id DESC LIMIT ?",
            (*candidates, limit),
        ).fetchall()
    return [sighting_record(r) for r in rows]
