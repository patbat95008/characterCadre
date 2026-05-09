import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { savesApi } from '../api/client'
import type { SaveSummaryRow } from '../types'

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export default function MainMenu() {
  const [saves, setSaves] = useState<SaveSummaryRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      const list = await savesApi.list()
      setSaves(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function handleDelete(id: string, name: string) {
    if (!confirm(`Delete save "${name}"? This cannot be undone.`)) return
    try {
      await savesApi.delete(id)
      load()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to delete')
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold text-gray-100">CharacterCadre</h1>
        <p className="text-sm text-gray-500 mt-1">
          Local roleplay with structured beats and a director.
        </p>
      </header>

      <section className="mb-12">
        <h2 className="text-sm uppercase tracking-wide text-gray-400 mb-3">Saved games</h2>
        {error && <p className="text-red-400 text-sm">{error}</p>}
        {saves === null && !error && <p className="text-gray-500 text-sm">Loading…</p>}
        {saves !== null && saves.length === 0 && (
          <div className="border border-gray-800 rounded p-6 text-center">
            <p className="text-gray-400 text-sm mb-3">No saves yet.</p>
            <Link
              to="/new-game"
              className="inline-block bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded text-sm"
            >
              Start a new game
            </Link>
          </div>
        )}
        {saves !== null && saves.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {saves.map(s => (
              <div
                key={s.id}
                className="border border-gray-800 rounded p-4 bg-gray-900 flex flex-col justify-between"
              >
                <div>
                  <h3 className="font-semibold text-gray-100">{s.name}</h3>
                  <p className="text-xs text-gray-500 mt-0.5">{s.scenario_name}</p>
                  <p className="text-xs text-gray-600 mt-2">
                    {s.message_count} messages · last played {fmtTime(s.updated_at)}
                  </p>
                  {s.current_beat_name && (
                    <p className="text-xs text-indigo-400 mt-1">Scene: {s.current_beat_name}</p>
                  )}
                  {s.sandbox_mode && (
                    <p className="text-xs text-amber-400 mt-1">Sandbox mode</p>
                  )}
                </div>
                <div className="flex gap-2 mt-4">
                  <Link
                    to={`/game/${s.id}`}
                    className="bg-blue-600 hover:bg-blue-500 text-white text-sm px-3 py-1.5 rounded"
                  >
                    Continue
                  </Link>
                  <button
                    onClick={() => handleDelete(s.id, s.name)}
                    className="text-sm border border-gray-700 hover:border-red-500 hover:text-red-300 text-gray-400 px-3 py-1.5 rounded"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Link
          to="/new-game"
          className="border border-blue-800 hover:border-blue-600 bg-blue-950/40 hover:bg-blue-950/70 rounded p-4 text-center transition-colors"
        >
          <div className="text-lg font-semibold text-blue-200">New Game</div>
          <p className="text-xs text-blue-400/70 mt-1">Pick a scenario and roster.</p>
        </Link>
        <Link
          to="/scenarios"
          className="border border-gray-800 hover:border-gray-600 rounded p-4 text-center transition-colors"
        >
          <div className="text-lg font-semibold text-gray-200">Edit Scenarios</div>
          <p className="text-xs text-gray-500 mt-1">Author scenes, beats, and DM info.</p>
        </Link>
        <Link
          to="/characters"
          className="border border-gray-800 hover:border-gray-600 rounded p-4 text-center transition-colors"
        >
          <div className="text-lg font-semibold text-gray-200">Edit Characters</div>
          <p className="text-xs text-gray-500 mt-1">Personalities, examples, avatars.</p>
        </Link>
      </section>
    </div>
  )
}
