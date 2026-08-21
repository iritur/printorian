import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import {
  Article,
  Modal,
  SECTION_META,
  api,
  translateError,
  useChrome,
  useSession,
} from '@printorian/ui'
import type { Block, Locale, Section } from '@printorian/ui'

import { BlockList, Meta, incomplete } from './JournalEditor'
import type { Draft } from './JournalEditor'

/**
 * The journal, from the farm's side — `manage_journal`.
 *
 * Engineer and above, the same tier as `manage_library`, because both are the
 * shop window: the person who can publish a model is the person who can publish
 * the report explaining it.
 *
 * The gate below is a courtesy, not a defence. Every write here is refused by the
 * server without the permission, and this only avoids showing somebody a form
 * whose save button will always fail.
 */

const MANAGE_JOURNAL = 'manage_journal'

interface PostRow {
  slug: string
  number: number
  title: string
  section: Section
  read_minutes: number
  is_published: boolean
  published_at: string | null
}

interface PostView extends PostRow {
  lede: string
  excerpt: string
  author: string
  data_note: string
  blocks: Block[]
}

const EMPTY: Draft = {
  title: '',
  section: 'cost',
  lede: '',
  excerpt: '',
  author: '',
  data_note: '',
  blocks: [],
  is_published: false,
}

export function JournalPage({ locale }: { locale: Locale }) {
  const { actor } = useSession()
  const [rows, setRows] = useState<PostRow[]>([])

  /* Reports and how many of them are live — a draft count staff act on. */
  useChrome({
    meta: [
      { label: 'JOURNAL.POSTS', value: String(rows.length) },
      { label: 'ЧЕРНОВИКОВ', value: String(rows.filter((row) => !row.is_published).length) },
    ],
  })
  /** The slug being edited, `""` for a new report, `null` for neither. */
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(false)
  /** Blocks the author still has to fill in. Cleared on the next save. */
  const [flagged, setFlagged] = useState<number[]>([])

  const may = actor?.permissions.includes(MANAGE_JOURNAL) ?? false

  const load = useCallback(async () => {
    try {
      // Drafts included, because the token carries the permission — this is the
      // list an editor needs and the public one is a different question.
      const body = await api.get<{ rows: PostRow[] }>('/journal?limit=60')
      setRows(body.rows)
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    }
  }, [locale])

  useEffect(() => {
    if (!may) return
    // The await is what makes the state update provably asynchronous — see
    // `packages/ui/src/effects.ts` for why every fetch-on-mount is shaped so.
    void (async () => {
      await load()
    })()
  }, [may, load])

  const open = async (slug: string) => {
    setError(null)
    try {
      const post = await api.get<PostView>(`/journal/${slug}`)
      setDraft({
        title: post.title,
        section: post.section,
        lede: post.lede,
        excerpt: post.excerpt,
        author: post.author,
        data_note: post.data_note,
        blocks: post.blocks,
        is_published: post.is_published,
      })
      setEditing(slug)
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    }
  }

  const save = async (publish?: boolean) => {
    // Checked before the request, so the author is told *which* block is empty
    // rather than being handed the server's field name for one of twelve.
    const blank = incomplete(draft.blocks)
    if (blank.length > 0) {
      setFlagged(blank)
      setError(
        `Не заполнены блоки: ${blank.map((index) => index + 1).join(', ')}. ` +
          'Заполните или удалите их.',
      )
      return
    }
    setFlagged([])
    setBusy(true)
    setError(null)
    const body = { ...draft, ...(publish === undefined ? {} : { is_published: publish }) }
    try {
      if (editing) {
        await api.patch<PostView>(`/journal/${editing}`, body)
      } else {
        const created = await api.post<PostView>('/journal', body)
        // Stay on the report that was just created rather than returning to the
        // list: the next thing an author does is keep writing it.
        setEditing(created.slug)
      }
      setDraft((current) => ({
        ...current,
        ...(publish === undefined ? {} : { is_published: publish }),
      }))
      await load()
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (slug: string) => {
    setBusy(true)
    try {
      await api.delete(`/journal/${slug}`)
      if (editing === slug) setEditing(null)
      await load()
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    } finally {
      setBusy(false)
    }
  }

  if (!may) return <p className="hv-hint">Нужно право «manage_journal».</p>

  /*
    A window, not a takeover.

    The editor replaced the whole screen, so "back to the list" was a button you
    had to find and the list itself was gone while you worked. It is a popup like
    every other create in the console now, and the list stays behind it — which is
    also what makes the ✕ mean something.
  */

  return (
    <div className="hv-stack">
      <div className="hv-row hv-row--between">
        <span className="hv-label">Журнал · {rows.length} отчётов</span>
        <button
          className="hv-btn hv-btn--primary"
          type="button"
          onClick={() => {
            setDraft(EMPTY)
            setEditing('')
          }}
        >
          Новый отчёт
        </button>
      </div>

      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}

      <div className="hv-table-wrap">
        <table className="hv-table">
          <thead>
            <tr>
              <th>№</th>
              <th>Заголовок</th>
              <th>Раздел</th>
              <th data-align="end">Чтение</th>
              <th>Состояние</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.slug} data-activatable="">
                <td className="hv-mono">#{row.number}</td>
                <td>{row.title}</td>
                <td>{SECTION_META[row.section].label}</td>
                <td data-align="end">{row.read_minutes} мин</td>
                <td>
                  <span className="hv-state" data-state={row.is_published ? 'idle' : 'paused'}>
                    {row.is_published ? 'Опубликован' : 'Черновик'}
                  </span>
                </td>
                <td>
                  <button
                    className="hv-btn hv-btn--sm"
                    type="button"
                    onClick={() => void open(row.slug)}
                  >
                    Открыть
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length === 0 && <p className="hv-hint">Ни одного отчёта. Начните с первого.</p>}

      {/* The list stays behind the popup rather than being replaced by it. The
          editor used to return instead of the list, so "back to the list" was a
          button you had to find and the list was gone while you worked — which is
          the difference between a window and a takeover. */}
      {editing !== null && (
        <Modal
          wide
          title={editing ? `Правка :: ${editing}` : 'Новый отчёт :: Журнал'}
          path={editing ? `/JOURNAL/EDITOR/${editing.toUpperCase()}` : '/JOURNAL/EDITOR/NEW'}
          pathStatus={draft.is_published ? 'СТАТУС :: ОПУБЛИКОВАН' : 'СТАТУС :: ЧЕРНОВИК'}
          status={preview ? 'ПРЕДПРОСМОТР' : 'ПРАВКА'}
          onClose={() => setEditing(null)}
          footer={
            <>
              <span className="hv-row">
                <button
                  className="hv-btn hv-btn--sm"
                  type="button"
                  aria-pressed={preview}
                  onClick={() => setPreview(!preview)}
                >
                  Предпросмотр
                </button>
                {editing && (
                  <button
                    className="hv-btn hv-btn--sm hv-btn--danger"
                    type="button"
                    disabled={busy}
                    onClick={() => void remove(editing)}
                  >
                    Удалить
                  </button>
                )}
              </span>
              <span className="hv-row">
                <button
                  className="hv-btn"
                  type="button"
                  disabled={busy || !draft.title.trim()}
                  onClick={() => void save(!draft.is_published)}
                >
                  {draft.is_published ? 'Снять с публикации' : 'Опубликовать'}
                </button>
                <button
                  className="hv-btn hv-btn--primary"
                  type="button"
                  disabled={busy || !draft.title.trim()}
                  onClick={() => void save()}
                >
                  Сохранить
                </button>
              </span>
            </>
          }
        >
          {error && (
            <p className="hv-hint hv-bad" role="alert">
              {error}
            </p>
          )}

          <Meta draft={draft} onChange={(patch) => setDraft({ ...draft, ...patch })} />

          {/*
            The reader's own renderer, not an approximation of it. Sharing `Article`
            between the storefront and this preview is what stops the two drifting —
            an author sees exactly what a reader will.
          */}
          {preview ? (
            <section className="hv-panel">
              <div className="hv-panel__head">
                <span>Предпросмотр</span>
                <span className="hv-panel__aside">КАК УВИДИТ ЧИТАТЕЛЬ</span>
              </div>
              <div className="hv-panel__body">
                <Article blocks={draft.blocks} />
              </div>
            </section>
          ) : (
            <BlockList
              blocks={draft.blocks}
              flagged={flagged}
              onChange={(blocks) => setDraft({ ...draft, blocks })}
            />
          )}
        </Modal>
      )}
    </div>
  )
}

function describe(exc: unknown, locale: Locale): string {
  if (exc instanceof ApiError) {
    return translateError(locale, { code: exc.code, details: exc.details })
  }
  return translateError(locale, { code: 'error.internal' })
}
