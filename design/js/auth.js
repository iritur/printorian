/* ============================================================================
   PRINTORIAN OS — authentication popup
   ----------------------------------------------------------------------------
   The same three modes as auth.html, in a modal, so signing in never costs you
   the page you were on. That matters most at the one moment it is actually
   demanded: the checkout, where navigating away loses a configured quote.

   Injected once from a single source for the same reason the nav is — on
   integration this becomes one React component, not markup pasted into
   fourteen files.

   Open it from any element carrying [data-auth-open]; an optional value picks
   the starting mode: signin (default), signup, recover.

   Demo only. Deleted on integration.
   ========================================================================= */

(function () {
  'use strict';

  var overlay, modal, lastFocus;

  var MODES = {
    signin:  { tab: 'Доступ :: Вход',            path: 'C:/PRINTORIAN/IDENTITY/AUTHENTICATE' },
    signup:  { tab: 'Доступ :: Регистрация',     path: 'C:/PRINTORIAN/IDENTITY/REGISTER' },
    recover: { tab: 'Доступ :: Восстановление',  path: 'C:/PRINTORIAN/IDENTITY/RECOVER' }
  };

  function build() {
    overlay = document.createElement('div');
    overlay.className = 'hv-overlay';
    overlay.id = 'hv-auth';
    overlay.hidden = true;

    overlay.innerHTML =
      '<div class="hv-modal" role="dialog" aria-modal="true" aria-labelledby="auth-t" style="width:min(520px,100%)">' +
        '<div class="hv-chrome" style="position:static;padding:var(--hv-3) var(--hv-3) 0">' +
          '<div class="hv-chrome__row">' +
            '<span class="hv-tab" id="auth-t">Доступ :: Вход</span>' +
            '<div class="hv-os" style="flex:1 1 auto">' +
              '<span class="hv-os__label">PRINTORIAN OS ./v2.0</span>' +
              '<button class="hv-os__x" type="button" data-auth-close aria-label="Закрыть">✕</button>' +
            '</div>' +
          '</div>' +
          '<div class="hv-path">' +
            '<div class="hv-path__crumbs"><span class="hv-path__here" data-auth-path>C:/PRINTORIAN/IDENTITY/AUTHENTICATE</span></div>' +
            '<div class="hv-path__status">СЕАНС :: <b>НЕ УСТАНОВЛЕН</b></div>' +
          '</div>' +
        '</div>' +

        '<div class="hv-modal__body hv-stack">' +

          '<div class="hv-seg" data-auth-tabs>' +
            '<button class="hv-seg__btn" type="button" data-auth-mode="signin"  aria-pressed="true">Вход</button>' +
            '<button class="hv-seg__btn" type="button" data-auth-mode="signup"  aria-pressed="false">Регистрация</button>' +
            '<button class="hv-seg__btn" type="button" data-auth-mode="recover" aria-pressed="false">Забыли пароль</button>' +
          '</div>' +

          /* ---------------------------------------------------- sign in */
          '<div data-auth-panel="signin" class="hv-stack">' +
            '<div class="hv-field">' +
              '<label class="hv-label" for="a-email">Электронная почта</label>' +
              '<input class="hv-input" id="a-email" type="email" autocomplete="email" placeholder="ВЫ@ПОЧТА.RU">' +
              '<span class="hv-field__err">Проверьте адрес электронной почты</span>' +
            '</div>' +
            '<div class="hv-field">' +
              '<div class="hv-row hv-row--between">' +
                '<label class="hv-label" for="a-pw" style="margin:0">Пароль</label>' +
                '<button class="hv-btn hv-btn--sm" type="button" data-auth-mode="recover">Забыли?</button>' +
              '</div>' +
              '<input class="hv-input" id="a-pw" type="password" autocomplete="current-password" placeholder="············">' +
            '</div>' +
            '<label class="hv-check">' +
              '<input type="checkbox" checked>' +
              '<span class="hv-check__body"><span class="hv-h">Запомнить на этом устройстве</span>' +
              '<span class="hv-hint">Сеанс живёт 12 часов</span></span>' +
            '</label>' +
            '<button class="hv-btn hv-btn--primary hv-btn--lg hv-btn--block" type="button">Войти</button>' +
            '<div class="hv-or">или</div>' +
            '<button class="hv-btn hv-btn--block" type="button">Войти по коду из письма</button>' +
          '</div>' +

          /* ---------------------------------------------------- sign up */
          '<div data-auth-panel="signup" hidden class="hv-stack">' +
            '<div class="hv-field">' +
              '<label class="hv-label" for="a-email2">Электронная почта</label>' +
              '<input class="hv-input" id="a-email2" type="email" autocomplete="email" placeholder="ВЫ@ПОЧТА.RU">' +
            '</div>' +
            '<div class="hv-field">' +
              '<label class="hv-label" for="a-pw2">Пароль</label>' +
              '<input class="hv-input" id="a-pw2" type="password" autocomplete="new-password" ' +
                     'data-auth-strength placeholder="НЕ КОРОЧЕ 12 СИМВОЛОВ">' +
              '<span class="hv-pw" data-level="0" style="margin-top:var(--hv-1)"><i></i><i></i><i></i><i></i></span>' +
              '<span class="hv-hint" data-auth-strength-label>МИНИМУМ 12 СИМВОЛОВ</span>' +
            '</div>' +
            '<label class="hv-check">' +
              '<input type="checkbox">' +
              '<span class="hv-check__body"><span class="hv-h">Я принимаю условия производства и возврата</span>' +
              '<span class="hv-hint">Обязательно для оформления заказов</span></span>' +
            '</label>' +
            '<button class="hv-btn hv-btn--primary hv-btn--lg hv-btn--block" type="button">Зарегистрироваться</button>' +
            '<p class="hv-micro" style="margin:0">' +
              'АДРЕС ДОСТАВКИ И РЕКВИЗИТЫ МОЖНО УКАЗАТЬ ПОЗЖЕ — ОНИ НУЖНЫ ТОЛЬКО ПРИ ОФОРМЛЕНИИ' +
            '</p>' +
          '</div>' +

          /* ---------------------------------------------------- recover */
          '<div data-auth-panel="recover" hidden class="hv-stack">' +
            '<p class="hv-prose" style="font-size:var(--hv-size-small);margin:0">' +
              'Укажите почту — пришлём код на шесть цифр. Ссылку не отправляем: ' +
              'код нельзя переслать случайно.' +
            '</p>' +
            '<div class="hv-field">' +
              '<label class="hv-label" for="a-email3">Электронная почта</label>' +
              '<input class="hv-input" id="a-email3" type="email" autocomplete="email" placeholder="ВЫ@ПОЧТА.RU">' +
            '</div>' +
            '<div>' +
              '<span class="hv-label" style="margin-bottom:var(--hv-2)">Код из письма</span>' +
              '<div class="hv-otp">' +
                '<input inputmode="numeric" maxlength="1" aria-label="Цифра 1">' +
                '<input inputmode="numeric" maxlength="1" aria-label="Цифра 2">' +
                '<input inputmode="numeric" maxlength="1" aria-label="Цифра 3">' +
                '<input inputmode="numeric" maxlength="1" aria-label="Цифра 4">' +
                '<input inputmode="numeric" maxlength="1" aria-label="Цифра 5">' +
                '<input inputmode="numeric" maxlength="1" aria-label="Цифра 6">' +
              '</div>' +
            '</div>' +
            '<button class="hv-btn hv-btn--primary hv-btn--lg hv-btn--block" type="button">Подтвердить</button>' +
            '<button class="hv-btn hv-btn--block" type="button" data-auth-mode="signin">Вернуться ко входу</button>' +
          '</div>' +
        '</div>' +

        '<div class="hv-panel__foot">' +
          '<span>ПАРОЛИ ХРАНЯТСЯ В ВИДЕ ХЭША ARGON2ID</span>' +
          '<a class="hv-mono" href="auth.html" style="color:inherit;text-decoration:none">ОТДЕЛЬНАЯ СТРАНИЦА ›</a>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);
    modal = overlay.querySelector('.hv-modal');

    overlay.addEventListener('click', function (ev) {
      if (ev.target === overlay || ev.target.closest('[data-auth-close]')) close();
      var m = ev.target.closest('[data-auth-mode]');
      if (m) setMode(m.dataset.authMode);
    });

    overlay.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); close(); }
    });

    wireStrength();
    wireOtp();
  }

  /* ------------------------------------------------------------- strength
     Deliberately crude and it says so: length first, variety second. A meter
     that claims more precision than it has teaches people to game the meter. */

  function wireStrength() {
    var input = overlay.querySelector('[data-auth-strength]');
    var meter = overlay.querySelector('.hv-pw');
    var label = overlay.querySelector('[data-auth-strength-label]');
    if (!input) return;

    input.addEventListener('input', function () {
      var v = input.value;
      var score = 0;
      if (v.length >= 12) score++;
      if (v.length >= 16) score++;
      if (/[^a-zA-Zа-яА-Я]/.test(v)) score++;
      if (/[A-ZА-Я]/.test(v) && /[a-zа-я]/.test(v)) score++;
      if (!v) score = 0;

      meter.dataset.level = String(score);
      label.textContent = !v ? 'МИНИМУМ 12 СИМВОЛОВ'
        : v.length < 12 ? 'СЛИШКОМ КОРОТКИЙ — ЕЩЁ ' + (12 - v.length)
        : ['', 'СЛАБЫЙ', 'СРЕДНИЙ', 'ХОРОШИЙ', 'НАДЁЖНЫЙ'][score];
    });
  }

  /* ------------------------------------------------------------------ otp
     Advance on entry, step back on backspace, and accept a pasted code into
     the first box — the three things people actually do with these. */

  function wireOtp() {
    var boxes = Array.prototype.slice.call(overlay.querySelectorAll('.hv-otp input'));
    boxes.forEach(function (box, i) {
      box.addEventListener('input', function () {
        box.value = box.value.replace(/\D/g, '').slice(0, 1);
        if (box.value && boxes[i + 1]) boxes[i + 1].focus();
      });
      box.addEventListener('keydown', function (ev) {
        if (ev.key === 'Backspace' && !box.value && boxes[i - 1]) boxes[i - 1].focus();
      });
      box.addEventListener('paste', function (ev) {
        var text = (ev.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '');
        if (!text) return;
        ev.preventDefault();
        boxes.forEach(function (b, j) { b.value = text[j] || ''; });
        (boxes[Math.min(text.length, boxes.length - 1)]).focus();
      });
    });
  }

  /* ----------------------------------------------------------------- mode */

  function setMode(mode) {
    if (!MODES[mode]) mode = 'signin';
    overlay.querySelectorAll('[data-auth-panel]').forEach(function (p) {
      p.hidden = p.dataset.authPanel !== mode;
    });
    overlay.querySelectorAll('[data-auth-tabs] [data-auth-mode]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.authMode === mode));
    });
    overlay.querySelector('#auth-t').textContent = MODES[mode].tab;
    overlay.querySelector('[data-auth-path]').textContent = MODES[mode].path;

    var first = overlay.querySelector('[data-auth-panel="' + mode + '"] input:not([type=checkbox])');
    if (first) first.focus();
  }

  /* ---------------------------------------------------------- open / close */

  function open(mode) {
    lastFocus = document.activeElement;
    overlay.hidden = false;
    document.body.style.overflow = 'hidden';
    setMode(mode);
  }

  function close() {
    overlay.hidden = true;
    document.body.style.overflow = '';
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  /* ------------------------------------------------------------------ init */

  build();

  document.addEventListener('click', function (ev) {
    var t = ev.target.closest('[data-auth-open]');
    if (!t) return;
    ev.preventDefault();
    open(t.dataset.authOpen || 'signin');
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !overlay.hidden) close();
  });
})();
