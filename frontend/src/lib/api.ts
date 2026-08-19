/**
 * Typed API client.
 *
 * One `request` helper owns fetch, JSON parsing and error shaping, so every
 * screen fails the same way and React Query can retry uniformly. `ApiError`
 * carries the HTTP status, which is what lets the UI distinguish "the backend
 * is down, show a retry state" from "this learner has no path yet, show an
 * empty state".
 */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ??
  'http://127.0.0.1:8000'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (cause) {
    throw new ApiError('Cannot reach the Lodestar API. Is the backend running?', 0, cause)
  }

  const text = await response.text()
  const parsed: unknown = text ? safeJson(text) : null

  if (!response.ok) {
    const detail =
      typeof parsed === 'object' && parsed !== null && 'detail' in parsed
        ? String((parsed as { detail: unknown }).detail)
        : response.statusText
    throw new ApiError(detail || `Request failed (${response.status})`, response.status, parsed)
  }
  return parsed as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

/* ------------------------------------------------------------------ */
/* Health                                                              */
/* ------------------------------------------------------------------ */
export type Health = {
  status: 'ok' | 'degraded'
  version: string
  llm_available: boolean
  llm_provider: string
  catalog_size: number
  graph_nodes: number
  graph_tracks: number
}

export const getHealth = () => request<Health>('/health')
