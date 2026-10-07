from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path, anthropic_api_key="", claude_model="claude-opus-5-5",
        led_driver="none", esphome_device="wine_rack", wled_host="",
        highlight_seconds=0, brightness=80, color_locate="FFB000",
        color_checkin="00FF40", color_suggest="B000FF", ha_url="", ha_token="",
        publish_sensor=False,
    )
    return TestClient(create_app(settings))


def add_rack(client, **kw):
    body = {"name": "Main", "rows": 2, "cols": 3, **kw}
    r = client.post("/api/racks", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_check_in_locate_check_out(client):
    rack = add_rack(client)
    r = client.post("/api/bottles", json={"name": "Barolo", "vintage": 2016, "food_pairings": ["truffle pasta"]})
    assert r.status_code == 201
    b = r.json()[0]
    assert (b["rack_id"], b["slot_row"], b["slot_col"]) == (rack["id"], 0, 0)

    leds = client.get("/api/leds").json()
    assert leds["slots"][0]["bottle_id"] == b["id"]

    assert client.get("/api/bottles", params={"q": "truffle"}).json()[0]["id"] == b["id"]
    assert client.post(f"/api/bottles/{b['id']}/locate").status_code == 200

    out = client.post(f"/api/bottles/{b['id']}/checkout").json()
    assert out["status"] == "out"
    assert client.get("/api/leds").json()["slots"] == []
    assert client.get("/api/bottles").json() == []

    back = client.post(f"/api/bottles/{b['id']}/return", json={}).json()
    assert back["status"] == "in"


def test_quantity_fills_next_slots_and_capacity(client):
    add_rack(client)
    r = client.post("/api/bottles", json={"name": "Rioja", "quantity": 4})
    assert [(b["slot_row"], b["slot_col"]) for b in r.json()] == [(0, 0), (0, 1), (0, 2), (1, 0)]
    r = client.post("/api/bottles", json={"name": "Chablis", "quantity": 3})
    assert r.status_code == 409


def test_chosen_slot_and_conflict(client):
    rack = add_rack(client)
    body = {"name": "Port", "rack_id": rack["id"], "row": 1, "col": 2}
    assert client.post("/api/bottles", json=body).status_code == 201
    assert client.post("/api/bottles", json=body).status_code == 409


def test_resize_and_chain_racks(client):
    a = add_rack(client)
    b = add_rack(client, name="Second")
    assert b["led_start"] == 6  # chained after rack A
    # Growing A would now overlap B's LEDs
    assert client.patch(f"/api/racks/{a['id']}", json={"rows": 3}).status_code == 409
    # Grow B instead
    assert client.patch(f"/api/racks/{b['id']}", json={"rows": 4}).json()["rows"] == 4
    # Can't shrink past a stored bottle
    client.post("/api/bottles", json={"name": "Sancerre", "rack_id": b["id"], "row": 3, "col": 0})
    r = client.patch(f"/api/racks/{b['id']}", json={"rows": 2})
    assert r.status_code == 409 and "Sancerre" in r.json()["detail"]


def test_analyze_without_key_still_saves_photo(client):
    r = client.post("/api/analyze", files={"image": ("label.jpg", b"\xff\xd8\xff fake", "image/jpeg")})
    data = r.json()
    assert data["wine"] is None and "API key" in data["error"]
    assert client.get(f"/api/photos/{data['photo']}").status_code == 200


def test_index_served(client):
    assert "Wine Cellar" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
