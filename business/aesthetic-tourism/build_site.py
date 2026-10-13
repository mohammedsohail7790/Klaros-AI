"""Builds site/ (the Aesthetic Tourism website) from the content below.

All content is the owner's own, carried over from the original index.html / script.js. This script only lays it out: nothing is added as a fact.
Counts shown in the hero strip are computed from the lists, so they can never drift from what the page actually lists.
Run:  python3 build_site.py
"""
from html import escape as e
from pathlib import Path

OUT = Path(__file__).parent / "site"

COUNTRIES = {
    "tr": dict(name="Turkey", iso="TR", cities="Istanbul, Antalya, Bursa, Ankara", best="Hair transplants, cosmetic surgery, dental", save="50–70%", lo=50, hi=70, acc="40+ JCI-accredited hospitals and a national Health Türkiye programme", note="typical saving vs US"),
    "th": dict(name="Thailand", iso="TH", cities="Bangkok, Phuket", best="Cosmetic and dental care with resort-style recovery", save="50–70%", lo=50, hi=70, acc="60+ JCI-accredited hospitals", note="typical saving vs US"),
    "kr": dict(name="South Korea", iso="KR", cities="Seoul", best="Plastic and cosmetic surgery", save="Varies by clinic", lo=None, hi=None, acc="Government visa facilitation and multilingual patient services", note="quoted by clinic"),
    "in": dict(name="India", iso="IN", cities="Delhi, Gurugram, Ahmedabad", best="Lowest prices, plus complex surgery", save="75–80%", lo=75, hi=80, acc="65+ JCI-accredited healthcare entities", note="typical saving vs US"),
}
HOSPITALS = [
    ("Medical Park Bahçelievler", "tr", "Istanbul", "Multispecialty hospital"), ("Vanity Cosmetic Surgery", "tr", "Istanbul", "Cosmetic surgery clinic"),
    ("Estetik International", "tr", "Istanbul", "Cosmetic surgery clinic"), ("Bumrungrad International", "th", "Bangkok", "Multispecialty hospital"),
    ("Bangpakok 9 International", "th", "Bangkok", "Multispecialty hospital"), ("MedPark Hospital", "th", "Bangkok", "Multispecialty hospital"),
    ("Asan Medical Center", "kr", "Seoul", "Multispecialty hospital"), ("VG Plastic Surgery", "kr", "Seoul", "Cosmetic surgery clinic"),
    ("ID Hospital", "kr", "Seoul", "Cosmetic surgery clinic"), ("Apollo Hospital", "in", "Ahmedabad", "Multispecialty hospital"),
    ("Max Smart Super Speciality", "in", "Delhi", "Multispecialty hospital"), ("Eugenix Hair Sciences", "in", "India", "Hair clinic"),
]
SURGEONS = [
    ("tr", "Levent Acar, MD", "Hair transplant, Turkey"), ("tr", "Semih Tiber Menteşe", "Cosmetic surgery, VM Medical Park, Istanbul. 14 years of experience."),
    ("tr", "Ergin Er", "Cosmetic surgery, Turkey. 34 years of experience."), ("tr", "Bulent Cihantimur", "Estetik International, Istanbul"),
    ("kr", "Sang Hoon Park", "ID Hospital, Seoul"), ("in", "Pradeep Sethi", "Eugenix Hair Sciences, India"),
]
FAQ = [
    ("How do I know a hospital is accredited?", "Look for Joint Commission International (JCI) accreditation and ask the hospital for its certificate. Turkey, Thailand and India each have dozens of JCI-accredited hospitals. Accreditation covers the hospital, so also ask about your surgeon's own training and case history."),
    ("What can a quote leave out?", "Follow-up visits, extra hospital nights, translation, travel insurance and treatment for complications are the usual gaps. Ask for a written quote that lists exactly what is included and what is not."),
    ("Can I combine dental and cosmetic treatment in one trip?", "Often, yes. It depends on your health, how long each procedure takes to heal and what your surgeon recommends. Your surgeon should confirm the plan before you book flights."),
    ("Do I need a visa?", "It depends on your passport. As of early 2026, Turkey allows US, UK and most EU citizens to enter visa-free for up to 90 days, while some nationalities need an e-visa. Rules for Thailand, South Korea and India differ, so check the official government site before you book."),
    ("What happens if something goes wrong after I fly home?", "Ask before you book: how long the hospital keeps in touch, whether follow-up appointments can be remote, and who pays if a revision is needed. Get the answers in writing."),
    ("Is Aesthetic Tourism a medical provider?", "No. We help you compare independent hospitals, clinics and surgeons and request quotes. Your treatment, and your medical decisions, are between you and your surgeon."),
]
TREATMENTS = [
    ("dental", "Dental", "Implants, veneers, crowns and full-mouth restorations. Often combined with a few days of recovery near the coast.", "M12 3c-2.2-1.6-5.5-1-6.5 1.8-.8 2.3.2 4.4.8 6.2.7 2.1.6 5.5 1.7 7 .6.8 1.6.7 2-.4l1.1-3.6c.3-.9 1.9-.9 2.2 0l1.1 3.6c.4 1.1 1.4 1.2 2 .4 1.1-1.5 1-4.9 1.7-7 .6-1.8 1.6-3.9.8-6.2C17.5 2 14.2 1.4 12 3Z"),
    ("hair", "Hair transplant", "Scalp, beard and eyebrow restoration with FUE and DHI techniques. Usually a single procedure with a short stay.", "M12 21c-4 0-6-2.5-6-6 0-2.2 1-3.4 1-5.5C7 6.6 9 3 12 3s5 3.600 5 6.500c0 2.100 1 3.300 1 5.500 0 3.500-2 6-6 6Zm0-5v-5m-2.500 3v-3m5 3v-3"),
    ("aesthetic", "Aesthetic surgery", "Rhinoplasty, tummy tuck, facelift and breast procedures, in hospitals and specialist cosmetic clinics.", "M12 3l1.800 4.700L18.500 9.500l-4.700 1.800L12 16l-1.800-4.700L5.500 9.500l4.700-1.800L12 3Zm7 11l.9 2.100 2.100.9-2.100.9L19 20l-.9-2.100-2.100-.9 2.100-.9.900-2.100Z"),
]
STEPS = [
    ("Tell us what you need", "Your procedure, the countries you're considering, your budget and any photos or records a surgeon should see."),
    ("Compare matched options", "We line up hospitals and surgeons that fit, with a written list of what each quote includes."),
    ("Speak to your surgeon", "Ask your questions in a video consultation before you book anything."),
    ("Travel and treatment", "Flights, hotel, transfers and translation, where your package includes them."),
    ("Recover and follow up", "A written aftercare plan and check-ins once you're home."),
]
QUOTE_GAPS = ["Follow-up visits", "Extra hospital nights", "Translation", "Travel insurance", "Treatment for complications"]


