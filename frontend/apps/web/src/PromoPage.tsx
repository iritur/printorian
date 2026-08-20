import { useEffect, useState } from 'react'

import type { Locale } from '@printorian/ui'
import { api, plural, translate, useChrome } from '@printorian/ui'

import { Preview, hours, money } from './modelCard'
import type { MeasuredPrint } from './modelCard'

/**
 * Measured facts about the farm, from `/public/stats`.
 *
 * Every figure is nullable and means it: `null` is "not enough history to say".
 * This page's entire argument is that the shop publishes real numbers with the
 * method behind them, so a figure it cannot support must be absent rather than
 * rounded down into something that looks like a measurement.
 */
interface FarmStats {
  window_days: number
  orders_delivered: number
  on_time_percent: string | null
  print_hours: string | null
  failure_percent: string | null
  has_history: boolean
  /** Live rather than windowed, and therefore true on a farm's first day. */
  printers_total: number
  printers_printing: number
  next_free_minutes: number | null
  catalog_models: number
  /** The configurator's own limit, so the copy cannot drift from it. */
  max_upload_bytes: number
}

/**
 * A catalogue model as the promo teaser draws it.
 *
 * The narrow slice of `/catalog`'s card the section needs — the full one carries
 * facets, a price ladder and a materials table that no teaser reads.
 */
interface TeaserModel {
  slug: string
  title: string
  materials: string[]
  print_count: number
  measured: MeasuredPrint | null
  preview: Record<string, unknown>
}

/** The nine stages an order walks, matching `OrderStatus` rather than a copy. */
const STEPS: { key: string; caption: string }[] = [
  { key: 'order.status.paid', caption: 'ЗАГРУЗКА ИЛИ КАТАЛОГ' },
  { key: 'order.status.prep', caption: 'ЦВЕТ · ТИРАЖ · ОБРАБОТКА' },
  { key: 'order.status.queued', caption: 'ПРИНТЕР НАЗНАЧЕН САМ' },
  { key: 'order.status.printing', caption: 'КРУГЛОСУТОЧНО' },
  { key: 'order.status.post_production', caption: 'ПО ВАШЕМУ ВЫБОРУ' },
  { key: 'order.status.quality_check', caption: 'ОБЯЗАТЕЛЬНЫЙ ЭТАП' },
  { key: 'order.status.packing', caption: 'ПО ГАБАРИТУ' },
  { key: 'order.status.shipped', caption: 'КУРЬЕР ИЛИ САМОВЫВОЗ' },
  { key: 'order.status.completed', caption: 'ПОДТВЕРЖДЕНИЕ ПОЛУЧЕНИЯ' },
]

const FEATURES: { title: string; body: string }[] = [
  {
    title: 'Заказ сам находит принтер',
    body: 'Оплаченный заказ уходит на машину без диспетчера. Система сама сверяет объём печати, сопло, загруженный материал и очередь — и записывает, почему выбрала именно эту машину.',
  },
  {
    title: 'Опоздали — стало дешевле',
    body: 'Срок обещает та машина, которая будет печатать. Если он сорван, скидка начисляется сама, по опубликованному правилу, а не после разговора с менеджером.',
  },
  {
    title: 'Проблемы видны до оплаты',
    body: 'Модель проверяется на герметичность и тонкие стенки при загрузке. О том, что деталь не напечатается как задумано, вы узнаёте до оплаты, а не после.',
  },
  {
    title: 'Принтер не врёт',
    body: 'Недоступная машина показывается как недоступная. Система никогда не подставляет правдоподобные данные вместо настоящих — сломанная связь видна сразу.',
  },
  {
    title: 'Материал подбирается по задаче',
    body: 'Можно выбрать материал по названию, а можно описать задачу — нагрузка, улица, контакт с едой — и получить подбор из того, что действительно есть на складе.',
  },
  {
    title: 'Скидка не создаёт обрыв',
    body: 'Тираж на единицу дешевле, и переход между ступенями не может сделать больший заказ дороже меньшего. Это проверяется тестом, а не внимательностью.',
  },
]

