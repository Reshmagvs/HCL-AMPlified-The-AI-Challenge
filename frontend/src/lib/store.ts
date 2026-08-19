/**
 * Client-side session state.
 *
 * Deliberately tiny: everything that belongs to the server (path, dashboard,
 * graph) lives in React Query, and this store holds only what the browser owns
 * — which learner is being viewed, and the intake conversation in progress.
 * Persisting the learner id to localStorage is what lets a reload land back on
 * the same plan instead of an empty intake screen.
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ProfileDraft } from './api'

export type ChatTurn = { role: 'learner' | 'assistant'; text: string }

type SessionState = {
  learnerId: number | null
  sessionId: string | null
  transcript: ChatTurn[]
  profile: ProfileDraft
  lastDiffVersion: number | null
  /** Skills recognised from what the learner said, recorded at commit time. */
  seededSkills: Record<string, number>
  goalNames: string[]

  setLearner: (id: number) => void
  setSession: (id: string) => void
  addTurn: (turn: ChatTurn) => void
  setProfile: (profile: ProfileDraft) => void
  setSeeded: (seeded: Record<string, number>, goalNames: string[]) => void
  noteDiff: (version: number | null) => void
  reset: () => void
}

const EMPTY = {
  learnerId: null,
  sessionId: null,
  transcript: [] as ChatTurn[],
  profile: {} as ProfileDraft,
  lastDiffVersion: null,
  seededSkills: {} as Record<string, number>,
  goalNames: [] as string[],
}

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      ...EMPTY,
      setLearner: (learnerId) => set({ learnerId }),
      setSession: (sessionId) => set({ sessionId }),
      addTurn: (turn) => set((state) => ({ transcript: [...state.transcript, turn] })),
      setProfile: (profile) => set({ profile }),
      setSeeded: (seededSkills, goalNames) => set({ seededSkills, goalNames }),
      noteDiff: (lastDiffVersion) => set({ lastDiffVersion }),
      reset: () => set({ ...EMPTY }),
    }),
    { name: 'lodestar-session' },
  ),
)

/** Fields shown on the intake profile card, in the order they fill in. */
export const PROFILE_FIELDS: { key: keyof ProfileDraft; label: string }[] = [
  { key: 'goal_text', label: 'Goal' },
  { key: 'hours_per_week', label: 'Hours a week' },
  { key: 'experience_level', label: 'Experience' },
  { key: 'completed_skills', label: 'Already knows' },
  { key: 'format_pref', label: 'Prefers' },
  { key: 'cost_pref', label: 'Cost' },
  { key: 'target_date', label: 'Target date' },
  { key: 'low_bandwidth', label: 'Light on data' },
]

export function formatField(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null
  if (Array.isArray(value)) return value.length ? value.join(', ') : null
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}
