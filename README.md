# Wine Cellar

A Home Assistant add-on for a home wine rack with an LED in every bottle slot.

- **Check in:** photograph the label. Claude identifies the wine (producer, vintage, grapes, region, drinking window) and suggests what to eat with it. The app picks a free slot and lights it green on the rack.
- **Find:** search by name, grape, region or food. Opening a bottle lights its slot, and "Light matches" lights every bottle the search found.
- **Check out:** "Take out" removes the bottle from the rack and turns its light off. "Put back in rack" returns it to a free slot.
- **Sommelier:** say what you're eating. Claude picks up to three bottles you already own and lights them.
- **Expandable racks:** add racks, or add rows and columns to an existing one. Racks chain along one LED strip. The wiring editor shows which LED is behind each slot, and you can light any slot to check it.

```
Phone/tablet ──► HA sidebar "Wine Cellar" (ingress) ──► add-on (FastAPI + SQLite)
                                                           │            │
                                             Claude API ◄──┘            └──► HA action esphome.wine_rack_locate
                                          (label + pairing)                          │
                                                                          ESP32 (ESPHome) ──► WS2812B strip
```

## Repository layout

| Path | What it is |
| --- | --- |
| `wine_cellar/` | The Home Assistant add-on (Dockerfile, `config.yaml`, app) |
| `wine_cellar/app/main.py` | API routes |
| `wine_cellar/app/layout.py` | Slot to LED index mapping for different strip wirings |
| `wine_cellar/app/leds.py` | ESPHome / WLED / UI-only drivers and the auto-off timer |
| `wine_cellar/app/sommelier.py` | Claude label reading and food pairing |
| `wine_cellar/app/static/` | The web UI (no build step) |
| `esphome/wine-rack.yaml` | ESP32 firmware |
| `tests/` | `pytest` tests for wiring maths and the check-in/out flow |

## 1. Hardware

- ESP32 dev board (for example an ESP32-DevKitC)
- WS2812B strip, 1 or more LEDs per bottle slot (30 or 60 LEDs/m usually lines up with rack spacing)
- 5 V power supply. Only a few LEDs are lit at once, but size the supply for the whole strip in case of a fault (about 60 mA per LED at full white). 5 V 4 A covers about 300 LEDs at the brightness this app uses.
- 330 Ω resistor on the data line, and a 1000 µF capacitor across 5 V/GND at the strip
- Optional but recommended: a 74AHCT125 level shifter (the ESP32 outputs 3.3 V and WS2812B expects 5 V logic)

Wiring: PSU 5 V to strip 5 V and ESP32 5 V/VIN. PSU GND to strip GND and ESP32 GND (the grounds must be shared). ESP32 GPIO16 to the 330 Ω resistor, then to strip DIN. For long runs, feed 5 V in again every 2 to 3 m.

## 2. Flash the ESP32 (ESPHome)

1. In Home Assistant install the **ESPHome Device Builder** add-on.
2. Create a new device, then replace its YAML with `esphome/wine-rack.yaml`. Set `num_leds` to the total LEDs on your strip and `led_pin` if you didn't use GPIO16.
3. Add `wine_rack_api_key`, `ota_password`, `wifi_ssid` and `wifi_password` to ESPHome's `secrets.yaml`. Generate the API key in the ESPHome dashboard.
4. Install over USB the first time, then adopt the device in **Settings → Devices & services**. HA now has the actions `esphome.wine_rack_locate` and `esphome.wine_rack_clear`.

**ESP8266 boards (NodeMCU / Wemos D1 mini):** use `esphome/wine-rack-esp8266.yaml` instead. The strip's data line must go to **GPIO3 (RX)**. Serial logging is off on this build because it shares that pin, so view logs over Wi-Fi. If a USB flash fails, unplug the strip's data wire and try again.

**First-time Wi-Fi setup:** if the board can't join the Wi-Fi in `secrets.yaml`, after about a minute it starts a **Wine-Rack** hotspot. Its password is `ap_password` in `esphome/secrets.yaml`. Join it from your phone and pick your network in the page that opens. The board saves the network and reboots onto it.

To flash from a Mac: `cd esphome && ../.esphome-venv/bin/esphome run wine-rack-esp8266.yaml`

**Using WLED instead:** flash WLED, set the add-on option `led_driver: wled` and `wled_host: 192.168.x.x`. Pulsing isn't available on WLED.

## 3. Install the add-on

1. Push this folder to a GitHub repo and set its URL in `repository.yaml` and `wine_cellar/config.yaml`.
2. In HA go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add the repo URL. Alternatively, copy `wine_cellar/` into `/addons/` on the HA host (Samba or SSH add-on) and it appears under **Local add-ons**.
3. Install **Wine Cellar**. In **Configuration**, set:
   - `anthropic_api_key`: get a key at https://console.anthropic.com
   - `led_driver`: `esphome` (default), `wled`, or `none` to try it without hardware
   - `esphome_device`: `wine_rack` unless you renamed the ESPHome device
   - `highlight_seconds`: how long slots stay lit (0 = until turned off)
   - colours for locate, check-in and sommelier picks
4. Start it, and enable **Show in sidebar**.

Claude runs on `claude-opus-5-5` by default (configurable with `claude_model`). It has server-side fallback turned on, so if the model's safety filters decline a request, the request is retried on another model rather than failing. Each label read costs a few US cents.

The add-on also publishes `sensor.wine_cellar_bottles` (bottle count, with per-style counts as attributes) for dashboards and automations.

## 4. Set up the rack

Open **Racks** and add a rack per section of shelving:

| Field | Meaning |
| --- | --- |
| Rows / Columns | Bottle positions. Row 1 is the top and column 1 is the left, looking at the rack. |
| LEDs per slot | LEDs that should light for one bottle |
| First LED # | Where this rack starts on the strip. Leave blank and the rack is placed after the previous one. |
| Strip starts at | The corner where the data line enters |
| Strip runs | Along rows (horizontal) or along columns (vertical) |
| Zig-zag wiring | Every other run comes back the other way |
| Unused LEDs per bend | LEDs skipped where the strip turns a corner |

Tap any slot in the wiring preview to light it, or use **Light whole rack** to check orientation. To expand, increase rows or columns. The app refuses changes that would collide with another rack's LEDs, or that would leave a stored bottle outside the rack.

## Local development

```bash
./scripts/dev.sh
```

This serves on http://127.0.0.1:8099 with UI-only LEDs. Set `ANTHROPIC_API_KEY` to enable label reading. To drive real hardware from your Mac, set `WINE_LED_DRIVER=esphome HA_URL=http://homeassistant.local:8123/api HA_TOKEN=<long-lived token>` or `WINE_LED_DRIVER=wled WINE_WLED_HOST=<ip>`.

```bash
.venv/bin/python -m pytest
```
