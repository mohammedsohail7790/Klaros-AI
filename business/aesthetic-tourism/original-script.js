(function(){
  "use strict";
  var $ = function(s, r){ return (r || document).querySelector(s); };
  var $$ = function(s, r){ return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var CONTACT_EMAIL = "quotes@aesthetictourism.example"; /* replace with your real inbox */

  var C = {
    tr: { name: "Turkey",      cities: "Istanbul, Antalya, Bursa, Ankara", save: "50–70%", note: "typical saving vs US" },
    th: { name: "Thailand",    cities: "Bangkok, Phuket",                  save: "50–70%", note: "typical saving vs US" },
    kr: { name: "South Korea", cities: "Seoul",                            save: "Varies", note: "quoted by clinic" },
    "in": { name: "India",     cities: "Delhi, Gurugram, Ahmedabad",       save: "75–80%", note: "typical saving vs US" }
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
      kr: "Best known for cosmetic surgery, so dental packages are less common."
    }},
    hair: { label: "Hair transplant", order: ["tr", "in", "th", "kr"], why: {
      tr: "Roughly half of Turkey's medical visitors come for hair transplants, so clinics here handle them at volume.",
      "in": "Dedicated hair clinics, such as Eugenix Hair Sciences, at the biggest savings of the four.",
      th: "Less of a specialty than in Turkey, with the option to recover in Bangkok or Phuket.",
      kr: "Offered by Seoul cosmetic clinics, usually alongside other procedures."
    }},
    aesthetic: { label: "Aesthetic surgery", order: ["kr", "tr", "th", "in"], why: {
      kr: "The global centre of plastic and cosmetic surgery, built on K-beauty.",
      tr: "Istanbul surgeons for rhinoplasty and body contouring at 50–70% below US prices.",
      th: "Cosmetic procedures at JCI-accredited hospitals, with resort-style recovery.",
      "in": "Facial and body procedures at the biggest savings of the four."
    }}
  };
  var H = [
    { n: "Medical Park Bahçelievler", c: "tr", city: "Istanbul", t: "Multispecialty hospital" },
    { n: "Vanity Cosmetic Surgery",   c: "tr", city: "Istanbul", t: "Cosmetic surgery clinic" },
    { n: "Estetik International",     c: "tr", city: "Istanbul", t: "Cosmetic surgery clinic" },
    { n: "Bumrungrad International",  c: "th", city: "Bangkok",  t: "Multispecialty hospital" },
    { n: "Bangpakok 9 International", c: "th", city: "Bangkok",  t: "Multispecialty hospital" },
    { n: "MedPark Hospital",          c: "th", city: "Bangkok",  t: "Multispecialty hospital" },
    { n: "Asan Medical Center",       c: "kr", city: "Seoul",    t: "Multispecialty hospital" },
    { n: "VG Plastic Surgery",        c: "kr", city: "Seoul",    t: "Cosmetic surgery clinic" },
    { n: "ID Hospital",               c: "kr", city: "Seoul",    t: "Cosmetic surgery clinic" },
    { n: "Apollo Hospital",           c: "in", city: "Ahmedabad", t: "Multispecialty hospital" },
    { n: "Max Smart Super Speciality", c: "in", city: "Delhi",   t: "Multispecialty hospital" },
    { n: "Eugenix Hair Sciences",     c: "in", city: "India",    t: "Hair clinic" }
  ];
  var FAQ = [
    ["How do I know a hospital is accredited?", "Look for Joint Commission International (JCI) accreditation and ask the hospital for its certificate. Turkey, Thailand and India each have dozens of JCI-accredited hospitals. Accreditation covers the hospital, so also ask about your surgeon's own training and case history."],
    ["What can a quote leave out?", "Follow-up visits, extra hospital nights, translation, travel insurance and treatment for complications are the usual gaps. Ask for a written quote that lists exactly what is included and what is not."],
    ["Can I combine dental and cosmetic treatment in one trip?", "Often, yes. It depends on your health, how long each procedure takes to heal and what your surgeon recommends. Your surgeon should confirm the plan before you book flights."],
    ["Do I need a visa?", "It depends on your passport. As of early 2026, Turkey allows US, UK and most EU citizens to enter visa-free for up to 90 days, while some nationalities need an e-visa. Rules for Thailand, South Korea and India differ, so check the official government site before you book."],
    ["What happens if something goes wrong after I fly home?", "Ask before you book: how long the hospital keeps in touch, whether follow-up appointments can be remote, and who pays if a revision is needed. Get the answers in writing."],
    ["Is Aesthetic Tourism a medical provider?", "No. We help you compare independent hospitals, clinics and surgeons and request quotes. Your treatment, and your medical decisions, are between you and your surgeon."]
  ];

  /* ---------- finder ---------- */
  var treatment = "dental";
  var openKey = "tr";
  var seg = $("#seg"), rows = $("#rows");

  function rowHTML(k, i, isOpen){
    var c = C[k];
    var listed = LISTED[k].join(", ");
    return '<li class="row" data-k="' + k + '" data-open="' + isOpen + '" style="--i:' + i + ';--c:var(--' + k + ');--c-t:var(--' + k + '-t)">' +
      '<button class="row-btn" type="button" aria-expanded="' + isOpen + '" aria-controls="m-' + k + '">' +
        '<span class="bar"></span>' +
        '<span><span class="row-name">' + c.name + '</span><span class="row-cities">' + c.cities + '</span></span>' +
        '<span class="row-save">' + c.save + '<small>' + c.note + '</small></span>' +
      '</button>' +
      '<div class="more" id="m-' + k + '"><div' + (isOpen ? '' : ' inert') + '><div class="more-in">' +
        '<p class="why">' + T[treatment].why[k] + '</p>' +
        '<p class="listed">Listed here: ' + listed + '</p>' +
        '<div class="actions">' +
          '<button class="btn btn-sm" type="button" data-quote="' + k + '">Ask for a ' + c.name + ' quote</button>' +
          '<button class="link" type="button" data-see="' + k + '">See hospitals in ' + c.name + '</button>' +
        '</div>' +
      '</div></div></div></li>';
  }
  function renderRows(){
    rows.innerHTML = T[treatment].order.map(function(k, i){ return rowHTML(k, i, k === openKey); }).join("");
  }
  function setOpen(k){
    openKey = (openKey === k) ? null : k;
    $$(".row", rows).forEach(function(r){
      var on = r.getAttribute("data-k") === openKey;
      r.setAttribute("data-open", on);
      $(".row-btn", r).setAttribute("aria-expanded", on);
      var inner = $(".more > div", r);
      if (on) inner.removeAttribute("inert"); else inner.setAttribute("inert", "");
    });
  }
  function setTreatment(t){
    if (t === treatment) return;
    treatment = t;
    var idx = Object.keys(T).indexOf(t);
    seg.style.setProperty("--i", idx);
    $$("button", seg).forEach(function(b){
      var on = b.getAttribute("data-t") === t;
      b.setAttribute("aria-checked", on);
      b.tabIndex = on ? 0 : -1;
    });
    renderRows();
  }
  seg.addEventListener("click", function(e){
    var b = e.target.closest("button[data-t]");
    if (b) setTreatment(b.getAttribute("data-t"));
  });
  seg.addEventListener("keydown", function(e){
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    var keys = Object.keys(T);
    var i = keys.indexOf(treatment);
    i = (i + (e.key === "ArrowRight" ? 1 : keys.length - 1)) % keys.length;
    setTreatment(keys[i]);
    $('button[data-t="' + keys[i] + '"]', seg).focus();
    e.preventDefault();
  });
  rows.addEventListener("click", function(e){
    var q = e.target.closest("[data-quote]");
    var s = e.target.closest("[data-see]");
    var r = e.target.closest(".row-btn");
    if (q){ prefillQuote(q.getAttribute("data-quote")); return; }
    if (s){ setHospFilter(s.getAttribute("data-see")); $("#hospitals").scrollIntoView(); return; }
    if (r){ setOpen(r.parentNode.getAttribute("data-k")); }
  });
  $$("[data-pick]").forEach(function(b){
    b.addEventListener("click", function(){
      setTreatment(b.getAttribute("data-pick"));
      $("#finder").scrollIntoView();
    });
  });
  renderRows();

  /* ---------- hospitals ---------- */
  var hospFilter = "all";
  var chips = $("#chips"), hosp = $("#hosp");
  function renderChips(){
    var items = [["all", "All countries"]].concat(Object.keys(C).map(function(k){ return [k, C[k].name]; }));
    chips.innerHTML = items.map(function(it){
      return '<button class="chip" type="button" data-f="' + it[0] + '" aria-pressed="' + (hospFilter === it[0]) + '">' + it[1] + '</button>';
    }).join("");
  }
  function renderHosp(){
    hosp.innerHTML = H.filter(function(h){ return hospFilter === "all" || h.c === hospFilter; }).map(function(h){
      return '<li style="--c:var(--' + h.c + ')"><span class="d"></span><span><span class="n">' + h.n + '</span><span class="t">' + h.t + '</span></span><span class="w">' + h.city + ', ' + C[h.c].name + '</span></li>';
    }).join("");
  }
  function setHospFilter(f){ hospFilter = f; renderChips(); renderHosp(); }
  chips.addEventListener("click", function(e){
    var b = e.target.closest("[data-f]");
    if (b) setHospFilter(b.getAttribute("data-f"));
  });
  renderChips(); renderHosp();

  /* ---------- faq ---------- */
  var faq = $("#faq");
  faq.innerHTML = FAQ.map(function(f, i){
    return '<div class="q" data-open="false"><h3><button type="button" aria-expanded="false" aria-controls="a' + i + '"><span>' + f[0] + '</span><span class="plus" aria-hidden="true"></span></button></h3>' +
      '<div class="more" id="a' + i + '"><div inert><p>' + f[1] + '</p></div></div></div>';
  }).join("");
  faq.addEventListener("click", function(e){
    var b = e.target.closest("button");
    if (!b) return;
    var q = b.closest(".q");
    var on = q.getAttribute("data-open") !== "true";
    q.setAttribute("data-open", on);
    b.setAttribute("aria-expanded", on);
    var inner = $(".more > div", q);
    if (on) inner.removeAttribute("inert"); else inner.setAttribute("inert", "");
  });

  /* ---------- quote form ---------- */
  var fT = $("#fTreatment"), fC = $("#fCountry");
  function prefillQuote(k){
    fT.value = T[treatment].label;
    fC.value = C[k].name;
    $("#quote").scrollIntoView();
  }
  $("#quoteForm").addEventListener("submit", function(e){
    e.preventDefault();
    var name = $("#fName").value.trim(), email = $("#fEmail").value.trim(), status = $("#status");
    if (!name || !/^\S+@\S+\.\S+$/.test(email)){
      status.textContent = "Add your name and a valid email so we can reply.";
      (name ? $("#fEmail") : $("#fName")).focus();
      return;
    }
    var body = "Procedure: " + fT.value + "\nDestination: " + fC.value + "\nName: " + name + "\nEmail: " + email + "\n\n" + $("#fNotes").value.trim();
    var href = "mailto:" + CONTACT_EMAIL + "?subject=" + encodeURIComponent("Quote request: " + fT.value) + "&body=" + encodeURIComponent(body);
    status.textContent = "Your email app should open with the request ready to send.";
    window.location.href = href;
  });

  /* ---------- mobile menu ---------- */
  var nav = $("#nav"), menuBtn = $("#menuBtn");
  function setMenu(on){ nav.setAttribute("data-open", on); menuBtn.setAttribute("aria-expanded", on); }
  menuBtn.addEventListener("click", function(){ setMenu(nav.getAttribute("data-open") !== "true"); });
  nav.addEventListener("click", function(e){ if (e.target.closest("a")) setMenu(false); });
  document.addEventListener("keydown", function(e){ if (e.key === "Escape") setMenu(false); });
})();