def range_svg(c):
    if c["lo"] is None:
        return '<svg class="range" viewBox="0 0 100 6" role="img" aria-label="Savings vary by clinic"><rect class="trk" width="100" height="6" rx="3"/><rect class="vary" width="100" height="6" rx="3"/></svg>'
    return f'<svg class="range" viewBox="0 0 100 6" role="img" aria-label="Typical saving {c["lo"]} to {c["hi"]} percent"><rect class="trk" width="100" height="6" rx="3"/><rect class="rng" x="{c["lo"]}" width="{c["hi"] - c["lo"]}" height="6" rx="3"/></svg>'


def initials(name):
    parts = [p for p in name.replace(",", "").split() if p not in ("MD",)]
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def build_html():
    n_h, n_s = len(HOSPITALS), len(SURGEONS)
    rows = "".join(
        f'<tr data-c="{k}"><th scope="row"><span class="dot"></span>{e(c["name"])}</th><td>{e(c["best"])}</td><td>{e(c["cities"])}</td>'
        f'<td class="num">{e(c["save"])}{range_svg(c)}</td><td>{e(c["acc"])}</td></tr>' for k, c in COUNTRIES.items()
    )
    treatments = "".join(
        f'<li class="trt"><span class="trt-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg></span>'
        f'<h3>{e(t)}</h3><p class="procs">{e(p)}</p><button class="btn btn-quiet btn-sm go" type="button" data-pick="{k}">Compare countries</button></li>' for k, t, p, path in TREATMENTS
    )
    steps = "".join(f"<li><div><h3>{e(t)}</h3><p>{e(p)}</p></div></li>" for t, p in STEPS)
    gaps = "".join(f"<li>{e(g)}</li>" for g in QUOTE_GAPS)
    chips = '<button class="chip" type="button" data-f="all" aria-pressed="true">All countries</button>' + "".join(f'<button class="chip" type="button" data-f="{k}" aria-pressed="false">{e(c["name"])}</button>' for k, c in COUNTRIES.items())
    hosp = "".join(f'<li data-c="{c}"><span class="d"></span><span><span class="n">{e(n)}</span><span class="t">{e(t)}</span></span><span class="w">{e(city)}, {e(COUNTRIES[c]["name"])}</span></li>' for n, c, city, t in HOSPITALS)
    docs = "".join(f'<li data-c="{c}"><span class="av" aria-hidden="true">{e(initials(n))}</span><div><p class="n">{e(n)}</p><p>{e(d)}</p></div></li>' for c, n, d in SURGEONS)
    faq = "".join(f'<details class="q"><summary>{e(q)}</summary><p>{e(a)}</p></details>' for q, a in FAQ)
    country_opts = '<option value="">Not sure yet</option>' + "".join(f'<option value="{e(c["iso"])}">{e(c["name"])}</option>' for c in COUNTRIES.values())
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>Aesthetic Tourism: compare hospitals, clinics and surgeons abroad</title>
<meta name="description" content="Compare dental, hair transplant and aesthetic surgery in Turkey, Thailand, South Korea and India. See hospitals, surgeons and typical savings side by side.">
<meta property="og:title" content="Aesthetic Tourism: compare hospitals, clinics and surgeons abroad">
<meta property="og:description" content="Compare dental, hair transplant and aesthetic surgery in Turkey, Thailand, South Korea and India.">
<meta name="klaros-api" content="https://klaros-backend.onrender.com">
<meta name="klaros-tenant" content="bea98419-5358-4a5b-ab53-7b9d7df27bba">
<link rel="icon" href="assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="assets/styles.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>

