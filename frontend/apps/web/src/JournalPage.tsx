import { useEffect, useMemo, useState } from 'react'

import { FilterChips, SECTIONS, SECTION_META, api, useChrome } from '@printorian/ui'
import type { Locale, Section } from '@printorian/ui'

/**
 * «Журнал» — the index.
 *
 * The lead block, the featured report, a filter row that counts itself, a grid,
 * and an archive of everything the grid did not show.
 *
 * The counts on the chips come from the server and describe the *journal*, not
 * the page. That is deliberate and it is the one thing a naive implementation
 * gets wrong: counting the rows on screen would tell a reader the farm has
 * written three reports about cost when it has written five and shown three.
 */

export interface PostCard {
  slug: string
  number: number
  title: string
  excerpt: string
  section: Section
  author: string
  read_minutes: number
  is_published: boolean
  published_at: string | null
}

interface JournalIndex {
  rows: PostCard[]
  counts: { section: Section; count: number }[]
  /** What the filter chips describe. Includes an editor's own drafts. */
  total: number
  /** Issues actually out. Never counts drafts, whoever is looking. */
  published_total: number
  /**
   * Reports per week, measured from the real publication gaps.
   *
   * `null` while the journal has no rhythm to measure — a first report, or a
   * batch published on one day. The stat is then absent rather than showing
   * «0 / НЕД», which reads as "we have stopped".
   */
  weekly_rate: string | null
}

/** How many cards the grid shows before the rest becomes archive rows. */
const GRID = 6

/**
 * The feed's address.
 *
 * Same-origin through the dev proxy and the production reverse proxy alike
 * (ADR-0003), so it needs no configured host — which is also what lets the
 * server derive absolute item URLs from the request.
 */
const RSS = '/api/journal/rss'

/**
 * A count the server has not sent yet.
 *
 * «—», never `0`. Zero is a meaningful answer here — "the journal is empty" — so
 * using it as a fallback makes "I do not know" indistinguishable from a fact, and
 * the reader has no way to tell which they are looking at. This is the same rule
 * the catalogue and the configurator follow for every unmeasured figure.
 */
const UNKNOWN = '—'

const count = (value: number | undefined) => (value === undefined ? UNKNOWN : value)

/**
 * A publication date, in the reader's own convention.
 *
 * Following the locale rather than hardcoding `ru-RU`: the journal's prose is
 * Russian, but 07.08.2026 and 07/08/2026 mean different days to different readers
 * and that is the one thing on this card that must not be ambiguous.
 */
const dateOf = (iso: string | null, locale: Locale) =>
  iso ? new Date(iso).toLocaleDateString(locale) : '—'

/** `1.0` → `1`, `0.5` → `0.5`. A whole number should not carry a decimal. */
function trimZero(rate: string): string {
  const value = Number(rate)
  return Number.isInteger(value) ? String(value) : rate
}

/**
 * «Рассылка» — the kit's subscription card.
 *
 * The address is really recorded and the one-click unsubscribe the card promises
 * really works. What does *not* happen yet is the sending: this deployment has no
 * mailer, so the confirmation says so rather than implying a letter is on its way.
 * A form that thanks you for subscribing to nothing is the kind of small lie this
 * whole storefront is built to avoid.
 */
