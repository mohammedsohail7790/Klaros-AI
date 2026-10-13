"""Design directions -> concrete tokens and one shared stylesheet.

A direction is a set of defaults (palette, type, radius, density, hero and card treatment), not a template: every
generated site goes through `resolve_design`, which fills what the brief left open and then enforces readable contrast.
The stylesheet is generated from the resolved tokens, so a change of direction or colour changes the whole site
consistently without rewriting any page.
"""

from __future__ import annotations

from app.builder.spec import DesignBrief, Direction, FontStack, Palette

FONT_STACKS: dict[FontStack, str] = {
    FontStack.SANS: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    FontStack.GEOMETRIC: '"Avenir Next", "Avenir", "Century Gothic", ui-sans-serif, system-ui, "Segoe UI", sans-serif',
    FontStack.SERIF: '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif',
    FontStack.MONO: 'ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    FontStack.ROUNDED: 'ui-rounded, "SF Pro Rounded", "Hiragino Maru Gothic ProN", Quicksand, ui-sans-serif, system-ui, sans-serif',
}

_P = Palette

_DEFAULTS: dict[Direction, dict] = {
    Direction.MINIMAL_EDITORIAL: dict(
        palette=_P(primary="#1a1a1a", secondary="#8a6f4d", accent="#b4532a", background="#fbfaf7", surface="#f2efe8", text="#1c1b19", muted="#6b675f"),
        heading_font=FontStack.SERIF, body_font=FontStack.SANS, radius="none", density="spacious", hero_layout="editorial", card_style="flat", dark=False),
    Direction.PREMIUM_SAAS: dict(
        palette=_P(primary="#4f46e5", secondary="#7c3aed", accent="#06b6d4", background="#ffffff", surface="#f5f6fb", text="#0f172a", muted="#5b667a"),
        heading_font=FontStack.GEOMETRIC, body_font=FontStack.SANS, radius="lg", density="comfortable", hero_layout="split", card_style="bordered", dark=False),
    Direction.ENTERPRISE_TECH: dict(
        palette=_P(primary="#0b5cab", secondary="#0f3d73", accent="#00a3a3", background="#ffffff", surface="#f1f5f9", text="#0b1b2b", muted="#53657a"),
        heading_font=FontStack.SANS, body_font=FontStack.SANS, radius="sm", density="comfortable", hero_layout="split", card_style="bordered", dark=False),
    Direction.ECOMMERCE: dict(
        palette=_P(primary="#111827", secondary="#be123c", accent="#d97706", background="#ffffff", surface="#f6f6f4", text="#111827", muted="#5c6370"),
        heading_font=FontStack.GEOMETRIC, body_font=FontStack.SANS, radius="md", density="comfortable", hero_layout="stacked", card_style="elevated", dark=False),
    Direction.REAL_ESTATE: dict(
        palette=_P(primary="#0f3d3e", secondary="#b08d57", accent="#1f7a5c", background="#fbfbf9", surface="#eef2ef", text="#14201f", muted="#586664"),
        heading_font=FontStack.SERIF, body_font=FontStack.SANS, radius="md", density="comfortable", hero_layout="stacked", card_style="elevated", dark=False),
    Direction.CREATIVE_PORTFOLIO: dict(
        palette=_P(primary="#111111", secondary="#ff4d2e", accent="#ffd400", background="#f7f5f2", surface="#ffffff", text="#111111", muted="#5f5f5f"),
        heading_font=FontStack.GEOMETRIC, body_font=FontStack.SANS, radius="none", density="spacious", hero_layout="editorial", card_style="flat", dark=False),
    Direction.ARCHITECTURE: dict(
        palette=_P(primary="#2b2b2b", secondary="#7d7466", accent="#a2603a", background="#f6f4f0", surface="#ebe7e0", text="#222222", muted="#66625b"),
        heading_font=FontStack.SERIF, body_font=FontStack.SANS, radius="none", density="spacious", hero_layout="editorial", card_style="flat", dark=False),
    Direction.CONSUMER_APP: dict(
        palette=_P(primary="#ff5a5f", secondary="#6c5ce7", accent="#00b894", background="#ffffff", surface="#fff4f2", text="#1f2430", muted="#5f6676"),
        heading_font=FontStack.ROUNDED, body_font=FontStack.ROUNDED, radius="xl", density="comfortable", hero_layout="split", card_style="elevated", dark=False),
    Direction.DARK_DASHBOARD: dict(
        palette=_P(primary="#7c8cff", secondary="#38bdf8", accent="#34d399", background="#0b0f1a", surface="#131a2b", text="#e6eaf5", muted="#94a0ba"),
        heading_font=FontStack.SANS, body_font=FontStack.SANS, radius="md", density="compact", hero_layout="split", card_style="bordered", dark=True),
    Direction.PLAYFUL_STARTUP: dict(
        palette=_P(primary="#7c3aed", secondary="#f472b6", accent="#fbbf24", background="#fffdf7", surface="#f5f0ff", text="#231942", muted="#675d85"),
        heading_font=FontStack.ROUNDED, body_font=FontStack.ROUNDED, radius="xl", density="comfortable", hero_layout="centered", card_style="elevated", dark=False),
}

