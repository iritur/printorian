import '@testing-library/jest-dom/vitest'

/**
 * Blob URLs, as a test double — always, not only when nothing else provides one.
 *
 * The configurator hands the uploaded mesh to the 3D view as an object URL rather
 * than posting it back and fetching it again. jsdom has no `URL.createObjectURL`
 * of its own, so without these every test that touches the file input dies inside
 * a state updater with `TypeError: URL.createObjectURL is not a function` — an
 * error that names the wrong culprit.
 *
 * These used to install only when `URL.createObjectURL` was missing, on the
 * theory that a real implementation would be better than a stub. Vitest 5.0.1
 * supplied one, and it is worse: its jsdom compatibility layer builds the URL by
 * reaching into jsdom's `Blob` for a private `_buffer` field, jsdom 30.1 renamed
 * that field, and the result is `TypeError: Cannot read properties of undefined
 * (reading '_buffer')` from inside the same state updater — 24 configurator
 * tests, on a dependency bump that touched neither the page nor the test
 * (vitest-dev/vitest#11336; fixed upstream by #11295, unreleased as of
 * 2026-09-24). No test here depends on a blob URL resolving: the 3D view's
 * loader fails on `blob:test/…` and settles as `failed`, which is what it did
 * under vitest 4 when this double was the only implementation there was, and no
 * test asserts a rendered mesh. A real URL buys nothing and costs a dependency
 * on two libraries agreeing about a private field. The double is unconditional
 * now, and stays so after vitest ships the fix: a URL nothing dereferences
 * should not depend on who made it.
 *
 * Deliberately a counter rather than a fixed string: a test asserting that the
 * previous URL was revoked needs the two to be distinguishable.
 */
let blobs = 0
URL.createObjectURL = () => `blob:test/${++blobs}`
URL.revokeObjectURL = () => undefined
