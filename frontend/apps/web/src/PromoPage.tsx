import { useEffect, useState } from 'react'

import type { Locale } from '@printorian/ui'
import { api, translate } from '@printorian/ui'

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
}: {
  locale: Locale
  onStart: () => void
  onCabinet: () => void
}) {
  const [stats, setStats] = useState<FarmStats | null>(null)

  useEffect(() => {
    api
      .get<FarmStats>('/public/stats')
      .then(setStats)
      // A landing page must render without them. Failing to reach the API is a
      // reason to show less, never a reason to show a blank screen.
      .catch(() => setStats(null))
  }, [])

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)
  const proof = stats?.has_history ? stats : null

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
          <button type="button" className="hv-btn hv-btn--primary hv-btn--lg" onClick={onStart}>
            {t('nav.configure')}
          </button>
        </div>

        {/*
          The ticker carries only figures the server measured. On a farm with no
          history it is absent rather than filled with plausible ones — which is
          the claim the section beneath it is making.
        */}
        {proof && (
          <div className="hv-ticker">
            <span>
              ЗАКАЗОВ ВЫПОЛНЕНО <b>{proof.orders_delivered}</b>
            </span>
            {proof.on_time_percent && (
              <span>
                СРОК СОБЛЮДЁН <b>{proof.on_time_percent}%</b>
              </span>
            )}
            {proof.print_hours && (
              <span>
                ЧАСОВ ПЕЧАТИ <b>{proof.print_hours}</b>
              </span>
            )}
            <span>ЗА {proof.window_days} СУТОК</span>
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

          {/*
            The kit shows a sample breakdown here. It is deliberately not
            reproduced: a hand-written table of prices on the page that argues
            prices are never hand-written would undo the argument. The real one
            is one click away and is computed by the same engine.
          */}
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

      <section className="hv-cta">
        <div>
          <div className="hv-h hv-h--lead">Проверьте на своей модели</div>
          <p className="hv-micro">РАСЧЁТ БЕЗ РЕГИСТРАЦИИ · STL</p>
        </div>
        <div className="hv-row">
          <button type="button" className="hv-btn hv-btn--primary hv-btn--lg" onClick={onStart}>
            Загрузить модель
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