function Newsletter() {
  const [email, setEmail] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'failed'>('idle')

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setState('sending')
    try {
      await api.post('/journal/subscribe', { email })
      setState('done')
      setEmail('')
    } catch {
      setState('failed')
    }
  }

  return (
    <form className="hv-frame hv-frame--live" onSubmit={(event) => void submit(event)}>
      <span className="hv-h hv-live">Рассылка</span>
      <p
        className="hv-prose"
        style={{ fontSize: 'var(--hv-size-small)', marginTop: 'var(--hv-2)' }}
      >
        Один отчёт в неделю. Без анонсов, скидок и «полезных подборок» — только новый
        материал журнала.
      </p>

      {state === 'done' ? (
        <p className="hv-hint hv-good" role="status" style={{ marginTop: 'var(--hv-3)' }}>
          Адрес сохранён. Письма начнут приходить, когда ферма подключит отправку.
        </p>
      ) : (
        <div className="hv-row" style={{ marginTop: 'var(--hv-3)' }}>
          <input
            className="hv-input"
            type="email"
            required
            placeholder="ВЫ@ПОЧТА.RU"
            style={{ flex: '1 1 200px' }}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-label="Электронная почта"
          />
          <button className="hv-btn" type="submit" disabled={state === 'sending'}>
            Подписаться
          </button>
        </div>
      )}

      {state === 'failed' && (
        <p className="hv-hint hv-bad" role="alert" style={{ marginTop: 'var(--hv-2)' }}>
          Не получилось сохранить адрес. Попробуйте ещё раз.
        </p>
      )}

      <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
        ОТПИСКА В ОДИН КЛИК · АДРЕС НЕ ПЕРЕДАЁТСЯ ТРЕТЬИМ ЛИЦАМ
      </p>
    </form>
  )
}

