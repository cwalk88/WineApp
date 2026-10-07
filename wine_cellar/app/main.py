"""Wine Cellar web app: FastAPI backend served through Home Assistant ingress."""

import logging
import threading
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_settings
from .db import Database
from .layout import RackGeometry, ranges_overlap
from .leds import LedController, make_driver
from .sommelier import ClaudeUnavailable, Sommelier

log = logging.getLogger("wine")
STATIC = Path(__file__).parent / "static"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_UPLOAD = 15 * 1024 * 1024


# ---- request bodies ----------------------------------------------------------

class RackIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    rows: int
    cols: int
    led_start: int | None = None  # None = place after the last rack on the strip
    leds_per_slot: int = 1
    start_corner: str = "top_left"
    orientation: str = "horizontal"
    serpentine: bool = False
    leds_between_runs: int = 0


class RackPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    rows: int | None = None
    cols: int | None = None
    led_start: int | None = None
    leds_per_slot: int | None = None
    start_corner: str | None = None
    orientation: str | None = None
    serpentine: bool | None = None
    leds_between_runs: int | None = None


class WineFields(BaseModel):
    name: str | None = None
    producer: str | None = None
    vintage: int | None = None
    style: str | None = None
    country: str | None = None
    region: str | None = None
    grapes: list[str] | None = None
    abv: float | None = None
    food_pairings: list[str] | None = None
    pairing_summary: str | None = None
    tasting_notes: str | None = None
    serving_temp_c: str | None = None
    decant_minutes: int | None = None
    drink_from: int | None = None
    drink_until: int | None = None
    notes: str | None = None
    photo: str | None = None


class CheckIn(WineFields):
    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=48)
    rack_id: int | None = None  # slot chosen by the user, else first free slots
    row: int | None = None
    col: int | None = None


class SlotRef(BaseModel):
    rack_id: int | None = None
    row: int | None = None
    col: int | None = None


class LedTest(BaseModel):
    rack_id: int
    row: int | None = None
    col: int | None = None


class PairRequest(BaseModel):
    meal: str = Field(min_length=2, max_length=500)


