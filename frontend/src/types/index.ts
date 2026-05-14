// ── Domain models (mirror of backend Pydantic models) ────────────────────────

export interface Message {
  id: string
  /** 'roll' is frontend-only — never stored in the backend save */
  role: 'user' | 'character' | 'dm' | 'roll'
  character_id: string | null
  content: string
  timestamp: string
  is_dm_only: boolean
  beat_id_at_time: string | null
  /** Only present when role === 'roll' */
  roll_data?: RollResultEvent
}

export interface Character {
  id: string
  name: string
  description: string
  description_summary: string
  description_hash: string
  response_examples: { user: string; char: string }[]
  is_dm: boolean
  avatar_path: string | null
}

export interface CharacterSummary {
  id: string
  name: string
  is_dm: boolean
  description_summary: string
  has_avatar: boolean
}

export interface Beat {
  id: string
  order: number
  name: string
  description: string
  summary: string
  summary_hash: string
  transition_condition: string
  starter_prompt: string
}

export interface Scenario {
  id: string
  name: string
  summary: string
  summary_hash: string
  initial_message: string
  system_prompt: string
  persistent_messages: string[]
  dm_only_info: string[]
  recommended_character_ids: string[]
  beats: Beat[]
}

export interface ScenarioSummaryRow {
  id: string
  name: string
  summary: string
  beat_count: number
  has_beats: boolean
}

export interface Save {
  id: string
  scenario_id: string
  name: string
  active_character_ids: string[]
  user_name: string
  current_beat_id: string | null
  sandbox_mode: boolean
  messages: Message[]
  max_context_tokens: number
  created_at: string
  updated_at: string
}

export interface SaveSummaryRow {
  id: string
  name: string
  scenario_id: string
  scenario_name: string
  message_count: number
  current_beat_id: string | null
  current_beat_name: string | null
  sandbox_mode: boolean
  created_at: string
  updated_at: string
}

// ── SSE event payloads ────────────────────────────────────────────────────────

export interface DirectorEvent {
  speaker_id: string | null
  dm_narrating: boolean
  direction_note: string
}

export interface NoticeEvent {
  level: 'info' | 'warning' | 'error'
  message: string
}

export interface BeatTransitionEvent {
  new_beat_id: string
  new_beat_name: string
}

export interface TokenEvent {
  character_id: string
  text: string
}

export interface MessageCompleteEvent {
  message_id: string
  character_id: string
}

export interface DiceSpec {
  dice: 'D20' | 'D100'
  difficulty: 'Easy' | 'Medium' | 'Hard'
}

export interface OptionItem {
  text: string
  advances_beat: boolean
  dice_roll: DiceSpec | null
}

export interface OptionsEvent {
  options: OptionItem[]
}

export type RollOutcome = 'critical_failure' | 'failure' | 'success' | 'critical_success'

export interface RollResultEvent {
  dice: string
  value: number
  max_value: number
  threshold: number
  difficulty: string
  outcome: RollOutcome
  is_nat_crit: boolean
}

export interface OptionsContextEvent {
  context: string
}

export interface RegenerateEvent {
  reason: string
  character_id?: string
}

export interface ValidationWarningEvent {
  reason: string
}

export interface ValidationFailedEvent {
  call: string
  reason: string
}

export interface ErrorEvent {
  reason: string
}
