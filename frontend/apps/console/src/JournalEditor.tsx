import { SECTIONS, SECTION_META } from '@printorian/ui'
import type { Block, Section } from '@printorian/ui'

/**
 * The block editor — the half of the journal console that edits an article body.
 *
 * Split from the page because the page is about *which* report and this is about
 * what is in one. Between them they were over the length this project holds
 * itself to, and the seam is obvious.
 *
 * Every block is a form over its own typed shape. Nothing here accepts markup:
 * the whole reason the body is structured is that a published report cannot put
 * HTML on the storefront, and a "just paste your HTML" escape hatch would hand
 * that back on the first inconvenient layout.
 */

export interface Draft {
  title: string
  section: Section
  lede: string
  excerpt: string
  author: string
  data_note: string
  blocks: Block[]
  is_published: boolean
}

/** A fresh block of each kind, with the fields its schema requires. */
export const BLANK: Record<Block['kind'], () => Block> = {
  heading: () => ({ kind: 'heading', text: 'Новый раздел' }),
  paragraph: () => ({ kind: 'paragraph', text: '' }),
  list: () => ({ kind: 'list', items: [''] }),
  callout: () => ({ kind: 'callout', title: '', text: '', tone: 'plain' }),
  quote: () => ({ kind: 'quote', text: '', cite: '' }),
  code: () => ({ kind: 'code', label: '', note: '', code: '' }),
  table: () => ({ kind: 'table', head: ['', ''], rows: [['', '']], align: [] }),
  figures: () => ({
    kind: 'figures',
    title: 'Итог отчёта в цифрах',
    aside: '',
    rows: [{ label: '', value: '', tone: 'plain' }],
    total_label: '',
    total_value: '',
    note: '',
  }),
}

export const KIND_LABEL: Record<Block['kind'], string> = {
  heading: 'Заголовок',
  paragraph: 'Абзац',
  list: 'Список',
  callout: 'Врезка',
  quote: 'Цитата',
  code: 'Код',
  table: 'Таблица',
  figures: 'Цифры',
}

/**
 * Which blocks are not ready to be saved, by position.
 *
 * The server refuses these too — every required field is `min_length=1` in the
 * schema — but it answers with the field name and not the block, so an author
 * with a dozen blocks gets «проверьте введённые данные» and no idea which one.
 * Catching it here costs a round trip and turns the message into a place to look.
 *
 * Deliberately the *same* rule the schema states, not a stricter one: a form that
 * refuses what the server would accept is its own kind of bug.
 */
export function incomplete(blocks: Block[]): number[] {
  const empty = (value: string) => !value.trim()
  return blocks.flatMap((block, index) => {
    switch (block.kind) {
      case 'heading':
      case 'paragraph':
        return empty(block.text) ? [index] : []
      case 'list':
        return block.items.every(empty) ? [index] : []
      case 'callout':
      case 'quote':
        return empty(block.text) ? [index] : []
      case 'code':
        return empty(block.code) ? [index] : []
      case 'table':
        return block.head.every(empty) ? [index] : []
      case 'figures':
        return block.rows.every((row) => empty(row.label) && empty(row.value)) ? [index] : []
    }
  })
}

