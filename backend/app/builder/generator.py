"""ProjectSpec -> project files. Pure and deterministic: the same spec always yields the same files.

That determinism is what makes incremental edits cheap and safe: an edit changes the spec (or a few of its routes), the
generator is run again, and only files whose content actually changed differ. Files a person has edited by hand are
passed in as `locked` and are never overwritten.
"""

from __future__ import annotations

import json
import posixpath
import re
from datetime import datetime, timezone
from html import escape as e

from app.builder.art import art_svg
from app.builder.clientjs import APP_JS
from app.builder.design import build_css, resolve_design
from app.builder.spec import EntitySpec, FieldSpec, ProjectSpec, RoutePlan, SectionKind, SectionPlan
from app.builder.workspace import Workspace

_SAFE_HTTPS = re.compile(r"^https://[^\s<>\"']+$", re.I)


# ---------------------------------------------------------------- paths
def route_dir(path: str, slug: str | None = None) -> str:
    """Directory of a route's page ('' for home). Detail routes need the record slug."""
    if path == "/":
        return ""
    p = path.strip("/")
    if p.endswith("[slug]"):
        if slug is None:
            raise ValueError("detail routes need a record slug")
        p = p[: -len("[slug]")] + slug
    return p


def route_file(path: str, slug: str | None = None) -> str:
    d = route_dir(path, slug)
    return f"{d}/index.html" if d else "index.html"


class _Ctx:
    def __init__(self, spec: ProjectSpec, file: str) -> None:
        self.spec = spec
        self.file = file
        self.base = posixpath.dirname(file) or "."

    def rel(self, target: str) -> str:
        return posixpath.relpath(target, self.base)

    def page(self, path: str, slug: str | None = None) -> str:
        d = route_dir(path, slug)
        r = posixpath.relpath(d or ".", self.base)
        return "./" if r == "." else r + "/"

    def asset(self, name: str) -> str:
        return self.rel(f"assets/{name}")

    def href(self, h: str) -> str:
        if h.startswith("/"):
            return self.page(h)
        return h

    def link_attrs(self, h: str) -> str:
        return ' rel="noopener noreferrer"' if h.startswith("https://") else ""


# ---------------------------------------------------------------- entities
def _fmt_price(v) -> str:
    try:
        n = float(v)
        return f"${n:,.0f}" if n == int(n) else f"${n:,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _record_art(ent: EntitySpec, rec: dict) -> str:
    return f"assets/art/{ent.name}-{rec.get('slug', 'item')}.svg"


def _rec_image(ctx: _Ctx, ent: EntitySpec, rec: dict) -> str:
    img = rec.get("image")
    if isinstance(img, str) and _SAFE_HTTPS.match(img):
        return img
    return ctx.rel(_record_art(ent, rec))


def _detail_route(spec: ProjectSpec, entity: str) -> RoutePlan | None:
    return next((r for r in spec.routes if r.is_detail and r.entity == entity), None)


def _card(ctx: _Ctx, ent: EntitySpec, rec: dict, *, saveable: bool) -> str:
    spec = ctx.spec
    detail = _detail_route(spec, ent.name)
    title = e(str(rec.get("title", "")))
    url = ctx.page(detail.path, str(rec["slug"])) if detail else None
    price_field = next((f for f in ent.fields if f.type == "price"), None)
    tags_field = next((f for f in ent.fields if f.type == "tags"), None)
    texts = [f for f in ent.fields if f.type in ("text", "longtext") and f.name not in ("description", "summary", "body")]
    attrs = [f'data-card data-id="{e(str(rec["slug"]))}"']
    haystack = " ".join(str(rec.get(f.name, "")) for f in ent.fields if f.searchable or f.type in ("text", "tags")) + " " + str(rec.get("title", ""))
    attrs.append(f'data-text="{e(haystack.lower())}"')
    for f in ent.fields:
        if f.filterable or f.sortable:
            val = rec.get(f.name, "")
            val = "|".join(val) if isinstance(val, list) else val
            attrs.append(f'data-f-{f.name}="{e(str(val))}"')
    attrs.append(f'data-f-title="{e(str(rec.get("title", "")).lower())}"')
    media = f'<img class="art" src="{e(_rec_image(ctx, ent, rec))}" alt="" loading="lazy" width="800" height="600">'
    if saveable:
        media += f'<button class="fav" type="button" data-save="{e(str(rec["slug"]))}" aria-pressed="false" aria-label="Save {title}">♥</button>'
    meta = []
    if price_field and rec.get(price_field.name) not in (None, ""):
        meta.append(f'<p class="price">{e(_fmt_price(rec[price_field.name]))}</p>')
    for f in texts[:2]:
        if rec.get(f.name):
            meta.append(f"<p>{e(str(rec[f.name]))}</p>")
    if tags_field and isinstance(rec.get(tags_field.name), list):
        meta.append('<div class="tags">' + "".join(f'<span class="tag">{e(t)}</span>' for t in rec[tags_field.name][:4]) + "</div>")
    heading = f'<a href="{e(url)}">{title}</a>' if url else title
    return f'<article class="card" {" ".join(attrs)}><div class="card-media">{media}</div><div class="card-body"><h3>{heading}</h3>{"".join(meta)}</div></article>'


