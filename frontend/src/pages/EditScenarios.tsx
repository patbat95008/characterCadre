import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { charactersApi, scenariosApi } from '../api/client'
import BeatsEditor from '../components/BeatsEditor'
import StringList from '../components/StringList'
import SummaryField from '../components/SummaryField'
import type { CharacterSummary, Scenario, ScenarioSummaryRow } from '../types'

function blankScenario(): Scenario {
  return {
    id: '',
    name: '',
    summary: '',
    summary_hash: '',
    initial_message: '',
    system_prompt: '',
    persistent_messages: [],
    dm_only_info: [],
    recommended_character_ids: [],
    beats: [],
  }
}

export default function EditScenarios() {
  const [list, setList] = useState<ScenarioSummaryRow[]>([])
  const [characters, setCharacters] = useState<CharacterSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Scenario | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<string | null>(null)

  async function loadList() {
    try {
      const [scenarios, chars] = await Promise.all([
        scenariosApi.list(),
        charactersApi.list(),
      ])
      setList(scenarios)
      setCharacters(chars)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    }
  }

  async function loadScenario(id: string) {
    try {
      const s = await scenariosApi.get(id)
      setDraft(s)
      setSelectedId(id)
      setSaveStatus(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    }
  }

  useEffect(() => {
    loadList()
  }, [])

  function startNew() {
    setDraft(blankScenario())
    setSelectedId(null)
    setSaveStatus(null)
  }

  async function save() {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      // Strip placeholder beat ids — backend assigns real UUIDs
      const beats = draft.beats.map(b => ({
        ...b,
        id: b.id.startsWith('new-') ? '' : b.id,
      }))
      const payload: Scenario = { ...draft, beats }

      let saved: Scenario
      if (draft.id) {
        saved = await scenariosApi.update(draft.id, payload)
      } else {
        saved = await scenariosApi.create(payload)
      }
      setDraft(saved)
      setSelectedId(saved.id)
      setSaveStatus('Saved.')
      await loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  async function deleteScenario() {
    if (!draft?.id) return
    if (!confirm(`Delete scenario "${draft.name}"?`)) return
    try {
      const result = await scenariosApi.delete(draft.id)
      if (result.in_use_by_saves) {
        alert('Scenario deleted, but at least one save still references it.')
      }
      setDraft(null)
      setSelectedId(null)
      await loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  async function regenScenarioSummary(): Promise<string> {
    if (!draft?.id) {
      throw new Error('Save the scenario first.')
    }
    const r = await scenariosApi.regenerateSummary(draft.id)
    return r.summary
  }

  function toggleRecommended(charId: string) {
    if (!draft) return
    const has = draft.recommended_character_ids.includes(charId)
    setDraft({
      ...draft,
      recommended_character_ids: has
        ? draft.recommended_character_ids.filter(id => id !== charId)
        : [...draft.recommended_character_ids, charId],
    })
  }

  return (
    <div className="flex h-screen">
      <aside className="w-64 border-r border-gray-800 bg-gray-900 flex flex-col">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <Link to="/" className="text-xs text-gray-400 hover:text-gray-200">← Menu</Link>
          <h2 className="text-sm font-semibold text-gray-200">Scenarios</h2>
        </div>
        <div className="p-3 border-b border-gray-800">
          <button
            onClick={startNew}
            className="w-full bg-blue-600 hover:bg-blue-500 text-white text-sm rounded py-1.5"
          >
            + New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {list.map(s => (
            <button
              key={s.id}
              onClick={() => loadScenario(s.id)}
              className={`w-full text-left px-4 py-2 text-sm border-b border-gray-800 hover:bg-gray-800 ${
                selectedId === s.id ? 'bg-gray-800' : ''
              }`}
            >
              <div className="text-gray-200 truncate">{s.name || '(unnamed)'}</div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {s.beat_count} {s.beat_count === 1 ? 'beat' : 'beats'}
              </div>
            </button>
          ))}
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        {!draft && (
          <div className="h-full flex items-center justify-center text-gray-500 text-sm">
            Select a scenario to edit, or create a new one.
          </div>
        )}
        {draft && (
          <div className="max-w-3xl mx-auto p-6 space-y-6">
            {error && (
              <div className="bg-red-950 border border-red-800 text-red-200 text-sm rounded px-3 py-2">
                {error}
              </div>
            )}
            {saveStatus && <div className="text-xs text-emerald-400">{saveStatus}</div>}

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400">Name</label>
              <input
                type="text"
                value={draft.name}
                onChange={e => setDraft({ ...draft, name: e.target.value })}
                className="w-full bg-gray-800 text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600"
              />
            </div>

            <SummaryField
              label="Director summary"
              value={draft.summary}
              onChange={v => setDraft({ ...draft, summary: v })}
              onRegenerate={regenScenarioSummary}
              helper="Used by the Director AI to orient itself to this scenario."
            />

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400">Initial message</label>
              <textarea
                value={draft.initial_message}
                onChange={e => setDraft({ ...draft, initial_message: e.target.value })}
                rows={6}
                placeholder="The opening DM message a new save begins with — used only when there are no beats."
                className="w-full bg-gray-800 text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
              />
            </div>

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400">System prompt</label>
              <textarea
                value={draft.system_prompt}
                onChange={e => setDraft({ ...draft, system_prompt: e.target.value })}
                rows={4}
                className="w-full bg-gray-800 text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
              />
            </div>

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400 block mb-2">
                Persistent messages (always shown to the LLM)
              </label>
              <StringList
                values={draft.persistent_messages}
                onChange={v => setDraft({ ...draft, persistent_messages: v })}
                rows={2}
              />
            </div>

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400 block mb-2">
                DM-only info (only the DM sees these)
              </label>
              <StringList
                values={draft.dm_only_info}
                onChange={v => setDraft({ ...draft, dm_only_info: v })}
                rows={3}
              />
            </div>

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400 block mb-2">
                Recommended characters
              </label>
              <div className="flex flex-wrap gap-2">
                {characters.map(c => {
                  const on = draft.recommended_character_ids.includes(c.id)
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => toggleRecommended(c.id)}
                      className={`text-xs px-3 py-1 rounded border transition-colors ${
                        on
                          ? 'bg-blue-900 border-blue-600 text-blue-100'
                          : 'border-gray-700 hover:border-gray-500 text-gray-400'
                      }`}
                    >
                      {c.name}
                      {c.is_dm && <span className="ml-1 text-indigo-300">(DM)</span>}
                    </button>
                  )
                })}
                {characters.length === 0 && (
                  <span className="text-xs text-gray-500">No characters yet — create some first.</span>
                )}
              </div>
            </div>

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400 block mb-2">
                Beats
              </label>
              <BeatsEditor
                scenarioId={draft.id || undefined}
                beats={draft.beats}
                onChange={beats => setDraft({ ...draft, beats })}
              />
            </div>

            <div className="flex gap-2 pt-3 border-t border-gray-800">
              <button
                onClick={save}
                disabled={busy}
                className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm px-4 py-2 rounded"
              >
                {busy ? 'Saving…' : 'Save'}
              </button>
              {draft.id && (
                <button
                  onClick={deleteScenario}
                  className="ml-auto text-sm border border-gray-700 hover:border-red-500 hover:text-red-300 text-gray-400 px-3 py-2 rounded"
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
