import { Fragment } from 'react'

import { anchorOf } from './blocks'
import type { Block } from './blocks'

/**
 * A report's body, as the kit draws it.
 *
 * Shared between the storefront and the console's editor preview, so what an
 * author sees while writing is the same component readers get — not a
 * approximation of it that drifts on the first styling change.
 *
 * **No `dangerouslySetInnerHTML` anywhere in this file.** Every block is a typed
 * shape rendered into elements, so the worst a malicious report can do is contain
 * unpleasant text. That is the whole reason the body is structured rather than a
 * markdown string.
 */

export interface ArticleProps {
  blocks: Block[]
}

export function Article({ blocks }: ArticleProps) {
  return (
    <div className="hv-article">
      {blocks.map((block, index) => (
        <Fragment key={index}>{renderBlock(block, index)}</Fragment>
      ))}
    </div>
  )
}

function renderBlock(block: Block, index: number) {
  switch (block.kind) {
    case 'heading':
      // The `id` the contents list scrolls to, derived the same way the server
      // derives the entry that links here — see `anchorOf`.
      return <h2 id={anchorOf(block.text, index)}>{block.text}</h2>

    case 'paragraph':
      return <p>{renderInline(block.text)}</p>

    case 'list':
      return (
        <ul>
          {block.items.map((item, position) => (
            <li key={position}>{renderInline(item)}</li>
          ))}
        </ul>
      )

    case 'callout':
      return (
        <div className={`hv-callout${block.tone === 'live' ? ' hv-callout--live' : ''}`}>
          {block.title && <strong>{block.title}</strong>}
          {block.title && ' '}
          {renderInline(block.text)}
        </div>
      )

    case 'quote':
      return (
        <blockquote className="hv-quote">
          {block.text}
          {block.cite && <cite>{block.cite}</cite>}
        </blockquote>
      )

    case 'code':
      return (
        <div className="hv-code">
          {(block.label || block.note) && (
            <div className="hv-code__head">
              <span>{block.label}</span>
              <span>{block.note}</span>
            </div>
          )}
          <pre>
            <code>{block.code}</code>
          </pre>
        </div>
      )

    case 'table':
      return (
        <div className="hv-table-wrap">
          <table className="hv-table">
            <thead>
              <tr>
                {block.head.map((cell, column) => (
                  <th key={column} {...alignment(block.align, column)}>
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, position) => (
                <tr key={position}>
                  {row.map((cell, column) => (
                    <td key={column} {...alignment(block.align, column)}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )

    case 'figures':
      return (
        <section className="hv-panel">
          <div className="hv-panel__head hv-panel__head--invert">
            <span>{block.title}</span>
            {block.aside && (
              <span className="hv-panel__aside" style={{ color: 'inherit' }}>
                {block.aside}
              </span>
            )}
          </div>
          <div className="hv-panel__body hv-panel__body--tight">
            <ul className="hv-leaders">
              {block.rows.map((row, position) => (
                <li
                  key={position}
                  className="hv-leader"
                  {...(row.tone === 'plain' ? {} : { 'data-tone': row.tone })}
                >
                  <span className="hv-leader__k">{row.label}</span>
                  <span className="hv-leader__fill" aria-hidden="true" />
                  <span className="hv-leader__v">{row.value}</span>
                </li>
              ))}
            </ul>
            {block.total_value && (
              <>
                <hr className="hv-hr hv-hr--heavy" />
                <div className="hv-slab hv-slab--lg">
                  <span>{block.total_label}</span>
                  <span className="hv-slab__v">{block.total_value}</span>
                </div>
              </>
            )}
            {block.note && (
              <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
                {block.note}
              </p>
            )}
          </div>
        </section>
      )
  }
}

function alignment(align: ('start' | 'end')[], column: number) {
  return align[column] === 'end' ? { 'data-align': 'end' } : {}
}

/**
 * The two marks a paragraph may carry: `**bold**` and `` `code` ``.
 *
 * Split into React elements rather than parsed into HTML. A markdown renderer
 * would be the obvious reach here and it is exactly what this avoids — the point
 * of the whole block scheme is that no report can produce markup, and a renderer
 * that emitted HTML would hand that back.
 *
 * Two marks and no more, on purpose. The kit's article uses bold for the sentence
 * a paragraph turns on and monospace for a figure or a symbol; anything else it
 * needs is its own block.
 */
function renderInline(text: string) {
  const pieces = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return pieces.map((piece, index) => {
    if (piece.startsWith('**') && piece.endsWith('**') && piece.length > 4) {
      return <strong key={index}>{piece.slice(2, -2)}</strong>
    }
    if (piece.startsWith('`') && piece.endsWith('`') && piece.length > 2) {
      return <code key={index}>{piece.slice(1, -1)}</code>
    }
    return <Fragment key={index}>{piece}</Fragment>
  })
}