def _thresholds(values: list[float]) -> list[float]:
    vs = sorted(set(values))
    if len(vs) <= 1:
        return vs
    out = []
    for q in (0.25, 0.5, 0.75, 1.0):
        out.append(vs[min(len(vs) - 1, int(round(q * (len(vs) - 1))))])
    return sorted(set(out))


def _toolbar(ent: EntitySpec) -> str:
    parts = [f'<input type="search" data-search placeholder="Search {e(ent.name)}" aria-label="Search {e(ent.name)}">']
    for f in ent.fields:
        if not f.filterable:
            continue
        label = f.name.replace("_", " ").title()
        if f.type in ("number", "price"):
            vals = []
            for r in ent.records:
                try:
                    vals.append(float(r.get(f.name)))
                except (TypeError, ValueError):
                    pass
            opts = "".join(f'<option value="{v:g}">Up to {e(_fmt_price(v) if f.type == "price" else f"{v:g}")}</option>' for v in _thresholds(vals))
            parts.append(f'<select data-filter="{e(f.name)}" data-kind="max" aria-label="{e(label)}"><option value="">Any {e(label.lower())}</option>{opts}</select>')
        else:
            vals = sorted({x for r in ent.records for x in (r.get(f.name) if isinstance(r.get(f.name), list) else [r.get(f.name)]) if x not in (None, "")})
            opts = "".join(f'<option value="{e(str(v))}">{e(str(v))}</option>' for v in vals)
            parts.append(f'<select data-filter="{e(f.name)}" data-kind="eq" aria-label="{e(label)}"><option value="">All {e(label.lower())}</option>{opts}</select>')
    sorts = [f for f in ent.fields if f.sortable]
    if sorts:
        opts = '<option value="">Sort</option>'
        for f in sorts:
            lab = f.name.replace("_", " ").title()
            opts += f'<option value="{e(f.name)}:asc">{e(lab)}: low to high</option><option value="{e(f.name)}:desc">{e(lab)}: high to low</option>'
        parts.append(f'<select data-sort aria-label="Sort results">{opts}</select>')
    return '<div class="toolbar">' + "".join(parts) + "</div>"


# ---------------------------------------------------------------- sections
def _head(ctx: _Ctx, s: SectionPlan, *, tag: str = "h2") -> str:
    if not (s.heading or s.eyebrow or s.body):
        return ""
    return (
        '<div class="section-head">'
        + (f'<span class="eyebrow">{e(s.eyebrow)}</span>' if s.eyebrow else "")
        + (f"<{tag}>{e(s.heading)}</{tag}>" if s.heading else "")
        + (f'<p class="lead">{e(s.body)}</p>' if s.body else "")
        + "</div>"
    )


def _cta_button(ctx: _Ctx, s: SectionPlan, cls: str = "btn-primary") -> str:
    if not (s.cta_label and s.cta_href):
        return ""
    return f'<a class="btn {cls}" href="{e(ctx.href(s.cta_href))}"{ctx.link_attrs(s.cta_href)}>{e(s.cta_label)}</a>'


