export { ApiClient, ApiError } from './client'
export type { ApiErrorBody, ClientOptions, RequestOptions } from './client'

/**
 * Generated types are re-exported from here once the schema has been built:
 *
 *   cd backend && python tools/export_openapi.py --out openapi.json
 *   cd frontend && npm run generate:api
 *
 * `src/generated/` is git-ignored and rebuilt — never edited by hand (ADR-0005).
 */
