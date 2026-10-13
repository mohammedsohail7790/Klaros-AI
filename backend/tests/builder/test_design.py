import pytest

from app.builder.design import _DEFAULTS, build_css, contrast_ratio, on_color, resolve_design
from app.builder.spec import DesignBrief, Direction, Palette


@pytest.mark.parametrize("direction", list(Direction))
def test_every_direction_resolves_to_readable_colours(direction):
    d = resolve_design(DesignBrief(direction=direction))
    p = d.palette
    assert contrast_ratio(p.text, p.background) >= 7.0
    assert contrast_ratio(p.muted, p.background) >= 4.5
    assert contrast_ratio(p.primary, p.background) >= 3.0
    css = build_css(DesignBrief(direction=direction))
    assert "--primary" in css and "prefers-reduced-motion" in css and ":focus-visible" in css


def test_low_contrast_choices_are_corrected_not_obeyed():
    bad = Palette(primary="#fafafa", secondary="#eeeeee", accent="#f0f0f0", background="#ffffff", surface="#f8f8f8", text="#dddddd", muted="#e0e0e0")
    d = resolve_design(DesignBrief(palette=bad))
    assert contrast_ratio(d.palette.text, d.palette.background) >= 7.0
    assert contrast_ratio(d.palette.primary, d.palette.background) >= 3.2


def test_explicit_choices_are_kept_and_open_ones_filled_from_the_direction():
    d = resolve_design(DesignBrief(direction=Direction.MINIMAL_EDITORIAL, radius="xl"))
    assert d.radius == "xl" and d.heading_font.value == "serif" and d.hero_layout == "editorial"


def test_directions_actually_differ():
    sheets = {x: build_css(DesignBrief(direction=x)) for x in Direction}
    assert len(set(sheets.values())) == len(Direction)
    assert "color-scheme: dark" in sheets[Direction.DARK_DASHBOARD]


def test_on_color_picks_a_readable_text_colour():
    assert on_color("#000000") == "#ffffff" and on_color("#ffffff") == "#111111"


def test_unsafe_palette_values_are_rejected():
    with pytest.raises(Exception):
        Palette(primary="red; } body { display:none", secondary="#fff000", accent="#fff000", background="#ffffff", surface="#ffffff", text="#000000", muted="#555555")