export function Meta({
  draft,
  onChange,
}: {
  draft: Draft
  onChange: (patch: Partial<Draft>) => void
}) {
  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>Отчёт</span>
        <span className="hv-panel__aside">ЗАГОЛОВОК · РАЗДЕЛ · АВТОР</span>
      </div>
      <div className="hv-panel__body hv-stack">
        <div className="hv-field">
          <label className="hv-label" htmlFor="j-title">
            Заголовок
          </label>
          <input
            className="hv-input"
            id="j-title"
            value={draft.title}
            onChange={(event) => onChange({ title: event.target.value })}
          />
          <span className="hv-hint">Из него получается адрес отчёта</span>
        </div>

        <div className="hv-grid hv-grid--2">
          <div className="hv-field">
            <label className="hv-label" htmlFor="j-section">
              Раздел
            </label>
            <select
              className="hv-select"
              id="j-section"
              value={draft.section}
              onChange={(event) => onChange({ section: event.target.value as Section })}
            >
              {SECTIONS.map((name) => (
                <option key={name} value={name}>
                  {SECTION_META[name].label}
                </option>
              ))}
            </select>
          </div>
          <div className="hv-field">
            <label className="hv-label" htmlFor="j-author">
              Автор
            </label>
            <input
              className="hv-input"
              id="j-author"
              placeholder="ИНЖЕНЕРНАЯ ГРУППА"
              value={draft.author}
              onChange={(event) => onChange({ author: event.target.value })}
            />
          </div>
        </div>

        <div className="hv-field">
          <label className="hv-label" htmlFor="j-lede">
            Лид
          </label>
          <textarea
            className="hv-input"
            id="j-lede"
            rows={2}
            value={draft.lede}
            onChange={(event) => onChange({ lede: event.target.value })}
          />
          <span className="hv-hint">Под заголовком статьи</span>
        </div>

        <div className="hv-field">
          <label className="hv-label" htmlFor="j-excerpt">
            Аннотация
          </label>
          <textarea
            className="hv-input"
            id="j-excerpt"
            rows={2}
            value={draft.excerpt}
            onChange={(event) => onChange({ excerpt: event.target.value })}
          />
          {/*
            Two separate fields on purpose, and the hint says why: a card is read
            at a glance in a grid, a standfirst at the top of an article, and the
            same sentence rarely does both jobs.
          */}
          <span className="hv-hint">На карточке в списке — не то же самое, что лид</span>
        </div>

        <div className="hv-field">
          <label className="hv-label" htmlFor="j-data">
            Данные отчёта
          </label>
          <input
            className="hv-input"
            id="j-data"
            placeholder="12 ПРИНТЕРОВ · 90 СУТОК"
            value={draft.data_note}
            onChange={(event) => onChange({ data_note: event.target.value })}
          />
          <span className="hv-hint">Пусто — строка не показывается</span>
        </div>
      </div>
    </section>
  )
}

export function BlockList({
  blocks,
  onChange,
  flagged = [],
}: {
  blocks: Block[]
  onChange: (blocks: Block[]) => void
  /** Positions the author still has to fill in, from `incomplete`. */
  flagged?: number[]
}) {
  const replace = (index: number, block: Block) =>
    onChange(blocks.map((entry, position) => (position === index ? block : entry)))

  const move = (index: number, by: number) => {
    const target = index + by
    if (target < 0 || target >= blocks.length) return
    const next = [...blocks]
    const [lifted] = next.splice(index, 1)
    next.splice(target, 0, lifted as Block)
    onChange(next)
  }

  return (
    <div className="hv-stack">
      {blocks.map((block, index) => (
        <section className="hv-panel" key={index}>
          <div className="hv-panel__head">
            <span>
              {String(index + 1).padStart(2, '0')} :: {KIND_LABEL[block.kind]}
              {flagged.includes(index) && <span className="hv-warn"> · ПУСТО</span>}
            </span>
            <span className="hv-row">
              <button
                className="hv-btn hv-btn--sm"
                type="button"
                onClick={() => move(index, -1)}
                disabled={index === 0}
                aria-label="Выше"
              >
                ↑
              </button>
              <button
                className="hv-btn hv-btn--sm"
                type="button"
                onClick={() => move(index, 1)}
                disabled={index === blocks.length - 1}
                aria-label="Ниже"
              >
                ↓
              </button>
              <button
                className="hv-btn hv-btn--sm"
                type="button"
                onClick={() => onChange(blocks.filter((_, position) => position !== index))}
                aria-label="Удалить блок"
              >
                ✕
              </button>
            </span>
          </div>
          <div className="hv-panel__body hv-stack">
            <BlockFields block={block} onChange={(next) => replace(index, next)} />
          </div>
        </section>
      ))}

      <div className="hv-row">
        {(Object.keys(BLANK) as Block['kind'][]).map((kind) => (
          <button
            key={kind}
            className="hv-btn hv-btn--sm"
            type="button"
            onClick={() => onChange([...blocks, BLANK[kind]()])}
          >
            + {KIND_LABEL[kind]}
          </button>
        ))}
      </div>
    </div>
  )
}

