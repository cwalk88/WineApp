"""SQLite storage for racks, bottles and the check-in/check-out log."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS racks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    rows              INTEGER NOT NULL,
    cols              INTEGER NOT NULL,
    led_start         INTEGER NOT NULL DEFAULT 0,
    leds_per_slot     INTEGER NOT NULL DEFAULT 1,
    start_corner      TEXT    NOT NULL DEFAULT 'top_left',
    orientation       TEXT    NOT NULL DEFAULT 'horizontal',
    serpentine        INTEGER NOT NULL DEFAULT 0,
    leds_between_runs INTEGER NOT NULL DEFAULT 0,
    sort_order        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bottles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    producer        TEXT NOT NULL DEFAULT '',
    vintage         INTEGER,
    style           TEXT NOT NULL DEFAULT 'red',
    country         TEXT NOT NULL DEFAULT '',
    region          TEXT NOT NULL DEFAULT '',
    grapes          TEXT NOT NULL DEFAULT '[]',
    abv             REAL,
    food_pairings   TEXT NOT NULL DEFAULT '[]',
    pairing_summary TEXT NOT NULL DEFAULT '',
    tasting_notes   TEXT NOT NULL DEFAULT '',
    serving_temp_c  TEXT NOT NULL DEFAULT '',
    decant_minutes  INTEGER,
    drink_from      INTEGER,
    drink_until     INTEGER,
    notes           TEXT NOT NULL DEFAULT '',
    photo           TEXT,
    status          TEXT NOT NULL DEFAULT 'in',
    rack_id         INTEGER REFERENCES racks(id),
    slot_row        INTEGER,
    slot_col        INTEGER,
    added_at        TEXT NOT NULL,
    checked_in_at   TEXT,
    checked_out_at  TEXT
);

-- A slot can only hold one bottle that is currently in the rack.
CREATE UNIQUE INDEX IF NOT EXISTS bottles_slot
    ON bottles(rack_id, slot_row, slot_col) WHERE status = 'in';

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bottle_id  INTEGER,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

JSON_FIELDS = ("grapes", "food_pairings")

BOTTLE_FIELDS = (
    "name", "producer", "vintage", "style", "country", "region", "grapes", "abv",
    "food_pairings", "pairing_summary", "tasting_notes", "serving_temp_c",
    "decant_minutes", "drink_from", "drink_until", "notes", "photo",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    # ---- racks -------------------------------------------------------------

    def racks(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM racks ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]

    def rack(self, rack_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM racks WHERE id = ?", (rack_id,)).fetchone()
        return dict(r) if r else None

    def create_rack(self, data: dict) -> dict:
        with self.conn() as c:
            order = c.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM racks").fetchone()[0]
            cur = c.execute(
                """INSERT INTO racks (name, rows, cols, led_start, leds_per_slot, start_corner,
                       orientation, serpentine, leds_between_runs, sort_order)
                   VALUES (:name, :rows, :cols, :led_start, :leds_per_slot, :start_corner,
                       :orientation, :serpentine, :leds_between_runs, :sort_order)""",
                {**data, "serpentine": int(data["serpentine"]), "sort_order": order},
            )
            rack_id = cur.lastrowid
        return self.rack(rack_id)

    def update_rack(self, rack_id: int, data: dict) -> dict:
        if "serpentine" in data:
            data["serpentine"] = int(data["serpentine"])
        cols = ", ".join(f"{k} = :{k}" for k in data)
        with self.conn() as c:
            c.execute(f"UPDATE racks SET {cols} WHERE id = :id", {**data, "id": rack_id})
        return self.rack(rack_id)

    def delete_rack(self, rack_id: int) -> None:
        with self.conn() as c:
            c.execute("UPDATE bottles SET rack_id = NULL WHERE rack_id = ? AND status != 'in'", (rack_id,))
            c.execute("DELETE FROM racks WHERE id = ?", (rack_id,))

    def occupied(self, rack_id: int | None = None) -> set[tuple[int, int, int]]:
        sql = "SELECT rack_id, slot_row, slot_col FROM bottles WHERE status = 'in'"
        args: tuple = ()
        if rack_id is not None:
            sql += " AND rack_id = ?"
            args = (rack_id,)
        with self.conn() as c:
            return {tuple(r) for r in c.execute(sql, args).fetchall()}

    # ---- bottles -----------------------------------------------------------

    @staticmethod
    def _bottle(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        b = dict(row)
        for f in JSON_FIELDS:
            b[f] = json.loads(b[f] or "[]")
        return b

    def bottle(self, bottle_id: int) -> dict | None:
        with self.conn() as c:
            return self._bottle(c.execute("SELECT * FROM bottles WHERE id = ?", (bottle_id,)).fetchone())

    def bottles(self, status: str = "in", q: str = "", style: str = "") -> list[dict]:
        sql = "SELECT * FROM bottles WHERE 1=1"
        args: list = []
        if status in ("in", "out"):
            sql += " AND status = ?"
            args.append(status)
        if style:
            sql += " AND style = ?"
            args.append(style)
        for term in q.split():
            like = f"%{term}%"
            sql += """ AND (name LIKE ? OR producer LIKE ? OR region LIKE ? OR country LIKE ?
                       OR grapes LIKE ? OR food_pairings LIKE ? OR pairing_summary LIKE ?
                       OR notes LIKE ? OR CAST(vintage AS TEXT) LIKE ?)"""
            args.extend([like] * 9)
        sql += " ORDER BY status, name COLLATE NOCASE, vintage"
        with self.conn() as c:
            return [self._bottle(r) for r in c.execute(sql, args).fetchall()]

    def add_bottle(self, data: dict, rack_id: int, row: int, col: int) -> dict:
        record = {f: data.get(f) for f in BOTTLE_FIELDS}
        for f in JSON_FIELDS:
            record[f] = json.dumps(record[f] or [])
        for f in ("producer", "country", "region", "pairing_summary", "tasting_notes",
                  "serving_temp_c", "notes"):
            record[f] = record[f] or ""
        record["style"] = record["style"] or "red"
        ts = now()
        with self.conn() as c:
            cur = c.execute(
                f"""INSERT INTO bottles ({", ".join(BOTTLE_FIELDS)}, status, rack_id, slot_row,
                        slot_col, added_at, checked_in_at)
                    VALUES ({", ".join(":" + f for f in BOTTLE_FIELDS)}, 'in', :rack_id, :row,
                        :col, :ts, :ts)""",
                {**record, "rack_id": rack_id, "row": row, "col": col, "ts": ts},
            )
            bottle_id = cur.lastrowid
            self._log(c, bottle_id, "check_in", f"rack {rack_id} r{row + 1} c{col + 1}")
        return self.bottle(bottle_id)

    def update_bottle(self, bottle_id: int, data: dict) -> dict:
        data = {k: v for k, v in data.items() if k in BOTTLE_FIELDS}
        for f in JSON_FIELDS:
            if f in data:
                data[f] = json.dumps(data[f] or [])
        if data:
            cols = ", ".join(f"{k} = :{k}" for k in data)
            with self.conn() as c:
                c.execute(f"UPDATE bottles SET {cols} WHERE id = :id", {**data, "id": bottle_id})
        return self.bottle(bottle_id)

    def move_bottle(self, bottle_id: int, rack_id: int, row: int, col: int) -> dict:
        with self.conn() as c:
            c.execute(
                "UPDATE bottles SET rack_id = ?, slot_row = ?, slot_col = ? WHERE id = ?",
                (rack_id, row, col, bottle_id),
            )
            self._log(c, bottle_id, "move", f"rack {rack_id} r{row + 1} c{col + 1}")
        return self.bottle(bottle_id)

    def check_out(self, bottle_id: int) -> dict:
        with self.conn() as c:
            c.execute(
                "UPDATE bottles SET status = 'out', checked_out_at = ? WHERE id = ?",
                (now(), bottle_id),
            )
            self._log(c, bottle_id, "check_out")
        return self.bottle(bottle_id)

    def check_in_again(self, bottle_id: int, rack_id: int, row: int, col: int) -> dict:
        with self.conn() as c:
            c.execute(
                """UPDATE bottles SET status = 'in', rack_id = ?, slot_row = ?, slot_col = ?,
                       checked_in_at = ?, checked_out_at = NULL WHERE id = ?""",
                (rack_id, row, col, now(), bottle_id),
            )
            self._log(c, bottle_id, "check_in", f"returned to rack {rack_id} r{row + 1} c{col + 1}")
        return self.bottle(bottle_id)

    def delete_bottle(self, bottle_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM bottles WHERE id = ?", (bottle_id,))
            self._log(c, bottle_id, "delete")

    # ---- events ------------------------------------------------------------

    @staticmethod
    def _log(c: sqlite3.Connection, bottle_id: int | None, action: str, detail: str = "") -> None:
        c.execute(
            "INSERT INTO events (bottle_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (bottle_id, action, detail, now()),
        )

    def events(self, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """SELECT e.*, b.name, b.vintage, b.producer FROM events e
                   LEFT JOIN bottles b ON b.id = e.bottle_id
                   ORDER BY e.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self.conn() as c:
            by_style = c.execute(
                "SELECT style, COUNT(*) n FROM bottles WHERE status = 'in' GROUP BY style"
            ).fetchall()
            out = c.execute("SELECT COUNT(*) FROM bottles WHERE status = 'out'").fetchone()[0]
        styles = {r["style"]: r["n"] for r in by_style}
        return {"in": sum(styles.values()), "out": out, "by_style": styles}
