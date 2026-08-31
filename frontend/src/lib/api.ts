/**
 * Typed API client.
 *
 * One `request` helper owns fetch, JSON parsing and error shaping, so every
 * screen fails the same way and React Query can retry uniformly. `ApiError`
 * carries the HTTP status, which is what lets the UI distinguish "the backend
 * is down, show a retry state" from "this learner has no path yet, show an
 * empty state".
 *
 * The types below mirror `backend/app/schemas.py` exactly. They are hand-kept
 * rather than generated: the contract is small, and a hand-written type that
 * disagrees with the server fails loudly in `npm run build`.
 */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ??
  'https://hcl-amplified-the-ai-challenge-production.up.railway.app'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }

  /** True when the backend could not be reached at all. */
  get isOffline(): boolean {
    return this.status === 0
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
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
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
/* Types                                                               */
/* ------------------------------------------------------------------ */
export type Health = {
  status: 'ok' | 'degraded'
  version: string
  llm_available: boolean
  llm_provider: string
  embedder: string
  catalog_size: number
  graph_nodes: number
  graph_tracks: number
  question_bank: number
}

export type ProfileDraft = {
  interests?: string[] | null
  experience_level?: 'beginner' | 'intermediate' | 'advanced' | null
  completed_skills?: string[] | null
  goal_text?: string | null
  hours_per_week?: number | null
  target_date?: string | null
  format_pref?: 'video' | 'text' | 'interactive' | 'any' | null
  cost_pref?: 'free' | 'any' | null
  language?: string | null
  low_bandwidth?: boolean | null
}

export type IntakeMessage = {
  session_id: string
  assistant_message: string
  profile: ProfileDraft
  ready: boolean
  llm_degraded: boolean
}

export type GoalCandidate = { skill_id: string; name: string; track: string; score: number }

export type IntakeCommit = {
  learner_id: number
  goal_node_ids: string[]
  goal_names: string[]
  candidates: GoalCandidate[]
  seeded_mastery: Record<string, number>
  llm_degraded: boolean
}

export type DiagnosticQuestion = {
  done: boolean
  quiz_item_id?: number | null
  skill_id?: string | null
  skill_name?: string | null
  question?: string | null
  options: string[]
  asked: number
  max_questions: number
  confidence: number
  llm_degraded: boolean
  /** confident | max_questions | nothing_to_measure | questions_not_ready */
  done_reason?: string
  /** Skills in the plan that have no question written yet. */
  unmeasured?: number
}

export type DiagnosticAnswer = {
  correct: boolean
  skill_id: string
  new_score: number
  confidence: number
  asked: number
  done: boolean
}

export type Resource = {
  id: string
  title: string
  provider: string
  url: string
  format: string
  cost: string
  duration_hours: number
  level: string
  rating: number | null
  description: string
  /** Found by live search rather than curated. The URL, title, provider,
   *  format and cost were read off the page that answered; duration and
   *  level are estimates either way. */
  discovered?: boolean
  found_at?: string
}

/** What the curriculum makes of a goal, and what it would do about it. */
export type Coverage = {
  goal_text: string
  covered: boolean
  matched_skill_id: string | null
  matched_skill_name: string | null
  reason: string
  already_built: boolean
  topic: string | null
  can_build: boolean
  build_unavailable_reason: string
}

/** Progress of a topic being built. `status` is none | queued | running | done | failed. */
export type BuildStatus = {
  goal_text: string
  status: 'none' | 'queued' | 'running' | 'done' | 'failed'
  stage: string
  detail: string
  progress: number
  topic: string
  goal_skill_ids: string[]
  skill_count: number
  resource_count: number
  error: string
  elapsed: number
}

export type Provenance = {
  skill: string
  skill_name?: string
  track?: string
  why_needed?: {
    goal: string
    /** Skill ids: the machine-readable trace shown in the "why" panel. */
    path_to_goal: string[]
    /** The same chain in words. Absent on plans built before it existed. */
    path_to_goal_names?: string[]
    is_goal: boolean
  }
  your_level?: { score: number; source: string; evidence_q_ids: number[]; threshold: number }
  why_this_resource?: {
    resource_id: string | null
    title: string | null
    provider: string | null
    beat_alternatives: number
    score: number | null
    reasons: string[]
  }
  placement?: { week: number; est_hours: number; unlocks: string[]; unlock_count: number }
  milestone?: { track: string; questions: number; covers_up_to: string }
}

export type PathItem = {
  id: number | null
  order_index: number
  week_number: number
  skill_id: string
  skill_name: string
  kind: 'resource' | 'milestone'
  status: 'pending' | 'in_progress' | 'done' | 'skipped'
  est_hours: number
  course: Resource | null
  alternatives: Resource[]
  provenance: Provenance
  rationale_text: string
}

export type LearningPath = {
  learner_id: number
  path_id: number | null
  version: number
  status: string
  total_hours: number
  finish_week: number
  hours_per_week: number
  goal_node_ids: string[]
  goal_names: string[]
  items: PathItem[]
  /** week -> hours actually allocated that week, spill included */
  week_load: Record<string, number>
  llm_degraded: boolean
}

export type WhatIf = {
  hours_per_week: number
  finish_week: number
  total_hours: number
  item_count: number
  weeks: { week: number; allocated_hours: number; item_count: number; skills: string[] }[]
  persisted: boolean
}

export type PathDiff = {
  from_version: number
  to_version: number
  added: { skill_id: string; kind: string; week: number }[]
  removed: { skill_id: string; kind: string; week: number }[]
  moved_weeks: { skill_id: string; from_week: number; to_week: number }[]
  resource_swapped: { skill_id: string; from_course_id: string; to_course_id: string }[]
  finish_week_delta: number
  unchanged: boolean
}

export type PathEventType =
  | 'milestone_failed'
  | 'too_easy'
  | 'too_hard'
  | 'behind_schedule'
  | 'goal_changed'
  | 'resource_disliked'
  | 'completed_item'

export type PathEventResult = {
  event: string
  message: string
  version: number
  diff: PathDiff
  options: string[]
  llm_degraded: boolean
}

export type Dashboard = {
  learner_id: number
  goal_names: string[]
  items_total: number
  items_done: number
  progress_pct: number
  hours_done: number
  hours_remaining: number
  finish_week: number
  current_week: number
  mastery_radar: { track: string; mastery: number; skills: number; mastered: number }[]
  milestones: { week: number; track: string; label: string; status: string }[]
  next_actions: PathItem[]
  activity: { id: number; type: string; payload: Record<string, unknown>; created_at: string }[]
}

export type GraphNode = {
  id: string
  name: string
  track: string
  difficulty: number
  est_hours: number
  mastery: number
  source: string | null
  in_path: boolean
  is_goal: boolean
  week: number | null
}

export type GraphPayload = {
  nodes: GraphNode[]
  edges: { source: string; target: string; in_path: boolean }[]
}

export type ChatReply = {
  reply: string
  citations: { title: string; url: string; skill: string }[]
  llm_degraded: boolean
}

/* ------------------------------------------------------------------ */
/* Endpoints                                                           */
/* ------------------------------------------------------------------ */
/**
 * What the language layer is doing and what it has spent.
 *
 * `credit_limit` is nullable on purpose: a free-tier key has no published
 * allowance, and the interface shows counts rather than inventing a ceiling.
 */
export type Usage = {
  provider: string
  chain: string[]
  tokens_per_second: number
  openrouter: {
    provider: string
    model: string
    free_models_available: number
    cooling_down: string[]
    retired: string[]
    tokens_per_second: number
    limit_published: boolean
    session: {
      requests: number
      failures: number
      prompt_tokens: number
      completion_tokens: number
      total_tokens: number
      cost: number
      since: number
    }
    account: {
      configured: boolean
      reachable?: boolean
      free_tier?: boolean
      credit_limit: number | null
      credit_used: number | null
      credit_used_today: number | null
    }
  } | null
}

export const api = {
  health: () => request<Health>('/health'),

  coverage: (goalText: string) =>
    request<Coverage>(`/api/topics/coverage?goal_text=${encodeURIComponent(goalText)}`),

  startBuild: (goalText: string, force = false) =>
    request<BuildStatus>('/api/topics/build', {
      method: 'POST',
      body: { goal_text: goalText, force },
    }),

  buildStatus: (goalText: string) =>
    request<BuildStatus>(`/api/topics/build?goal_text=${encodeURIComponent(goalText)}`),

  intakeMessage: (message: string, sessionId: string | null) =>
    request<IntakeMessage>('/api/intake/message', {
      method: 'POST',
      body: { message, session_id: sessionId },
    }),

  intakeCommit: (sessionId: string | null, profile?: ProfileDraft, displayName = 'Learner') =>
    request<IntakeCommit>('/api/intake/commit', {
      method: 'POST',
      body: { session_id: sessionId, profile, display_name: displayName },
    }),

  nextQuestion: (learnerId: number) =>
    request<DiagnosticQuestion>(`/api/diagnostic/next/${learnerId}`),

  answerQuestion: (quizItemId: number, chosenIndex: number | null, dontKnow = false) =>
    request<DiagnosticAnswer>('/api/diagnostic/answer', {
      method: 'POST',
      body: { quiz_item_id: quizItemId, chosen_index: chosenIndex, dont_know: dontKnow },
    }),

  generatePath: (learnerId: number) =>
    request<LearningPath>(`/api/path/generate/${learnerId}`, { method: 'POST' }),

  getPath: (learnerId: number, version?: number) =>
    request<LearningPath>(
      `/api/path/${learnerId}${version === undefined ? '' : `?version=${version}`}`,
    ),

  usage: () => request<Usage>('/api/usage'),

  whatIf: (learnerId: number, hoursPerWeek: number) =>
    request<WhatIf>('/api/path/whatif', {
      method: 'POST',
      body: { learner_id: learnerId, hours_per_week: hoursPerWeek },
    }),

  sendEvent: (learnerId: number, type: PathEventType, payload: Record<string, unknown> = {}) =>
    request<PathEventResult>('/api/path/event', {
      method: 'POST',
      body: { learner_id: learnerId, type, payload },
    }),

  diff: (learnerId: number, from: number, to: number) =>
    request<PathDiff>(`/api/path/${learnerId}/diff/${from}/${to}`),

  dashboard: (learnerId: number) => request<Dashboard>(`/api/dashboard/${learnerId}`),

  graph: (learnerId: number) => request<GraphPayload>(`/api/graph/${learnerId}`),

  chat: (learnerId: number, message: string) =>
    request<ChatReply>(`/api/chat/${learnerId}`, { method: 'POST', body: { message } }),
}