function BlockFields({ block, onChange }: { block: Block; onChange: (block: Block) => void }) {
  switch (block.kind) {
    case 'heading':
      return (
        <input
          className="hv-input"
          value={block.text}
          onChange={(event) => onChange({ ...block, text: event.target.value })}
        />
      )

    case 'paragraph':
      return (
        <>
          <textarea
            className="hv-input"
            rows={5}
            value={block.text}
            onChange={(event) => onChange({ ...block, text: event.target.value })}
          />
          <span className="hv-hint">**жирный** и `моноширинный` — больше разметки нет</span>
        </>
      )

    case 'list':
      return (
        <>
          {block.items.map((item, index) => (
            <div className="hv-row" key={index}>
              <input
                className="hv-input"
                style={{ flex: '1 1 auto' }}
                value={item}
                onChange={(event) =>
                  onChange({
                    ...block,
                    items: block.items.map((entry, position) =>
                      position === index ? event.target.value : entry,
                    ),
                  })
                }
              />
              <button
                className="hv-btn hv-btn--sm"
                type="button"
                onClick={() =>
                  onChange({
                    ...block,
                    items: block.items.filter((_, position) => position !== index),
                  })
                }
                // A list with no items fails validation at the edge, so the last
                // one cannot be removed — the block itself is what gets deleted.
                disabled={block.items.length === 1}
                aria-label="Удалить пункт"
              >
                ✕
              </button>
            </div>
          ))}
          <button
            className="hv-btn hv-btn--sm"
            type="button"
            onClick={() => onChange({ ...block, items: [...block.items, ''] })}
          >
            + Пункт
          </button>
        </>
      )

    case 'callout':
      return (
        <>
          <input
            className="hv-input"
            placeholder="ЗАГОЛОВОК ВРЕЗКИ"
            value={block.title}
            onChange={(event) => onChange({ ...block, title: event.target.value })}
          />
          <textarea
            className="hv-input"
            rows={3}
            value={block.text}
            onChange={(event) => onChange({ ...block, text: event.target.value })}
          />
          <div className="hv-seg" role="group" aria-label="Тон врезки">
            {(['plain', 'live'] as const).map((tone) => (
              <button
                key={tone}
                className="hv-seg__btn"
                type="button"
                aria-pressed={block.tone === tone}
                onClick={() => onChange({ ...block, tone })}
              >
                {tone === 'plain' ? 'Обычная' : 'Акцентная'}
              </button>
            ))}
          </div>
        </>
      )

    case 'quote':
      return (
        <>
          <textarea
            className="hv-input"
            rows={3}
            value={block.text}
            onChange={(event) => onChange({ ...block, text: event.target.value })}
          />
          <input
            className="hv-input"
            placeholder="ОТЧЁТ #52 · КАРТА ОБСЛУЖИВАНИЯ"
            value={block.cite}
            onChange={(event) => onChange({ ...block, cite: event.target.value })}
          />
        </>
      )

    case 'code':
      return (
        <>
          <div className="hv-grid hv-grid--2">
            <input
              className="hv-input"
              placeholder="PRICING/ENGINE.PY"
              value={block.label}
              onChange={(event) => onChange({ ...block, label: event.target.value })}
            />
            <input
              className="hv-input"
              placeholder="ФРАГМЕНТ"
              value={block.note}
              onChange={(event) => onChange({ ...block, note: event.target.value })}
            />
          </div>
          <textarea
            className="hv-input hv-mono"
            rows={8}
            spellCheck={false}
            value={block.code}
            onChange={(event) => onChange({ ...block, code: event.target.value })}
          />
        </>
      )

    case 'table':
      return <TableFields block={block} onChange={onChange} />

    case 'figures':
      return <FiguresFields block={block} onChange={onChange} />
  }
}