def _example(s: SectionPlan) -> str:
    return '<p class="example-note">Example content — replace with your own before publishing</p>' if s.example else ""


def _wrap(inner: str, *, alt: bool, cls: str = "") -> str:
    return f'<section class="section{" alt" if alt else ""}{(" " + cls) if cls else ""}"><div class="container">{inner}</div></section>'


def _render_section(ctx: _Ctx, route: RoutePlan, s: SectionPlan, idx: int, alt: bool, *, first: bool, record: dict | None = None) -> str:
    spec = ctx.spec
    k = s.kind
    ent = spec.entity(s.entity or route.entity or "") if (s.entity or route.entity) else None

    if k is SectionKind.HERO:
        layout = resolve_design(spec.design).hero_layout
        art = ""
        if layout in ("split", "stacked"):
            art = f'<div class="hero-art"><img src="{e(ctx.asset("art/hero.svg"))}" alt="" width="800" height="600"></div>'
        second = ""
        if s.blocks and s.blocks[0].meta and (s.blocks[0].meta.startswith("/") or s.blocks[0].meta.startswith("https://")):
            second = f'<a class="btn btn-ghost" href="{e(ctx.href(s.blocks[0].meta))}">{e(s.blocks[0].title)}</a>'
        text = (
            (f'<span class="eyebrow">{e(s.eyebrow)}</span>' if s.eyebrow else "")
            + f"<h1>{e(s.heading or spec.name)}</h1>"
            + (f'<p class="lead">{e(s.body)}</p>' if s.body else "")
            + (f'<div class="actions">{_cta_button(ctx, s)}{second}</div>' if (s.cta_label or second) else "")
            + _example(s)
        )
        return f'<section class="hero hero-{layout}"><div class="container grid"><div>{text}</div>{art}</div></section>'

    if k is SectionKind.LOGOS:
        items = "".join(f"<li>{e(b.title)}</li>" for b in s.blocks)
        return f'<section class="facts" aria-label="At a glance"><div class="container"><ul>{items}</ul></div></section>' if items else ""

    if k is SectionKind.FEATURES:
        cards = "".join(
            f'<article class="card"><div class="card-body"><span class="icon" aria-hidden="true">{e((b.meta or b.title)[:1].upper())}</span><h3>{e(b.title)}</h3>'
            + (f"<p>{e(b.body)}</p>" if b.body else "")
            + "</div></article>"
            for b in s.blocks
        )
        return _wrap(_head(ctx, s) + f'<div class="grid-cards">{cards}</div>' + _example(s), alt=alt)

    if k is SectionKind.SPLIT:
        bullets = "".join(f"<li>{e(i)}</li>" for b in s.blocks for i in b.items) or "".join(f"<li>{e(b.title)}</li>" for b in s.blocks)
        slug = f"split-{idx}"
        media = f'<div class="split-media"><img src="{e(ctx.asset(f"art/{slug}.svg"))}" alt="" loading="lazy" width="800" height="600"></div>'
        text = _head(ctx, s) + (f'<ul class="checks">{bullets}</ul>' if bullets else "") + (f'<div class="actions">{_cta_button(ctx, s)}</div>' if s.cta_label else "") + _example(s)
        return _wrap(f'<div class="split{" reverse" if idx % 2 else ""}"><div>{text}</div>{media}</div>', alt=alt)

    if k is SectionKind.STEPS:
        items = "".join(f"<li><h3>{e(b.title)}</h3>{f'<p>{e(b.body)}</p>' if b.body else ''}</li>" for b in s.blocks)
        return _wrap(_head(ctx, s) + f'<ol class="steps">{items}</ol>' + _example(s), alt=alt)

    if k is SectionKind.PRICING:
        plans = ""
        for i, b in enumerate(s.blocks):
            featured = " featured" if i == 1 and len(s.blocks) >= 3 else ""
            plans += (
                f'<article class="card plan{featured}"><h3>{e(b.title)}</h3><p class="amount">{e(b.meta or "")}</p>'
                + (f"<p>{e(b.body)}</p>" if b.body else "")
                + (f'<ul class="checks">{"".join(f"<li>{e(x)}</li>" for x in b.items)}</ul>' if b.items else "")
                + (f'<a class="btn {"btn-primary" if featured else "btn-ghost"}" href="{e(ctx.href(s.cta_href or "/contact"))}">{e(s.cta_label or "Get started")}</a>' if (s.cta_href and s.cta_href.startswith("/") and spec.route(s.cta_href)) or s.cta_href else "")
                + "</article>"
            )
        return _wrap(_head(ctx, s) + f'<div class="plans">{plans}</div>' + _example(s), alt=alt)

    if k is SectionKind.FAQ:
        items = "".join(f"<details><summary>{e(b.title)}</summary><p>{e(b.body or '')}</p></details>" for b in s.blocks)
        return _wrap(_head(ctx, s) + f'<div class="faq">{items}</div>' + _example(s), alt=alt)

    if k is SectionKind.CTA:
        return (
            '<section class="section tight"><div class="container"><div class="cta"><div>'
            + (f"<h2>{e(s.heading)}</h2>" if s.heading else "")
            + (f"<p>{e(s.body)}</p>" if s.body else "")
            + f"</div>{_cta_button(ctx, s)}</div></div></section>"
        )

    if k is SectionKind.CONTENT:
        paras = "".join(f"<p>{e(b.body)}</p>" for b in s.blocks if b.body)
        heads = "".join(f"<h3>{e(b.title)}</h3><p>{e(b.body or '')}</p>" for b in s.blocks if b.body and b.title)
        return _wrap(_head(ctx, s) + f'<div class="article">{heads or paras}</div>' + _example(s), alt=alt)

    if k is SectionKind.FORM:
        fields = ""
        extra = [b for b in s.blocks]
        base = [("name", "Your name", "text", True), ("email", "Email address", "email", True)]
        for name, label, typ, req in base:
            fields += f'<div class="field"><label for="f-{name}">{label}</label><input id="f-{name}" name="{name}" type="{typ}" autocomplete="{"name" if name == "name" else "email"}"{" required" if req else ""}><span class="error" data-error-for="{name}"></span></div>'
        for b in extra:
            nm = re.sub(r"[^a-z0-9]+", "_", b.title.lower()).strip("_")[:30] or "field"
            fields += f'<div class="field"><label for="f-{nm}">{e(b.title)}</label><input id="f-{nm}" name="{nm}" type="text"><span class="error" data-error-for="{nm}"></span></div>'
        fields += '<div class="field"><label for="f-message">How can we help?</label><textarea id="f-message" name="message" rows="5" required></textarea><span class="error" data-error-for="message"></span></div>'
        form = (
            f'<form class="form" data-enquiry novalidate>{fields}<input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">'
            f'<button class="btn btn-primary" type="submit">{e(s.cta_label or "Send message")}</button><p class="form-status" data-status role="status" aria-live="polite" hidden></p></form>'
        )
        return _wrap(_head(ctx, s) + form, alt=alt)

    if k in (SectionKind.COLLECTION, SectionKind.GALLERY, SectionKind.ARTICLE_LIST, SectionKind.MENU) and ent:
        saveable = any(f.key == "saved_items" for f in spec.features) and k is SectionKind.COLLECTION
        cards = "".join(_card(ctx, ent, r, saveable=saveable) for r in ent.records)
        toolbar = _toolbar(ent) if k is SectionKind.COLLECTION and any(f.filterable or f.sortable for f in ent.fields) or k is SectionKind.COLLECTION else ""
        count = f'<p class="result-count" data-count aria-live="polite">{len(ent.records)} results</p>' if k is SectionKind.COLLECTION else ""
        empty = '<p class="empty" data-empty hidden>Nothing matches yet — try removing a filter.</p>'
        if route.path == "/saved":
            body = f'<div data-saved-view><div class="grid-cards" data-items>{cards}</div><p class="empty" data-empty hidden>You have not saved anything yet. Use the heart on any card to keep it here.</p></div>'
        else:
            body = f'<div data-collection>{toolbar}{count}<div class="grid-cards" data-items>{cards}</div>{empty}</div>'
        return _wrap(_head(ctx, s) + body + _example(s), alt=alt)

    if k is SectionKind.DETAIL and ent and record is not None:
        crumb = ""
        lst = next((r for r in spec.routes if r.entity == ent.name and not r.is_detail), None)
        if lst:
            crumb = f'<p class="crumbs"><a href="{e(ctx.page(lst.path))}">{e(lst.title)}</a> / {e(str(record.get("title", "")))}</p>'
        specs = ""
        for f in ent.fields:
            v = record.get(f.name)
            if v in (None, "") or f.name in ("description", "summary", "body", "image"):
                continue
            v = _fmt_price(v) if f.type == "price" else (", ".join(v) if isinstance(v, list) else v)
            specs += f"<dt>{e(f.name.replace('_', ' ').title())}</dt><dd>{e(str(v))}</dd>"
        desc = next((record[n] for n in ("description", "summary", "body") if record.get(n)), "")
        form_fields = (
            '<div class="field"><label for="d-name">Your name</label><input id="d-name" name="name" required><span class="error" data-error-for="name"></span></div>'
            '<div class="field"><label for="d-email">Email address</label><input id="d-email" name="email" type="email" required><span class="error" data-error-for="email"></span></div>'
            f'<input type="hidden" name="subject" value="Enquiry about {e(str(record.get("title", "")))}">'
            '<div class="field"><label for="d-message">Message</label><textarea id="d-message" name="message" rows="4" required></textarea><span class="error" data-error-for="message"></span></div>'
        )
        img = e(_rec_image(ctx, ent, record))
        return _wrap(
            f'{crumb}<div class="detail"><div><div class="detail-art"><img src="{img}" alt="" width="800" height="600"></div><h2>About this {e(ent.singular.lower())}</h2>'
            f'<p class="lead">{e(str(desc))}</p><dl class="spec-list">{specs}</dl></div>'
            f'<aside class="card aside card-body"><h3>Interested?</h3><p>Send an enquiry and we will get back to you.</p>'
            f'<form class="form" data-enquiry novalidate>{form_fields}<input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">'
            f'<button class="btn btn-primary" type="submit">Send enquiry</button><p class="form-status" data-status role="status" aria-live="polite" hidden></p></form></aside></div>',
            alt=alt,
        )

    if k is SectionKind.ARTICLE and ent and record is not None:
        body = str(record.get("body") or record.get("summary") or "")
        paras = "".join(f"<p>{e(p)}</p>" for p in re.split(r"\n\s*\n|(?<=[.!?])\s{2,}", body) if p.strip())
        lst = next((r for r in spec.routes if r.entity == ent.name and not r.is_detail), None)
        crumb = f'<p class="crumbs"><a href="{e(ctx.page(lst.path))}">{e(lst.title)}</a></p>' if lst else ""
        date = f'<p class="tag">{e(str(record.get("date")))}</p>' if record.get("date") else ""
        return _wrap(f'{crumb}<div class="article">{date}<h2>{e(str(record.get("title", "")))}</h2>{paras}</div>', alt=alt)

    if k is SectionKind.HOURS:
        rows = "".join(f"<tr><th scope='row'>{e(b.title)}</th><td>{e(b.body or '')}</td></tr>" for b in s.blocks)
        return _wrap(_head(ctx, s) + f'<table class="hours"><tbody>{rows}</tbody></table>' + _example(s), alt=alt)

    if k is SectionKind.TEAM:
        cards = "".join(
            f'<article class="card"><div class="card-body"><span class="icon" aria-hidden="true">{e(b.title[:1].upper())}</span><h3>{e(b.title)}</h3>'
            + (f"<p>{e(b.meta)}</p>" if b.meta else "") + (f"<p>{e(b.body)}</p>" if b.body else "") + "</div></article>"
            for b in s.blocks
        )
        return _wrap(_head(ctx, s) + f'<div class="grid-cards">{cards}</div>' + _example(s), alt=alt)

    if k is SectionKind.DASHBOARD and ent:
        price = next((f for f in ent.fields if f.type in ("price", "number")), None)
        cols = [("title", "Name")] + [(f.name, f.name.replace("_", " ").title()) for f in ent.fields[:4]]
        head = "".join(f"<th scope='col'>{e(c[1])}</th>" for c in cols)
        rows = "".join("<tr>" + "".join(f"<td>{e(', '.join(r.get(c[0], [])) if isinstance(r.get(c[0]), list) else str(r.get(c[0], '')))}</td>" for c in cols) + "</tr>" for r in ent.records)
        sum_attr = f' data-sum="{e(price.name)}"' if price else ""
        kpis = (
            f'<div class="kpis"><div class="card kpi"><span>Total {e(ent.name)}</span><strong data-kpi="count">{len(ent.records)}</strong></div>'
            + (f'<div class="card kpi"><span>Total {e(price.name)}</span><strong data-kpi="sum">—</strong></div><div class="card kpi"><span>Average {e(price.name)}</span><strong data-kpi="avg">—</strong></div>' if price else "")
            + "</div>"
        )
        return _wrap(f'<div data-dashboard data-src="{e(ctx.rel(f"data/{ent.name}.json"))}"{sum_attr}>{_head(ctx, s)}{kpis}<div class="table-wrap"><table class="data"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div></div>' + _example(s), alt=alt)

    return ""