export function JournalPage({
  locale,
  onRead,
}: {
  locale: Locale
  onRead: (slug: string) => void
}) {
  const [index, setIndex] = useState<JournalIndex | null>(null)
  const [featured, setFeatured] = useState<PostCard | null>(null)
  const [section, setSection] = useState<Section | null>(null)

  /*
    The kit's `JOURNAL.INDEX[18]` plus the filter, because the filter is the one
    thing about this screen's state that a link does not carry — «PRESET ::
    ALL_REPORTS» in the kit, and the chosen section when there is one.
  */
  useChrome(
    index
      ? {
          meta: [
            { label: 'JOURNAL.INDEX', value: String(index.published_total) },
            {
              label: 'PRESET',
              value: section ? SECTION_META[section].label.toUpperCase() : 'ALL_REPORTS',
            },
          ],
        }
      : null,
  )
  const [text, setText] = useState('')
  const [debounced, setDebounced] = useState('')

  // Typing is not a query. Without this every keystroke is a round trip and the
  // answers arrive out of order.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(text), 220)
    return () => window.clearTimeout(timer)
  }, [text])

  useEffect(() => {
    const params = new URLSearchParams()
    if (section) params.set('section', section)
    if (debounced.trim()) params.set('q', debounced.trim())
    void api
      .get<JournalIndex>(`/journal?${params}`)
      .then(setIndex)
      .catch(() => setIndex({ rows: [], counts: [], total: 0, published_total: 0, weekly_rate: null }))
  }, [section, debounced])

  // The featured report is the newest one overall, so it does not follow the
  // filters: the lead block introduces the journal, not the current query.
  useEffect(() => {
    void api
      .get<PostCard | null>('/journal/latest')
      .then(setFeatured)
      .catch(() => setFeatured(null))
  }, [])

  const counted = useMemo(() => {
    /*
      `null` until the index arrives, not `0`.

      A section the server has counted at zero and a section nobody has counted
      yet are different facts, and the chips used to print both as `0` while the
      «Все» chip beside them printed «—» — one row of numbers making two
      different promises. The em dash is `FilterChips`' answer to a null count.
    */
    if (index === null) return SECTIONS.map((name) => ({ name, count: null }))
    const table = new Map(index.counts.map((entry) => [entry.section, entry.count]))
    return SECTIONS.map((name) => ({ name, count: table.get(name) ?? 0 }))
  }, [index])

  const rows = index?.rows ?? []
  const grid = rows.slice(0, GRID)
  const archive = rows.slice(GRID)
  /*
    Whether the journal *has* a back catalogue, which is what decides the footer's
    shape — not whether the current filter happens to fill one. Measured against
    the published count so the panel does not appear and disappear as an editor
    toggles between sections.
  */
  const hasArchive = (index?.published_total ?? 0) > GRID

  return (
    <div className="hv-stack hv-stack--4">
      <section className="hv-cols hv-cols--2">
        <div className="hv-frame hv-frame--wide">
          {/*
            Issues out, not rows on screen. An editor with a draft open sees «12
            ВЫПУСКОВ» here and «ВСЕ 13» on the chip below, and both are true: one
            counts what the farm published, the other what the filter will show.
          */}
          <span className="hv-micro">
            ЖУРНАЛ ФЕРМЫ · {count(index?.published_total)} ВЫПУСКОВ
          </span>
          <h1 className="hv-display hv-display--rule" style={{ marginTop: 'var(--hv-3)' }}>
            Журнал
          </h1>
          <p className="hv-prose" style={{ marginTop: 'var(--hv-4)' }}>
            Как устроена автоматическая ферма 3D-печати изнутри. Мы публикуем расчёты,
            решения и ошибки — включая те, которые пришлось откатывать. Ничего
            маркетингового: только то, что можно проверить по цифрам.
          </p>
          <div className="hv-row" style={{ marginTop: 'var(--hv-4)' }}>
            {featured && (
              <button
                className="hv-btn hv-btn--primary"
                type="button"
                onClick={() => onRead(featured.slug)}
              >
                Читать последний отчёт
              </button>
            )}
            {/*
              A real link, not a button with a handler. A reader subscribes by
              handing this address to their own reader, so it has to be something
              they can copy, middle-click or drag — all of which a `<button>`
              takes away. `hv-btn` renders an anchor identically.
            */}
            <a className="hv-btn" href={RSS} rel="alternate" type="application/rss+xml">
              Подписаться на RSS
            </a>
          </div>
        </div>

        {/*
          The featured report. Absent for an empty journal rather than a frame
          around nothing — which is also why the server answers `null` here
          instead of inventing a placeholder.
        */}
        {featured && (
          <button
            className="hv-frame hv-post"
            type="button"
            style={{ justifyContent: 'space-between' }}
            onClick={() => onRead(featured.slug)}
          >
            <div className="hv-post__meta">
              <span>
                ОТЧЁТ :: <b>#{featured.number}</b>
              </span>
              <span>{dateOf(featured.published_at, locale)}</span>
              <span>{SECTION_META[featured.section].label.toUpperCase()}</span>
              <span>{featured.read_minutes} МИН ЧТЕНИЯ</span>
            </div>
            <div>
              <h2 className="hv-post__title" style={{ fontSize: '1.4rem' }}>
                {featured.title}
              </h2>
              <p className="hv-post__excerpt" style={{ marginTop: 'var(--hv-2)' }}>
                {featured.excerpt}
              </p>
            </div>
            <div className="hv-post__foot">
              <span>ГЛАВНЫЙ МАТЕРИАЛ ВЫПУСКА</span>
              <span>ЧИТАТЬ ›</span>
            </div>
          </button>
        )}
      </section>

      <div className="hv-row hv-row--between">
        {/*
          Clicking the active chip clears it — the rule now lives in the shared
          component rather than in this file, which is the point of there being
          a shared component: the console's tables and the account's order
          history had each decided it for themselves.
        */}
        <FilterChips
          label="Разделы журнала"
          all={{ label: 'Все', count: index?.total ?? null }}
          chips={counted.map((entry) => ({
            key: entry.name,
            label: SECTION_META[entry.name].label,
            count: entry.count,
            tone: SECTION_META[entry.name].tone,
          }))}
          active={section}
          onSelect={(key) => setSection(key as Section | null)}
        />
        <input
          className="hv-input"
          type="search"
          placeholder="ПОИСК ПО ЖУРНАЛУ"
          style={{ width: 260 }}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </div>

      {rows.length === 0 ? (
        <p className="hv-hint">
          {debounced.trim() || section
            ? 'По этому запросу отчётов нет.'
            : 'Журнал пока пуст.'}
        </p>
      ) : (
        <div className="hv-grid hv-grid--3">
          {grid.map((post) => (
            <button
              key={post.slug}
              className="hv-frame hv-post"
              type="button"
              data-status={post.section}
              onClick={() => onRead(post.slug)}
            >
              <div className="hv-post__meta">
                <span>
                  ОТЧЁТ :: <b>#{post.number}</b>
                </span>
                <span>{dateOf(post.published_at, locale)}</span>
                {/*
                  Only an editor ever sees this, because only an editor is served
                  drafts — but when they do, it has to be unmistakable which cards
                  the public cannot see.
                */}
                {!post.is_published && <span className="hv-warn">ЧЕРНОВИК</span>}
              </div>
              <h2 className="hv-post__title">{post.title}</h2>
              <p className="hv-post__excerpt">{post.excerpt}</p>
              <div className="hv-post__foot">
                <span>{SECTION_META[post.section].label.toUpperCase()}</span>
                <span>{post.read_minutes} МИН</span>
              </div>
            </button>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        /*
          Two columns whenever the journal has a back catalogue at all, filter or
          no filter.

          This used to drop the class when `archive` came back empty, which made
          clicking a chip reflow the whole footer: the archive vanished, the stats
          and the newsletter fell into the wide `1fr` cell, and three stat frames
          stretched to a third of the window each. The archive being empty is a
          fact about the *filter*; the section's shape is a fact about the journal,
          and the two should not be the same decision.
        */
        <section className={hasArchive ? 'hv-cols hv-cols--2' : undefined}>
          {hasArchive && (
            <div className="hv-panel">
              <div className="hv-panel__head">
                <span>Архив</span>
                {archive.length > 0 && (
                  <span className="hv-panel__aside">
                    ВЫПУСКИ #{archive[archive.length - 1]?.number}–#{archive[0]?.number}
                  </span>
                )}
              </div>
              <div className="hv-panel__body hv-panel__body--tight">
                {/*
                  Empty when the filter has nothing older to show — said out loud,
                  because a panel headed «Архив» with nothing under it reads as a
                  page that failed to load.
                */}
                {archive.length === 0 && (
                  <p className="hv-hint" style={{ padding: 'var(--hv-2) 0' }}>
                    В этом разделе больше ничего нет.
                  </p>
                )}
                {archive.map((post) => (
                  <button
                    key={post.slug}
                    className="hv-post-row"
                    type="button"
                    onClick={() => onRead(post.slug)}
                  >
                    <span className="hv-post-row__n">#{post.number}</span>
                    <span className="hv-post-row__t">{post.title}</span>
                    <span className="hv-leader__fill" aria-hidden="true" />
                    <span className="hv-post-row__d">{dateOf(post.published_at, locale)}</span>
                  </button>
                ))}
              </div>
              <div className="hv-panel__foot">
                <span>
                  ПОКАЗАНО {rows.length} ИЗ {index?.total ?? rows.length}
                </span>
              </div>
            </div>
          )}

          <div className="hv-stack">
            <div className="hv-grid hv-grid--3">
              <div className="hv-frame hv-stat">
                <span className="hv-label">Публикаций</span>
                {/* Published, for the same reason the lead block is. */}
                <span className="hv-stat__v">{count(index?.published_total)}</span>
              </div>
              <div className="hv-frame hv-stat">
                <span className="hv-label">Разделов</span>
                {/*
                  Sections that actually carry a report, not the five the enum
                  defines: a journal with nothing in Постобработка has four.
                  Zero-padded, as the kit sets it — «05» beside «18» keeps the
                  three figures the same width in a tabular face.
                */}
                <span className="hv-stat__v">
                  {String(
                    counted.filter((entry) => entry.count !== null && entry.count > 0).length,
                  ).padStart(2, '0')}
                </span>
              </div>
              {/*
                «ВЫХОДИТ 1 / НЕД» — measured, not declared. Absent while the
                journal has no rhythm, because a cadence nobody has established is
                not a fact about the farm.
              */}
              {index?.weekly_rate && (
                <div className="hv-frame hv-stat" data-tone="live">
                  <span className="hv-label">Выходит</span>
                  <span className="hv-stat__v">
                    {trimZero(index.weekly_rate)}
                    <small> / НЕД</small>
                  </span>
                </div>
              )}
            </div>

            <Newsletter />
          </div>
        </section>
      )}
    </div>
  )
}
