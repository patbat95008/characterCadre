import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { charactersApi, savesApi, scenariosApi } from '../api/client'
import type { CharacterSummary, ScenarioSummaryRow } from '../types'

export default function NewGame() {
  const navigate = useNavigate()
  const [scenarios, setScenarios] = useState<ScenarioSummaryRow[]>([])
  const [characters, setCharacters] = useState<CharacterSummary[]>([])
  const [scenarioId, setScenarioId] = useState<string | null>(null)
  const [activeIds, setActiveIds] = useState<Set<string>>(new Set())
  const [userName, setUserName] = useState<string>('Player')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    Promise.all([scenariosApi.list(), charactersApi.list()])
      .then(([s, c]) => {
        setScenarios(s)
        setCharacters(c)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
  }, [])

  async function pickScenario(id: string) {
    setScenarioId(id)
    try {
      const full = await scenariosApi.get(id)
      setActiveIds(new Set(full.recommended_character_ids))
    } catch {
      setActiveIds(new Set())
    }
  }

  function toggleCharacter(id: string) {
    const next = new Set(activeIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setActiveIds(next)
  }

  const dmsSelected = characters.filter(c => activeIds.has(c.id) && c.is_dm).length
  const validRoster = dmsSelected === 1
  const canStart = !!scenarioId && validRoster && userName.trim().length > 0 && !busy

  async function start() {
    if (!scenarioId) return
    setBusy(true)
    setError(null)
    try {
      const save = await savesApi.create({
        scenario_id: scenarioId,
        active_character_ids: Array.from(activeIds),
        user_name: userName.trim(),
      })
      navigate(`/game/${save.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start')
      setBusy(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-gray-100">New Game</h1>
        <Link to="/" className="text-xs text-gray-400 hover:text-gray-200">← Menu</Link>
      </div>
      {error && (
        <div className="bg-red-950 border border-red-800 text-red-200 text-sm rounded px-3 py-2 mb-4">
          {error}
        </div>
      )}

      <section className="mb-8">
        <h2 className="text-sm uppercase tracking-wide text-gray-400 mb-3">1. Choose a scenario</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {scenarios.map(s => (
            <button
              key={s.id}
              onClick={() => pickScenario(s.id)}
              className={`text-left border rounded p-4 transition-colors ${
                scenarioId === s.id
                  ? 'border-blue-500 bg-blue-950/30'
                  : 'border-gray-800 hover:border-gray-600'
              }`}
            >
              <div className="font-semibold text-gray-100">{s.name}</div>
              <div className="text-xs text-gray-500 mt-1">{s.summary || '—'}</div>
              <div className="text-[11px] text-gray-600 mt-2">
                {s.beat_count > 0 ? `${s.beat_count} beats` : 'No beats (single arc)'}
              </div>
            </button>
          ))}
        </div>
      </section>

      {scenarioId && (
        <section className="mb-8">
          <h2 className="text-sm uppercase tracking-wide text-gray-400 mb-3">
            2. Choose characters (must include exactly one DM)
          </h2>
          <div className="flex flex-wrap gap-2">
            {characters.map(c => {
              const on = activeIds.has(c.id)
              return (
                <button
                  key={c.id}
                  onClick={() => toggleCharacter(c.id)}
                  className={`text-sm px-3 py-1.5 rounded border transition-colors ${
                    on
                      ? c.is_dm
                        ? 'bg-indigo-900 border-indigo-500 text-indigo-100'
                        : 'bg-blue-900 border-blue-500 text-blue-100'
                      : 'border-gray-700 hover:border-gray-500 text-gray-400'
                  }`}
                >
                  {c.name}
                  {c.is_dm && <span className="ml-1 text-[10px] uppercase">DM</span>}
                </button>
              )
            })}
          </div>
          {!validRoster && (
            <p className="text-xs text-amber-400 mt-2">
              Pick exactly one DM character (currently {dmsSelected}).
            </p>
          )}
        </section>
      )}

      {scenarioId && (
        <section className="mb-8">
          <h2 className="text-sm uppercase tracking-wide text-gray-400 mb-3">3. Your name</h2>
          <input
            type="text"
            value={userName}
            onChange={e => setUserName(e.target.value)}
            className="w-full max-w-md bg-gray-800 text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600"
          />
          <p className="text-[11px] text-gray-500 mt-1">Used wherever {`{{user}}`} appears.</p>
        </section>
      )}

      <button
        onClick={start}
        disabled={!canStart}
        className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm px-5 py-2 rounded"
      >
        {busy ? 'Starting…' : 'Start Adventure'}
      </button>
    </div>
  )
}
