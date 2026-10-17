"""SQLite schema and helpers (items, rooms, sightings, settings).

Owner: A. A fresh connection per call keeps this simple and thread-safe; paths come from env
at call time so tests can point DB_PATH / THUMBS_DIR at a temp dir.
"""
import io
import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    name TEXT PRIMARY KEY, aliases TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '', home_spot TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS rooms (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL, room TEXT NOT NULL,
    spot TEXT NOT NULL, x1 REAL, y1 REAL, x2 REAL, y2 REAL,
    confidence REAL NOT NULL, ts TEXT NOT NULL, thumb TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_sightings_item_ts ON sightings (item, ts);
"""

_initialized: set[str] = set()


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
    try:
        if path not in _initialized:
            conn.executescript(SCHEMA)
            _seed_defaults(conn)
            _initialized.add(path)
        yield conn
        conn.commit()
    finally:
        conn.close()


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


def resolve_item(text: str) -> str | None:
    """Map spoken text ("my cell phone", "keys") to a watchlist item name, or None."""
    t = text.strip().lower()
    for prefix in ("my ", "the "):
        if t.startswith(prefix):
            t = t[len(prefix):]
    for item in list_items():
        names = {item["name"], *[a.lower() for a in item["aliases"]]}
        if t in names or t.rstrip("s") in names:
            return item["name"]
    return None


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


def add_sighting(item: str, room: str, spot: str, box: list[float], confidence: float,
                 ts: str, thumb: str) -> dict:
    """Insert a sighting and return it in contract shape."""
    with connect() as c:
        c.execute("INSERT INTO sightings (item,room,spot,x1,y1,x2,y2,confidence,ts,thumb) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)", (item, room, spot, *box, confidence, ts, thumb))
    return {"item": item, "room": room, "spot": spot, "box": box,
            "confidence": confidence, "ts": ts, "thumb": thumb}


def recent_sightings(item: str, limit: int = 5) -> list[dict]:
    """Newest sightings of item first, in contract shape."""
    with connect() as c:
        rows = c.execute("SELECT * FROM sightings WHERE item=? ORDER BY ts DESC, id DESC LIMIT ?",
                         (item, limit)).fetchall()
    return [{"item": r["item"], "room": r["room"], "spot": r["spot"],
             "box": [r["x1"], r["y1"], r["x2"], r["y2"]], "confidence": r["confidence"],
             "ts": r["ts"], "thumb": r["thumb"]} for r in rows]
