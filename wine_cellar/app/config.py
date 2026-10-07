"""Runtime settings.

Inside Home Assistant the Supervisor writes the add-on options to
/data/options.json and injects SUPERVISOR_TOKEN. For local development the
same settings can be supplied as environment variables (WINE_* / ANTHROPIC_API_KEY).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path
    anthropic_api_key: str
    claude_model: str
    led_driver: str  # "esphome" | "wled" | "none"
    esphome_device: str
    wled_host: str
    highlight_seconds: int
    brightness: int
    color_locate: str
    color_checkin: str
    color_suggest: str
    ha_url: str
    ha_token: str
    publish_sensor: bool


def _hex(value: str, default: str) -> str:
    v = (value or default).lstrip("#")
    return v if len(v) == 6 else default.lstrip("#")


def load_settings() -> Settings:
    options_path = Path(os.environ.get("WINE_OPTIONS", "/data/options.json"))
    opts: dict = {}
    if options_path.exists():
        opts = json.loads(options_path.read_text())

    def opt(key: str, default=None):
        if key in opts and opts[key] not in (None, ""):
            return opts[key]
        return os.environ.get(f"WINE_{key.upper()}", default)

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    if supervisor_token:
        ha_url, ha_token = "http://supervisor/core/api", supervisor_token
    else:
        ha_url = os.environ.get("HA_URL", "").rstrip("/")
        ha_token = os.environ.get("HA_TOKEN", "")

    return Settings(
        data_dir=Path(os.environ.get("WINE_DATA_DIR", "/data")),
        anthropic_api_key=opt("anthropic_api_key", os.environ.get("ANTHROPIC_API_KEY", "")),
        claude_model=opt("claude_model", "claude-opus-5-5"),
        led_driver=str(opt("led_driver", "esphome")).lower(),
        esphome_device=opt("esphome_device", "wine_rack"),
        wled_host=str(opt("wled_host", "")).rstrip("/"),
        highlight_seconds=int(opt("highlight_seconds", 90)),
        brightness=max(1, min(100, int(opt("brightness", 80)))),
        color_locate=_hex(opt("color_locate", ""), "FFB000"),
        color_checkin=_hex(opt("color_checkin", ""), "00FF40"),
        color_suggest=_hex(opt("color_suggest", ""), "B000FF"),
        ha_url=ha_url,
        ha_token=ha_token,
        publish_sensor=str(opt("publish_sensor", "true")).lower() in ("1", "true", "yes"),
    )
