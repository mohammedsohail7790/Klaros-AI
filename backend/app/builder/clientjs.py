"""The one script every generated project ships (`assets/app.js`).

Vanilla, strict, no eval, no dynamic code, no third-party requests. It only enhances markup that already works without
it (the pages are complete HTML): mobile menu, filtering/sorting/search over cards, favourites (localStorage), form
validation and submission, dashboard figures.
"""

APP_JS = r"""'use strict';
(function () {
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var btn = $('.menu-btn'), nav = $('.nav');
  if (btn && nav) btn.addEventListener('click', function () {
    var open = nav.classList.toggle('open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  /* collections: search, filter, sort — over cards that are already in the page */
  $$('[data-collection]').forEach(function (root) {
    var list = $('[data-items]', root), cards = $$('[data-card]', list), count = $('[data-count]', root);
    var q = $('[data-search]', root), filters = $$('[data-filter]', root), sorter = $('[data-sort]', root), empty = $('[data-empty]', root);
    function apply() {
      var term = q ? q.value.trim().toLowerCase() : '', shown = 0;
      cards.forEach(function (c) {
        var ok = !term || (c.getAttribute('data-text') || '').indexOf(term) !== -1;
        filters.forEach(function (f) {
          var v = f.value, key = f.getAttribute('data-filter'), kind = f.getAttribute('data-kind');
          if (!v || !ok) return;
          var cv = c.getAttribute('data-f-' + key) || '';
          if (kind === 'max') ok = parseFloat(cv) <= parseFloat(v);
          else if (kind === 'min') ok = parseFloat(cv) >= parseFloat(v);
          else ok = cv.split('|').indexOf(v) !== -1;
        });
        c.hidden = !ok; if (ok) shown++;
      });
      if (count) count.textContent = shown + (shown === 1 ? ' result' : ' results');
      if (empty) empty.hidden = shown !== 0;
    }
    function sort() {
      if (!sorter || !sorter.value) return;
      var parts = sorter.value.split(':'), key = parts[0], dir = parts[1] === 'desc' ? -1 : 1;
      cards.sort(function (a, b) {
        var x = a.getAttribute('data-f-' + key) || '', y = b.getAttribute('data-f-' + key) || '';
        var nx = parseFloat(x), ny = parseFloat(y);
        if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * dir;
        return x.localeCompare(y) * dir;
      }).forEach(function (c) { list.appendChild(c); });
    }
    if (q) q.addEventListener('input', apply);
    filters.forEach(function (f) { f.addEventListener('change', apply); });
    if (sorter) sorter.addEventListener('change', function () { sort(); apply(); });
    apply();
  });

  /* favourites: stored only in this visitor's browser */
  var KEY = 'klaros:saved:' + location.pathname.split('/')[1];
  var mem = [];
  function saved() { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return mem; } }
  function store(v) { mem = v; try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* storage blocked (e.g. a sandboxed preview): kept for this page view only */ } }
  $$('[data-save]').forEach(function (b) {
    var id = b.getAttribute('data-save');
    function paint() { var on = saved().indexOf(id) !== -1; b.setAttribute('aria-pressed', on ? 'true' : 'false'); b.title = on ? 'Remove from saved' : 'Save'; }
    b.addEventListener('click', function (e) {
      e.preventDefault(); var s = saved(), i = s.indexOf(id);
      if (i === -1) s.push(id); else s.splice(i, 1);
      store(s); paint();
    });
    paint();
  });
  $$('[data-saved-view]').forEach(function (root) {
    var s = saved(), cards = $$('[data-card]', root), shown = 0;
    cards.forEach(function (c) { var on = s.indexOf(c.getAttribute('data-id')) !== -1; c.hidden = !on; if (on) shown++; });
    var empty = $('[data-empty]', root); if (empty) empty.hidden = shown !== 0;
  });

  /* forms: validate, then send to the enquiry endpoint when the site has one */
  var meta = $('meta[name="klaros-enquiry"]'), endpoint = meta ? meta.getAttribute('content') : '';
  $$('form[data-enquiry]').forEach(function (form) {
    var status = $('[data-status]', form);
    function say(state, msg) { if (status) { status.hidden = false; status.setAttribute('data-state', state); status.textContent = msg; } }
    function err(el, msg) { var e = $('[data-error-for="' + el.name + '"]', form); if (e) e.textContent = msg || ''; el.setAttribute('aria-invalid', msg ? 'true' : 'false'); }
    form.addEventListener('submit', function (ev) {
      ev.preventDefault(); var ok = true, data = {};
      $$('input, textarea, select', form).forEach(function (el) {
        if (!el.name || el.type === 'submit') return;
        var v = (el.value || '').trim(); data[el.name] = v; err(el, '');
        if (el.required && !v) { err(el, 'This field is required.'); ok = false; }
        else if (el.type === 'email' && v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { err(el, 'Enter a valid email address.'); ok = false; }
      });
      if (data.website) return; /* honeypot */
      if (!ok) { say('error', 'Please fix the highlighted fields.'); var first = $('[aria-invalid="true"]', form); if (first) first.focus(); return; }
      if (!endpoint) { say('error', 'This form is not connected yet, so nothing was sent. The site owner can publish the site to start receiving enquiries.'); return; }
      var submit = $('[type=submit]', form); if (submit) submit.disabled = true; say('info', 'Sending…');
      var extra = Object.keys(data).filter(function (k) { return ['name','email','message','website','subject'].indexOf(k) === -1 && data[k]; }).map(function (k) { return k.replace(/_/g, ' ') + ': ' + data[k]; });
      var payload = { name: data.name, source: 'WEB', email: data.email || null, description: [data.message].concat(extra).filter(Boolean).join('\n').slice(0, 2000), service_requested: data.subject ? String(data.subject).slice(0, 255) : null, website: data.website || null };
      fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        .then(function (r) { if (!r.ok) throw new Error('bad status'); say('success', 'Thank you — your message was sent.'); form.reset(); })
        .catch(function () { say('error', 'Sorry, that did not send. Please try again in a moment.'); })
        .then(function () { if (submit) submit.disabled = false; });
    });
  });

  /* dashboard figures computed from the page's own data */
  $$('[data-dashboard]').forEach(function (root) {
    var url = root.getAttribute('data-src');
    fetch(url).then(function (r) { return r.json(); }).then(function (rows) {
      var total = $('[data-kpi="count"]', root); if (total) total.textContent = String(rows.length);
      var key = root.getAttribute('data-sum');
      var sum = $('[data-kpi="sum"]', root); if (sum && key) sum.textContent = rows.reduce(function (a, r) { return a + (parseFloat(r[key]) || 0); }, 0).toLocaleString();
      var avg = $('[data-kpi="avg"]', root); if (avg && key && rows.length) avg.textContent = Math.round(rows.reduce(function (a, r) { return a + (parseFloat(r[key]) || 0); }, 0) / rows.length).toLocaleString();
    }).catch(function () { /* the table below is already in the page */ });
  });

  $$('[data-year]').forEach(function (el) { el.textContent = String(new Date().getFullYear()); });
})();
"""
