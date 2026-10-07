import pytest

from app.layout import RackGeometry, ranges_overlap


def g(**kw):
    base = dict(rows=3, cols=4)
    base.update(kw)
    return RackGeometry(**base)


def test_simple_row_major():
    r = g()
    assert r.slot_leds(0, 0) == [0]
    assert r.slot_leds(0, 3) == [3]
    assert r.slot_leds(1, 0) == [4]
    assert r.slot_leds(2, 3) == [11]
    assert r.led_count == 12


def test_serpentine_reverses_every_other_row():
    r = g(serpentine=True)
    assert r.slot_leds(0, 3) == [3]
    assert r.slot_leds(1, 3) == [4]   # second row runs right-to-left
    assert r.slot_leds(1, 0) == [7]
    assert r.slot_leds(2, 0) == [8]


def test_bottom_left_start_vertical_runs():
    r = g(start_corner="bottom_left", orientation="vertical")
    assert r.slot_leds(2, 0) == [0]   # bottom-left is LED 0
    assert r.slot_leds(0, 0) == [2]   # up the first column
    assert r.slot_leds(2, 1) == [3]


def test_top_right_start():
    r = g(start_corner="top_right")
    assert r.slot_leds(0, 3) == [0]
    assert r.slot_leds(0, 0) == [3]


def test_multiple_leds_offset_and_bend_gaps():
    r = g(leds_per_slot=2, led_start=100, leds_between_runs=1)
    assert r.slot_leds(0, 0) == [100, 101]
    assert r.slot_leds(0, 3) == [106, 107]
    assert r.slot_leds(1, 0) == [109, 110]  # 8 LEDs + 1 skipped at the bend
    assert r.led_count == 3 * 8 + 2
    assert r.led_end == 126


def test_every_slot_unique_for_all_wirings():
    for corner in ("top_left", "top_right", "bottom_left", "bottom_right"):
        for orient in ("horizontal", "vertical"):
            for serp in (False, True):
                r = g(rows=5, cols=7, start_corner=corner, orientation=orient, serpentine=serp, leds_per_slot=2)
                seen = [i for row in range(5) for col in range(7) for i in r.slot_leds(row, col)]
                assert sorted(seen) == list(range(70))


def test_out_of_range_slot():
    with pytest.raises(ValueError):
        g().slot_leds(3, 0)


def test_overlap():
    a = g()                       # 0..11
    assert ranges_overlap(a, g(led_start=11))
    assert not ranges_overlap(a, g(led_start=12))
