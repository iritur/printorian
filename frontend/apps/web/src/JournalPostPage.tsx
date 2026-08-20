import { useEffect, useRef, useState } from 'react'

import { Article, SECTION_META, anchorOf, api, useChrome } from '@printorian/ui'
import type { Block, Locale, Section } from '@printorian/ui'

/**
 * One report, as the kit's `blog-post` draws it: contents on the left, the
 * article in the middle, the report's own data on the right.
 *
 * The contents list is not written by the author. It is derived from the
 * headings the article actually contains — by the server for the text, by
 * `anchorOf` for the target — so it cannot describe a section that is not there
 * or miss one that is.
 */

interface TocEntry {
  anchor: string
  text: string
}

interface Neighbour {
  slug: string
  number: number
  title: string
}

interface PostView {
  slug: string
  number: number
  title: string
  lede: string
  excerpt: string
  section: Section
  author: string
  data_note: string
  read_minutes: number
  is_published: boolean
  published_at: string | null
  blocks: Block[]
  toc: TocEntry[]
  neighbours: Neighbour[]
}

export function JournalPostPage({
  locale,
  slug,
  onBack,
  onRead,
  onConfigure,
}: {
  locale: Locale
  slug: string
  onBack: () => void
  onRead: (slug: string) => void
  onConfigure: () => void
}) {
  const [post, setPost] = useState<PostView | null>(null)
  const [error, setError] = useState(false)
  /** Which heading the reader is in, for the contents list's `aria-current`. */
  const [here, setHere] = useState<string | null>(null)
  const progress = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setPost(null)
    setError(false)
    void api
      .get<PostView>(`/journal/${slug}`)
      .then(setPost)
      .catch(() => setError(true))
  }, [slug])

  /*
    The kit's `CODE · REPORT_DATE · SECTION`, with the report number in place of
    the code — the kit's `2AFPQJJTVNGDOBFF` is an account identifier and has no
    business on a page anybody can read without signing in.

    A draft says so here as well as on the page. The chrome is what ends up in a
    screenshot, and an unpublished report that looked published in one is exactly
    the confusion this strip should prevent.
  */
  useChrome(
    post
      ? {
          meta: [
            { label: 'ОТЧЁТ', value: `#${post.number}` },
            { label: 'SECTION', value: SECTION_META[post.section].label.toUpperCase() },
            ...(post.is_published ? [] : [{ label: 'СТАТУС', value: 'ЧЕРНОВИК' }]),
          ],
        }
      : null,
  )

  /**
   * The reading bar, and the contents entry that follows the reader down.
   *
   * The kit runs this as an inline script and calls it demo-only; this is the
   * component version it points at. Passive, because a scroll handler that can
   * block scrolling is worse than no scroll handler.
   */
  useEffect(() => {
    if (!post) return
    const tick = () => {
      const scrollable = document.documentElement.scrollHeight - window.innerHeight
      const bar = progress.current
      if (bar) {
        bar.style.transform = `scaleX(${scrollable > 0 ? window.scrollY / scrollable : 0})`
      }
      // The last heading whose top has passed the upper third of the viewport.
      // A midpoint would flip while a short section is still being read.
      const passed = post.toc
        .map((entry) => document.getElementById(entry.anchor))
        .filter((node): node is HTMLElement => node !== null)
        .filter((node) => node.getBoundingClientRect().top <= window.innerHeight / 3)
      setHere(passed.at(-1)?.id ?? post.toc[0]?.anchor ?? null)
    }
    tick()
    window.addEventListener('scroll', tick, { passive: true })
    window.addEventListener('resize', tick)
    return () => {
      window.removeEventListener('scroll', tick)
      window.removeEventListener('resize', tick)
    }
  }, [post])

  if (error) {
    return (
      <div className="hv-stack">
        <p className="hv-hint hv-bad" role="alert">
          Такого отчёта нет.
        </p>
        <button className="hv-btn" type="button" onClick={onBack}>
          ← Весь журнал
        </button>
      </div>
    )
  }

  if (!post) return <p className="hv-hint">Загрузка…</p>

  // The reader's own date convention — see `JournalPage.dateOf`.
  const date = post.published_at
    ? new Date(post.published_at).toLocaleDateString(locale)
    : '—'

  return (
    <>
      <div className="hv-progress" ref={progress} />

      <div className="hv-cols hv-cols--3r">
        <aside className="hv-sticky hv-stack">
          {post.toc.length > 0 && (
            <section className="hv-panel">
              <div className="hv-panel__head">
                <span>Содержание</span>
                <span className="hv-panel__aside">{post.toc.length}</span>
              </div>
              <nav className="hv-tree" style={{ padding: 'var(--hv-2) 0' }}>
                {post.toc.map((entry) => (
                  <a
                    key={entry.anchor}
                    className="hv-tree__item"
                    href={`#${entry.anchor}`}
                    {...(here === entry.anchor ? { 'aria-current': true } : {})}
                  >
                    {entry.text}
                    {here === entry.anchor && <span className="hv-nav__chev">›</span>}
                  </a>
                ))}
              </nav>
              <div className="hv-panel__foot">
                <span>{post.read_minutes} МИН ЧТЕНИЯ</span>
              </div>
            </section>
          )}

          {post.neighbours.length > 0 && (
            <section className="hv-panel">
              <div className="hv-panel__head">
                <span>Другие отчёты</span>
              </div>
              <nav className="hv-nav">
                {post.neighbours.map((other) => (
                  <button
                    key={other.slug}
                    className="hv-nav__item"
                    type="button"
                    onClick={() => onRead(other.slug)}
                  >
                    <span className="hv-nav__lead">
                      #{other.number} · {other.title}
                    </span>
                    <span className="hv-nav__chev">›</span>
                  </button>
                ))}
              </nav>
              <div className="hv-panel__foot">
                <button
                  className="hv-mono"
                  type="button"
                  onClick={onBack}
                  style={{ color: 'inherit', background: 'none', border: 0, cursor: 'pointer' }}
                >
                  ← ВЕСЬ ЖУРНАЛ
                </button>
              </div>
            </section>
          )}
        </aside>

        <article className="hv-stack hv-stack--4">
          <header className="hv-frame hv-frame--wide">
            <div className="hv-post__meta" style={{ marginBottom: 'var(--hv-3)' }}>
              <span>
                ОТЧЁТ :: <b>#{post.number}</b>
              </span>
              <span>{date}</span>
              <span>{SECTION_META[post.section].label.toUpperCase()}</span>
              <span>{post.read_minutes} МИН ЧТЕНИЯ</span>
              {!post.is_published && <span className="hv-warn">ЧЕРНОВИК</span>}
            </div>
            <h1 className="hv-display" style={{ fontSize: 'clamp(1.7rem,4.4vw,3rem)' }}>
              {post.title}
            </h1>
            {post.lede && (
              <p className="hv-lede" style={{ marginTop: 'var(--hv-4)', maxWidth: '70ch' }}>
                {post.lede}
              </p>
            )}
            {(post.author || post.data_note) && (
              <div className="hv-row" style={{ marginTop: 'var(--hv-4)' }}>
                {post.author && <span className="hv-micro">АВТОР :: {post.author.toUpperCase()}</span>}
                <span className="hv-spacer" />
                {/* Absent rather than blank for a report resting on no dataset. */}
                {post.data_note && (
                  <span className="hv-micro">ДАННЫЕ :: {post.data_note.toUpperCase()}</span>
                )}
              </div>
            )}
          </header>

          <Article blocks={post.blocks} />
        </article>

        <aside className="hv-sticky hv-stack">
          {post.data_note && (
            <section className="hv-frame hv-frame--wide">
              <span className="hv-micro">ДАННЫЕ ОТЧЁТА</span>
              <div className="hv-stack hv-stack--2" style={{ marginTop: 'var(--hv-3)' }}>
                <div className="hv-annot">
                  <span>
                    <span className="hv-annot__k">Выборка</span>
                    <span className="hv-annot__v">{post.data_note.toUpperCase()}</span>
                  </span>
                </div>
                <div className="hv-annot">
                  <span>
                    <span className="hv-annot__k">Опубликован</span>
                    <span className="hv-annot__v">{date}</span>
                  </span>
                </div>
              </div>
            </section>
          )}

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>Проверить на себе</span>
            </div>
            <div className="hv-panel__body hv-stack">
              <p className="hv-prose" style={{ fontSize: 'var(--hv-size-small)', margin: 0 }}>
                Загрузите модель — увидите ту же смету построчно, с основанием расчёта
                под каждой цифрой.
              </p>
              <button
                className="hv-btn hv-btn--primary hv-btn--block"
                type="button"
                onClick={onConfigure}
              >
                Открыть конфигуратор
              </button>
            </div>
          </section>
        </aside>
      </div>
    </>
  )
}

/** Re-exported so the editor's preview links to the same anchors readers do. */
export { anchorOf }
