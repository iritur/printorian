/**
 * Generate the TypeScript API client from the backend's OpenAPI schema.
 *
 * ADR-0005: this output is never hand-edited. CI regenerates and fails on any
 * diff, so a backend contract change breaks the client build at the commit that
 * caused it rather than at runtime months later — which is exactly how V1's
 * hand-written `shared/api.ts` drifted away from the C# it was supposed to match.
 *
 *   node scripts/generate-api-client.mjs [--schema ../backend/openapi.json]
 */

import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '..', '..')

const args = process.argv.slice(2)
const schemaFlag = args.indexOf('--schema')
const schemaPath =
  schemaFlag === -1
    ? resolve(repoRoot, 'backend', 'openapi.json')
    : resolve(process.cwd(), args[schemaFlag + 1])

const outputPath = resolve(here, '..', 'packages', 'api-client', 'src', 'generated', 'schema.ts')

if (!existsSync(schemaPath)) {
  console.error(
    `No schema at ${schemaPath}\n` +
      'Export it first:\n' +
      '  cd backend && python tools/export_openapi.py --out openapi.json',
  )
  process.exit(1)
}

mkdirSync(dirname(outputPath), { recursive: true })

/**
 * Resolve the CLI's JS entry point and run it under the current `node`.
 *
 * Spawning `npx.cmd` fails with EINVAL on Windows, and going through a shell
 * would need careful quoting for non-ASCII paths. This avoids both.
 */
function resolveCli() {
  const require = createRequire(import.meta.url)
  const manifestPath = require.resolve('openapi-typescript/package.json')
  const { bin } = JSON.parse(readFileSync(manifestPath, 'utf8'))
  const entry = typeof bin === 'string' ? bin : bin['openapi-typescript']
  return resolve(dirname(manifestPath), entry)
}

const frontendRoot = resolve(here, '..')

// openapi-typescript percent-encodes the paths it is given and then fails to
// read them back when they contain non-ASCII characters — which any Windows
// profile with a Cyrillic username has. Staging the schema next to the workspace
// and passing relative paths keeps every argument ASCII.
const stagedSchema = resolve(frontendRoot, '.openapi-schema.json')
copyFileSync(schemaPath, stagedSchema)

console.log(`generating client from ${schemaPath}`)
try {
  execFileSync(
    process.execPath,
    [
      resolveCli(),
      relative(frontendRoot, stagedSchema),
      '--output',
      relative(frontendRoot, outputPath),
    ],
    { stdio: 'inherit', cwd: frontendRoot },
  )
} finally {
  rmSync(stagedSchema, { force: true })
}
console.log(`wrote ${outputPath}`)
