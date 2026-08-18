/* ============================================================================
   PRINTORIAN OS — navigation overlay behaviour
   ----------------------------------------------------------------------------
   The menu is rendered from one route table rather than copied into eight HTML
   files. That is not only to keep the templates readable: on integration this
   becomes a single React component fed by the permission list, so a single
   source here is already the right shape.

   Interaction contract
     Ctrl/Cmd+K, or `/`   open
     type                 filter
     ArrowUp / ArrowDown  move
     Enter                go
     Esc                  close, focus returns to the trigger

   Delete on integration along with kit.js.
   ========================================================================= */

(function () {
  'use strict';

  /* ------------------------------------------------------------ route table
     `note` is the right-hand column, `pv` is the preview pane. Figures are
     placeholders — they come from the API once this is wired up. */

  var ROUTES = [
    {
      href: 'promo.html', realm: 'public', label: 'О ферме', note: 'ГЛАВНАЯ · ВИТРИНА',
      mark: 'HOME', kicker: 'C:/PRINTORIAN',
      text: 'Что делает ферма и почему цена показывается построчно. Единственный экран, обращённый наружу.',
      stats: [['Заказов выполнено', '1 184'], ['Срок соблюдён', '96%'], ['Моделей в каталоге', '146']],
      shape: 'cube'
    },
    {
      href: 'dashboard.html', realm: 'control', label: 'Сводка', note: 'СОСТОЯНИЕ ФЕРМЫ · KPI',
      mark: 'FARM', kicker: 'C:/DASHBOARD/FARM.OVERVIEW',
      text: 'Заказы, парк, финансы и расписание на одном экране. Состояние каждой машины — светящимся квадратом, детали — по клику.',
      stats: [['Печатают', '7 из 12'], ['Заказов в работе', '18'], ['Прибыль за месяц', '459 тыс ₽']],
      shape: 'grid'
    },
    {
      href: 'catalog.html', realm: 'public', label: 'Каталог', note: 'ГОТОВЫЕ МОДЕЛИ · 146',
      mark: 'LIB', kicker: 'C:/CATALOG/LOCAL.LIBRARY',
      text: 'Локальная библиотека проверенных моделей. Время и цена — факт с последней печати, а не оценка по объёму.',
      stats: [['Моделей', '146'], ['Напечатано раз', '5 214'], ['Средняя оценка', '4.6']],
      shape: 'stack'
    },
    {
      href: 'configurator.html', realm: 'public', label: 'Конфигуратор', note: 'РАСЧЁТ · ЗАГРУЗКА МОДЕЛИ',
      mark: 'QUOTE', kicker: 'C:/STORE/CONFIGURATOR',
      text: 'Загрузка модели, подбор материала, цвета и количество — с прозрачной ценой, которая пересчитывается на каждом изменении.',
      stats: [['Средний расчёт', '1.2 с'], ['Строк в смете', '14'], ['Материалов в подборе', '24']],
      shape: 'cube'
    },
    {
      href: 'account.html', realm: 'public', label: 'Кабинет', note: 'ПРОФИЛЬ · ТАРИФ · БЕЗОПАСНОСТЬ',
      mark: 'USER', kicker: 'C:/CABINET/ACCOUNT',
      text: 'Профиль, персональный тариф, адреса, оплата, загруженные модели, уведомления и активные сеансы.',
      stats: [['Заказов', '14'], ['Тариф', 'Silver · −4%'], ['Сэкономлено', '14 820 ₽']],
      shape: 'key'
    },
    {
      href: 'cabinet.html', realm: 'public', label: 'Мои заказы', note: 'ХОД ВЫПОЛНЕНИЯ · ОЧЕРЕДЬ',
      mark: 'TRACK', kicker: 'C:/CABINET/ORDERS',
      text: 'Девять этапов от оплаты до отправки. Если производство выходит за обещанный срок, цена снижается автоматически.',
      stats: [['Активных заказов', '2'], ['Этап', '5 из 9'], ['До готовности', '7 ч 26 м']],
      shape: 'pipe'
    },
    {
      href: 'orders.html', realm: 'control', label: 'Диспетчерская', note: 'ВСЕ ЗАКАЗЫ · ВОЗВРАТЫ',
      mark: 'DESK', kicker: 'C:/ORDERING/DESK',
      text: 'Все заказы фермы, перевод по этапам, возвраты и маржа по каждой позиции.',
      stats: [['В производстве', '11'], ['Лист ожидания', '3'], ['Риск просрочки', '2']],
      shape: 'grid'
    },
    {
      href: 'fleet.html', realm: 'control', label: 'Принтеры', note: 'ПАРК · ОБСЛУЖИВАНИЕ',
      mark: 'FARM', kicker: 'C:/PRODUCTION/FLEET',
      text: 'Состояние каждой машины в реальном времени, карта обслуживания, слоты AMS и амортизация.',
      stats: [['Печатают', '7 / 12'], ['Требуют внимания', '2'], ['Наработка за сутки', '148 ч']],
      shape: 'nodes'
    },
    {
      href: 'postproduction.html', realm: 'control', label: 'Постобработка', note: 'ЗАДАНИЯ · ИНСТРУКЦИИ',
      mark: 'POST', kicker: 'C:/PRODUCTION/POSTPROCESS',
      text: 'Очередь заданий поста, пошаговые инструкции с нормами времени и накопленные отметки операторов.',
      stats: [['В очереди', '7'], ['Темп к норме', '104%'], ['Качество', '98.4%']],
      shape: 'pipe'
    },
    {
      href: 'packaging.html', realm: 'control', label: 'Упаковка', note: 'КОМПЛЕКТНОСТЬ · ОТСЕЧКА',
      mark: 'PACK', kicker: 'C:/PRODUCTION/PACKING',
      text: 'Очередь упаковки к времени забора курьера, подбор тары по габариту, расход упаковочных материалов.',
      stats: [['К отправке', '5'], ['До отсечки', '2 ч 14 м'], ['Комплектность', '100%']],
      shape: 'stack'
    },
    {
      href: 'service.html', realm: 'control', label: 'Сервис', note: 'РЕМОНТ · ТО · УСТАНОВКА',
      mark: 'SERV', kicker: 'C:/PRODUCTION/SERVICE',
      text: 'Заявки на ремонт, обслуживание, установку машин, загрузку материала и перемещение партий.',
      stats: [['Открытых заявок', '9'], ['Готовность парка', '83%'], ['MTTR', '1.8 ч']],
      shape: 'nodes'
    },
    {
      href: 'purchasing.html', realm: 'control', label: 'Закупки', note: 'ПОСТАВЩИКИ · ЗАКАЗЫ',
      mark: 'BUY', kicker: 'C:/SUPPLY/PURCHASING',
      text: 'Материалы, запчасти, упаковка и оборудование. Заказы поставщикам с накопленной оценкой каждого.',
      stats: [['Открытых заказов', '7'], ['В пути', '4'], ['Бюджет месяца', '68%']],
      shape: 'grid'
    },
    {
      href: 'store.html', realm: 'control', label: 'Склад', note: 'ЯЧЕЙКИ · ДВИЖЕНИЯ',
      mark: 'HOLD', kicker: 'C:/SUPPLY/STORE',
      text: 'Карта хранения по зонам и ячейкам, движения, партии, оборачиваемость и инвентаризация.',
      stats: [['Занято ячеек', '71%'], ['Стоимость остатков', '186 тыс ₽'], ['Залежалого', '7']],
      shape: 'stack'
    },
    {
      href: 'logistics.html', realm: 'control', label: 'Логистика', note: 'ОТПРАВЛЕНИЯ · ПЕРЕВОЗЧИКИ',
      mark: 'SHIP', kicker: 'C:/LOGISTICS/SHIPMENTS',
      text: 'Отгрузка к отсечке, трекинг, оценка перевозчиков, зоны и тарифы, возвраты и розыск.',
      stats: [['В пути', '14'], ['В срок', '94%'], ['Средняя доставка', '250 ₽']],
      shape: 'pipe'
    },
    {
      href: 'materials.html', realm: 'control', label: 'Материалы', note: 'СКЛАД · ПАРТИИ',
      mark: 'STOCK', kicker: 'C:/INVENTORY/MATERIALS',
      text: 'Свойства, остатки, размещение по полкам и слотам, цена закупки и цена для заказчика.',
      stats: [['Позиций', '24'], ['На складе', '84.6 кг'], ['Заморожено', '186 400 ₽']],
      shape: 'stack'
    },
    {
      href: 'blog.html', realm: 'public', label: 'Журнал', note: 'ОТЧЁТЫ · ИНЖЕНЕРНЫЕ ЗАМЕТКИ',
      mark: 'LOG', kicker: 'C:/JOURNAL/REPORTS',
      text: 'Как устроена ферма изнутри: расчёт себестоимости, планировщик, драйверы принтеров, выбор материалов.',
      stats: [['Публикаций', '18'], ['Последняя', '07.08.2026'], ['Разделов', '5']],
      shape: 'doc'
    },
    {
      href: 'settings.html', realm: 'control', label: 'Настройки', note: 'ПАРАМЕТРЫ · ДИАГНОСТИКА',
      mark: 'CONF', kicker: 'C:/SYSTEM/SETTINGS',
      text: 'Все константы фермы: тарифы, веса планировщика, SLA, склад, сервис, логистика, финансы и диагностика подсистем.',
      stats: [['Разделов', '15'], ['Снимок тарифов', '8F41C2'], ['Проверок', '14 из 15']],
      shape: 'grid'
    },
    {
      href: 'users.html', realm: 'control', label: 'Пользователи', note: 'РОЛИ · ДОСТУП',
      mark: 'AUTH', kicker: 'C:/IDENTITY/USERS',
      text: 'Учётные записи, роли как наборы прав, активные сессии. Экраны показываются по правам, а не по роли.',
      stats: [['Учётных записей', '34'], ['Активных сессий', '6'], ['Ролей', '5']],
      shape: 'key'
    },
    {
      href: 'auth.html', realm: 'public', label: 'Вход', note: 'РЕГИСТРАЦИЯ · ВОССТАНОВЛЕНИЕ',
      mark: 'AUTH', kicker: 'C:/IDENTITY/AUTHENTICATE',
      text: 'Вход, регистрация и восстановление доступа. Те же три режима доступны всплывающим окном с любого экрана.',
      stats: [['Хэш', 'Argon2id'], ['Сеанс', '12 ч'], ['Блокировка', 'после 5 попыток']],
      shape: 'key'
    },
    {
      href: 'index.html', realm: 'control', label: 'Design Kit', note: 'КОМПОНЕНТЫ · ТОКЕНЫ',
      mark: 'KIT', kicker: 'C:/VISUAL_STYLE/BASE.MODULE',
      text: 'Справочник визуального языка: палитра, типографика, обвязка окна, контейнеры, индикаторы, управление.',
      stats: [['Экранов', '9'], ['Файлов стилей', '3'], ['Тем', '2']],
      shape: 'cube'
    }
  ];

  /* Small line drawings, one per destination. Deliberately schematic — they
     are route markers, not illustration. */
  var SHAPES = {
    cube:  '<path d="M20 60 L70 34 L120 60 L70 86 Z"/><path d="M20 60 L20 40 L70 14 L120 40 L120 60"/><path d="M70 34 L70 14"/>',
    pipe:  '<path d="M14 50 H126"/><rect x="14" y="42" width="30" height="16"/><rect x="56" y="42" width="30" height="16"/><rect x="98" y="42" width="28" height="16"/>',
    grid:  '<rect x="16" y="24" width="48" height="24"/><rect x="76" y="24" width="48" height="24"/><rect x="16" y="58" width="48" height="24"/><rect x="76" y="58" width="48" height="24"/>',
    nodes: '<circle cx="34" cy="34" r="12"/><circle cx="106" cy="34" r="12"/><circle cx="34" cy="74" r="12"/><circle cx="106" cy="74" r="12"/>',
    stack: '<path d="M70 20 L124 44 L70 68 L16 44 Z"/><path d="M16 58 L70 82 L124 58"/><path d="M16 72 L70 96 L124 72"/>',
    doc:   '<path d="M40 16 H88 L104 32 V96 H40 Z"/><path d="M88 16 V32 H104"/><path d="M54 52 H90 M54 66 H90 M54 80 H76"/>',
    key:   '<circle cx="46" cy="52" r="20"/><path d="M64 52 H124"/><path d="M108 52 V70 M120 52 V66"/>'
  };

  /* ------------------------------------------------------------------ build */

  var GLYPHS = 'АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ0123456789/\\:.·▮';
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var here = (location.pathname.split('/').pop() || 'index.html');
  var myRealm = document.documentElement.dataset.realm || 'control';
  var realmFilter = 'all';
  var menu, list, preview, query, items, lastFocus, activeIndex = 0;

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function build() {
    menu = el('div', 'hv-menu');
    menu.id = 'hv-menu';
    menu.hidden = true;
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-modal', 'true');
    menu.setAttribute('aria-label', 'Навигация');

    menu.innerHTML =
      '<div class="hv-menu__bg"></div>' +
      '<div class="hv-menu__frame"></div>' +
      '<div class="hv-menu__scan"></div>' +

      '<header class="hv-chrome">' +
        '<div class="hv-chrome__row">' +
          '<span class="hv-tab">Навигация :: Система</span>' +
          '<label class="hv-meta">' +
            '<span>ПЕРЕЙТИ ::</span>' +
            '<input class="hv-menu__query" type="text" autocomplete="off" spellcheck="false" ' +
                   'placeholder="НАЧНИТЕ ВВОДИТЬ НАЗВАНИЕ РАЗДЕЛА" aria-label="Фильтр разделов">' +
            '<i class="hv-menu__caret"></i>' +
            '<i class="hv-meta__sep"></i>' +
            '<span class="hv-menu__count"></span>' +
          '</label>' +
          '<div class="hv-os">' +
            '<span class="hv-os__label">PRINTORIAN OS ./v2.0</span>' +
            '<button class="hv-os__x" type="button" data-menu-close aria-label="Закрыть меню">✕</button>' +
          '</div>' +
        '</div>' +
        '<div class="hv-path">' +
          '<div class="hv-path__crumbs"><span class="hv-path__here">C:/PRINTORIAN/SYSTEM/NAV.MODULE</span></div>' +
          '<div class="hv-path__status">МОДУЛЬ :: <b>ЗАГРУЖЕН</b></div>' +
        '</div>' +
      '</header>' +

      '<div class="hv-menu__field">' +
        '<div>' +
          '<div class="hv-menu__realms">' +
            '<button class="hv-menu__realm" type="button" data-realm-filter="all" aria-pressed="true">Всё <b>20</b></button>' +
            '<button class="hv-menu__realm" type="button" data-realm-filter="public">Витрина <b>7</b></button>' +
            '<button class="hv-menu__realm" type="button" data-realm-filter="control">Пульт <b>13</b></button>' +
          '</div>' +
          '<nav class="hv-menu__list"></nav>' +
        '</div>' +
        '<aside class="hv-menu__preview"><div class="hv-menu__pv"></div></aside>' +
      '</div>' +

      '<footer class="hv-menu__foot">' +
        '<span><span class="hv-key">↑</span><span class="hv-key">↓</span> выбор</span>' +
        '<span><span class="hv-key">↵</span> перейти</span>' +
        '<span><span class="hv-key">ESC</span> закрыть</span>' +
        '<span><span class="hv-key">CTRL</span><span class="hv-key">K</span> открыть откуда угодно</span>' +
        '<span class="hv-spacer"></span>' +
        '<span data-clock></span>' +
      '</footer>';

    list = menu.querySelector('.hv-menu__list');
    preview = menu.querySelector('.hv-menu__preview');
    query = menu.querySelector('.hv-menu__query');

    /* The list is built in two territories with the access border between
       them. Numbering restarts per territory: "витрина 03" and "пульт 03" are
       different places, and one running 1..20 would imply they are a single
       ranked list. */
    var order = ['public', 'control'];
    var TERR = {
      public:  { k: 'ВИТРИНА',  s: 'ЧТО ВИДИТ ЗАКАЗЧИК' },
      control: { k: 'ПУЛЬТ',    s: 'УПРАВЛЕНИЕ ФЕРМОЙ' }
    };

    order.forEach(function (realm, ri) {
      var group = ROUTES.filter(function (r) { return r.realm === realm; });

      if (ri > 0) {
        var border = el('div', 'hv-menu__border', '<span>Граница доступа</span>' +
          '<span>Требуется роль сотрудника</span>');
        border.dataset.realmBlock = realm;
        list.appendChild(border);
      }

      var head = el('div', 'hv-menu__terr',
        '<b>' + TERR[realm].k + '</b><span>' + TERR[realm].s + ' · ' + group.length + '</span>');
      head.dataset.realmBlock = realm;
      list.appendChild(head);

      group.forEach(function (r, n) {
        var i = ROUTES.indexOf(r);
        var a = el('a', 'hv-menu__item');
        a.href = r.href;
        a.style.setProperty('--i', i);
        a.dataset.index = i;
        a.dataset.realm = realm;
        // Staff see everything here; a customer session would set this from
        // `actor.permissions` and the row would render locked instead.
        a.dataset.locked = 'false';
        a.innerHTML =
          '<span class="hv-menu__n"><i class="hv-menu__flag"></i>' + String(n + 1).padStart(2, '0') + '</span>' +
          '<span class="hv-menu__label" data-text="' + r.label + '">' + r.label +
            '<span class="hv-menu__go">›</span></span>' +
          '<span class="hv-menu__note">' + (r.href === here ? 'ВЫ ЗДЕСЬ' : r.note) + '</span>';
        list.appendChild(a);
      });
    });

    document.body.appendChild(menu);
    items = Array.prototype.slice.call(list.querySelectorAll('.hv-menu__item'));

    menu.querySelectorAll('[data-realm-filter]').forEach(function (b) {
      b.addEventListener('click', function () {
        realmFilter = b.dataset.realmFilter;
        menu.querySelectorAll('[data-realm-filter]').forEach(function (x) {
          x.setAttribute('aria-pressed', String(x === b));
        });
        filter();
      });
    });

    // Pointer and keyboard drive the same single notion of "active".
    list.addEventListener('mousemove', function (ev) {
      var it = ev.target.closest('.hv-menu__item');
      if (it) setActive(+it.dataset.index, false);
    });
    query.addEventListener('input', filter);
    menu.addEventListener('keydown', onKeydown);
  }

  /* --------------------------------------------------------------- preview */

  function renderPreview(r) {
    preview.dataset.swapping = 'true';
    preview.querySelector('.hv-menu__pv').innerHTML =
      '<div class="hv-frame hv-frame--wide">' +
        '<span class="hv-micro">' + r.kicker + '</span>' +
        '<div class="hv-menu__pv-mark" style="margin:var(--hv-2) 0 var(--hv-3)">' + r.mark + '</div>' +
        '<svg class="hv-menu__pv-svg" viewBox="0 0 140 100" width="100%" height="120" ' +
             'fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.75" aria-hidden="true">' +
          (SHAPES[r.shape] || SHAPES.cube) +
        '</svg>' +
        '<p class="hv-prose" style="font-size:var(--hv-size-small);margin:var(--hv-3) 0 0">' + r.text + '</p>' +
        '<hr class="hv-hr">' +
        '<ul class="hv-leaders">' +
          r.stats.map(function (s) {
            return '<li class="hv-leader"><span class="hv-leader__k">' + s[0] + '</span>' +
                   '<span class="hv-leader__fill"></span>' +
                   '<span class="hv-leader__v">' + s[1] + '</span></li>';
          }).join('') +
        '</ul>' +
      '</div>';
    // Let the flicker keyframes restart on the next swap.
    setTimeout(function () { preview.dataset.swapping = 'false'; }, 200);
  }

  /* ------------------------------------------------------- decode-in effect
     Characters resolve out of noise. Capped at ~30fps and skipped entirely
     under reduced motion — it is flavour, and it must never delay the label
     being readable by a screen reader, which reads `data-text`. */

  function decode(node) {
    if (reduced) return;
    var final = node.dataset.text || node.textContent;
    var go = node.querySelector('.hv-menu__go');
    var frame = 0;
    var total = final.length + 8;

    clearInterval(node._decode);
    node._decode = setInterval(function () {
      var out = '';
      for (var i = 0; i < final.length; i++) {
        if (final[i] === ' ') { out += ' '; continue; }
        if (i < frame - 6) out += final[i];
        else if (i < frame) out += GLYPHS[(Math.random() * GLYPHS.length) | 0];
        else out += ' ';
      }
      node.textContent = out;
      if (go) node.appendChild(go);
      if (++frame > total) { clearInterval(node._decode); node.textContent = final; if (go) node.appendChild(go); }
    }, 28);
  }

  /* ---------------------------------------------------------------- active */

  /* `i` is always a ROUTES index. Rows are ordered by territory, so it can
     never be used as a position in `items` — look the row up by its stored
     index instead. */
  function rowFor(i) {
    for (var k = 0; k < items.length; k++) {
      if (+items[k].dataset.index === i) return items[k];
    }
    return null;
  }

  function setActive(i, scroll) {
    var visible = items.filter(function (it) { return !it.hidden; });
    if (!visible.length) return;
    var row = rowFor(i);
    var target = row && !row.hidden ? row : visible[0];

    items.forEach(function (it) { it.dataset.active = 'false'; });
    target.dataset.active = 'true';
    activeIndex = +target.dataset.index;

    decode(target.querySelector('.hv-menu__label'));
    renderPreview(ROUTES[activeIndex]);
    if (scroll) target.scrollIntoView({ block: 'nearest' });
  }

  function move(step) {
    var visible = items.filter(function (it) { return !it.hidden; });
    if (!visible.length) return;
    var pos = visible.findIndex(function (it) { return +it.dataset.index === activeIndex; });
    var next = visible[(pos + step + visible.length) % visible.length];
    setActive(+next.dataset.index, true);
  }

  /* ---------------------------------------------------------------- filter */

  function filter() {
    var q = query.value.trim().toLowerCase();
    var shown = 0;
    var perRealm = { public: 0, control: 0 };

    // Rows are grouped by territory now, so position in `items` no longer
    // matches position in ROUTES — the route comes from the stored index.
    items.forEach(function (it) {
      var r = ROUTES[+it.dataset.index];
      var hay = (r.label + ' ' + r.note + ' ' + r.mark + ' ' + r.kicker).toLowerCase();
      var hit = (!q || hay.indexOf(q) !== -1) &&
                (realmFilter === 'all' || realmFilter === r.realm);
      it.hidden = !hit;
      if (hit) { shown++; perRealm[r.realm]++; }
    });

    // A territory header with nothing under it is noise, and so is the border
    // when only one side of it is showing.
    menu.querySelectorAll('[data-realm-block]').forEach(function (b) {
      b.hidden = perRealm[b.dataset.realmBlock] === 0;
    });
    var bothSides = perRealm.public > 0 && perRealm.control > 0;
    menu.querySelectorAll('.hv-menu__border').forEach(function (b) { b.hidden = !bothSides; });

    menu.querySelector('.hv-menu__count').textContent =
      q ? 'НАЙДЕНО :: ' + shown : 'РАЗДЕЛОВ :: ' + ROUTES.length;

    if (shown) {
      var first = items.filter(function (it) { return !it.hidden; })[0];
      setActive(+first.dataset.index, true);
    }
  }

  /* ----------------------------------------------------------- open / close */

  /* `wantRealm` is set when the menu is opened by the realm badge: the point
     of that gesture is to cross, so it lands you in the other territory
     already filtered. Opened any other way, it shows both. */
  function open(wantRealm) {
    if (!menu.hidden) return;
    lastFocus = document.activeElement;
    menu.hidden = false;
    document.body.style.overflow = 'hidden';
    query.value = '';

    realmFilter = wantRealm || 'all';
    menu.querySelectorAll('[data-realm-filter]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.realmFilter === realmFilter));
    });
    filter();

    // Start on the current page's own entry, so the menu opens oriented —
    // unless we were asked to cross, in which case the filter has already
    // chosen the first row of the destination territory.
    if (!wantRealm) {
      var self = ROUTES.findIndex(function (r) { return r.href === here; });
      setActive(self > -1 ? self : 0, false);
    }
    query.focus();
  }

  function close() {
    if (menu.hidden) return;
    menu.hidden = true;
    document.body.style.overflow = '';
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function onKeydown(ev) {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); move(1); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); move(-1); }
    else if (ev.key === 'Enter') {
      var target = rowFor(activeIndex);
      if (target && !target.hidden) { ev.preventDefault(); location.href = target.href; }
    } else if (ev.key === 'Escape') { ev.preventDefault(); close(); }
    else if (ev.key === 'Tab') {
      // Focus stays in the field: the list is driven by arrows, so there is
      // nowhere useful for Tab to go and letting it escape strands the user.
      ev.preventDefault();
      query.focus();
    }
  }

  /* ---------------------------------------------------------------- trigger */

  function mountTrigger() {
    if (document.querySelector('[data-menu-open]')) return;
    var host = document.querySelector('.hv-appbar');
    var btn = el('button', 'hv-menu-trigger');
    btn.type = 'button';
    btn.setAttribute('data-menu-open', '');
    btn.setAttribute('aria-haspopup', 'dialog');
    btn.innerHTML =
      '<span class="hv-menu-trigger__bars"><i></i><i></i><i></i></span>' +
      '<span>Меню</span>' +
      '<span class="hv-menu-trigger__hint">CTRL K</span>';

    /* The realm badge. Always present, always says which side of the boundary
       you are standing on, and clicking it opens the menu already filtered to
       the *other* side — the only reason anyone looks at it is to cross. */
    var other = myRealm === 'public' ? 'control' : 'public';
    var badge = el('button', 'hv-realm');
    badge.type = 'button';
    badge.setAttribute('data-realm-open', other);
    badge.title = myRealm === 'public'
      ? 'Вы в витрине. Открыть разделы управления фермой'
      : 'Вы в пульте управления. Открыть витрину';
    badge.innerHTML =
      '<i class="hv-realm__flag"></i>' +
      '<span>' + (myRealm === 'public' ? 'Витрина' : 'Пульт') + '</span>';

    if (host) {
      host.insertBefore(btn, host.firstChild);
      var right = host.querySelector('.hv-appbar__right');
      if (right) right.insertBefore(badge, right.firstChild);
      else host.appendChild(badge);
    } else {
      // Screens without an app bar (the kit) get both in the window chrome.
      var row = document.querySelector('.hv-chrome__row');
      if (row) {
        btn.style.flex = 'none';
        badge.style.flex = 'none';
        row.insertBefore(btn, row.querySelector('.hv-os'));
        row.insertBefore(badge, row.querySelector('.hv-os'));
      }
    }
  }

  /* ------------------------------------------------------------------- init */

  build();
  mountTrigger();

  document.addEventListener('click', function (ev) {
    var cross = ev.target.closest('[data-realm-open]');
    if (cross) { ev.preventDefault(); open(cross.dataset.realmOpen); return; }
    if (ev.target.closest('[data-menu-open]')) { ev.preventDefault(); open(); }
    else if (ev.target.closest('[data-menu-close]')) { ev.preventDefault(); close(); }
  });

  document.addEventListener('keydown', function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k') { ev.preventDefault(); open(); return; }
    if (ev.key === 'Escape' && !menu.hidden) { close(); return; }
    // Bare `/` opens too, but not while the user is typing somewhere else.
    var t = ev.target;
    var typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    if (ev.key === '/' && !typing && menu.hidden) { ev.preventDefault(); open(); }
  });
})();
