import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { avatarUrl, charactersApi, savesApi } from '../api/client'
import MarkdownMessage from '../components/MarkdownMessage'
import { useStream } from '../hooks/useStream'
import type {
  BeatTransitionEvent,
  Character,
  DirectorEvent,
  Message,
  MessageCompleteEvent,
  OptionsContextEvent,
  OptionsEvent,
  Save,
  Scenario,
  ValidationFailedEvent,
} from '../types'
import { scenariosApi } from '../api/client'

function pickColor(charId: string | null, role: string): string {
  if (role === 'dm') return 'bg-indigo-700'
  if (role === 'user') return 'bg-blue-700'
  // Hash the character id into a color bucket
  if (!charId) return 'bg-gray-600'
  let h = 0
  for (const ch of charId) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  const colors = ['bg-amber-700', 'bg-emerald-700', 'bg-rose-700', 'bg-purple-700', 'bg-cyan-700']
  return colors[h % colors.length]
}

export default function Game() {
  const { saveId = '' } = useParams<{ saveId: string }>()
  const navigate = useNavigate()
  const { status, startStream } = useStream()
  const isStreaming = status === 'streaming'

  const [save, setSave] = useState<Save | null>(null)
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [characterIndex, setCharacterIndex] = useState<Record<string, Character>>({})
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [options, setOptions] = useState<string[] | null>(null)
  const [optionsContext, setOptionsContext] = useState<string | null>(null)
  const [contextExpanded, setContextExpanded] = useState(false)
  const [currentBeatName, setCurrentBeatName] = useState<string | null>(null)
  const [sandboxMode, setSandboxMode] = useState(false)
  const [showBeatMenu, setShowBeatMenu] = useState(false)
  const [showEndingModal, setShowEndingModal] = useState(false)
  const [toast, setToast] = useState<{ text: string; type: 'error' | 'warning' } | null>(null)
  const [warnings, setWarnings] = useState<Record<string, string>>({})
  const [isRegenerating, setIsRegenerating] = useState(false)
  const [lastAiMsgId, setLastAiMsgId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editContent, setEditContent] = useState('')
  const [loadError, setLoadError] = useState(false)

  const placeholdersRef = useRef<Record<string, string>>({})
  const dmNarratingRef = useRef(false)
  const dmDoneRef = useRef(false)
  const pendingWarningRef = useRef<string | null>(null)
  const lastUserTextRef = useRef('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const showToast = useCallback((text: string, type: 'error' | 'warning' = 'error') => {
    setToast({ text, type })
    setTimeout(() => setToast(null), 4000)
  }, [])

  useEffect(() => {
    if (!saveId) return
    let cancelled = false
    ;(async () => {
      try {
        const s = await savesApi.get(saveId)
        if (cancelled) return
        setSave(s)
        setMessages(s.messages)
        setSandboxMode(s.sandbox_mode)
        const sc = await scenariosApi.get(s.scenario_id)
        if (cancelled) return
        setScenario(sc)
        if (s.current_beat_id) {
          const beat = sc.beats.find(b => b.id === s.current_beat_id)
          setCurrentBeatName(beat?.name ?? null)
        }
        // Pre-load character details (names + avatars)
        const chars = await Promise.all(s.active_character_ids.map(id => charactersApi.get(id).catch(() => null)))
        if (cancelled) return
        const idx: Record<string, Character> = {}
        for (const c of chars) if (c) idx[c.id] = c
        setCharacterIndex(idx)

        // If the chat has no user messages yet, ask the backend for opening options.
        if (!s.messages.some(m => m.role === 'user')) {
          try {
            const r = await savesApi.seedOptions(s.id)
            if (!cancelled) {
              setOptions(r.options)
              if (r.context) setOptionsContext(r.context)
            }
          } catch {
            /* ignore */
          }
        }
      } catch {
        if (!cancelled) setLoadError(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [saveId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function getSenderName(charId: string | null, role: string): string {
    if (role === 'user') return save?.user_name || 'Player'
    if (role === 'dm') {
      if (charId && characterIndex[charId]) return characterIndex[charId].name
      // Find the DM in the active roster
      for (const c of Object.values(characterIndex)) if (c.is_dm) return c.name
      return 'Narrator'
    }
    if (charId && characterIndex[charId]) return characterIndex[charId].name
    return 'Character'
  }

  const send = useCallback((overrideText?: string) => {
    if (!save) return
    const text = (overrideText ?? input).trim()
    if (!text || isStreaming) return
    if (overrideText === undefined) setInput('')
    lastUserTextRef.current = text
    setOptions(null)
    setOptionsContext(null)
    setContextExpanded(false)
    setIsRegenerating(false)

    placeholdersRef.current = {}
    dmNarratingRef.current = false
    dmDoneRef.current = false
    pendingWarningRef.current = null

    const now = new Date().toISOString()
    const userMsg: Message = {
      id: `local-user-${Date.now()}`,
      role: 'user',
      character_id: null,
      content: text,
      timestamp: now,
      is_dm_only: false,
      beat_id_at_time: save.current_beat_id,
    }
    setMessages(prev => [...prev, userMsg])

    startStream(
      '/api/chat/turn',
      { user_message: text, save_id: save.id },
      {
        onToken: (token, characterId) => {
          const charId = characterId ?? 'unknown'
          const isDm = dmNarratingRef.current && !dmDoneRef.current
          const role: 'character' | 'dm' = isDm ? 'dm' : 'character'

          const existingId = placeholdersRef.current[charId]
          if (existingId && existingId.startsWith('stream-')) {
            setMessages(prev => prev.map(m => (m.id === existingId ? { ...m, content: m.content + token } : m)))
          } else {
            const id = `stream-${charId}-${Date.now()}`
            placeholdersRef.current[charId] = id
            const placeholder: Message = {
              id,
              role,
              character_id: charId,
              content: token,
              timestamp: new Date().toISOString(),
              is_dm_only: isDm,
              beat_id_at_time: null,
            }
            setMessages(prev => [...prev, placeholder])
            setLastAiMsgId(id)
          }
          setIsRegenerating(false)
        },
        onDone: () => { /* options arrive separately */ },
        onError: (reason) => {
          const localIds = new Set([userMsg.id, ...Object.values(placeholdersRef.current)])
          setMessages(prev => prev.filter(m => !localIds.has(m.id)))
          placeholdersRef.current = {}
          const label =
            reason === 'ollama_unreachable' ? 'Ollama is unreachable — is it running?' :
            reason === 'ollama_timeout' ? 'Ollama timed out. Try again.' :
            'Connection error. Try again.'
          showToast(label, 'error')
        },
        onDirector: (event: DirectorEvent) => {
          dmNarratingRef.current = event.dm_narrating
          dmDoneRef.current = false
        },
        onBeatTransition: (event: BeatTransitionEvent) => {
          setCurrentBeatName(event.new_beat_name)
          setSave(s => (s ? { ...s, current_beat_id: event.new_beat_id } : s))
        },
        onEndingReached: () => {
          setSandboxMode(true)
          setShowEndingModal(true)
        },
        onMessageComplete: (event: MessageCompleteEvent) => {
          const charId = event.character_id
          const placeholder = placeholdersRef.current[charId]
          if (placeholder) {
            setMessages(prev => prev.map(m => (m.id === placeholder ? { ...m, id: event.message_id } : m)))
            placeholdersRef.current[charId] = event.message_id
            setLastAiMsgId(event.message_id)
          }
          if (pendingWarningRef.current !== null) {
            const reason = pendingWarningRef.current
            setWarnings(prev => ({ ...prev, [event.message_id]: reason }))
            pendingWarningRef.current = null
          }
          if (dmNarratingRef.current && !dmDoneRef.current) dmDoneRef.current = true
        },
        onOptionsContext: (event: OptionsContextEvent) => setOptionsContext(event.context),
        onOptions: (event: OptionsEvent) => setOptions([...event.options]),
        onRegenerate: (event) => {
          setIsRegenerating(true)
          const charId = event.character_id
          if (charId) {
            const placeholderId = placeholdersRef.current[charId]
            if (placeholderId && placeholderId.startsWith('stream-')) {
              setMessages(prev => prev.filter(m => m.id !== placeholderId))
              delete placeholdersRef.current[charId]
            }
          }
        },
        onValidationWarning: (event) => {
          pendingWarningRef.current = event.reason
        },
        onValidationFailed: (event: ValidationFailedEvent) => {
          showToast(`Validation issue (${event.call}): ${event.reason}`, 'warning')
        },
        onNotice: (event) => {
          showToast(event.message, event.level === 'error' ? 'error' : 'warning')
        },
      },
    )
  }, [save, input, isStreaming, startStream, showToast])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  async function advanceBeat(nextBeatId: string, wipeContext: boolean) {
    if (!save) return
    if (wipeContext && !confirm('Wipe the current chat history?')) return
    try {
      const updated = await savesApi.advanceBeat(save.id, { next_beat_id: nextBeatId, wipe_context: wipeContext })
      setSave(updated)
      setMessages(updated.messages)
      setSandboxMode(updated.sandbox_mode)
      const beat = scenario?.beats.find(b => b.id === updated.current_beat_id)
      setCurrentBeatName(beat?.name ?? null)
      setShowBeatMenu(false)
      // Refresh options
      const r = await savesApi.seedOptions(updated.id)
      setOptions(r.options)
      setOptionsContext(r.context || null)
      setContextExpanded(false)
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Beat advance failed')
    }
  }

  async function toggleSandbox() {
    if (!save) return
    try {
      const updated = await savesApi.setSandboxMode(save.id, !sandboxMode)
      setSandboxMode(updated.sandbox_mode)
      setSave(updated)
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Toggle failed')
    }
  }

  async function setMaxTokens(value: number) {
    if (!save) return
    try {
      const updated = await savesApi.update(save.id, { max_context_tokens: value })
      setSave(updated)
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Update failed')
    }
  }

  function startEdit(msg: Message) {
    if (isStreaming) return
    setEditingId(msg.id)
    setEditContent(msg.content)
  }

  function saveEdit(msgId: string) {
    setMessages(prev => prev.map(m => (m.id === msgId ? { ...m, content: editContent } : m)))
    setEditingId(null)
  }

  if (!saveId) return <div className="p-8 text-gray-400">No save id.</div>

  const forwardBeats = scenario?.beats
    .filter(b => {
      if (!save?.current_beat_id) return true
      const cur = scenario.beats.find(x => x.id === save.current_beat_id)
      return cur ? b.order >= cur.order : true
    })
    .sort((a, b) => a.order - b.order) ?? []

  return (
    <div className="flex flex-col h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <div className="border-b border-gray-800 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/" className="text-xs text-gray-500 hover:text-gray-200">← Menu</Link>
          <div>
            <h1 className="text-lg font-semibold text-gray-200">{save?.name ?? '…'}</h1>
            {currentBeatName ? (
              <p className="text-xs text-indigo-400">Scene: {currentBeatName}</p>
            ) : sandboxMode ? (
              <p className="text-xs text-amber-400">Sandbox mode</p>
            ) : null}
          </div>
        </div>
        <div className="relative">
          <button
            onClick={() => setShowBeatMenu(o => !o)}
            className="text-xs text-gray-400 hover:text-gray-200 border border-gray-700 hover:border-gray-500 rounded px-3 py-1.5"
          >
            ⚙ Beat Control
          </button>
          {showBeatMenu && (
            <div className="absolute right-0 top-full mt-2 w-72 bg-gray-900 border border-gray-700 rounded shadow-lg z-30 p-3 space-y-3">
              <div>
                <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Manual advance</div>
                {forwardBeats.length === 0 && (
                  <div className="text-xs text-gray-500">No beats in this scenario.</div>
                )}
                {forwardBeats.map(b => (
                  <div key={b.id} className="flex items-center gap-2 text-xs py-1">
                    <span className="flex-1 truncate text-gray-300">
                      {b.name} {b.id === save?.current_beat_id && <span className="text-indigo-400">(current)</span>}
                    </span>
                    <button
                      onClick={() => advanceBeat(b.id, false)}
                      className="text-blue-300 hover:text-blue-200 px-1.5"
                    >
                      Advance
                    </button>
                    <button
                      onClick={() => advanceBeat(b.id, true)}
                      className="text-red-400 hover:text-red-300 px-1.5"
                      title="Wipe chat history and jump"
                    >
                      + Wipe
                    </button>
                  </div>
                ))}
              </div>
              <div className="border-t border-gray-800 pt-2">
                <label className="flex items-center gap-2 text-xs text-gray-300">
                  <input
                    type="checkbox"
                    checked={sandboxMode}
                    onChange={toggleSandbox}
                  />
                  Sandbox mode (Director ignores beats)
                </label>
              </div>
              <div className="border-t border-gray-800 pt-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                  Max context tokens
                </div>
                <input
                  type="range"
                  min={2048}
                  max={32_768}
                  step={1024}
                  value={save?.max_context_tokens ?? 8192}
                  onChange={e => setMaxTokens(Number(e.target.value))}
                  className="w-full"
                />
                <div className="text-xs text-gray-400 text-right">{save?.max_context_tokens}</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {loadError && (
          <div className="text-red-400 text-sm text-center py-4">
            Failed to load this save. Is the backend running?
          </div>
        )}
        {messages.map(msg => {
          const isUser = msg.role === 'user'
          const isDm = msg.role === 'dm'
          const isEditing = editingId === msg.id
          const warning = warnings[msg.id]
          const senderName = getSenderName(msg.character_id, msg.role)
          const character = msg.character_id ? characterIndex[msg.character_id] : null
          const avatarSrc = character?.avatar_path ? avatarUrl(character.avatar_path) : null

          return (
            <div key={msg.id} className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
              {!isUser && (
                <div className="flex items-center gap-2 mb-1 px-1">
                  <div
                    className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0 overflow-hidden ${
                      avatarSrc ? '' : pickColor(msg.character_id, msg.role)
                    }`}
                  >
                    {avatarSrc ? (
                      <img src={avatarSrc} alt="" className="w-full h-full object-cover" />
                    ) : (
                      senderName[0]
                    )}
                  </div>
                  <span className="text-xs text-gray-500">{senderName}</span>
                </div>
              )}
              {isEditing ? (
                <div className="w-full max-w-2xl">
                  <textarea
                    value={editContent}
                    onChange={e => setEditContent(e.target.value)}
                    rows={3}
                    className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  <div className="flex gap-2 mt-1">
                    <button
                      onClick={() => saveEdit(msg.id)}
                      className="text-xs bg-blue-600 hover:bg-blue-500 text-white px-3 py-1 rounded"
                    >
                      Save
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="text-xs bg-gray-700 hover:bg-gray-600 text-white px-3 py-1 rounded"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div
                  onClick={() => startEdit(msg)}
                  title="Click to edit"
                  className={[
                    'rounded-lg px-4 py-2 max-w-2xl text-sm leading-relaxed cursor-pointer',
                    isUser
                      ? 'bg-blue-900 text-blue-50'
                      : isDm
                        ? 'bg-indigo-950 text-indigo-100 italic border-l-2 border-indigo-500'
                        : 'bg-gray-800 text-gray-100',
                    msg.content === '' ? 'animate-pulse min-w-8' : '',
                  ].filter(Boolean).join(' ')}
                >
                  {isUser
                    ? (msg.content || (isRegenerating ? 'Regenerating…' : '…'))
                    : <MarkdownMessage content={msg.content || (isRegenerating ? 'Regenerating…' : '…')} />
                  }
                </div>
              )}
              {warning && (
                <div className="mt-1 text-[11px] text-amber-400">⚠ {warning}</div>
              )}
              {!isUser && msg.id === lastAiMsgId && !isStreaming && !isEditing && (
                <button
                  onClick={() => send(lastUserTextRef.current)}
                  className="text-xs text-gray-600 hover:text-gray-400 mt-1 px-1"
                >
                  ↺ Regenerate
                </button>
              )}
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Options */}
      {options && !isStreaming && (
        <div className="border-t border-gray-800">
          {optionsContext && (
            <div className="px-4 pt-2">
              <button
                onClick={() => setContextExpanded(o => !o)}
                className="text-xs text-gray-600 hover:text-gray-400 flex items-center gap-1"
              >
                <span>{contextExpanded ? '▾' : '▸'}</span>
                <span>Director's scene brief</span>
              </button>
              {contextExpanded && (
                <div className="mt-2 mb-1 text-xs text-gray-400 bg-gray-900 border border-gray-800 rounded px-3 py-2 whitespace-pre-wrap leading-relaxed max-h-52 overflow-y-auto">
                  {optionsContext}
                </div>
              )}
            </div>
          )}
          <div className="px-4 py-3 grid grid-cols-2 gap-2">
            {options.map((opt, i) => (
              <button
                key={i}
                onClick={() => send(opt)}
                className="bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm rounded px-3 py-2 text-left"
              >
                {opt}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-gray-800 px-4 py-3 flex gap-2 items-end">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isStreaming}
          placeholder={isStreaming ? 'Characters are responding…' : 'Type a message… (Enter to send, Shift+Enter for newline)'}
          rows={2}
          className="flex-1 bg-gray-800 text-gray-100 placeholder-gray-500 rounded px-3 py-2 resize-none text-sm focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:opacity-50"
        />
        <button
          onClick={() => send()}
          disabled={isStreaming || !input.trim()}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded px-4 py-2 text-sm font-medium"
        >
          Send
        </button>
      </div>

      {/* Toast */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 px-4 py-3 rounded shadow-lg text-sm max-w-xs z-50 text-white ${
            toast.type === 'error' ? 'bg-red-800' : 'bg-amber-700'
          }`}
        >
          {toast.text}
        </div>
      )}

      {/* Ending modal */}
      {showEndingModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-40 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded p-6 max-w-md text-center space-y-4">
            <h3 className="text-lg font-semibold text-indigo-300">The story has reached its conclusion.</h3>
            <p className="text-sm text-gray-400">
              You can keep playing in sandbox mode (no further beats will fire), or return to the
              main menu.
            </p>
            <div className="flex justify-center gap-3">
              <button
                onClick={() => setShowEndingModal(false)}
                className="bg-blue-600 hover:bg-blue-500 text-white text-sm px-4 py-2 rounded"
              >
                Continue in sandbox
              </button>
              <button
                onClick={() => navigate('/')}
                className="border border-gray-700 hover:border-gray-500 text-gray-300 text-sm px-4 py-2 rounded"
              >
                Return to menu
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
