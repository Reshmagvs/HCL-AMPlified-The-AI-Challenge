/**
 * A three-state view of a React Query result: data, failure, or still trying.
 *
 * Why this exists rather than reading `isLoading` / `isError` directly: React
 * Query can leave a query in `status: 'pending'` with `fetchStatus: 'paused'`
 * indefinitely when it decides the client is offline. `status` never becomes
 * `'error'`, so a screen keyed on `isError` renders its loading skeleton for
 * ever while the backend is down — the exact white-screen-adjacent failure the
 * interface is supposed to make impossible.
 *
 * `failureReason` is populated as soon as one attempt has actually failed, and
 * that is the honest signal: something went wrong, we have the error, show it
 * and offer a retry. Setting `networkMode: 'always'` is still worth doing, but
 * this does not depend on it holding.
 */

import type { UseQueryResult } from '@tanstack/react-query'

export type QueryState<T> = {
  data: T | undefined
  /** Non-null once at least one attempt has failed, whatever the status says. */
  failure: Error | null
  retry: () => void
}

export function queryState<T>(query: UseQueryResult<T>): QueryState<T> {
  const failure =
    (query.error as Error | null) ??
    (query.failureCount > 0 ? ((query.failureReason as Error | null) ?? null) : null)

  return {
    data: query.data,
    failure: query.data === undefined ? failure : null,
    retry: () => void query.refetch(),
  }
}
