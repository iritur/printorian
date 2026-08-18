import '@testing-library/jest-dom/vitest'

/**
 * Blob URLs, which jsdom does not implement.
 *
 * The configurator hands the uploaded mesh to the 3D view as an object URL rather
 * than posting it back and fetching it again. jsdom has no `URL.createObjectURL`
 * at all, so without these every test that touches the file input dies inside a
 * state updater with `TypeError: URL.createObjectURL is not a function` — an error
 * that names the wrong culprit.
 *
 * Deliberately a counter rather than a fixed string: a test asserting that the
 * previous URL was revoked needs the two to be distinguishable.
 */
let blobs = 0
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => `blob:test/${++blobs}`
}
if (typeof URL.revokeObjectURL !== 'function') {
  URL.revokeObjectURL = () => undefined
}