function TableFields({
  block,
  onChange,
}: {
  block: Extract<Block, { kind: 'table' }>
  onChange: (block: Block) => void
}) {
  const columns = block.head.length
  return (
    <>
      <span className="hv-label">Шапка</span>
      <div className="hv-row">
        {block.head.map((cell, column) => (
          <input
            key={column}
            className="hv-input"
            style={{ flex: '1 1 120px' }}
            value={cell}
            onChange={(event) =>
              onChange({
                ...block,
                head: block.head.map((entry, position) =>
                  position === column ? event.target.value : entry,
                ),
              })
            }
          />
        ))}
        <button
          className="hv-btn hv-btn--sm"
          type="button"
          onClick={() =>
            onChange({
              ...block,
              head: [...block.head, ''],
              rows: block.rows.map((row) => [...row, '']),
            })
          }
        >
          + Столбец
        </button>
      </div>

      <span className="hv-label">Строки</span>
      {block.rows.map((row, index) => (
        <div className="hv-row" key={index}>
          {Array.from({ length: columns }, (_, column) => (
            <input
              key={column}
              className="hv-input"
              style={{ flex: '1 1 120px' }}
              value={row[column] ?? ''}
              onChange={(event) =>
                onChange({
                  ...block,
                  rows: block.rows.map((entry, position) =>
                    position === index
                      ? Array.from({ length: columns }, (_, cell) =>
                          cell === column ? event.target.value : (entry[cell] ?? ''),
                        )
                      : entry,
                  ),
                })
              }
            />
          ))}
          <button
            className="hv-btn hv-btn--sm"
            type="button"
            onClick={() =>
              onChange({
                ...block,
                rows: block.rows.filter((_, position) => position !== index),
              })
            }
            disabled={block.rows.length === 1}
            aria-label="Удалить строку"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        className="hv-btn hv-btn--sm"
        type="button"
        onClick={() =>
          onChange({ ...block, rows: [...block.rows, Array.from({ length: columns }, () => '')] })
        }
      >
        + Строка
      </button>

      <span className="hv-label">Выравнивание по правому краю</span>
      <div className="hv-row">
        {block.head.map((cell, column) => (
          <button
            key={column}
            className="hv-btn hv-btn--sm"
            type="button"
            aria-pressed={block.align[column] === 'end'}
            onClick={() =>
              onChange({
                ...block,
                align: Array.from({ length: columns }, (_, position) =>
                  position === column
                    ? block.align[column] === 'end'
                      ? 'start'
                      : 'end'
                    : (block.align[position] ?? 'start'),
                ),
              })
            }
          >
            {cell || `#${column + 1}`}
          </button>
        ))}
      </div>
    </>
  )
}

function FiguresFields({
  block,
  onChange,
}: {
  block: Extract<Block, { kind: 'figures' }>
  onChange: (block: Block) => void
}) {
  const TONES = ['plain', 'good', 'warn', 'bad'] as const
  return (
    <>
      <div className="hv-grid hv-grid--2">
        <input
          className="hv-input"
          placeholder="ЗАГОЛОВОК ПАНЕЛИ"
          value={block.title}
          onChange={(event) => onChange({ ...block, title: event.target.value })}
        />
        <input
          className="hv-input"
          placeholder="BAMBU X1C · PETG-CF"
          value={block.aside}
          onChange={(event) => onChange({ ...block, aside: event.target.value })}
        />
      </div>

      {block.rows.map((row, index) => (
        <div className="hv-row" key={index}>
          <input
            className="hv-input"
            style={{ flex: '2 1 180px' }}
            placeholder="Амортизация"
            value={row.label}
            onChange={(event) =>
              onChange({
                ...block,
                rows: block.rows.map((entry, position) =>
                  position === index ? { ...entry, label: event.target.value } : entry,
                ),
              })
            }
          />
          <input
            className="hv-input"
            style={{ flex: '1 1 100px' }}
            placeholder="41.00 ₽/ч"
            value={row.value}
            onChange={(event) =>
              onChange({
                ...block,
                rows: block.rows.map((entry, position) =>
                  position === index ? { ...entry, value: event.target.value } : entry,
                ),
              })
            }
          />
          <select
            className="hv-select"
            style={{ flex: '0 1 110px' }}
            value={row.tone}
            onChange={(event) =>
              onChange({
                ...block,
                rows: block.rows.map((entry, position) =>
                  position === index
                    ? { ...entry, tone: event.target.value as (typeof TONES)[number] }
                    : entry,
                ),
              })
            }
          >
            {TONES.map((tone) => (
              <option key={tone} value={tone}>
                {tone}
              </option>
            ))}
          </select>
          <button
            className="hv-btn hv-btn--sm"
            type="button"
            onClick={() =>
              onChange({ ...block, rows: block.rows.filter((_, p) => p !== index) })
            }
            disabled={block.rows.length === 1}
            aria-label="Удалить строку"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        className="hv-btn hv-btn--sm"
        type="button"
        onClick={() =>
          onChange({ ...block, rows: [...block.rows, { label: '', value: '', tone: 'plain' }] })
        }
      >
        + Строка
      </button>

      <div className="hv-grid hv-grid--2">
        <input
          className="hv-input"
          placeholder="Час печати"
          value={block.total_label}
          onChange={(event) => onChange({ ...block, total_label: event.target.value })}
        />
        <input
          className="hv-input"
          placeholder="99.74 ₽"
          value={block.total_value}
          onChange={(event) => onChange({ ...block, total_value: event.target.value })}
        />
      </div>
      <input
        className="hv-input"
        placeholder="БЕЗ МАТЕРИАЛА · БЕЗ НАКЛАДНЫХ · БЕЗ ПРИБЫЛИ"
        value={block.note}
        onChange={(event) => onChange({ ...block, note: event.target.value })}
      />
    </>
  )
}
