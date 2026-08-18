/**
 * The transport both clients share.
 *
 * Thin on purpose: request/response *types* come from `./generated/schema`,
 * which is produced from the backend's OpenAPI document (ADR-0005). Nothing here
 * restates the shape of the domain — that is precisely the duplication that let
 * V1's web and desktop drift apart.
 */

export interface ApiErrorBody {
  code: string
  details?: Record<string, unknown>
}

/** Thrown for any non-2xx response. Carries the backend's code, never prose. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(status: number, body: ApiErrorBody) {
    // `message` is for developers and logs; users see the translated `code`.
    super(`${body.code} (HTTP ${status})`)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details ?? {}
  }
}

export interface ClientOptions {
  baseUrl: string
  /** Bearer token for desktop and kiosk clients. The storefront uses a cookie. */
  token?: string | undefined
  /**
   * Whether to send cookies. Defaults to sending them only when there is no
   * token, which is the right answer for both usual cases.
   *
   * Set it explicitly for a cross-origin client whose *first* call has no token
   * yet — signing in from the console — because the default would still ask for
   * cookies and the browser then demands
   * `Access-Control-Allow-Credentials: true` from an API that declines to give it.
   */
  credentials?: 'include' | 'omit'
  fetchImpl?: typeof fetch
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  headers?: Record<string, string>
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly fetchImpl: typeof fetch
  private readonly credentials: 'include' | 'omit' | undefined
  private token: string | undefined

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '')
    this.token = options.token
    this.credentials = options.credentials
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
  }

  setToken(token: string | undefined): void {
    this.token = token
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { ...options.headers }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    if (this.token) headers.Authorization = `Bearer ${this.token}`

    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: options.method ?? 'GET',
      headers,
      // Both apps are same-origin with the API (ADR-0016) and authenticated by
      // the session cookie, so they must send credentials. A token-carrying
      // caller is cross-origin and must *not*: `credentials: 'include'` obliges
      // the server to answer with `Access-Control-Allow-Credentials: true`, and
      // granting that to another origin is exactly what the API declines to do.
      // Omitting cookies is what lets CORS stay strict.
      credentials: this.credentials ?? (this.token ? 'omit' : 'include'),
      ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
      ...(options.signal ? { signal: options.signal } : {}),
    })

    if (response.status === 204) return undefined as T
    if (!response.ok) throw new ApiError(response.status, await readErrorBody(response))
    return (await response.json()) as T
  }

  get<T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>): Promise<T> {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(path, { ...options, method: 'POST', body })
  }

  put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PUT', body })
  }

  /**
   * A partial update.
   *
   * Distinct from `put` because the server reads these bodies with
   * `exclude_unset`: an absent field means "leave it alone", not "set it to
   * null". Sending a partial body to a PUT route would blank everything it
   * omitted.
   */
  patch<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PATCH', body })
  }

  delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>(path, { ...options, method: 'DELETE' })
  }

  /**
   * Post a file.
   *
   * Not `post()`: that JSON-encodes its body and sets `Content-Type`, both of
   * which are wrong for multipart. The header is deliberately *not* set here
   * either — `fetch` derives it from the `FormData`, including the boundary it
   * generated, and naming it by hand produces a boundary that does not match
   * the body.
   */
  async upload<T>(path: string, form: FormData, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { ...options.headers }
    if (this.token) headers.Authorization = `Bearer ${this.token}`

    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: options.method ?? 'POST',
      headers,
      body: form,
      credentials: this.credentials ?? (this.token ? 'omit' : 'include'),
      ...(options.signal ? { signal: options.signal } : {}),
    })

    if (response.status === 204) return undefined as T
    if (!response.ok) throw new ApiError(response.status, await readErrorBody(response))
    return (await response.json()) as T
  }

  /**
   * Fetch a file, with the name the server gave it.
   *
   * `request()` cannot be used: it parses every response as JSON, and this one
   * is bytes. The filename comes from `Content-Disposition` because the server
   * stores models by content hash — a directory of digests is unusable to a
   * person, so the original upload name travels in the header.
   */
  async download(
    path: string,
    options: Omit<RequestOptions, 'method' | 'body'> = {},
  ): Promise<{ blob: Blob; filename: string }> {
    const headers: Record<string, string> = { ...options.headers }
    if (this.token) headers.Authorization = `Bearer ${this.token}`

    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: 'GET',
      headers,
      credentials: this.credentials ?? (this.token ? 'omit' : 'include'),
      ...(options.signal ? { signal: options.signal } : {}),
    })

    if (!response.ok) throw new ApiError(response.status, await readErrorBody(response))
    return {
      blob: await response.blob(),
      filename: filenameFrom(response.headers.get('Content-Disposition')),
    }
  }
}

/** The name out of `attachment; filename="part.stl"`, or a usable fallback. */
export function filenameFrom(disposition: string | null): string {
  const quoted = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition ?? '')
  const name = quoted?.[1]?.trim()
  // A server that sends no name is not an error worth failing a download over;
  // the engineer is about to open this in a slicer either way.
  return name ? decodeURIComponent(name) : 'model'
}

/**
 * Never let a malformed error response mask the real failure.
 *
 * A proxy returning an HTML 502 must still surface as a usable ApiError rather
 * than a JSON parse exception that hides what actually went wrong.
 */
async function readErrorBody(response: Response): Promise<ApiErrorBody> {
  try {
    const parsed = (await response.json()) as unknown
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      'code' in parsed &&
      typeof (parsed as ApiErrorBody).code === 'string'
    ) {
      return parsed as ApiErrorBody
    }
  } catch {
    // fall through
  }
  return { code: 'error.internal', details: { status: response.status } }
}