# ---------------------------------------------------------------- pages
def _nav(ctx: _Ctx, current: str) -> str:
    items = ""
    for r in ctx.spec.nav_routes():
        cur = ' aria-current="page"' if (r.path == current or (current.startswith(r.path + "/") and r.path != "/")) else ""
        items += f'<li><a href="{e(ctx.page(r.path))}"{cur}>{e(r.nav_label or r.title)}</a></li>'
    return items


def _footer(ctx: _Ctx) -> str:
    spec = ctx.spec
    links = "".join(f'<li><a href="{e(ctx.page(r.path))}">{e(r.nav_label or r.title)}</a></li>' for r in spec.nav_routes())
    contact = f'<li><a href="mailto:{e(spec.contact_email)}">{e(spec.contact_email)}</a></li>' if spec.contact_email else ""
    contact_col = f'<div><h2>Contact</h2><ul>{contact}</ul></div>' if contact else ""
    return (
        f'<footer class="site-footer"><div class="container"><div class="footer-grid"><div><a class="brand" href="{e(ctx.page("/"))}"><span class="brand-mark" aria-hidden="true">{e(spec.name[:1].upper())}</span>{e(spec.name)}</a>'
        + (f'<p class="lead foot-lead">{e(spec.tagline)}</p>' if spec.tagline else "")
        + f'</div><div><h2>Pages</h2><ul>{links}</ul></div>{contact_col}</div>'
        f'<p class="legal">© <span data-year>{datetime.now(timezone.utc).year}</span> {e(spec.name)}. All rights reserved.</p></div></footer>'
    )


