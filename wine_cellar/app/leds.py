"""LED drivers and the highlight controller.

Drivers
  esphome  ESP32 running esphome/wine-rack.yaml, called through Home Assistant
           services (esphome.<device>_locate / esphome.<device>_clear)
  wled     ESP32 running WLED, called directly over its JSON API
  none     no hardware; highlights only show in the web UI
"""

import logging
import re
import threading
from dataclasses import dataclass, field

import httpx

from .config import Settings

log = logging.getLogger("wine.leds")


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


class LedDriver:
    name = "none"

    def show(self, leds: list[int], color: str, pulse: bool, seconds: int, brightness: int) -> None:
        log.info("LEDs (no hardware) -> %s #%s", leds, color)

    def clear(self) -> None:
        log.info("LEDs (no hardware) cleared")


class EsphomeDriver(LedDriver):
    name = "esphome"

    def __init__(self, settings: Settings):
        if not settings.ha_url or not settings.ha_token:
            raise RuntimeError("ESPHome driver needs Home Assistant API access (HA_URL / HA_TOKEN)")
        # HA registers ESPHome actions under the device name with anything that
        # isn't a letter or digit turned into "_" (wine-rack -> wine_rack).
        device = re.sub(r"[^a-z0-9]+", "_", settings.esphome_device.strip().lower()).strip("_")
        self.service = f"esphome.{device}"
        self.base = f"{settings.ha_url}/services/esphome/{device}"
        self.headers = {"Authorization": f"Bearer {settings.ha_token}"}

    def _call(self, action: str, payload: dict) -> None:
        r = httpx.post(f"{self.base}_{action}", json=payload, headers=self.headers, timeout=10)
        if r.status_code == 400:
            raise RuntimeError(
                f"Home Assistant has no action {self.service}_{action}. Check the ESPHome device "
                f"is adopted in HA and that esphome_device matches its name. ({r.text.strip()[:200]})"
            )
        r.raise_for_status()

    def show(self, leds, color, pulse, seconds, brightness):
        red, green, blue = hex_to_rgb(color)
        self._call("locate", {
            "leds": leds, "red": red, "green": green, "blue": blue,
            "pulse": pulse, "seconds": seconds, "brightness": brightness,
        })

    def clear(self):
        self._call("clear", {})


class WledDriver(LedDriver):
    name = "wled"

    def __init__(self, settings: Settings):
        if not settings.wled_host:
            raise RuntimeError("WLED driver needs wled_host, e.g. http://192.168.1.50")
        host = settings.wled_host
        self.url = (host if host.startswith("http") else f"http://{host}") + "/json/state"
        self._total: int | None = None

    def _strip_length(self) -> int:
        if self._total is None:
            info = httpx.get(self.url.replace("/json/state", "/json/info"), timeout=5).json()
            self._total = int(info["leds"]["count"])
        return self._total

    def show(self, leds, color, pulse, seconds, brightness):
        # Blank the whole segment, then paint the requested LEDs. WLED has no
        # per-pixel pulse, so pulse is ignored; the controller handles timeouts.
        pixels: list = [0, self._strip_length(), "000000"]
        for i in leds:
            pixels += [i, color]
        state = {
            "on": True,
            "bri": round(brightness * 255 / 100),
            "seg": {"id": 0, "fx": 0, "frz": False, "i": pixels},
        }
        httpx.post(self.url, json=state, timeout=5).raise_for_status()

    def clear(self):
        httpx.post(self.url, json={"on": False}, timeout=5).raise_for_status()


def make_driver(settings: Settings) -> LedDriver:
    try:
        if settings.led_driver == "esphome":
            return EsphomeDriver(settings)
        if settings.led_driver == "wled":
            return WledDriver(settings)
    except RuntimeError as e:
        log.warning("%s - falling back to UI-only highlights", e)
    return LedDriver()


@dataclass
class Highlight:
    slots: list[dict] = field(default_factory=list)  # [{rack_id, row, col, bottle_id}]
    color: str = ""
    reason: str = ""


class LedController:
    """Owns what is currently lit, pushes it to the driver and turns it off later."""

    def __init__(self, driver: LedDriver, settings: Settings):
        self.driver = driver
        self.settings = settings
        self.current = Highlight()
        self.last_error = ""
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def show(self, slots: list[dict], leds: list[int], color: str, reason: str,
             pulse: bool = True, seconds: int | None = None) -> None:
        seconds = self.settings.highlight_seconds if seconds is None else seconds
        with self._lock:
            self._cancel_timer()
            self.current = Highlight(slots=slots, color=color, reason=reason)
            try:
                self.driver.show(sorted(set(leds)), color, pulse, seconds, self.settings.brightness)
                self.last_error = ""
            except Exception as e:  # hardware problems must never break check-in
                self.last_error = f"{self.driver.name}: {e}"
                log.error("LED show failed: %s", e)
            if seconds > 0:
                self._timer = threading.Timer(seconds, self.clear)
                self._timer.daemon = True
                self._timer.start()

    def clear(self) -> None:
        with self._lock:
            self._cancel_timer()
            self.current = Highlight()
            try:
                self.driver.clear()
                self.last_error = ""
            except Exception as e:
                self.last_error = f"{self.driver.name}: {e}"
                log.error("LED clear failed: %s", e)

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
