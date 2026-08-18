/* ============================================================================
   PRINTORIAN OS — template demo behaviour
   ----------------------------------------------------------------------------
   This exists so the static templates can be *reviewed* as interfaces: tabs
   switch, tags filter, modals open, the clock runs. None of it is intended to
   ship — on integration React owns state and this file is deleted. Everything
   here is driven by data-attributes so the markup carries the contract.
   ========================================================================= */

(function () {
  'use strict';

  /* ---------------------------------------------------- theme: VOID / PAPER */

  var root = document.documentElement;
  var stored = null;
  try { stored = localStorage.getItem('hv-theme'); } catch (e) { /* private mode */ }
  if (stored) root.setAttribute('data-theme', stored);

  function setTheme(name) {
    root.setAttribute('data-theme', name);
    try { localStorage.setItem('hv-theme', name); } catch (e) { /* ignore */ }
    document.querySelectorAll('[data-theme-set]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', String(btn.dataset.themeSet === name));
    });
  }

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-theme-set]');
    if (btn) setTheme(btn.dataset.themeSet);
  });

  document.querySelectorAll('[data-theme-set]').forEach(function (btn) {
    btn.setAttribute('aria-pressed', String(btn.dataset.themeSet === (root.getAttribute('data-theme') || 'void')));
  });

  /* ------------------------------------------------------------------ tabs
     [data-tabs] wraps buttons carrying [data-tab-target]; panels carry
     [data-tab-panel] with the matching value. */

  document.querySelectorAll('[data-tabs]').forEach(function (group) {
    var scope = document.querySelector(group.dataset.tabs) || document;

    // A rail marker travels between entries instead of blinking from one to
    // the next. Only built where the markup opts in with .hv-rail.
    var mark = null;
    if (group.classList.contains('hv-rail')) {
      mark = document.createElement('i');
      mark.className = 'hv-rail__mark';
      mark.dataset.ready = 'false';
      group.appendChild(mark);
    }

    function moveMark(btn) {
      if (!mark || !btn) return;
      mark.style.setProperty('--y', btn.offsetTop + 'px');
      mark.style.setProperty('--h', btn.offsetHeight + 'px');
      mark.dataset.ready = 'true';
    }

    function select(btn, animate) {
      group.querySelectorAll('[data-tab-target]').forEach(function (b) {
        b.setAttribute('aria-selected', String(b === btn));
      });

      scope.querySelectorAll('[data-tab-panel]').forEach(function (p) {
        var on = p.dataset.tabPanel === btn.dataset.tabTarget;
        p.hidden = !on;
        p.classList.remove('is-entering');
        if (on && animate) {
          // Reading offsetWidth flushes style so the class re-triggers the
          // animation; without it the browser coalesces remove+add into a
          // no-op and only the very first switch would animate.
          void p.offsetWidth;
          p.classList.add('is-entering');
        }
      });

      moveMark(btn);
    }

    group.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-tab-target]');
      if (btn) select(btn, true);
    });

    // Clean up so a panel is never left mid-animation if it is hidden again.
    scope.querySelectorAll('[data-tab-panel]').forEach(function (p) {
      p.addEventListener('animationend', function (ev) {
        if (ev.target === p) p.classList.remove('is-entering');
      });
    });

    var initial = group.querySelector('[data-tab-target][aria-selected="true"]') ||
                  group.querySelector('[data-tab-target]');
    if (initial) moveMark(initial);
    // Fonts land after first paint and change row heights, so measure again.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () { moveMark(group.querySelector('[aria-selected="true"]')); });
    }
    window.addEventListener('resize', function () {
      moveMark(group.querySelector('[data-tab-target][aria-selected="true"]'));
    });
  });

  /* --------------------------------------------------- status tag filtering
     A tag filters the table it names. Counts stay fixed — they describe the
     dataset, not the current view, which is what makes them a summary. */

  document.querySelectorAll('[data-filter-group]').forEach(function (group) {
    var table = document.querySelector(group.dataset.filterGroup);
    if (!table) return;
    group.addEventListener('click', function (ev) {
      var tag = ev.target.closest('[data-filter]');
      if (!tag) return;
      var wasOn = tag.getAttribute('aria-pressed') === 'true';
      group.querySelectorAll('[data-filter]').forEach(function (t) {
        t.setAttribute('aria-pressed', 'false');
      });
      var value = wasOn ? '' : tag.dataset.filter;
      if (!wasOn) tag.setAttribute('aria-pressed', 'true');
      table.querySelectorAll('tbody tr').forEach(function (row) {
        row.hidden = Boolean(value) && value !== 'all' && row.dataset.status !== value;
      });
    });
  });

  /* ----------------------------------------------------------- table sorting
     Header buttons cycle asc -> desc. Sort key comes from [data-sort-value] on
     the cell when present, so "2 ч 40 м" sorts by its underlying minutes. */

  document.querySelectorAll('[data-sortable]').forEach(function (table) {
    table.querySelectorAll('th [data-sort]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var th = btn.closest('th');
        var index = Array.prototype.indexOf.call(th.parentNode.children, th);
        var desc = th.getAttribute('aria-sort') === 'ascending';

        table.querySelectorAll('th').forEach(function (h) {
          h.removeAttribute('aria-sort');
          var ind = h.querySelector('.hv-table__ind');
          if (ind) ind.textContent = '';
        });
        th.setAttribute('aria-sort', desc ? 'descending' : 'ascending');
        var ind = th.querySelector('.hv-table__ind');
        if (ind) ind.textContent = desc ? '▼' : '▲';

        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var av = cellKey(a.cells[index]);
          var bv = cellKey(b.cells[index]);
          if (typeof av === 'number' && typeof bv === 'number') return desc ? bv - av : av - bv;
          return desc ? String(bv).localeCompare(String(av), 'ru') : String(av).localeCompare(String(bv), 'ru');
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });

  function cellKey(cell) {
    if (!cell) return '';
    var raw = cell.dataset.sortValue !== undefined ? cell.dataset.sortValue : cell.textContent.trim();
    var num = parseFloat(String(raw).replace(/ /g, '').replace(/\s/g, '').replace(',', '.'));
    return isNaN(num) || !/^[-+]?[\d\s.,]+$/.test(String(raw).replace(/ /g, '')) ? raw : num;
  }

  /* ---------------------------------------------------------------- overlay */

  document.addEventListener('click', function (ev) {
    var opener = ev.target.closest('[data-open]');
    if (opener) {
      var target = document.getElementById(opener.dataset.open);
      if (target) { target.hidden = false; document.body.style.overflow = 'hidden'; }
      return;
    }
    var closer = ev.target.closest('[data-close]');
    if (closer || ev.target.classList.contains('hv-overlay')) {
      var overlay = ev.target.closest('.hv-overlay');
      if (overlay) { overlay.hidden = true; document.body.style.overflow = ''; }
    }
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape') return;
    document.querySelectorAll('.hv-overlay:not([hidden])').forEach(function (o) { o.hidden = true; });
    document.body.style.overflow = '';
  });

  /* ------------------------------------------------------- toggle groups
     [data-toggle-group] with [data-toggle] children: one pressed at a time. */

  document.querySelectorAll('[data-toggle-group]').forEach(function (group) {
    var multi = group.dataset.toggleGroup === 'multi';
    group.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-toggle]');
      if (!btn) return;
      if (multi) {
        btn.setAttribute('aria-pressed', String(btn.getAttribute('aria-pressed') !== 'true'));
      } else {
        group.querySelectorAll('[data-toggle]').forEach(function (b) {
          b.setAttribute('aria-pressed', String(b === btn));
        });
      }
    });
  });

  /* ---------------------------------------------------------- bound outputs
     [data-bind="#id"] mirrors an input's value, optionally through a suffix. */

  document.querySelectorAll('[data-bind]').forEach(function (input) {
    var out = document.querySelector(input.dataset.bind);
    if (!out) return;
    var render = function () { out.textContent = input.value + (input.dataset.suffix || ''); };
    input.addEventListener('input', render);
    render();
  });

  /* ---------------------------------------------------------------- switches
     A switch is a button, not a checkbox, because it carries `aria-checked`
     and no form value — the settings screen sends a patch, not a form post. */

  document.addEventListener('click', function (ev) {
    var sw = ev.target.closest('.hv-switch');
    if (!sw || sw.disabled) return;
    sw.setAttribute('aria-checked', String(sw.getAttribute('aria-checked') !== 'true'));
    markDirty(sw);
  });

  /* ------------------------------------------------------- dirty tracking
     Every control inside [data-dirty-scope] is compared against the value it
     was rendered with. A row that differs from its default gets marked, shows
     what the default was, and offers a revert — and the save bar counts them.

     Baselines come from the DOM as authored, so "default" here means "what the
     server sent", which is exactly what it should mean. */

  var scope = document.querySelector('[data-dirty-scope]');

  if (scope) {
    var controls = Array.prototype.slice.call(
      scope.querySelectorAll('input, select, textarea, .hv-switch')
    );

    controls.forEach(function (c) {
      c._baseline = readControl(c);
      var ev = c.tagName === 'SELECT' || c.type === 'checkbox' || c.type === 'radio' ? 'change' : 'input';
      c.addEventListener(ev, function () { markDirty(c); });
    });

    scope.addEventListener('click', function (ev) {
      var revert = ev.target.closest('[data-revert]');
      if (!revert) return;
      var row = revert.closest('.hv-set');
      row.querySelectorAll('input, select, textarea, .hv-switch').forEach(function (c) {
        writeControl(c, c._baseline);
      });
      recount();
    });
  }

  function readControl(c) {
    if (c.classList.contains('hv-switch')) return c.getAttribute('aria-checked');
    if (c.type === 'checkbox' || c.type === 'radio') return String(c.checked);
    return c.value;
  }

  function writeControl(c, v) {
    if (c.classList.contains('hv-switch')) { c.setAttribute('aria-checked', v); return; }
    if (c.type === 'checkbox' || c.type === 'radio') { c.checked = v === 'true'; return; }
    c.value = v;
    // Keep any [data-bind] readout in step with a reverted slider.
    c.dispatchEvent(new Event('input'));
  }

  function markDirty(c) {
    var row = c.closest('.hv-set');
    if (row) {
      var changed = Array.prototype.slice
        .call(row.querySelectorAll('input, select, textarea, .hv-switch'))
        .some(function (x) { return x._baseline !== undefined && readControl(x) !== x._baseline; });
      row.dataset.changed = String(changed);
    }
    recount();
  }

  function recount() {
    if (!scope) return;
    var n = scope.querySelectorAll('.hv-set[data-changed="true"]').length;
    var bar = document.querySelector('.hv-savebar');
    if (!bar) return;
    bar.dataset.dirty = String(n > 0);
    var label = bar.querySelector('[data-dirty-count]');
    if (label) {
      label.textContent = n
        ? 'НЕСОХРАНЁННЫХ ИЗМЕНЕНИЙ :: ' + n
        : 'ИЗМЕНЕНИЙ НЕТ — ВСЁ СОХРАНЕНО';
    }
    bar.querySelectorAll('[data-needs-dirty]').forEach(function (b) { b.disabled = !n; });
  }

  recount();

  /* ---------------------------------------------------------------- catalogue
     Search, facets and sort all feed one `apply()` — a catalogue where the
     three fight each other (sorting that clears the filter, a search that
     ignores the facets) is the classic failure, and a single pass avoids it.

     Cards carry their own values as data attributes, so the sort keys are
     declared in the markup rather than duplicated here. */

  var grid = document.querySelector('[data-catalog]');

  if (grid) {
    var cards = Array.prototype.slice.call(grid.querySelectorAll('[data-model]'));
    var search = document.querySelector('[data-catalog-search]');
    var sortBtns = Array.prototype.slice.call(document.querySelectorAll('[data-sort-key]'));
    var facets = Array.prototype.slice.call(document.querySelectorAll('[data-facet]'));
    var chipBox = document.querySelector('[data-catalog-chips]');
    var countEl = document.querySelector('[data-catalog-count]');
    var emptyEl = document.querySelector('[data-catalog-empty]');

    // Higher-is-better keys start descending; cost-like keys start ascending.
    var DESC_FIRST = { popular: 1, rating: 1, date: 1, prints: 1 };
    var sortKey = 'popular';
    var sortDir = -1;

    sortBtns.forEach(function (b) {
      b.addEventListener('click', function () {
        var k = b.dataset.sortKey;
        if (k === sortKey) sortDir = -sortDir;
        else { sortKey = k; sortDir = DESC_FIRST[k] ? -1 : 1; }
        apply();
      });
    });

    if (search) search.addEventListener('input', apply);
    facets.forEach(function (f) { f.addEventListener('change', apply); });

    if (chipBox) {
      chipBox.addEventListener('click', function (ev) {
        var chip = ev.target.closest('[data-chip-for]');
        if (!chip) return;
        var input = document.getElementById(chip.dataset.chipFor);
        if (input) { input.checked = false; apply(); }
      });
    }

    function apply() {
      var q = search ? search.value.trim().toLowerCase() : '';

      // Facets group as OR within a group, AND across groups — the behaviour
      // people expect without being told: "PLA or PETG, and small".
      var active = {};
      facets.forEach(function (f) {
        if (!f.checked) return;
        (active[f.dataset.facet] = active[f.dataset.facet] || []).push(f.value);
      });

      var shown = 0;
      cards.forEach(function (c) {
        var hay = (c.dataset.name + ' ' + (c.dataset.tags || '')).toLowerCase();
        var hit = !q || hay.indexOf(q) !== -1;
        Object.keys(active).forEach(function (g) {
          var mine = (c.dataset[g] || '').split(' ');
          if (!active[g].some(function (v) { return mine.indexOf(v) !== -1; })) hit = false;
        });
        c.hidden = !hit;
        if (hit) shown++;
      });

      var visible = cards.filter(function (c) { return !c.hidden; });
      visible.sort(function (a, b) {
        var av = parseFloat(a.dataset[sortKey]);
        var bv = parseFloat(b.dataset[sortKey]);
        if (isNaN(av) || isNaN(bv)) {
          return String(a.dataset[sortKey]).localeCompare(String(b.dataset[sortKey]), 'ru') * sortDir;
        }
        return (av - bv) * sortDir;
      });
      visible.forEach(function (c) { grid.appendChild(c); });

      sortBtns.forEach(function (b) {
        var on = b.dataset.sortKey === sortKey;
        b.setAttribute('aria-pressed', String(on));
        var dir = b.querySelector('.hv-sort__dir');
        if (dir) dir.textContent = on ? (sortDir === 1 ? '▲' : '▼') : '';
      });

      if (countEl) countEl.textContent = shown;
      if (emptyEl) emptyEl.hidden = shown > 0;

      if (chipBox) {
        chipBox.innerHTML = facets.filter(function (f) { return f.checked; })
          .map(function (f) {
            return '<button class="hv-chip" type="button" data-chip-for="' + f.id + '">' +
                   (f.dataset.label || f.value) + '</button>';
          }).join('');
      }
    }

    apply();
  }

  /* ------------------------------------------------------- view switching
     Grid and list are the same cards under a different class, so nothing is
     re-rendered and scroll position survives the switch. */

  document.querySelectorAll('[data-view-set]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var target = document.querySelector(btn.dataset.viewTarget || '[data-catalog]');
      if (!target) return;
      var mode = btn.dataset.viewSet;
      target.classList.toggle('hv-cat', mode === 'grid');
      target.classList.toggle('hv-cat--list', mode === 'list');
      target.querySelectorAll('[data-model]').forEach(function (c) {
        c.classList.toggle('hv-model--row', mode === 'list');
      });
      document.querySelectorAll('[data-view-set]').forEach(function (b) {
        b.setAttribute('aria-pressed', String(b === btn));
      });
    });
  });

  /* ------------------------------------------------------------------ clock
     The chrome carries a live timestamp — a console that shows a frozen clock
     reads as a screenshot. Updated once a second, UTC-free, local time. */

  var clocks = document.querySelectorAll('[data-clock]');
  if (clocks.length) {
    var tick = function () {
      var d = new Date();
      var pad = function (n) { return String(n).padStart(2, '0'); };
      var stamp = pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + '.' + d.getFullYear() +
        ' :: ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
      clocks.forEach(function (el) { el.textContent = stamp; });
    };
    tick();
    setInterval(tick, 1000);
  }
})();