_RADII = {"none": ("0px", "0px"), "sm": ("4px", "6px"), "md": ("8px", "12px"), "lg": ("12px", "18px"), "xl": ("18px", "28px")}
_SECTION_Y = {"compact": "3.25rem", "comfortable": "5rem", "spacious": "7rem"}


# ---- colour maths (WCAG 2.x) ----
def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(h: str) -> float:
    r, g, b = (_lin(x) for x in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def on_color(bg: str) -> str:
    """Readable text colour for a filled background."""
    return "#ffffff" if contrast_ratio("#ffffff", bg) >= contrast_ratio("#111111", bg) else "#111111"


def _mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round((x * (1 - t) + y * t) * 255):02x}" for x, y in zip(ra, rb))


def _ensure_contrast(fg: str, bg: str, minimum: float, toward: str) -> str:
    """Nudge `fg` toward `toward` until it reaches `minimum` contrast on `bg` (bounded, never loops forever)."""
    out = fg
    for step in range(1, 11):
        if contrast_ratio(out, bg) >= minimum:
            return out
        out = _mix(fg, toward, step / 10)
    return toward


def resolve_design(brief: DesignBrief) -> DesignBrief:
    """Fill every open choice from the direction's defaults, then guarantee readable text and link colours."""
    d = _DEFAULTS[brief.direction]
    data = brief.model_dump()
    for key in ("heading_font", "body_font", "radius", "density", "hero_layout", "card_style", "dark"):
        if data[key] is None:
            data[key] = d[key]
    pal = brief.palette or d["palette"]
    p = pal.model_dump()
    want_dark = bool(data["dark"])
    is_dark = luminance(p["background"]) < 0.25
    if want_dark and not is_dark:  # same brand colours on a dark canvas
        p.update(background="#0b0f1a", surface="#131a2b", text="#e6eaf5", muted="#94a0ba")
        p["primary"] = _mix(p["primary"], "#ffffff", 0.35)
    elif not want_dark and is_dark:
        p.update(background="#ffffff", surface="#f5f6fb", text="#0f172a", muted="#5b667a")
        p["primary"] = _mix(p["primary"], "#000000", 0.25)
    p["text"] = _ensure_contrast(p["text"], p["background"], 7.0, "#ffffff" if want_dark else "#000000")
    p["muted"] = _ensure_contrast(p["muted"], p["background"], 4.6, p["text"])
    # the primary colour is used for links and outlines on the page background, so it must be readable there as well
    p["primary"] = _ensure_contrast(p["primary"], p["background"], 3.2, p["text"])
    data["palette"] = Palette(**p)
    return DesignBrief(**data)


