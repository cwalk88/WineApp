"""Map rack slots (row, col) to WS2812B LED indices.

Coordinates used everywhere in the app: row 0 is the TOP row and col 0 is the
LEFT column, as seen standing in front of the rack.

Physical wiring is described per rack:

* led_start         index of the first LED of this rack on the (chained) strip
* leds_per_slot     how many LEDs sit under/over each bottle position
* start_corner      where the strip enters the rack: top_left | top_right |
                    bottom_left | bottom_right
* orientation       horizontal = the strip runs along rows,
                    vertical   = the strip runs along columns
* serpentine        True if every other run doubles back (zig-zag wiring)
* leds_between_runs LEDs wasted at each bend/jump between runs (often 0-2)

Several racks can be chained on one strip by giving each a different
led_start, which is how capacity is expanded.
"""

from dataclasses import dataclass

CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")
ORIENTATIONS = ("horizontal", "vertical")


@dataclass
class RackGeometry:
    rows: int
    cols: int
    led_start: int = 0
    leds_per_slot: int = 1
    start_corner: str = "top_left"
    orientation: str = "horizontal"
    serpentine: bool = False
    leds_between_runs: int = 0

    @classmethod
    def from_row(cls, row) -> "RackGeometry":
        return cls(
            rows=row["rows"],
            cols=row["cols"],
            led_start=row["led_start"],
            leds_per_slot=row["leds_per_slot"],
            start_corner=row["start_corner"],
            orientation=row["orientation"],
            serpentine=bool(row["serpentine"]),
            leds_between_runs=row["leds_between_runs"],
        )

    def validate(self) -> None:
        if self.rows < 1 or self.cols < 1:
            raise ValueError("A rack needs at least 1 row and 1 column")
        if self.rows > 100 or self.cols > 100:
            raise ValueError("Racks are limited to 100 x 100 slots")
        if self.leds_per_slot < 1 or self.leds_per_slot > 20:
            raise ValueError("LEDs per slot must be between 1 and 20")
        if self.led_start < 0 or self.leds_between_runs < 0:
            raise ValueError("LED offsets cannot be negative")
        if self.start_corner not in CORNERS:
            raise ValueError(f"start_corner must be one of {CORNERS}")
        if self.orientation not in ORIENTATIONS:
            raise ValueError(f"orientation must be one of {ORIENTATIONS}")

    @property
    def _runs(self) -> tuple[int, int]:
        """(number of runs, slots per run)."""
        if self.orientation == "horizontal":
            return self.rows, self.cols
        return self.cols, self.rows

    @property
    def led_count(self) -> int:
        """Total LEDs this rack occupies on the strip (including bend gaps)."""
        runs, per_run = self._runs
        return runs * per_run * self.leds_per_slot + (runs - 1) * self.leds_between_runs

    @property
    def led_end(self) -> int:
        """One past the last LED index used by this rack."""
        return self.led_start + self.led_count

    def slot_leds(self, row: int, col: int) -> list[int]:
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise ValueError(f"Slot ({row}, {col}) is outside a {self.rows}x{self.cols} rack")

        # Re-express the slot relative to the corner the strip starts from.
        r = row if self.start_corner.startswith("top") else self.rows - 1 - row
        c = col if self.start_corner.endswith("left") else self.cols - 1 - col

        if self.orientation == "horizontal":
            run, pos, per_run = r, c, self.cols
        else:
            run, pos, per_run = c, r, self.rows

        if self.serpentine and run % 2 == 1:
            pos = per_run - 1 - pos

        run_len = per_run * self.leds_per_slot + self.leds_between_runs
        base = self.led_start + run * run_len + pos * self.leds_per_slot
        return list(range(base, base + self.leds_per_slot))


def ranges_overlap(a: RackGeometry, b: RackGeometry) -> bool:
    return a.led_start < b.led_end and b.led_start < a.led_end
