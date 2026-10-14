(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === "text") n.textContent = attrs[k]; else n.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }

  var C = {
    tr: { name: "Turkey", iso: "TR", cities: "Istanbul, Antalya, Bursa, Ankara", save: "50–70%", note: "typical saving vs US" },
    th: { name: "Thailand", iso: "TH", cities: "Bangkok, Phuket", save: "50–70%", note: "typical saving vs US" },
    kr: { name: "South Korea", iso: "KR", cities: "Seoul", save: "Varies", note: "quoted by clinic" },
    "in": { name: "India", iso: "IN", cities: "Delhi, Gurugram, Ahmedabad", save: "75–80%", note: "typical saving vs US" }
  };
  var LISTED = {
    tr: ["Medical Park Bahçelievler", "Vanity Cosmetic Surgery", "Estetik International"],
    th: ["Bumrungrad International", "Bangpakok 9 International", "MedPark Hospital"],
    kr: ["Asan Medical Center", "VG Plastic Surgery", "ID Hospital"],
    "in": ["Apollo Hospital", "Max Smart Super Speciality", "Eugenix Hair Sciences"]
  };
  var T = {
    dental: { label: "Dental", order: ["tr", "th", "in", "kr"], why: {
      tr: "Antalya and Istanbul pair dental work with recovery on the coast or in the city.",
      th: "Known for dental and cosmetic care, with recovery in Bangkok or Phuket.",
      "in": "Among the lowest prices of the four, at large accredited hospitals.",
      kr: "Best known for cosmetic surgery, so dental packages are less common." } },
    hair: { label: "Hair transplant", order: ["tr", "in", "th", "kr"], why: {
      tr: "Roughly half of Turkey's medical visitors come for hair transplants, so clinics here handle them at volume.",
      "in": "Dedicated hair clinics, such as Eugenix Hair Sciences, at the biggest savings of the four.",
      th: "Less of a specialty than in Turkey, with the option to recover in Bangkok or Phuket.",
      kr: "Offered by Seoul cosmetic clinics, usually alongside other procedures." } },
    aesthetic: { label: "Aesthetic surgery", order: ["kr", "tr", "th", "in"], why: {
      kr: "The global centre of plastic and cosmetic surgery, built on K-beauty.",
      tr: "Istanbul surgeons for rhinoplasty and body contouring at 50–70% below US prices.",
      th: "Cosmetic procedures at JCI-accredited hospitals, with resort-style recovery.",
      "in": "Facial and body procedures at the biggest savings of the four." } }
  };

  /* nav */
  var menuBtn = $("#menuBtn"), nav = $("#nav");
  function setMenu(open) { nav.setAttribute("data-open", String(open)); menuBtn.setAttribute("aria-expanded", String(open)); }
  menuBtn.addEventListener("click", function () { setMenu(nav.getAttribute("data-open") !== "true"); });
  $$("a", nav).forEach(function (a) { a.addEventListener("click", function () { setMenu(false); }); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });

  /* finder */
  var treatment = "dental", openKey = "tr";
  var seg = $("#seg"), rows = $("#rows");
  function pickTreatment(t, focus) {
    treatment = t; openKey = T[t].order[0];
    var btns = $$("button", seg);
    btns.forEach(function (b, i) {
      var on = b.getAttribute("data-t") === t;
      b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1;
      if (on) { seg.setAttribute("data-i", String(i)); if (focus) b.focus(); }
    });
    var sel = $("#fTreatment"); if (sel) sel.value = T[t].label;
    renderRows();
  }
  function renderRows() {
    while (rows.firstChild) rows.removeChild(rows.firstChild);
    T[treatment].order.forEach(function (k) {
      var c = C[k], isOpen = k === openKey;
      var li = el("li", { "class": "row", "data-k": k, "data-open": String(isOpen) });
      var head = el("button", { "class": "row-btn", type: "button", "aria-expanded": String(isOpen), "aria-controls": "m-" + k }, [
        el("span", { "class": "bar" }),
        el("span", {}, [el("span", { "class": "row-name", text: c.name }), el("span", { "class": "row-cities", text: c.cities })]),
        el("span", { "class": "row-save" }, [document.createTextNode(c.save), el("small", { text: c.note })])
      ]);
      head.addEventListener("click", function () { openKey = isOpen ? "" : k; renderRows(); });
      var ask = el("button", { "class": "btn btn-sm", type: "button", text: "Ask about " + c.name });
      ask.addEventListener("click", function () { var f = $("#fCountry"); if (f) f.value = c.iso; location.hash = "quote"; });
      var inner = el("div", {}, [el("div", { "class": "more-in" }, [
        el("p", { "class": "why", text: T[treatment].why[k] }),
        el("p", { "class": "listed", text: "Listed here: " + LISTED[k].join(", ") }),
        el("div", { "class": "actions" }, [ask])
      ])]);
      if (!isOpen) inner.setAttribute("inert", "");
      li.appendChild(head); li.appendChild(el("div", { "class": "more", id: "m-" + k }, [inner]));
      rows.appendChild(li);
    });
  }
  $$("button", seg).forEach(function (b, i, all) {
    b.addEventListener("click", function () { pickTreatment(b.getAttribute("data-t")); });
    b.addEventListener("keydown", function (e) {
      var d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (d) { e.preventDefault(); pickTreatment(all[(i + d + all.length) % all.length].getAttribute("data-t"), true); }
    });
  });
  $$("[data-pick]").forEach(function (b) {
    b.addEventListener("click", function () { pickTreatment(b.getAttribute("data-pick")); var f = $("#finder"); if (f) f.scrollIntoView({ behavior: "smooth", block: "center" }); });
  });
  pickTreatment("dental");

  /* hospitals filter */
  var chips = $$(".chip"), hosp = $$("#hosp li");
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var f = chip.getAttribute("data-f");
      chips.forEach(function (c) { c.setAttribute("aria-pressed", String(c === chip)); });
      hosp.forEach(function (li) { li.hidden = f !== "all" && li.getAttribute("data-c") !== f; });
    });
  });

  /* quote form: sends a real lead to Klaros when the site is published; asks for consent when the business requires it */
  var apiMeta = $('meta[name="klaros-api"]'), tenantMeta = $('meta[name="klaros-tenant"]');
  var api = apiMeta ? apiMeta.getAttribute("content") : "", tenant = tenantMeta ? tenantMeta.getAttribute("content") : "";
  var form = $("#quoteForm"), statusEl = $("#status"), submit = $("#submitBtn"), box = $("#consentBox"), boxText = $("#consentText"), boxInput = $("#fConsent");
  var wording = null, consentRequired = false, ready = !api || !tenant; /* not published yet: submit explains itself */
  function say(state, msg) { statusEl.setAttribute("data-state", state); statusEl.textContent = msg; }
  if (api && tenant) {
    submit.disabled = true;
    fetch(api + "/api/v1/public/leads/" + encodeURIComponent(tenant) + "/consent")
      .then(function (r) { if (!r.ok) throw new Error("status"); return r.json(); })
      .then(function (d) {
        consentRequired = !!d.required;
        if (consentRequired && d.wording) { wording = d.wording; boxText.textContent = d.wording.text; box.hidden = false; ready = true; }
        else if (consentRequired) { say("error", "This form is not open yet. Please check back soon."); }
        else { ready = true; }
      })
      .catch(function () { say("error", "The form could not load. Please refresh the page."); })
      .then(function () { submit.disabled = !ready; });
  }
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var name = $("#fName"), email = $("#fEmail"), ok = true;
    [name, email].forEach(function (i) { i.setAttribute("aria-invalid", "false"); });
    if (!name.value.trim()) { name.setAttribute("aria-invalid", "true"); ok = false; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim())) { email.setAttribute("aria-invalid", "true"); ok = false; }
    if (!ok) { say("error", "Please enter your name and a valid email address."); (name.getAttribute("aria-invalid") === "true" ? name : email).focus(); return; }
    if ($('input[name="website"]', form).value) return; /* honeypot */
    if (!api || !tenant) { say("error", "This form is not connected yet, so nothing was sent."); return; }
    if (consentRequired && !(wording && boxInput.checked)) { say("error", "Please tick the box to agree before sending."); boxInput.focus(); return; }
    var country = $("#fCountry").value;
    var payload = {
      name: name.value.trim(), source: "WEB", email: email.value.trim(),
      service_requested: $("#fTreatment").value,
      description: $("#fNotes").value.trim().slice(0, 2000) || null,
      preferred_destination_country: country || null,
      website: null
    };
    if (consentRequired) payload.consent = { scopes: ["contact", "store_personal_data", "store_medical_information"], wording_version: wording.version };
    submit.disabled = true; say("info", "Sending…");
    fetch(api + "/api/v1/public/leads/" + encodeURIComponent(tenant), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); say("success", "Thank you. We'll email you with options and what each quote includes."); form.reset(); if (boxInput) boxInput.checked = false; })
      .catch(function () { say("error", "Sorry, that did not send. Please try again in a moment."); })
      .then(function () { submit.disabled = !ready; });
  });
})();
