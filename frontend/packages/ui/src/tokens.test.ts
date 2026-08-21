import { describe, expect, it } from 'vitest'

/*
 * The design kit's exit criterion, made executable.
 *
 * It used to be a line of prose in `docs/DESIGN-KIT-INTEGRATION.md` — a `grep`
 * that, as written, did not run at all, and that counted build output when it
 * was repaired. A criterion nothing checks is a criterion that comes back, and
 * D13 says a thing is done when a test says so. So it lives here instead.
 *
 * `import.meta.glob` rather than `node:fs` because the frontend has no
 * `@types/node` and adding one to read four hundred files would be a
 * dependency bought for a lint. The patterns reach only `apps/x/src` and
 * `packages/x/src`, which excludes `node_modules` and `dist` by construction
 * rather than by an ignore list that can drift.
 */
const SOURCES: Record<string, string> = {
  ...import.meta.glob('../../../apps/*/src/**/*.{css,ts,tsx}', {
    query: '?raw',
    import: 'default',
    eager: true,
  }),
  ...import.meta.glob('../../../packages/*/src/**/*.{css,ts,tsx}', {
    query: '?raw',
    import: 'default',
    eager: true,
  }),
}

/*
 * The retired prefix is assembled rather than written out, so this file does
 * not become the hit that the check is looking for. That is not cleverness for
 * its own sake: the same grep is what a reviewer runs by hand, and a guard that
 * reports itself teaches everyone to ignore it.
 */
const RETIRED_PREFIX = `--${'pr'}-`

// `import.meta.glob` keys are relative to *this* file, so a sibling comes back
// as `./tokens.css`. A failure has to name something a reader can open, so the
// two shapes are put back onto repository paths before they are reported.
function repoPath(key: string): string {
  return key.startsWith('./')
    ? `frontend/packages/ui/src/${key.slice(2)}`
    : `frontend/${key.replace(/^(\.\.\/)+/, '')}`
}

function offenders(): string[] {
  const hits: string[] = []
  for (const [key, source] of Object.entries(SOURCES)) {
    if (key.endsWith('tokens.test.ts')) continue
    source.split('\n').forEach((line, i) => {
      if (line.includes(RETIRED_PREFIX)) hits.push(`${repoPath(key)}:${i + 1}: ${line.trim()}`)
    })
  }
  return hits
}

describe('the pre-Harvester token prefix', () => {
  it('is read from a corpus that is actually there', () => {
    // Not ceremony. A glob that matched nothing — a moved package, a changed
    // root — would make the assertion below pass forever while checking
    // nothing, and so would Vitest's default of stubbing every CSS import to
    // an empty string. Both failure modes are silent and both look like
    // success, so the two stylesheets that carry the migration are named and
    // their contents are required to be non-empty.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(50)
    for (const sheet of ['./tokens.css', '../../../apps/console/src/console.css']) {
      expect(SOURCES[sheet] ?? '').not.toHaveLength(0)
    }
  })

  it('survives nowhere in the frontend', () => {
    // Reported as the offending lines, not as a count. A failure here means
    // someone reintroduced a token that now resolves to nothing, which makes
    // the property fall back to its initial value silently — so the file and
    // the line is the whole of what the next person needs.
    expect(offenders()).toEqual([])
  })
})

describe('packages/ui/src/tokens.css', () => {
  const sheet = SOURCES['./tokens.css'] ?? ''

  it('no longer declares an alias table', () => {
    // The aliases were the bridge, and their absence is what the criterion
    // above is really measuring: a stylesheet could satisfy the grep while this
    // file quietly went on redefining the old names under a new spelling.
    expect(sheet).not.toContain(':root {')
  })

  it('still carries the bare-control fallbacks', () => {
    // Deliberately asserted, because deleting these *looks* like finishing the
    // job. `system.css` resets a bare `<button>`'s colour but not its
    // background, so without them the unconverted admin screens render
    // near-white text on the browser's default light button face. They go when
    // the last bare control is converted, and not a commit sooner.
    expect(sheet).toContain("button:not([class*='hv-'])")
    expect(sheet).toContain('--hv-bg-inset')
  })
})
