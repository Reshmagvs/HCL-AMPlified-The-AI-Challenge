import { useQuery } from '@tanstack/react-query'
import { getHealth } from './lib/api'

/**
 * Phase 0 shell: proves the frontend, the typed client and the API agree.
 * Replaced by the routed application in the interface phase.
 */
export default function App() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
  })

  return (
    <main className="mx-auto flex min-h-full max-w-3xl flex-col justify-center gap-6 p-6">
      <header>
        <p className="label">Lodestar</p>
        <h1 className="text-3xl font-bold tracking-tight">
          Learning is a dependency graph, not a search result.
        </h1>
      </header>
      <div className="edge-rule" />

      {isLoading && <p className="text-mist-500">Contacting the API…</p>}

      {isError && (
        <div className="card p-5">
          <p className="font-semibold text-signal-bad">Backend unreachable</p>
          <p className="mt-1 text-sm text-mist-500">{(error as Error).message}</p>
          <button className="btn-ghost mt-4" onClick={() => void refetch()}>
            Retry
          </button>
        </div>
      )}

      {data && (
        <dl className="card grid grid-cols-2 gap-x-6 gap-y-4 p-5 sm:grid-cols-3">
          {Object.entries(data).map(([key, value]) => (
            <div key={key}>
              <dt className="label">{key.replace(/_/g, ' ')}</dt>
              <dd className="mt-0.5 font-mono text-sm text-mist-100">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </main>
  )
}
