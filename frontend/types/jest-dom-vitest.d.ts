/**
 * jest-dom's matchers on vitest 5's `expect`, until jest-dom types them itself.
 *
 * `@testing-library/jest-dom/vitest` registers the matchers at runtime with
 * `expect.extend`, and types them by augmenting vitest's `Assertion<T>`. Vitest
 * 5.0 made that interface `Assertion<R, T>` — two parameters, the first the
 * matcher's return type — so jest-dom 7.0.1's one-parameter augmentation no
 * longer merges with it. Nothing says so: `skipLibCheck` hides the mismatch in
 * jest-dom's own `.d.ts`, and what surfaces is 208 `TS2339` errors across every
 * test file — «Property 'toBeInTheDocument' does not exist» — on a suite that
 * runs green, because the runtime half still works.
 *
 * That is what #105 (vitest 4.1.11 → 5.0.0) merged with, red; the frontend job
 * had not run on `main` since, because the backend job ahead of it was already
 * failing on a different bump.
 *
 * `Matchers<R, T>` is the extension point vitest documents for custom matchers
 * and is what `Assertion` itself extends, so this is the augmentation vitest
 * asks for rather than a reach into its internals. The parameter order follows
 * jest-dom's own pending fix (testing-library/jest-dom#742, open since
 * 2026-09-23): the actual value's type is jest-dom's `E`, the return type its
 * `R`. Delete this file when a jest-dom release carries that change — it is
 * reachable from every project's `tsconfig.json` `include`, and those four
 * lines go with it.
 */

import type { TestingLibraryMatchers } from '@testing-library/jest-dom/matchers'

declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- an interface merge is the whole point
  interface Matchers<R extends void | Promise<void>, T> extends TestingLibraryMatchers<T, R> {}
}
