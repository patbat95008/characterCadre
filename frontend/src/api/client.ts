import type {
  Character,
  CharacterSummary,
  OptionItem,
  Save,
  SaveSummaryRow,
  Scenario,
  ScenarioSummaryRow,
} from '../types'

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) message = String(body.detail)
    } catch {
      /* ignore */
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

// ── Characters ────────────────────────────────────────────────────────────────

export const charactersApi = {
  list: () => fetch('/api/characters').then(r => jsonOrThrow<CharacterSummary[]>(r)),

  get: (id: string) =>
    fetch(`/api/characters/${id}`).then(r => jsonOrThrow<Character>(r)),

  create: (c: Character) =>
    fetch('/api/characters', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(c),
    }).then(r => jsonOrThrow<Character>(r)),

  update: (id: string, c: Character) =>
    fetch(`/api/characters/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(c),
    }).then(r => jsonOrThrow<Character>(r)),

  delete: (id: string) =>
    fetch(`/api/characters/${id}`, { method: 'DELETE' }).then(r =>
      jsonOrThrow<{ deleted: boolean; in_use_by_saves: boolean }>(r),
    ),

  regenerateSummary: (id: string) =>
    fetch(`/api/characters/${id}/regenerate-summary`, { method: 'POST' }).then(r =>
      jsonOrThrow<{ summary: string }>(r),
    ),

  uploadAvatar: (id: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`/api/characters/${id}/avatar`, {
      method: 'POST',
      body: fd,
    }).then(r => jsonOrThrow<{ avatar_path: string }>(r))
  },

  deleteAvatar: (id: string) =>
    fetch(`/api/characters/${id}/avatar`, { method: 'DELETE' }).then(r =>
      jsonOrThrow<{ avatar_path: string }>(r),
    ),

  importJson: (payload: unknown) =>
    fetch('/api/characters/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(r => jsonOrThrow<Character>(r)),

  exportJson: (id: string) =>
    fetch(`/api/characters/${id}/export`).then(r => jsonOrThrow<Record<string, unknown>>(r)),
}

// ── Scenarios ─────────────────────────────────────────────────────────────────

export const scenariosApi = {
  list: () => fetch('/api/scenarios').then(r => jsonOrThrow<ScenarioSummaryRow[]>(r)),

  get: (id: string) => fetch(`/api/scenarios/${id}`).then(r => jsonOrThrow<Scenario>(r)),

  create: (s: Scenario) =>
    fetch('/api/scenarios', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    }).then(r => jsonOrThrow<Scenario>(r)),

  update: (id: string, s: Scenario) =>
    fetch(`/api/scenarios/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    }).then(r => jsonOrThrow<Scenario>(r)),

  delete: (id: string) =>
    fetch(`/api/scenarios/${id}`, { method: 'DELETE' }).then(r =>
      jsonOrThrow<{ deleted: boolean; in_use_by_saves: boolean }>(r),
    ),

  regenerateSummary: (id: string) =>
    fetch(`/api/scenarios/${id}/regenerate-summary`, { method: 'POST' }).then(r =>
      jsonOrThrow<{ summary: string }>(r),
    ),

  regenerateBeatSummary: (scenarioId: string, beatId: string) =>
    fetch(`/api/scenarios/${scenarioId}/beats/${beatId}/regenerate-summary`, {
      method: 'POST',
    }).then(r => jsonOrThrow<{ summary: string }>(r)),
}

// ── Saves ─────────────────────────────────────────────────────────────────────

export const savesApi = {
  list: () => fetch('/api/saves').then(r => jsonOrThrow<SaveSummaryRow[]>(r)),

  get: (id: string) => fetch(`/api/saves/${id}`).then(r => jsonOrThrow<Save>(r)),

  create: (body: {
    scenario_id: string
    active_character_ids: string[]
    user_name: string
    name?: string
  }) =>
    fetch('/api/saves', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => jsonOrThrow<Save>(r)),

  update: (id: string, patch: { name?: string; max_context_tokens?: number }) =>
    fetch(`/api/saves/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }).then(r => jsonOrThrow<Save>(r)),

  delete: (id: string) =>
    fetch(`/api/saves/${id}`, { method: 'DELETE' }).then(r =>
      jsonOrThrow<{ deleted: boolean }>(r),
    ),

  advanceBeat: (id: string, body: { next_beat_id: string; wipe_context: boolean }) =>
    fetch(`/api/saves/${id}/advance-beat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => jsonOrThrow<Save>(r)),

  setSandboxMode: (id: string, enabled: boolean) =>
    fetch(`/api/saves/${id}/sandbox-mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }).then(r => jsonOrThrow<Save>(r)),

  seedOptions: (id: string) =>
    fetch(`/api/saves/${id}/seed-options`).then(r => jsonOrThrow<{ options: OptionItem[]; context: string }>(r)),
}

// ── Streaming chat ────────────────────────────────────────────────────────────

export async function sendTurn(saveId: string, userMessage: string): Promise<Response> {
  const res = await fetch('/api/chat/turn', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_message: userMessage, save_id: saveId }),
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
  return res
}

export function avatarUrl(path: string | null | undefined): string | null {
  if (!path) return null
  if (path.startsWith('http')) return path
  if (path.startsWith('/')) return path
  return `/${path}`
}
