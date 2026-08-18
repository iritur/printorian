import type { StatusTag } from './types'

export interface StatusTagsProps<T> {
  rows: readonly T[]
  tags: ReadonlyArray<StatusTag<T>>
  active: string | null
  onToggle: (key: string | null) => void
  allLabel: string
}

/**
 * The counter chips above a table.
 *
 * Counts are always computed from the unfiltered rows, so switching filters does
 * not change the numbers — the chips are a picture of the whole set, which is
 * what makes "4 in printer, 12 in stock, 2 ordered" readable at a glance.
 */
export function StatusTags<T>({
  rows,
  tags,
  active,
  onToggle,
  allLabel,
}: StatusTagsProps<T>) {
  const counts = new Map<string, number>()
  for (const tag of tags) {
    counts.set(tag.key, rows.filter((row) => tag.match(row)).length)
  }

  return (
    <div className="hv-tags" role="group" aria-label={allLabel}>
      <button
        type="button"
        className="hv-tag"
        data-tone="neutral"
        aria-pressed={active === null}
        onClick={() => onToggle(null)}
      >
        {/*
          The label and the count are separate elements because Harvester tones
          only the number: the count is what carries the signal, and colouring
          the whole tag would make a wall of them read as an alarm.
        */}
        <span className="hv-tag__k">{allLabel}</span>
        <span className="hv-tag__n">{rows.length}</span>
      </button>

      {tags.map((tag) => (
        <button
          key={tag.key}
          type="button"
          className="hv-tag"
          data-tone={tag.tone ?? 'neutral'}
          aria-pressed={active === tag.key}
          onClick={() => onToggle(active === tag.key ? null : tag.key)}
        >
          <span className="hv-tag__k">{tag.label}</span>
          <span className="hv-tag__n">{counts.get(tag.key) ?? 0}</span>
        </button>
      ))}
    </div>
  )
}