# ---- app ---------------------------------------------------------------------

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.data_dir / "wine.db")
    photos = settings.data_dir / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    leds = LedController(make_driver(settings), settings)
    claude = Sommelier(settings.anthropic_api_key, settings.claude_model)

    app = FastAPI(title="Wine Cellar")
    app.state.settings, app.state.db, app.state.leds = settings, db, leds

    # -- helpers --

    def geometry(rack_id: int) -> RackGeometry:
        rack = db.rack(rack_id)
        if not rack:
            raise HTTPException(404, "Rack not found")
        return RackGeometry.from_row(rack)

    def free_slots(n: int, rack_id: int | None = None) -> list[tuple[int, int, int]]:
        taken = db.occupied()
        found = []
        for rack in db.racks():
            if rack_id is not None and rack["id"] != rack_id:
                continue
            for r in range(rack["rows"]):
                for c in range(rack["cols"]):
                    if (rack["id"], r, c) not in taken:
                        found.append((rack["id"], r, c))
                        if len(found) == n:
                            return found
        return found

    def check_slot_free(rack_id: int, row: int, col: int, ignore_bottle: int | None = None) -> None:
        g = geometry(rack_id)
        if not (0 <= row < g.rows and 0 <= col < g.cols):
            raise HTTPException(400, "That slot is outside the rack")
        for b in db.bottles(status="in"):
            if (b["rack_id"], b["slot_row"], b["slot_col"]) == (rack_id, row, col) and b["id"] != ignore_bottle:
                raise HTTPException(409, f"That slot already holds {b['name']}")

    def light(bottles_or_slots: list[dict], color: str, reason: str, seconds: int | None = None) -> None:
        slots, indices = [], []
        for item in bottles_or_slots:
            rack_id = item.get("rack_id")
            row = item.get("slot_row", item.get("row"))
            col = item.get("slot_col", item.get("col"))
            if rack_id is None or row is None:
                continue
            indices += geometry(rack_id).slot_leds(row, col)
            slots.append({"rack_id": rack_id, "row": row, "col": col, "bottle_id": item.get("id")})
        leds.show(slots, indices, color, reason, seconds=seconds)

    def publish_sensor() -> None:
        if not (settings.publish_sensor and settings.ha_url and settings.ha_token):
            return

        def push():
            stats = db.stats()
            try:
                httpx.post(
                    f"{settings.ha_url}/states/sensor.wine_cellar_bottles",
                    headers={"Authorization": f"Bearer {settings.ha_token}"},
                    json={
                        "state": stats["in"],
                        "attributes": {
                            "friendly_name": "Wine cellar bottles",
                            "unit_of_measurement": "bottles",
                            "icon": "mdi:bottle-wine",
                            "checked_out": stats["out"],
                            **{f"{k}_bottles": v for k, v in stats["by_style"].items()},
                        },
                    },
                    timeout=5,
                )
            except httpx.HTTPError as e:
                log.debug("Sensor publish failed: %s", e)

        threading.Thread(target=push, daemon=True).start()

    def bottle_or_404(bottle_id: int) -> dict:
        b = db.bottle(bottle_id)
        if not b:
            raise HTTPException(404, "Bottle not found")
        return b

    # -- status --

    @app.get("/api/status")
    def status():
        racks = db.racks()
        return {
            "claude": claude.enabled,
            "model": settings.claude_model,
            "led_driver": leds.driver.name,
            "led_error": leds.last_error,
            "capacity": sum(r["rows"] * r["cols"] for r in racks),
            "stats": db.stats(),
            "colors": {
                "locate": settings.color_locate,
                "checkin": settings.color_checkin,
                "suggest": settings.color_suggest,
            },
        }

    # -- racks --

    def validate_geometry(g: RackGeometry, rack_id: int | None) -> None:
        try:
            g.validate()
        except ValueError as e:
            raise HTTPException(400, str(e))
        for other in db.racks():
            if other["id"] != rack_id and ranges_overlap(g, RackGeometry.from_row(other)):
                raise HTTPException(
                    409,
                    f"LEDs {g.led_start}-{g.led_end - 1} overlap rack '{other['name']}' "
                    f"(LEDs {other['led_start']}-{RackGeometry.from_row(other).led_end - 1})",
                )

    @app.get("/api/racks")
    def list_racks():
        out = []
        for r in db.racks():
            g = RackGeometry.from_row(r)
            out.append({**r, "serpentine": bool(r["serpentine"]), "led_end": g.led_end, "led_count": g.led_count})
        return out

    @app.post("/api/racks", status_code=201)
    def create_rack(body: RackIn):
        data = body.model_dump()
        if data["led_start"] is None:
            data["led_start"] = max((RackGeometry.from_row(r).led_end for r in db.racks()), default=0)
        validate_geometry(RackGeometry(**{k: v for k, v in data.items() if k != "name"}), None)
        return db.create_rack(data)

    @app.patch("/api/racks/{rack_id}")
    def update_rack(rack_id: int, body: RackPatch):
        rack = db.rack(rack_id)
        if not rack:
            raise HTTPException(404, "Rack not found")
        changes = body.model_dump(exclude_none=True)
        merged = {**rack, **changes}
        g = RackGeometry.from_row({**merged, "serpentine": int(merged["serpentine"])})
        validate_geometry(g, rack_id)
        stranded = [
            b for b in db.bottles(status="in")
            if b["rack_id"] == rack_id and (b["slot_row"] >= g.rows or b["slot_col"] >= g.cols)
        ]
        if stranded:
            names = ", ".join(f"{b['name']} (row {b['slot_row'] + 1}, col {b['slot_col'] + 1})" for b in stranded[:5])
            raise HTTPException(409, f"Can't shrink the rack - move these bottles first: {names}")
        return db.update_rack(rack_id, changes)

    @app.delete("/api/racks/{rack_id}", status_code=204)
    def delete_rack(rack_id: int):
        if db.occupied(rack_id):
            raise HTTPException(409, "Empty the rack before deleting it")
        db.delete_rack(rack_id)

    @app.post("/api/racks/{rack_id}/test")
    def test_rack(rack_id: int):
        """Light every slot of a rack so wiring/orientation can be checked."""
        g = geometry(rack_id)
        slots = [{"rack_id": rack_id, "row": r, "col": c} for r in range(g.rows) for c in range(g.cols)]
        light(slots, settings.color_checkin, "test", seconds=15)
        return {"leds": g.led_count}

    # -- bottles --

    @app.get("/api/bottles")
    def list_bottles(status: str = "in", q: str = "", style: str = ""):
        return db.bottles(status=status, q=q, style=style)

    @app.get("/api/bottles/{bottle_id}")
    def get_bottle(bottle_id: int):
        return bottle_or_404(bottle_id)

    @app.post("/api/analyze")
    async def analyze(image: UploadFile = File(...)):
        if image.content_type not in IMAGE_TYPES:
            raise HTTPException(415, "Please upload a JPEG, PNG, WebP or GIF photo")
        data = await image.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Photo is too large")
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}[image.content_type]
        name = f"{uuid.uuid4().hex}.{ext}"
        (photos / name).write_bytes(data)
        result = {"photo": name, "wine": None, "error": ""}
        try:
            result["wine"] = await run_in_threadpool(claude.analyze_label, data, image.content_type)
        except ClaudeUnavailable as e:
            result["error"] = str(e)
        return result

    @app.post("/api/bottles", status_code=201)
    def check_in(body: CheckIn):
        wine = body.model_dump(exclude={"quantity", "rack_id", "row", "col"})
        if body.rack_id is not None and body.row is not None and body.col is not None:
            check_slot_free(body.rack_id, body.row, body.col)
            slots = [(body.rack_id, body.row, body.col)]
            if body.quantity > 1:
                extra = [s for s in free_slots(body.quantity, body.rack_id) if s != slots[0]]
                slots += extra[: body.quantity - 1]
        else:
            slots = free_slots(body.quantity, body.rack_id)
        if len(slots) < body.quantity:
            free = len(slots)
            raise HTTPException(409, f"Only {free} free slot{'s' if free != 1 else ''} left - add or enlarge a rack")
        added = [db.add_bottle(wine, *s) for s in slots]
        light(added, settings.color_checkin, "checkin")
        publish_sensor()
        return added

    @app.patch("/api/bottles/{bottle_id}")
    def edit_bottle(bottle_id: int, body: WineFields):
        bottle_or_404(bottle_id)
        return db.update_bottle(bottle_id, body.model_dump(exclude_unset=True))

    @app.post("/api/bottles/{bottle_id}/move")
    def move_bottle(bottle_id: int, body: SlotRef):
        b = bottle_or_404(bottle_id)
        if b["status"] != "in":
            raise HTTPException(409, "That bottle is checked out")
        if body.rack_id is None or body.row is None or body.col is None:
            raise HTTPException(400, "Choose a slot")
        check_slot_free(body.rack_id, body.row, body.col, ignore_bottle=bottle_id)
        b = db.move_bottle(bottle_id, body.rack_id, body.row, body.col)
        light([b], settings.color_checkin, "move")
        return b

    @app.post("/api/bottles/{bottle_id}/locate")
    def locate(bottle_id: int):
        b = bottle_or_404(bottle_id)
        if b["status"] != "in":
            raise HTTPException(409, "That bottle is checked out")
        light([b], settings.color_locate, "locate")
        return {"ok": True, "led_error": leds.last_error}

    @app.post("/api/locate")
    def locate_many(ids: list[int]):
        found = [b for b in (db.bottle(i) for i in ids) if b and b["status"] == "in"]
        light(found, settings.color_locate, "locate")
        return {"lit": len(found)}

    @app.post("/api/bottles/{bottle_id}/checkout")
    def check_out(bottle_id: int):
        b = bottle_or_404(bottle_id)
        if b["status"] != "in":
            raise HTTPException(409, "Already checked out")
        b = db.check_out(bottle_id)
        if any(s.get("bottle_id") == bottle_id for s in leds.current.slots):
            leds.clear()
        publish_sensor()
        return b

    @app.post("/api/bottles/{bottle_id}/return")
    def return_bottle(bottle_id: int, body: SlotRef):
        b = bottle_or_404(bottle_id)
        if b["status"] == "in":
            raise HTTPException(409, "That bottle is already in the rack")
        if body.rack_id is not None and body.row is not None and body.col is not None:
            check_slot_free(body.rack_id, body.row, body.col)
            slot = (body.rack_id, body.row, body.col)
        else:
            free = free_slots(1, body.rack_id)
            if not free:
                raise HTTPException(409, "No free slots - add or enlarge a rack")
            slot = free[0]
        b = db.check_in_again(bottle_id, *slot)
        light([b], settings.color_checkin, "checkin")
        publish_sensor()
        return b

    @app.delete("/api/bottles/{bottle_id}", status_code=204)
    def delete_bottle(bottle_id: int):
        b = bottle_or_404(bottle_id)
        db.delete_bottle(bottle_id)
        if b.get("photo") and not any(o.get("photo") == b["photo"] for o in db.bottles(status="all")):
            (photos / Path(b["photo"]).name).unlink(missing_ok=True)
        publish_sensor()

    @app.get("/api/slots/free")
    def next_free(rack_id: int | None = None, count: int = 1):
        return [{"rack_id": r, "row": row, "col": col} for r, row, col in free_slots(count, rack_id)]

    # -- LEDs --

    @app.get("/api/leds")
    def led_state():
        cur = leds.current
        return {"slots": cur.slots, "color": cur.color, "reason": cur.reason, "error": leds.last_error}

    @app.post("/api/leds/clear")
    def led_clear():
        leds.clear()
        return {"ok": True, "error": leds.last_error}

    @app.post("/api/leds/test")
    def led_test(body: LedTest):
        g = geometry(body.rack_id)
        if body.row is None or body.col is None:
            raise HTTPException(400, "Choose a slot")
        light([{"rack_id": body.rack_id, "row": body.row, "col": body.col}], settings.color_locate, "test", seconds=15)
        return {"leds": g.slot_leds(body.row, body.col), "error": leds.last_error}

    # -- sommelier --

    @app.post("/api/pair")
    def pair(body: PairRequest):
        cellar = db.bottles(status="in")
        try:
            result = claude.pair(body.meal, cellar[:400])
        except ClaudeUnavailable as e:
            raise HTTPException(503, str(e))
        by_id = {b["id"]: b for b in cellar}
        result["picks"] = [{**p, "bottle": by_id[p["bottle_id"]]} for p in result["picks"]]
        if result["picks"]:
            light([p["bottle"] for p in result["picks"]], settings.color_suggest, "suggest")
        return result

    # -- history / photos / UI --

    @app.get("/api/events")
    def events(limit: int = 50):
        return db.events(min(limit, 500))

    @app.get("/api/photos/{name}")
    def photo(name: str):
        path = photos / Path(name).name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "max-age=31536000, immutable"})

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