def build_css(brief: DesignBrief) -> str:
    d = resolve_design(brief)
    p = d.palette
    assert p is not None
    r_s, r_l = _RADII[d.radius]
    y = _SECTION_Y[d.density]
    on_primary = on_color(p.primary)
    on_accent = on_color(p.accent)
    border = _mix(p.background, p.text, 0.14)
    shadow = "none" if d.card_style != "elevated" else ("0 1px 2px rgba(0,0,0,.25), 0 12px 32px -12px rgba(0,0,0,.5)" if d.dark else "0 1px 2px rgba(16,24,40,.06), 0 14px 34px -16px rgba(16,24,40,.22)")
    card_border = "1px solid " + border if d.card_style in ("bordered", "elevated") else "0"
    hero_bg = f"radial-gradient(60% 80% at 100% 0%, {_mix(p.background, p.secondary, 0.16)} 0%, transparent 70%), radial-gradient(50% 70% at 0% 100%, {_mix(p.background, p.primary, 0.1)} 0%, transparent 70%)"
    return f""":root {{
  --primary: {p.primary}; --secondary: {p.secondary}; --accent: {p.accent};
  --bg: {p.background}; --surface: {p.surface}; --text: {p.text}; --muted: {p.muted};
  --on-primary: {on_primary}; --on-accent: {on_accent}; --border: {border};
  --radius: {r_s}; --radius-lg: {r_l}; --section-y: {y}; --shadow: {shadow};
  --font-heading: {FONT_STACKS[d.heading_font]}; --font-body: {FONT_STACKS[d.body_font]};
  --max: 1160px; color-scheme: {"dark" if d.dark else "light"};
}}
*,*::before,*::after {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 400 1.0625rem/1.65 var(--font-body); -webkit-font-smoothing: antialiased; }}
img, svg {{ max-width: 100%; display: block; }}
h1,h2,h3,h4 {{ font-family: var(--font-heading); line-height: 1.12; letter-spacing: -0.02em; margin: 0; text-wrap: balance; }}
h1 {{ font-size: clamp(2.4rem, 5.6vw, 4.2rem); font-weight: 700; }}
h2 {{ font-size: clamp(1.75rem, 3.4vw, 2.6rem); font-weight: 700; }}
h3 {{ font-size: 1.2rem; font-weight: 650; }}
p {{ margin: 0; }}
a {{ color: var(--primary); text-underline-offset: 3px; }}
a:hover {{ text-decoration-thickness: 2px; }}
:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; border-radius: 3px; }}
.skip {{ position: absolute; left: .75rem; top: -4rem; background: var(--primary); color: var(--on-primary); padding: .6rem 1rem; border-radius: var(--radius); z-index: 100; }}
.skip:focus {{ top: .75rem; }}
.container {{ width: min(100% - 3rem, var(--max)); margin-inline: auto; }}
.section {{ padding-block: var(--section-y); }}
.section.alt {{ background: var(--surface); }}
.section.tight {{ padding-block: calc(var(--section-y) * .55); }}
.eyebrow {{ display: inline-block; font: 650 .78rem/1 var(--font-body); letter-spacing: .09em; text-transform: uppercase; color: var(--primary); margin-bottom: 1rem; }}
.lead {{ font-size: 1.2rem; color: var(--muted); max-width: 40rem; }}
.section-head {{ max-width: 46rem; margin-bottom: 2.75rem; }}
.section-head .lead {{ margin-top: 1rem; }}
.example-note {{ display: inline-block; margin-top: 1rem; font-size: .8rem; color: var(--muted); border: 1px dashed var(--border); padding: .25rem .6rem; border-radius: 999px; }}
/* header */
.site-header {{ {"position: sticky; top: 0;" if d.sticky_header else ""} z-index: 50; background: color-mix(in srgb, var(--bg) 92%, transparent); backdrop-filter: saturate(1.4) blur(10px); border-bottom: 1px solid var(--border); }}
.site-header .bar {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; min-height: 4.25rem; }}
.brand {{ display: inline-flex; align-items: center; gap: .65rem; font: 700 1.15rem var(--font-heading); color: var(--text); text-decoration: none; }}
.brand-mark {{ display: grid; place-items: center; width: 2.1rem; height: 2.1rem; border-radius: var(--radius); background: var(--primary); color: var(--on-primary); font-size: 1rem; }}
.nav ul {{ display: flex; gap: .25rem; list-style: none; margin: 0; padding: 0; flex-wrap: wrap; }}
.nav a {{ display: block; padding: .5rem .8rem; border-radius: var(--radius); color: var(--text); text-decoration: none; font-weight: 500; font-size: .98rem; }}
.nav a:hover {{ background: var(--surface); }}
.nav a[aria-current="page"] {{ color: var(--primary); font-weight: 650; }}
.menu-btn {{ display: none; border: 1px solid var(--border); background: transparent; color: var(--text); border-radius: var(--radius); padding: .5rem .8rem; font: inherit; cursor: pointer; }}
/* buttons */
.btn {{ display: inline-flex; align-items: center; justify-content: center; gap: .5rem; padding: .85rem 1.4rem; border-radius: var(--radius); border: 1px solid transparent; font: 650 1rem var(--font-body); text-decoration: none; cursor: pointer; transition: transform .15s ease, box-shadow .15s ease, background .15s ease; }}
.btn-primary {{ background: var(--primary); color: var(--on-primary); }}
.btn-primary:hover {{ transform: translateY(-1px); box-shadow: 0 10px 24px -10px var(--primary); }}
.btn-ghost {{ background: transparent; color: var(--text); border-color: var(--border); }}
.btn-ghost:hover {{ background: var(--surface); }}
.btn[disabled] {{ opacity: .55; cursor: not-allowed; transform: none; box-shadow: none; }}
.actions {{ display: flex; flex-wrap: wrap; gap: .8rem; margin-top: 2rem; }}
/* hero */
.hero {{ padding-block: calc(var(--section-y) * .9) var(--section-y); background: {hero_bg}; }}
.hero .grid {{ display: grid; gap: 3rem; align-items: center; }}
.hero-split .grid {{ grid-template-columns: minmax(0,1.1fr) minmax(0,.9fr); }}
.hero-centered .grid, .hero-stacked .grid {{ text-align: center; justify-items: center; }}
.hero-centered .lead, .hero-stacked .lead {{ margin-inline: auto; }}
.hero-centered .actions, .hero-stacked .actions {{ justify-content: center; }}
.hero-editorial h1 {{ font-size: clamp(2.8rem, 7.5vw, 6rem); max-width: 14ch; }}
.hero .lead {{ margin-top: 1.4rem; }}
.hero-art {{ aspect-ratio: 4/3.2; border-radius: var(--radius-lg); overflow: hidden; box-shadow: var(--shadow); border: {card_border}; width: 100%; }}
.hero-art img {{ width: 100%; height: 100%; object-fit: cover; }}
/* cards + grids */
.grid-cards {{ display: grid; gap: 1.5rem; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); }}
.card {{ background: var(--bg); border: {card_border}; border-radius: var(--radius-lg); box-shadow: var(--shadow); overflow: hidden; display: flex; flex-direction: column; }}
.section.alt .card {{ background: var(--bg); }}
.card-body {{ padding: 1.4rem; display: grid; gap: .6rem; }}
.card-body p {{ color: var(--muted); font-size: .97rem; }}
.card .art {{ aspect-ratio: 4/3; width: 100%; height: auto; object-fit: cover; background: var(--surface); }}
.card h3 a {{ color: inherit; text-decoration: none; }}
.card h3 a:hover {{ color: var(--primary); }}
.icon {{ display: grid; place-items: center; width: 2.6rem; height: 2.6rem; border-radius: var(--radius); background: color-mix(in srgb, var(--primary) 12%, transparent); color: var(--primary); font-weight: 700; }}
.price {{ font: 700 1.1rem var(--font-heading); color: var(--text); }}
.tags {{ display: flex; flex-wrap: wrap; gap: .4rem; }}
.tag {{ font-size: .78rem; padding: .2rem .6rem; border-radius: 999px; background: var(--surface); color: var(--muted); border: 1px solid var(--border); }}
.facts {{ background: var(--primary); color: var(--on-primary); padding-block: 1.1rem; }}
.facts ul {{ list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: .5rem 2.2rem; justify-content: center; font-weight: 550; }}
/* split */
.split {{ display: grid; gap: 4rem; align-items: center; grid-template-columns: 1fr 1fr; }}
.split.reverse .split-media {{ order: -1; }}
.split-media {{ aspect-ratio: 5/4; border-radius: var(--radius-lg); overflow: hidden; border: {card_border}; }}
.split-media img {{ width: 100%; height: 100%; object-fit: cover; }}
.checks {{ list-style: none; padding: 0; margin: 1.5rem 0 0; display: grid; gap: .7rem; }}
.checks li {{ display: flex; gap: .7rem; align-items: baseline; font-weight: 500; }}
.checks li::before {{ content: "✓"; color: var(--primary); font-weight: 800; }}
/* steps / pricing / faq */
.steps {{ counter-reset: s; display: grid; gap: 1.5rem; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); list-style: none; padding: 0; margin: 0; }}
.steps li {{ counter-increment: s; border-top: 3px solid var(--primary); padding-top: 1rem; }}
.steps li::before {{ content: counter(s, decimal-leading-zero); font: 700 .9rem var(--font-heading); color: var(--primary); }}
.steps h3 {{ margin: .4rem 0; }}
.steps p {{ color: var(--muted); }}
.plans {{ display: grid; gap: 1.5rem; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); align-items: stretch; }}
.plan {{ padding: 2rem; display: grid; gap: 1rem; align-content: start; }}
.plan.featured {{ border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary), var(--shadow); }}
.plan .amount {{ font: 800 2.6rem var(--font-heading); letter-spacing: -.03em; }}
.faq details {{ border-bottom: 1px solid var(--border); padding-block: 1.1rem; }}
.faq summary {{ cursor: pointer; font-weight: 650; display: flex; justify-content: space-between; gap: 1rem; list-style: none; }}
.faq summary::after {{ content: "+"; font-size: 1.4rem; line-height: 1; color: var(--primary); }}
.faq details[open] summary::after {{ content: "−"; }}
.faq p {{ margin-top: .8rem; color: var(--muted); max-width: 46rem; }}
/* cta */
.cta {{ background: var(--primary); color: var(--on-primary); border-radius: var(--radius-lg); padding: clamp(2rem, 5vw, 4rem); display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: center; justify-content: space-between; }}
.cta h2 {{ color: inherit; }}
.cta p {{ opacity: .9; margin-top: .6rem; max-width: 34rem; }}
.cta .btn-primary {{ background: var(--on-primary); color: var(--primary); }}
/* forms */
.form {{ display: grid; gap: 1.1rem; max-width: 34rem; }}
.field {{ display: grid; gap: .4rem; }}
.field label {{ font-weight: 600; font-size: .95rem; }}
.field input, .field textarea, .field select, .toolbar input, .toolbar select {{ width: 100%; padding: .8rem .9rem; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg); color: var(--text); font: inherit; }}
.field input:focus, .field textarea:focus, .toolbar input:focus {{ outline: 3px solid color-mix(in srgb, var(--primary) 35%, transparent); border-color: var(--primary); }}
.field [aria-invalid="true"] {{ border-color: #c62828; }}
.error {{ color: #c62828; font-size: .88rem; min-height: 1.1em; }}
.form-status {{ padding: .9rem 1rem; border-radius: var(--radius); background: var(--surface); border: 1px solid var(--border); }}
.form-status[data-state="success"] {{ border-color: #2e7d32; }}
.form-status[data-state="error"] {{ border-color: #c62828; }}
.hp {{ position: absolute; left: -9999px; width: 1px; height: 1px; opacity: 0; }}
/* collections */
.toolbar {{ display: grid; gap: .8rem; grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr)); margin-bottom: 1.5rem; }}
.toolbar input[type=search] {{ grid-column: 1 / -1; }}
.section.tight + .section {{ padding-top: 2.25rem; }}
.section.page-head {{ padding-bottom: 0; }}
.section.page-head .section-head {{ margin-bottom: 0; }}
.foot-lead {{ font-size: 1rem; margin-top: .9rem; }}
.result-count {{ color: var(--muted); font-size: .92rem; margin-bottom: 1rem; }}
.empty {{ padding: 3rem 1rem; text-align: center; color: var(--muted); border: 1px dashed var(--border); border-radius: var(--radius-lg); }}
.fav {{ position: absolute; top: .7rem; right: .7rem; border: 0; border-radius: 999px; background: var(--bg); color: var(--text); width: 2.4rem; height: 2.4rem; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.2); font-size: 1.1rem; }}
.fav[aria-pressed="true"] {{ color: #c62828; }}
.card-media {{ position: relative; }}
.detail {{ display: grid; gap: 3rem; grid-template-columns: minmax(0,1.3fr) minmax(0,.7fr); align-items: start; }}
.detail-art {{ border-radius: var(--radius-lg); overflow: hidden; border: {card_border}; margin-bottom: 1.5rem; }}
.detail-art img {{ width: 100%; height: auto; aspect-ratio: 4/3; object-fit: cover; }}
.spec-list {{ display: grid; grid-template-columns: auto 1fr; gap: .5rem 1.5rem; margin: 1.5rem 0; }}
.spec-list dt {{ color: var(--muted); }}
.spec-list dd {{ margin: 0; font-weight: 600; }}
.aside {{ padding: 1.6rem; position: sticky; top: 6rem; }}
.crumbs {{ font-size: .9rem; color: var(--muted); margin-bottom: 1.2rem; }}
.article {{ max-width: 44rem; }}
.article p {{ margin-top: 1.2rem; font-size: 1.12rem; }}
.hours {{ width: 100%; border-collapse: collapse; }}
.hours th, .hours td {{ text-align: left; padding: .75rem 0; border-bottom: 1px solid var(--border); }}
.kpis {{ display: grid; gap: 1.2rem; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); margin-bottom: 2rem; }}
.kpi {{ padding: 1.3rem; }}
.kpi strong {{ display: block; font: 750 2rem var(--font-heading); }}
table.data {{ width: 100%; border-collapse: collapse; }}
table.data th, table.data td {{ text-align: left; padding: .8rem 1rem; border-bottom: 1px solid var(--border); }}
table.data th {{ color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .06em; }}
.table-wrap {{ overflow-x: auto; border: {card_border}; border-radius: var(--radius-lg); }}
/* footer */
.site-footer {{ background: var(--surface); border-top: 1px solid var(--border); padding-block: 3.5rem 2rem; margin-top: 0; }}
.footer-grid {{ display: grid; gap: 2.5rem; grid-template-columns: 1.4fr repeat(auto-fit, minmax(9rem, 1fr)); }}
.footer-grid h2 {{ font-size: .8rem; letter-spacing: .09em; text-transform: uppercase; color: var(--muted); margin-bottom: .9rem; }}
.footer-grid ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem; }}
.footer-grid a {{ color: var(--text); text-decoration: none; }}
.footer-grid a:hover {{ color: var(--primary); text-decoration: underline; }}
.legal {{ margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--muted); font-size: .9rem; }}
@media (max-width: 900px) {{
  .hero-split .grid, .split, .detail {{ grid-template-columns: 1fr; }}
  .split.reverse .split-media {{ order: 0; }}
  .aside {{ position: static; }}
  .menu-btn {{ display: inline-block; }}
  .nav {{ display: none; width: 100%; }}
  .nav.open {{ display: block; }}
  .site-header .bar {{ flex-wrap: wrap; padding-block: .6rem; }}
  .nav ul {{ flex-direction: column; padding-bottom: .8rem; }}
}}
@media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation: none !important; transition: none !important; scroll-behavior: auto !important; }} }}
@media (scripting: none) {{ .menu-btn {{ display: none; }} .nav {{ display: block !important; }} }}
"""