<header class="top">
  <div class="wrap">
    <a class="brand" href="#top"><span class="mark" aria-hidden="true">A</span>Aesthetic Tourism</a>
    <button class="menu-btn" id="menuBtn" type="button" aria-expanded="false" aria-controls="nav">Menu</button>
    <nav class="nav" id="nav" aria-label="Main" data-open="false">
      <a href="#treatments">Treatments</a>
      <a href="#destinations">Destinations</a>
      <a href="#hospitals">Hospitals</a>
      <a href="#surgeons">Surgeons</a>
      <a href="#how">How it works</a>
      <a class="btn btn-sm" href="#quote">Get a quote</a>
    </nav>
  </div>
</header>

<main id="main">
<span id="top"></span>

<section class="hero">
  <div class="wrap hero-grid">
    <div>
      <p class="eyebrow rise d0">Compare before you travel</p>
      <h1><span class="ln rise d1">Pick a procedure.</span><span class="ln rise d2">Compare the countries that do it best.</span></h1>
      <p class="lede rise d3">Dental work, hair transplants and aesthetic surgery in Turkey, Thailand, South Korea and India. See the savings, hospitals and surgeons side by side, then ask for a quote.</p>
      <div class="hero-actions rise d4">
        <a class="btn" href="#quote">Get a quote</a>
        <a class="btn btn-quiet" href="#how">How it works</a>
      </div>
      <dl class="facts rise d5">
        <div><dt>Destinations</dt><dd class="num">{len(COUNTRIES)}</dd></div>
        <div><dt>Hospitals and clinics listed</dt><dd class="num">{n_h}</dd></div>
        <div><dt>Surgeons listed</dt><dd class="num">{n_s}</dd></div>
        <div><dt>Treatment types</dt><dd class="num">{len(TREATMENTS)}</dd></div>
      </dl>
    </div>

    <div class="finder rise d3" id="finder">
      <p class="finder-q" id="finderQ">Which procedure are you considering?</p>
      <div class="seg" role="radiogroup" aria-labelledby="finderQ" id="seg" data-i="0">
        <span class="pill" aria-hidden="true"></span>
        <button type="button" role="radio" aria-checked="true" data-t="dental" tabindex="0">Dental</button>
        <button type="button" role="radio" aria-checked="false" data-t="hair" tabindex="-1">Hair transplant</button>
        <button type="button" role="radio" aria-checked="false" data-t="aesthetic" tabindex="-1">Aesthetic surgery</button>
      </div>
      <ul class="rows" id="rows" aria-live="polite"></ul>
      <p class="foot-note">Savings are typical ranges from published industry sources, compared with US prices. Your written quote is what counts.</p>
    </div>
  </div>
