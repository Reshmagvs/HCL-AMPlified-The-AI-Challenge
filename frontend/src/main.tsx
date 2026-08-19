import React from 'react'
import ReactDOM from 'react-dom/client'
import { onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ApiError } from './lib/api'
import './index.css'

// React Query's offline detection is not evidence of anything here: the API is
// usually on localhost, and some embedded browsers report themselves offline
// permanently. Left alone, it *pauses* failing queries instead of erroring them,
// which stranded every screen on its loading skeleton for ever when the backend
// was stopped. Replacing the event listener with a no-op stops the browser's
// online/offline events from ever flipping the flag back.
onlineManager.setEventListener(() => () => {})
onlineManager.setOnline(true)

/**
 * `networkMode: 'always'` matters more than it looks. By default React Query
 * *pauses* a query when the browser reports itself offline, and a paused query
 * never transitions to an error state -- so stopping the backend left every
 * screen on its loading skeleton for ever instead of showing a retry. Lodestar
 * usually talks to localhost, where the browser's online heuristic is not
 * evidence of anything, so failures should surface as failures.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retry a server-side hiccup once, but never retry "cannot reach the API".
      // A retry on an unreachable host is what React Query pauses, and a paused
      // query never reaches an error state -- so the screen would sit on its
      // skeleton instead of offering the retry button the learner needs.
      retry: (count, error) =>
        !(error instanceof ApiError && error.isOffline) && count < 1,
      networkMode: 'always',
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
    mutations: { retry: false, networkMode: 'always' },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)