def _document(spec: ProjectSpec, file: str, route: RoutePlan, title: str, body: str, *, description: str) -> str:
    ctx = _Ctx(spec, file)
    full_title = spec.name if route.path == "/" else f"{title} — {spec.name}"
    return (
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(full_title)}</title>"
        f'<meta name="description" content="{e(description[:300])}">'
        f'<meta property="og:title" content="{e(full_title)}"><meta property="og:description" content="{e(description[:300])}">'
        f'<meta name="klaros-enquiry" content=""><link rel="stylesheet" href="{e(ctx.asset("styles.css"))}"></head>'
        f'<body><a class="skip" href="#main">Skip to content</a>'
        f'<header class="site-header"><div class="container bar"><a class="brand" href="{e(ctx.page("/"))}"><span class="brand-mark" aria-hidden="true">{e(spec.name[:1].upper())}</span>{e(spec.name)}</a>'
        f'<button class="menu-btn" type="button" aria-expanded="false" aria-controls="site-nav">Menu</button>'
        f'<nav class="nav" id="site-nav" aria-label="Main"><ul>{_nav(ctx, route.path)}</ul></nav></div></header>'
        f'<main id="main">{body}</main>{_footer(ctx)}<script src="{e(ctx.asset("app.js"))}" defer></script></body></html>\n'
    )