</section>

<section class="sec" id="treatments">
  <div class="wrap">
    <div class="sec-head">
      <p class="eyebrow">Treatments</p>
      <h2>Three kinds of treatment, one place to compare them</h2>
      <p class="lede">Each one has a different best destination. Choose a treatment to see how the four countries line up.</p>
    </div>
    <ul class="trt-grid">{treatments}</ul>
  </div>
</section>

<section class="sec alt" id="destinations">
  <div class="wrap">
    <div class="sec-head">
      <p class="eyebrow">Destinations</p>
      <h2>Four destinations, compared</h2>
      <p class="lede">What each country is known for, where the clinics are, and how its hospitals are accredited.</p>
    </div>
    <div class="tablewrap" tabindex="0" role="region" aria-label="Destination comparison table">
      <table>
        <thead><tr><th scope="col">Country</th><th scope="col">Best known for</th><th scope="col">Main hubs</th><th scope="col">Typical saving vs US</th><th scope="col">Accreditation and support</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p class="foot-note">Savings are estimates from published industry sources and change by clinic, procedure and season. South Korea is quoted clinic by clinic.</p>
  </div>
</section>

<section class="sec" id="how">
  <div class="wrap split">
    <div class="sec-head">
      <p class="eyebrow">How it works</p>
      <h2>From first question to follow-up care</h2>
      <p class="lede">Five steps, and you decide at each one.</p>
    </div>
    <ol class="steps">{steps}</ol>
  </div>
</section>

<section class="sec alt" id="quote-check">
  <div class="wrap split">
    <div class="sec-head">
      <p class="eyebrow">Before you accept a quote</p>
      <h2>What a good quote spells out</h2>
      <p class="lede">A low price can hide gaps. Ask for a written quote that says, for each of these, whether it is included.</p>
    </div>
    <ul class="checks">{gaps}</ul>
  </div>
</section>

<section class="sec" id="hospitals">
  <div class="wrap">
    <div class="sec-head">
      <p class="eyebrow">Hospitals and clinics</p>
      <h2>Hospitals and clinics</h2>
      <p class="lede">Filter by country to see who we list.</p>
    </div>
    <div class="chips" id="chips" role="group" aria-label="Filter by country">{chips}</div>
    <ul class="hosp" id="hosp">{hosp}</ul>
    <p class="foot-note">Listings are for comparison only and are not an endorsement. Details come from public sources and can change, so confirm them directly with the hospital or clinic.</p>
  </div>