const CONTRAST: [string, string, string][] = [
  ['Цена', 'Смета построчно', '«От 500 ₽»'],
  ['Срок', 'Обещан машиной', '«Дня три-четыре»'],
  ['Просрочка', 'Скидка автоматически', 'Извинения'],
  ['Проблемы модели', 'Видны до оплаты', 'Выясняются после'],
  ['Ход печати', '9 этапов онлайн', '«Ещё печатается»'],
  ['Брак', 'Перепечать за наш счёт', 'Спор'],
]

/**
 * The kit's worked example, printed as the kit prints it.
 *
 * Hard-coded, and that is the point of it: it shows what the *form* of an
 * estimate is — eight named articles, each with the basis it was derived from,
 * a discount that is a line rather than a wink, and the farm's own margin
 * spelled out. None of that can be demonstrated by a page that only promises to
 * show it later.
 *
 * Three things keep it from reading as a quote for whoever is looking. It names
 * a specific part and quantity in its header, «Так выглядит смета» says plainly
 * that it is an illustration, and the panel below it explains that the real one
 * is built from the reader's own model. The figures are the same ones report #57
 * works through in the journal, so the example and the article cannot drift into
 * saying different things.
 *
 * **One line is not the kit's.** The kit prints eight articles and a total of
 * 6 016 ₽, and the eight add up to 5 190 — it is 826 ₽ short, and the total and
 * the per-unit figure agree with each other rather than with the list. Shipping
 * that verbatim would put a sum that does not add up on the one page arguing
 * that every figure can be checked, and the reader who checks is exactly the
 * reader it is written for. The gap is filled with overhead, which is a real
 * category in the engine (`overhead.general`) sitting exactly where the engine
 * puts it — after the risk buffer, before the adjustments — so the nine lines
 * now reach the kit's own total.
 *
 * If it should stop being hard-coded, the honest version is a fixed public spec
 * priced by the real engine on request — same numbers, computed, and they would
 * move whenever the tariffs did.
 */
const SAMPLE: { article: string; basis?: string; amount: string; tone?: 'good' }[] = [
  { article: 'Материал', basis: '412.8 Г × 2.94 ₽/Г', amount: '1 213 ₽' },
  { article: 'Продувка при смене цвета', basis: '62 Г — УХОДИТ В ОТХОД', amount: '182 ₽' },
  { article: 'Амортизация принтера', basis: '23.5 Ч × 41.00 ₽/Ч', amount: '963 ₽' },
  { article: 'Электроэнергия', basis: '9.4 КВТ·Ч × 6.20 ₽', amount: '58 ₽' },
  { article: 'Труд · подготовка и надзор', amount: '1 530 ₽' },
  { article: 'Резерв на брак', basis: '6% · КЛАСС РИСКА B', amount: '254 ₽' },
  { article: 'Накладные расходы', basis: 'ПОМЕЩЕНИЕ · СКЛАД · УЧЁТ', amount: '826 ₽' },
  { article: 'Скидка за объём', amount: '− 396 ₽', tone: 'good' },
  { article: 'Прибыль', basis: '28% МАРЖА — ДА, МЫ ЕЁ ПОКАЗЫВАЕМ', amount: '1 386 ₽' },
]

function SampleEstimate({ onStart }: { onStart: () => void }) {
  return (
    <section className="hv-panel">
      <div className="hv-panel__head hv-panel__head--invert">
        <span>Так выглядит смета</span>
        <span className="hv-panel__aside" style={{ color: 'inherit' }}>
          BRACKET_V4 · 10 ШТ
        </span>
      </div>
      <div className="hv-panel__body hv-panel__body--tight">
        <ul className="hv-leaders">
          {SAMPLE.map((row) => (
            <li
              key={row.article}
              className="hv-leader"
              {...(row.tone ? { 'data-tone': row.tone } : {})}
            >
              <span className="hv-leader__k">
                {row.article}
                {row.basis && <span className="hv-leader__basis">{row.basis}</span>}
              </span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">{row.amount}</span>
            </li>
          ))}
        </ul>
        <hr className="hv-hr hv-hr--heavy" />
        <div className="hv-slab hv-slab--lg">
          <span>Итого</span>
          <span className="hv-slab__v">6 016 ₽</span>
        </div>
        <div className="hv-slab hv-slab--outline" style={{ marginTop: 'var(--hv-1)' }}>
          <span>За штуку</span>
          <span className="hv-slab__v">601.60 ₽</span>
        </div>
      </div>
      <div className="hv-panel__foot">
        <span>СРОК :: 74 Ч</span>
        {/* A link in the kit; a button here, because the configurator is a
            screen change rather than a page load. */}
        <button type="button" className="hv-mono" onClick={onStart}>
          СВОЯ МОДЕЛЬ ›
        </button>
      </div>
    </section>
  )
}

