import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { avatarUrl, charactersApi } from '../api/client'
import ResponseExamplePairs from '../components/ResponseExamplePairs'
import SummaryField from '../components/SummaryField'
import type { Character, CharacterSummary } from '../types'

function blankCharacter(): Character {
  return {
    id: '',
    name: '',
    description: '',
    description_summary: '',
    description_hash: '',
    response_examples: [],
    is_dm: false,
    avatar_path: null,
  }
}

export default function EditCharacters() {
  const [list, setList] = useState<CharacterSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Character | null>(null)
  const [originalSummary, setOriginalSummary] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const importInputRef = useRef<HTMLInputElement>(null)

  async function loadList() {
    try {
      setList(await charactersApi.list())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load list')
    }
  }

  async function loadCharacter(id: string) {
    try {
      const c = await charactersApi.get(id)
      setDraft(c)
      setOriginalSummary(c.description_summary)
      setSelectedId(id)
      setSaveStatus(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load character')
    }
  }

  useEffect(() => {
    loadList()
  }, [])

  function startNew() {
    setDraft(blankCharacter())
    setOriginalSummary('')
    setSelectedId(null)
    setSaveStatus(null)
  }

  async function save() {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      let saved: Character
      if (draft.id) {
        saved = await charactersApi.update(draft.id, draft)
      } else {
        saved = await charactersApi.create(draft)
      }
      setDraft(saved)
      setOriginalSummary(saved.description_summary)
      setSelectedId(saved.id)
      setSaveStatus('Saved.')
      await loadList()
      // Poll briefly for background summary regen
      pollForSummary(saved.id, saved.description_hash)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  function pollForSummary(id: string, expectedHash: string) {
    let attempts = 0
    const interval = window.setInterval(async () => {
      attempts++
      try {
        const fresh = await charactersApi.get(id)
        if (fresh.description_hash === expectedHash && fresh.description_summary) {
          if (
            draftRef.current &&
            draftRef.current.id === id &&
            !summaryEditedRef.current
          ) {
            setDraft(d => (d && d.id === id ? { ...d, description_summary: fresh.description_summary } : d))
            setOriginalSummary(fresh.description_summary)
          }
          window.clearInterval(interval)
        }
      } catch {
        /* ignore */
      }
      if (attempts > 8) window.clearInterval(interval)
    }, 1500)
  }
  const draftRef = useRef<Character | null>(null)
  const summaryEditedRef = useRef<boolean>(false)
  draftRef.current = draft
  summaryEditedRef.current = !!draft && draft.description_summary !== originalSummary

  async function regenSummary(): Promise<string> {
    if (!draft?.id) {
      throw new Error('Save the character first.')
    }
    const r = await charactersApi.regenerateSummary(draft.id)
    return r.summary
  }

  async function deleteCharacter() {
    if (!draft?.id) return
    if (!confirm(`Delete "${draft.name}"?`)) return
    try {
      const result = await charactersApi.delete(draft.id)
      if (result.in_use_by_saves) {
        alert('This character was deleted, but at least one existing save still references it.')
      }
      setDraft(null)
      setSelectedId(null)
      await loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  async function uploadAvatar(file: File) {
    if (!draft?.id) {
      alert('Save the character first to upload an avatar.')
      return
    }
    try {
      const result = await charactersApi.uploadAvatar(draft.id, file)
      setDraft({ ...draft, avatar_path: result.avatar_path })
      await loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    }
  }

  async function handleImport(file: File) {
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      const imported = await charactersApi.importJson(parsed)
      await loadList()
      await loadCharacter(imported.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed')
    }
  }

  async function exportCharacter() {
    if (!draft?.id) return
    try {
      const card = await charactersApi.exportJson(draft.id)
      const blob = new Blob([JSON.stringify(card, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${draft.name.replace(/\s+/g, '_') || 'character'}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    }
  }

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-64 border-r border-gray-800 bg-gray-900 flex flex-col">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <Link to="/" className="text-xs text-gray-400 hover:text-gray-200">← Menu</Link>
          <h2 className="text-sm font-semibold text-gray-200">Characters</h2>
        </div>
        <div className="p-3 space-y-2 border-b border-gray-800">
          <button
            onClick={startNew}
            className="w-full bg-blue-600 hover:bg-blue-500 text-white text-sm rounded py-1.5"
          >
            + New
          </button>
          <button
            onClick={() => importInputRef.current?.click()}
            className="w-full border border-gray-700 hover:border-gray-500 text-gray-300 text-sm rounded py-1.5"
          >
            Import (SillyTavern JSON)
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) handleImport(f)
              if (e.target) e.target.value = ''
            }}
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {list.map(c => (
            <button
              key={c.id}
              onClick={() => loadCharacter(c.id)}
              className={`w-full text-left px-4 py-2 text-sm border-b border-gray-800 hover:bg-gray-800 ${
                selectedId === c.id ? 'bg-gray-800' : ''
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="flex-1 text-gray-200 truncate">{c.name || '(unnamed)'}</span>
                {c.is_dm && (
                  <span className="text-[10px] uppercase bg-indigo-900 text-indigo-200 px-1.5 rounded">
                    DM
                  </span>
                )}
              </div>
              {c.description_summary && (
                <div className="text-[11px] text-gray-500 truncate mt-0.5">{c.description_summary}</div>
              )}
            </button>
          ))}
        </div>
      </aside>

      {/* Main area */}
      <main className="flex-1 overflow-y-auto">
        {!draft && (
          <div className="h-full flex items-center justify-center text-gray-500 text-sm">
            Select a character to edit, or create a new one.
          </div>
        )}
        {draft && (
          <div className="max-w-3xl mx-auto p-6 space-y-5">
            {error && (
              <div className="bg-red-950 border border-red-800 text-red-200 text-sm rounded px-3 py-2">
                {error}
              </div>
            )}
            {saveStatus && (
              <div className="text-xs text-emerald-400">{saveStatus}</div>
            )}
            <div className="flex gap-4">
              <div className="flex-shrink-0">
                <div className="w-24 h-24 bg-gray-800 rounded-full overflow-hidden border border-gray-700 flex items-center justify-center">
                  {draft.avatar_path ? (
                    <img
                      src={avatarUrl(draft.avatar_path) || ''}
                      alt={draft.name}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <span className="text-gray-600 text-xl">?</span>
                  )}
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="hidden"
                  onChange={e => {
                    const f = e.target.files?.[0]
                    if (f) uploadAvatar(f)
                    if (e.target) e.target.value = ''
                  }}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="mt-2 text-xs text-blue-300 hover:text-blue-200 w-24"
                >
                  Upload avatar
                </button>
              </div>
              <div className="flex-1 space-y-3">
                <div>
                  <label className="text-xs uppercase tracking-wide text-gray-400">Name</label>
                  <input
                    type="text"
                    value={draft.name}
                    onChange={e => setDraft({ ...draft, name: e.target.value })}
                    className="w-full bg-gray-800 text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600"
                  />
                </div>
                <label className="inline-flex items-center gap-2 text-sm text-gray-300">
                  <input
                    type="checkbox"
                    checked={draft.is_dm}
                    onChange={e => setDraft({ ...draft, is_dm: e.target.checked })}
                  />
                  This is the Dungeon Master / narrator
                </label>
              </div>
            </div>

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400">Description</label>
              <textarea
                value={draft.description}
                onChange={e => setDraft({ ...draft, description: e.target.value })}
                rows={8}
                placeholder="Personality, voice, behaviors. Use {{user}} and {{char}} where appropriate."
                className="w-full bg-gray-800 text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y leading-relaxed"
              />
            </div>

            <SummaryField
              label="Director hint (one-sentence summary)"
              value={draft.description_summary}
              onChange={v => setDraft({ ...draft, description_summary: v })}
              onRegenerate={regenSummary}
              edited={summaryEditedRef.current}
              helper="Used by the Director AI in-game to decide when this persona speaks. Auto-generated on save, but you can edit it directly."
            />

            <div>
              <label className="text-xs uppercase tracking-wide text-gray-400 block mb-2">
                Response examples
              </label>
              <ResponseExamplePairs
                pairs={draft.response_examples}
                onChange={pairs => setDraft({ ...draft, response_examples: pairs })}
              />
            </div>

            <div className="flex gap-2 pt-2 border-t border-gray-800">
              <button
                onClick={save}
                disabled={busy}
                className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm px-4 py-2 rounded"
              >
                {busy ? 'Saving…' : 'Save'}
              </button>
              {draft.id && (
                <>
                  <button
                    onClick={exportCharacter}
                    className="text-sm border border-gray-700 hover:border-gray-500 text-gray-200 px-3 py-2 rounded"
                  >
                    Export JSON
                  </button>
                  <button
                    onClick={deleteCharacter}
                    className="ml-auto text-sm border border-gray-700 hover:border-red-500 hover:text-red-300 text-gray-400 px-3 py-2 rounded"
                  >
                    Delete
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