</section>

<section class="sec alt" id="surgeons">
  <div class="wrap">
    <div class="sec-head">
      <p class="eyebrow">Surgeons</p>
      <h2>Surgeons</h2>
      <p class="lede">Every surgeon is tied to a hospital or clinic you can look up.</p>
    </div>
    <ul class="docs">{docs}</ul>
  </div>
</section>

<section class="sec" id="faq">
  <div class="wrap">
    <div class="sec-head">
      <p class="eyebrow">Questions</p>
      <h2>Questions people ask before they book</h2>
    </div>
    <div class="faq">{faq}</div>
  </div>
</section>

<section class="sec quote" id="quote">
  <div class="wrap quote-grid">
    <div class="sec-head">
      <p class="eyebrow">Get a quote</p>
      <h2>Tell us what you're planning</h2>
      <p class="lede">Share the procedure and the countries you're leaning towards. We'll come back with options and what each quote includes.</p>
      <p class="safe-note">Please keep your notes brief and don't add detailed medical history or photos here. If anything is urgent or an emergency, contact your local emergency services, not this site.</p>
    </div>
    <form id="quoteForm" novalidate>
      <div class="two">
        <label>Procedure
          <select id="fTreatment" name="treatment">
            <option value="Dental">Dental</option>
            <option value="Hair transplant">Hair transplant</option>
            <option value="Aesthetic surgery">Aesthetic surgery</option>
          </select>
        </label>
        <label>Destination
          <select id="fCountry" name="country">{country_opts}</select>
        </label>
      </div>
      <div class="two">
        <label>Your name<input id="fName" name="name" type="text" autocomplete="name" required></label>
        <label>Email<input id="fEmail" name="email" type="email" autocomplete="email" required></label>
      </div>
      <label>Anything we should know<textarea id="fNotes" name="notes" maxlength="1000" placeholder="Timing, budget, what you'd like changed"></textarea></label>
      <label class="hp">Leave this empty<input name="website" type="text" tabindex="-1" autocomplete="off"></label>
      <div class="consent" id="consentBox" hidden>
        <label class="check"><input id="fConsent" type="checkbox"><span id="consentText"></span></label>
      </div>
      <div><button class="btn" id="submitBtn" type="submit">Request a quote</button></div>
      <p class="status" id="status" role="status" aria-live="polite"></p>
    </form>
  </div>
</section>

</main>

<footer>
  <div class="wrap">
    <div class="foot">
      <div>
        <a class="brand" href="#top"><span class="mark" aria-hidden="true">A</span>Aesthetic Tourism</a>
        <p class="muted small foot-lead">Compare hospitals, clinics and surgeons abroad.</p>
      </div>
      <div>
        <p class="foot-h">Explore</p>
        <ul>
          <li><a href="#treatments">Treatments</a></li>
          <li><a href="#destinations">Destinations</a></li>
          <li><a href="#hospitals">Hospitals</a></li>
          <li><a href="#surgeons">Surgeons</a></li>
        </ul>
      </div>
      <div>
        <p class="foot-h">Get started</p>
        <ul>
          <li><a href="#how">How it works</a></li>
          <li><a href="#faq">Questions</a></li>
          <li><a href="#quote">Get a quote</a></li>
        </ul>
      </div>
    </div>
    <p class="legal">© 2026 Aesthetic Tourism LLC. Aesthetic Tourism connects patients with independent hospitals, clinics and surgeons and does not provide medical care. Nothing on this site is medical advice. Prices and savings are estimates and change by clinic, procedure and season.</p>
  </div>
</footer>

<script src="assets/app.js" defer></script>
</body>
</html>
'''


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(build_html(), encoding="utf-8")
    print("wrote", OUT / "index.html")
