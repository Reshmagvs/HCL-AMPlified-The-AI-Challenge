/**
 * What the language layer is using, and how much of it is left.
 *
 * The honest part of this component is the part that refuses to draw a bar.
 * OpenRouter publishes no daily request allowance for a free-tier key, so
 * there is no denominator — and a progress bar reading "12% used" against a
 * number nobody stated would be a fabrication dressed up as a measurement.
 *
 * So the bar appears only when a real credit limit exists. When it does not,
 * the same space shows what *is* measured — requests made, tokens spent, cost
 * incurred (zero, by construction), and which models are currently refusing —
 * and says plainly that the cap is unpublished.
 *
 * It is also a live picture of the fallback chain, which is the thing most
 * worth being able to see: when the hosted models are throttled, this is where
 * you watch the local model take over instead of wondering why replies slowed
 * down.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

const POLL_MS = 20_000

/** Human-readable provider names. The ids are ours; these are for people. */
const LABELS: Record<string, string> = {
  openrouter: 'Hosted (free models)',
  ollama: 'Local model',
  mock: 'Offline templates',
  gemini: 'Gemini',
  none: 'None',
}

export function UsagePanel() {
  const [open, setOpen] = useState(false)
  const usage = useQuery({
    queryKey: ['usage'],
    queryFn: api.usage,
    refetchInterval: open ? POLL_MS : false,
    refetchIntervalInBackground: false,
  })

  const data = usage.data
  if (!data) return null

  const hosted = data.openrouter
  const spent = hosted?.session
  const label = LABELS[data.provider] ?? data.provider

  return (
    <div className="relative">
      <button
        type="button"
        className="chip hover:bg-paper-200"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        title="Which model is answering, and what it has used"
      >
        <span
          aria-hidden
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
            data.provider === 'mock' ? 'bg-amber-500' : 'bg-sage-500'
          }`}
        />
        <span className="hidden sm:inline">{label}</span>
        <span className="sm:hidden">Model</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full z-30 mt-2 w-[320px] rounded-xl border border-paper-400 bg-paper-50 p-4 shadow-lg">
          <p className="label">Answering now</p>
          <p className="mt-0.5 text-[13px] font-medium text-ink-900">{label}</p>
          {hosted?.model && (
            <p className="font-mono text-[11px] leading-snug text-ink-400">{hosted.model}</p>
          )}

          <div className="rule my-3" />

          <p className="label">Fallback order</p>
          <ol className="mt-1 space-y-0.5">
            {data.chain.map((name) => (
              <li
                key={name}
                className={`flex items-center gap-2 text-[12px] ${
                  name === data.provider ? 'font-semibold text-ink-900' : 'text-ink-400'
                }`}
              >
                <span aria-hidden>{name === data.provider ? '▸' : '·'}</span>
                {LABELS[name] ?? name}
                {name === data.provider && (
                  <span className="ml-auto font-mono text-[11px] text-ink-400">
                    {data.tokens_per_second} tok/s
                  </span>
                )}
              </li>
            ))}
          </ol>

          {hosted && spent && (
            <>
              <div className="rule my-3" />
              <p className="label">This session</p>

              {hosted.limit_published && hosted.account.credit_limit ? (
                <CreditBar
                  used={hosted.account.credit_used ?? 0}
                  limit={hosted.account.credit_limit}
                />
              ) : (
                <p className="mt-1 text-[12px] leading-snug text-ink-500">
                  These models cost nothing and publish no daily request cap, so there is no
                  quota to show a bar against. What is measured:
                </p>
              )}

              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[12px]">
                <Stat label="Requests" value={String(spent.requests)} />
                <Stat label="Tokens" value={spent.total_tokens.toLocaleString()} />
                <Stat label="Cost" value={`$${spent.cost.toFixed(4)}`} />
                <Stat
                  label="Free models"
                  value={`${hosted.free_models_available} ready`}
                />
              </dl>

              {spent.failures > 0 && (
                <p className="mt-2 text-[12px] text-ink-500">
                  {spent.failures} request{spent.failures === 1 ? '' : 's'} fell through to the
                  next provider.
                </p>
              )}

              {hosted.cooling_down.length > 0 && (
                <p className="mt-2 text-[12px] leading-snug text-ink-500">
                  Resting after refusing:{' '}
                  <span className="font-mono text-[11px]">
                    {hosted.cooling_down.map((m) => m.split('/').pop()).join(', ')}
                  </span>
                </p>
              )}
            </>
          )}

          <p className="mt-3 text-[11px] leading-snug text-ink-400">
            Nothing here is billed. If every hosted model is busy, the local model answers
            instead — slower, but it never runs out.
          </p>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-ink-400">{label}</dt>
      <dd className="font-mono font-medium text-ink-800">{value}</dd>
    </div>
  )
}

/** Only rendered when a real limit exists to divide by. */
function CreditBar({ used, limit }: { used: number; limit: number }) {
  const pct = Math.min(100, Math.round((used / limit) * 100))
  return (
    <div className="mt-1">
      <div className="flex items-baseline justify-between text-[12px]">
        <span className="text-ink-500">Credit used</span>
        <span className="font-mono text-ink-800">
          ${used.toFixed(2)} of ${limit.toFixed(2)}
        </span>
      </div>
      <div
        className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-paper-300"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-full rounded-full bg-clay-500" style={{ width: `${Math.max(2, pct)}%` }} />
      </div>
    </div>
  )
}