/**
 * The one outward-facing screen.
 *
 * It uses the same parts as the instrument panel at a larger size rather than a
 * second visual language — the argument of the whole product is that the panel
 * *is* the brand, so the landing page has to look like the thing it sells.
 *
 * The prose is static because it is prose. The **numbers are not**: they come
 * from `/public/stats` and disappear when the farm cannot support them.
 */
export function PromoPage({
  locale,
  onStart,
  onCabinet,
  onCatalog,
}: {
  locale: Locale
  onStart: () => void
  onCabinet: () => void
  /** The kit's second hero button — «N готовых моделей». */
  onCatalog: () => void
}) {
  const [stats, setStats] = useState<FarmStats | null>(null)
  const [models, setModels] = useState<TeaserModel[] | null>(null)

  useEffect(() => {
    api
      .get<FarmStats>('/public/stats')
      .then(setStats)
      // A landing page must render without them. Failing to reach the API is a
      // reason to show less, never a reason to show a blank screen.
      .catch(() => setStats(null))

    /*
      The four most-printed models, and the caption above them has to stay true:
      «все хотя бы раз напечатаны у нас». Eight are asked for and only the ones
      with a *measured* print are kept, so a catalogue whose top entries have
      never been run shows fewer cards rather than a claim it cannot support.
    */
    api
      .get<{ rows: TeaserModel[] }>('/catalog?sort=popular&limit=8')
      .then((table) => setModels(table.rows.filter((row) => row.measured).slice(0, 4)))
      .catch(() => setModels([]))
  }, [])

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)
  const proof = stats?.has_history ? stats : null

  /*
    The kit's strip here reads «KN-SOL.21 · ПРИНТЕРОВ :: 12 · ЗАКАЗОВ ВЫПОЛНЕНО
    :: 1 184». Two of those three the farm can state and one it cannot.

    The fleet size is missing on purpose. There is no public endpoint for it and
    there should not be one invented for a status strip: how many machines the
    farm runs is what `/public/stats` deliberately withholds, and the count is a
    capacity figure a competitor would like more than a customer would.

    What is here is measured and windowed, and says over what — «за 90 дней»
    beside a delivered count, because an unqualified total invites the reader to
    assume it is lifetime.
  */
  useChrome(
    proof
      ? {
          meta: [
            { label: 'ВЫПОЛНЕНО', value: String(proof.orders_delivered) },
            { label: 'ЗА', value: `${proof.window_days} ДН` },
            ...(proof.on_time_percent
              ? [{ label: 'В СРОК', value: `${proof.on_time_percent}%` }]
              : []),
          ],
        }
      : null,
  )

  return (
    <>
      <section className="hv-hero">
        <div>
          <span className="hv-micro">АВТОМАТИЧЕСКАЯ ФЕРМА 3D-ПЕЧАТИ</span>
          <h1 className="hv-hero__title">Printorian</h1>
        </div>

        <p className="hv-hero__sub">
          Загрузите модель — увидите <b>полную смету построчно</b> с основанием расчёта под
          каждой цифрой. Не «от 500 ₽» и не «уточним по телефону»: сумма и срок известны до
          оплаты, а срок обещает та машина, которая будет печатать.
        </p>

        <div className="hv-row">
          {/*
            The kit's primary reads «Рассчитать за 1.2 секунды». The time is not
            here: nothing measures how long a quote takes, so the claim would be
            a decoration on the one page arguing that this shop decorates
            nothing. Instrumenting `/pricing/quote` would make it sayable.
          */}
          <button type="button" className="hv-btn hv-btn--primary hv-btn--lg" onClick={onStart}>
            {t('nav.configure')}
          </button>
          {/*
            «146 готовых моделей» in the kit, counted here. Absent on a farm
            whose catalogue is empty rather than offering a door into nothing.
          */}
          {(stats?.catalog_models ?? 0) > 0 && (
            <button type="button" className="hv-btn hv-btn--lg" onClick={onCatalog}>
              {stats?.catalog_models}{' '}
              {plural(
                stats?.catalog_models ?? 0,
                'готовая модель',
                'готовые модели',
                'готовых моделей',
              )}
            </button>
          )}
        </div>

        {/*
          The kit's five-item ticker, carrying only figures the server measured.

          Two kinds of fact sit here and they behave differently. The fleet line
          and the wait describe *this second*, so they are true on a farm's first
          day. Everything else is counted over the window, so it is absent until
          there is a history — and the window is stated, because «заказов
          выполнено 1 184» unqualified invites the reader to assume a lifetime.

          «Средний расчёт 1.2 с» is the one kit item with nothing behind it.
          Nothing times a quote, so it is missing rather than asserted.
        */}
        {stats && (
          <div className="hv-ticker">
            {stats.printers_total > 0 && (
              <span>
                СЕЙЧАС ПЕЧАТАЕТСЯ{' '}
                <b className="hv-live">
                  {stats.printers_printing} из {stats.printers_total}
                </b>
              </span>
            )}
            {proof?.on_time_percent && (
              <span>
                СРОК СОБЛЮДЁН <b>{proof.on_time_percent}%</b>
              </span>
            )}
            {proof && (
              <span>
                ЗАКАЗОВ ВЫПОЛНЕНО <b>{proof.orders_delivered}</b> ЗА {proof.window_days} СУТОК
              </span>
            )}
            {/*
              `0` is «сейчас» and `null` is silence — a farm whose machines have
              not reported must not answer «через 0 мин», which is the reassuring
              figure ADR-0007 stops a driver inventing.
            */}
            {stats.next_free_minutes !== null && (
              <span>
                СВОБОДНАЯ МАШИНА{' '}
                <b>
                  {stats.next_free_minutes === 0
                    ? 'СЕЙЧАС'
                    : `ЧЕРЕЗ ${stats.next_free_minutes} МИН`}
                </b>
              </span>
            )}
          </div>
        )}
      </section>

      <section className="hv-main">
        <div className="hv-cols hv-cols--2">
          <div>
            <span className="hv-micro">ГЛАВНОЕ ОТЛИЧИЕ</span>
            <h2 className="hv-display hv-display--rule">Цена без тумана</h2>
            <p className="hv-prose">
              Почти все считают «по объёму, умножить на коэффициент». Мы считаем независимые
              статьи и показываем каждую. Под ценой стоит основание: сколько граммов, по какой
              ставке, сколько часов машины.
            </p>
            <p className="hv-prose">
              Это не щедрость, а следствие устройства: расчёт — чистая функция от тарифов, и
              если цифру нельзя вывести из тарифа, её не существует. Побочный эффект оказался
              важнее — видя структуру, заказчик сам находит, где сэкономить.
            </p>
          </div>

          <div className="hv-stack">
            <SampleEstimate onStart={onStart} />

            <div className="hv-frame">
              <span className="hv-micro">СМЕТА СТРОИТСЯ ПОД ВАШУ МОДЕЛЬ</span>
              <p className="hv-prose">
                Каждая строка — отдельная статья с основанием расчёта. Наведите на любой
                параметр в конфигураторе, и увидите, на сколько он изменит сумму, до того как
                согласитесь.
              </p>
              <div className="hv-row">
                <button type="button" className="hv-btn" onClick={onStart}>
                  Посчитать свою модель
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="hv-main">
        <span className="hv-micro">ЧТО ЭТО ДАЁТ</span>
        <div className="hv-grid hv-grid--3">
          {FEATURES.map((feature, index) => (
            <div className="hv-feature" key={feature.title}>
              <span className="hv-feature__n">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <h3 className="hv-feature__t">{feature.title}</h3>
                <p className="hv-feature__b">{feature.body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="hv-main">
        <div className="hv-frame hv-frame--wide">
          <div className="hv-row">
            <h2 className="hv-h">Как это работает</h2>
            <span className="hv-micro">ОТ ФАЙЛА ДО КУРЬЕРА</span>
          </div>
          {/*
            The stage names come from the order state machine, not from a copy of
            it. A stage renamed on the server renames itself here, which is what
            keeps a marketing page from describing a pipeline that changed.
          */}
          <div className="hv-pipe">
            {STEPS.map((step, index) => (
              <div className="hv-pipe__step" key={step.key}>
                <div className="hv-pipe__n">{String(index + 1).padStart(2, '0')}</div>
                <div className="hv-pipe__k">
                  {t(step.key as Parameters<typeof translate>[1])}
                </div>
                <div className="hv-pipe__t">{step.caption}</div>
              </div>
            ))}
          </div>
          <p className="hv-micro">ВСЕ ДЕВЯТЬ ЭТАПОВ ВИДНЫ В КАБИНЕТЕ · С ВРЕМЕНЕМ</p>
        </div>
      </section>

      {proof && (
        <section className="hv-main">
          <div className="hv-frame hv-frame--wide">
            <span className="hv-micro">ЗА {proof.window_days} СУТОК РАБОТЫ · ИЗМЕРЕНО</span>
            <div className="hv-grid hv-grid--4">
              <div className="hv-bignum">
                <span className="hv-bignum__v">{proof.orders_delivered}</span>
                <span className="hv-bignum__k">Заказов выполнено</span>
              </div>
              {proof.on_time_percent && (
                <div className="hv-bignum">
                  <span className="hv-bignum__v">{proof.on_time_percent}%</span>
                  <span className="hv-bignum__k">В обещанный срок</span>
                  <span className="hv-bignum__d">ОСТАЛЬНЫМ НАЧИСЛЕНА СКИДКА</span>
                </div>
              )}
              {proof.print_hours && (
                <div className="hv-bignum">
                  <span className="hv-bignum__v">{proof.print_hours}</span>
                  <span className="hv-bignum__k">Часов печати</span>
                  <span className="hv-bignum__d">В ТОМ ЧИСЛЕ НОЧЬЮ</span>
                </div>
              )}
              {proof.failure_percent && (
                <div className="hv-bignum">
                  <span className="hv-bignum__v">{proof.failure_percent}%</span>
                  <span className="hv-bignum__k">Доля брака</span>
                  <span className="hv-bignum__d">ПЕРЕПЕЧАТЬ ЗА НАШ СЧЁТ</span>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      <section className="hv-main">
        <div className="hv-cols hv-cols--2">
          <div>
            <span className="hv-micro">ЧЕСТНО О РАЗНИЦЕ</span>
            <h2 className="hv-display">Не для всех</h2>
            <p className="hv-prose">
              Мы не самые дешёвые. Резерв на брак, обязательный контроль качества и оплата
              простоя машин — всё это в цене. Если нужна одна деталь «как получится» и
              подешевле, дешевле будет у частника с одним принтером.
            </p>
            <p className="hv-prose">
              Мы нужны, когда важен предсказуемый срок, повторяемость от партии к партии и
              возможность спросить, откуда взялась каждая строка в счёте.
            </p>
          </div>

          <div className="hv-table-wrap">
            <table className="hv-table hv-vs-table">
              <thead>
                <tr>
                  <th />
                  <th>Printorian</th>
                  <th>Обычно</th>
                </tr>
              </thead>
              <tbody>
                {CONTRAST.map(([topic, ours, theirs]) => (
                  <tr key={topic}>
                    <td>{topic}</td>
                    <td>{ours}</td>
                    <td>{theirs}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/*
        The kit's catalogue teaser. Every figure on a card is measured — the time
        and the price come from the last real print of that model, which is what
        `measured` being `null` until then is for. A model the farm has never
        printed is not shown here at all, because the caption says it has been.
      */}
      {models !== null && models.length > 0 && (
        <section className="hv-main">
          <div className="hv-row hv-row--between" style={{ marginBottom: 'var(--hv-3)' }}>
            <div className="hv-row">
              <h2 className="hv-h">Готовые модели</h2>
              <span className="hv-micro">ВСЕ ХОТЯ БЫ РАЗ НАПЕЧАТАНЫ У НАС</span>
            </div>
            <button type="button" className="hv-btn hv-btn--sm" onClick={onCatalog}>
              Весь каталог{stats?.catalog_models ? ` · ${stats.catalog_models}` : ''} ›
            </button>
          </div>

          <div className="hv-cat">
            {models.map((model) => (
              <button
                key={model.slug}
                type="button"
                className="hv-frame hv-model"
                data-model=""
                onClick={onCatalog}
              >
                <div className="hv-model__view">
                  <span className="hv-model__tag hv-model__tag--tl">
                    {hours(model.measured?.minutes ?? '0')}
                  </span>
                  <Preview card={model} />
                  {model.measured?.price && (
                    <span className="hv-model__tag hv-model__tag--br">
                      {money(model.measured.price, locale)}
                    </span>
                  )}
                </div>
                <div className="hv-model__body">
                  <h3 className="hv-model__title">{model.title}</h3>
                  <div className="hv-model__meta">
                    <span>{(model.materials[0] ?? '').toUpperCase()}</span>
                    <span>
                      НАПЕЧАТАН {model.print_count}{' '}
                      {plural(model.print_count, 'РАЗ', 'РАЗА', 'РАЗ')}
                    </span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="hv-cta">
        <div>
          <div className="hv-h hv-h--lead">Проверьте на своей модели</div>
          {/*
            `color: inherit` and the dimming are the kit's, and they are not
            decoration: `hv-cta` is the one inverted band on the page, and
            `hv-micro`'s own colour is chosen for a dark ground.

            The kit writes «STL, 3MF, STEP». Only STL is true — `ingest` measures
            an STL and nothing else, so a 3MF is stored without geometry and a
            STEP is not even a recognised extension. Either would be accepted and
            then refused a price, which is a worse first impression than a
            shorter list. The size comes from the server's own `max_upload_bytes`
            rather than a number typed here, so raising the limit changes the
            promise with it.
          */}
          <p
            className="hv-micro"
            style={{ margin: 'var(--hv-2) 0 0', color: 'inherit', opacity: 0.7 }}
          >
            РАСЧЁТ БЕЗ РЕГИСТРАЦИИ · STL
            {stats ? ` · ДО ${Math.round(stats.max_upload_bytes / (1024 * 1024))} МБ` : ''}
          </p>
        </div>
        <div className="hv-row">
          <button type="button" className="hv-btn hv-btn--primary hv-btn--lg" onClick={onStart}>
            Загрузить модель
          </button>
          <button type="button" className="hv-btn hv-btn--lg" onClick={onCatalog}>
            Выбрать готовую
          </button>
        </div>
      </section>

      {/*
        The kit's footer offers six destinations. Three of them — the catalogue,
        the journal and the public fleet view — do not exist yet, and the fourth,
        the dashboard, is staff-only and lives in the console (ADR-0016). They are
        left out rather than rendered dead: a link that goes nowhere is a worse
        answer than no link, and on the page arguing that this shop does not
        overpromise, it is the wrong kind of irony.
      */}
      <section className="hv-main">
        <div className="hv-grid hv-grid--4">
          <div>
            <span className="hv-label">Заказчику</span>
            <nav className="hv-nav">
              <button type="button" className="hv-nav__item" onClick={onStart}>
                <span className="hv-nav__lead">Рассчитать</span>
              </button>
              <button type="button" className="hv-nav__item" onClick={onCabinet}>
                <span className="hv-nav__lead">{t('nav.my_orders')}</span>
              </button>
            </nav>
          </div>

          <div>
            <span className="hv-label">О ферме</span>
            <p className="hv-prose hv-prose--small">
              Ферма печатает круглосуточно. Каждый заказ проходит девять этапов, и
              все они видны в кабинете — с временем и номером машины.
            </p>
          </div>

          <div>
            <span className="hv-label">Контакты</span>
            <p className="hv-prose hv-prose--small">
              Москва, цех KN-SOL.21
              <br />
              Выдача с 09:00 до 21:00
              <br />
              farm@printorian.example
            </p>
          </div>

          <div>
            <span className="hv-label">Оплата</span>
            <p className="hv-prose hv-prose--small">
              Карта · СБП · счёт для юрлиц
              <br />
              Возврат до начала печати — 100%
            </p>
          </div>
        </div>
      </section>
    </>
  )
}
