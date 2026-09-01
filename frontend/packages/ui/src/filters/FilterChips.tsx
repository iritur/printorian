import type { Tone } from '../DataTable/types'

/**
 * The kit's fifth tone. `.hv-tag[data-tone='live']` is styled in `system.css`
 * beside the other four, and the journal and the account history both use it —
 * so the chip's tone is a wider set than the table's, which never draws `live`
 * because a *row* is a fact and only a count can be a thing still moving.
 */
export type ChipTone = Tone | 'live'

export interface FilterChip {
  /** What `active` is compared against, and what `onSelect` hands back. */
  key: string
  label: string
  /**
   * How many rows this chip stands for, or `null` for **not counted yet**.
   *
   * ADR-0007 lives on this one field. Zero is a real answer here — "no orders
   * were cancelled" — so a screen that has not loaded its rows must say `null`
   * and get «—», not pass `0` and quietly claim a measurement it never made.
   */
  count: number | null
  // `| undefined` spelled out: `exactOptionalPropertyTypes` is on, so a caller
  // mapping over rows that have no tone passes the property as `undefined`
  // rather than omitting it.
  tone?: ChipTone | undefined
}

export interface FilterChipsProps {
  chips: readonly FilterChip[]
  /**
   * The «Все» chip, drawn first, standing for *no filter at all*.
   *
   * Separate from `chips` because "no filter" is not one of the values being
   * filtered on: it has no key, it is never toned, and its count is the size of
   * the whole set rather than of a slice. Omit it where a screen genuinely has
   * no unfiltered state.
   */
  all?: { label: string; count: number | null }
  /** The chip in force, or `null` when the «Все» chip is. */
  active: string | null
  onSelect: (key: string | null) => void
  /** Accessible name for the group. */
  label: string
}

/** Not counted. The same em dash every unmeasured figure in this app renders. */
const UNCOUNTED = '—'

/**
 * The counter chips above a filtered list.
 *
 * One component because there were three, hand-rolled and drifting: the
 * console's table tags, the account's four order groups and the journal's
 * sections each carried their own copy of the markup, and each had decided for
 * itself whether clicking the chip already in force clears the filter. Two said
 * yes and one said no, which is the four-bespoke-divergent-screens failure in
 * miniature — the same control behaving differently depending on where you met
 * it.
 *
 * So the rule is settled here, once: **clicking the active chip clears it.** A
 * filter you cannot turn off is a trap, and on a screen whose «Все» chip has
 * scrolled out of view it is one you cannot see your way out of.
 *
 * The counts are the caller's, deliberately. A screen counting rows it holds and
 * a screen reading a total off the server are answering the same question from
 * different places, and a component that insisted on doing the arithmetic itself
 * would force the second kind to fabricate rows it does not have.
 */
export function FilterChips({ chips, all, active, onSelect, label }: FilterChipsProps) {
  return (
    <div className="hv-tags" role="group" aria-label={label}>
      {all && (
        <Chip
          label={all.label}
          count={all.count}
          pressed={active === null}
          onPress={() => onSelect(null)}
        />
      )}

      {chips.map((chip) => (
        <Chip
          key={chip.key}
          label={chip.label}
          count={chip.count}
          tone={chip.tone}
          pressed={active === chip.key}
          onPress={() => onSelect(active === chip.key ? null : chip.key)}
        />
      ))}
    </div>
  )
}

function Chip({
  label,
  count,
  tone,
  pressed,
  onPress,
}: {
  label: string
  count: number | null
  tone?: ChipTone | undefined
  pressed: boolean
  onPress: () => void
}) {
  return (
    <button type="button" className="hv-tag" data-tone={tone ?? 'neutral'} aria-pressed={pressed} onClick={onPress}>
      {/*
        The label and the count are separate elements because Harvester tones
        only the number: the count is what carries the signal, and colouring the
        whole tag would make a wall of them read as an alarm.
      */}
      <span className="hv-tag__k">{label}</span>
      <span className="hv-tag__n">{count === null ? UNCOUNTED : count}</span>
    </button>
  )
}