def _page_body(spec: ProjectSpec, route: RoutePlan, file: str, record: dict | None) -> str:
    ctx = _Ctx(spec, file)
    out: list[str] = []
    sections = list(route.sections)
    if not sections or sections[0].kind is not SectionKind.HERO:
        title = str(record.get("title", route.title)) if record else route.title
        lead = "" if record else route.purpose
        out.append(
            f'<section class="section tight page-head"><div class="container"><div class="section-head"><h1>{e(title)}</h1>'
            + (f'<p class="lead">{e(lead)}</p>' if lead else "")
            + "</div></div></section>"
        )
    alt = False
    for i, s in enumerate(sections):
        html = _render_section(ctx, route, s, i, alt, first=(i == 0), record=record)
        if html:
            out.append(html)
            if s.kind not in (SectionKind.HERO, SectionKind.LOGOS, SectionKind.CTA):
                alt = not alt
    return "".join(out)


def generate_project(spec: ProjectSpec, *, existing: Workspace | None = None, locked: set[str] | None = None) -> Workspace:
    """Build every file the spec describes. `locked` paths (hand-edited files) are carried over untouched."""
    locked = locked or set()
    ws = Workspace()
    design = resolve_design(spec.design)
    assert design.palette is not None

    def put(path: str, content: str) -> None:
        if path in locked and existing is not None and existing.exists(path):
            ws.write(path, existing.read(path))
        else:
            ws.write(path, content)

    put("assets/styles.css", build_css(spec.design))
    put("assets/app.js", APP_JS)
    put("assets/art/hero.svg", art_svg(spec.name + "hero", design.palette, spec.name))

    for ent in spec.entities:
        data = []
        for rec in ent.records:
            r = dict(rec)
            img = r.get("image")
            r["image"] = img if isinstance(img, str) and _SAFE_HTTPS.match(img) else _record_art(ent, rec)
            data.append(r)
            if not (isinstance(img, str) and _SAFE_HTTPS.match(img)):
                put(_record_art(ent, rec), art_svg(f"{ent.name}-{rec.get('slug')}", design.palette, str(rec.get("title", ""))))
        put(f"data/{ent.name}.json", json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))  # sorted: Postgres JSONB does not keep key order, and one spec must give the same bytes

    descr_default = spec.summary or spec.tagline or spec.name
    split_idx_seen: set[int] = set()
    for route in spec.routes:
        if route.is_detail:
            ent = spec.entity(route.entity or "")
            for rec in (ent.records if ent else []):
                file = route_file(route.path, str(rec["slug"]))
                body = _page_body(spec, route, file, rec)
                put(file, _document(spec, file, route, str(rec.get("title", route.title)), body, description=str(rec.get("summary") or rec.get("description") or descr_default)))
        else:
            file = route_file(route.path)
            body = _page_body(spec, route, file, None)
            put(file, _document(spec, file, route, route.title, body, description=route.purpose or descr_default))
        for i, s in enumerate(route.sections):
            if s.kind is SectionKind.SPLIT:
                put(f"assets/art/split-{i}.svg", art_svg(f"{spec.name}{route.path}{i}", design.palette, s.heading or spec.name))

    # 404 page, project notes
    nf = RoutePlan(path="/", title="Page not found", purpose="")
    nf_ctx_body = '<section class="section"><div class="container"><div class="section-head"><h1>Page not found</h1><p class="lead">That page does not exist. Try the menu above.</p></div></div></section>'
    put("404.html", _document(spec, "404.html", nf, "Page not found", nf_ctx_body, description="Page not found"))
    notes = [f"# {spec.name}", "", spec.summary or spec.tagline, "", "## Pages", ""]
    notes += [f"- `{r.path}` — {r.title}" for r in spec.routes]
    notes += ["", "## Features", ""] + [f"- **{f.label}** — {f.status.value}{(': ' + f.note) if f.note else ''}" for f in spec.features]
    notes += ["", "## Running it", "", "This is a static site (HTML, CSS and JavaScript). Open `index.html` or serve the folder with any static host.", "Forms send to the enquiry endpoint set in the `klaros-enquiry` meta tag when the site is published from Klaros."]
    put("README.md", "\n".join(notes) + "\n")
    return ws
