import { useState } from 'react'
import type { Character, Save } from '../types'

interface SettingsSidebarProps {
  save: Save | null
  characterIndex: Record<string, Character>
  onContextLengthChange: (value: number) => void
  onSandboxChange: () => void
  responseReserve: number
  onResponseReserveChange: (value: number) => void
  maxResponseTokens: number | null
  onMaxResponseTokensChange: (value: number | null) => void
  favoredIds: string[]
  onFavorChange: (id: string, checked: boolean) => void
  persistFavored: boolean
  onPersistFavoredChange: (value: boolean) => void
  isStreaming: boolean
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">{children}</div>
  )
}

export default function SettingsSidebar({
  save,
  characterIndex,
  onContextLengthChange,
  onSandboxChange,
  responseReserve,
  onResponseReserveChange,
  maxResponseTokens,
  onMaxResponseTokensChange,
  favoredIds,
  onFavorChange,
  persistFavored,
  onPersistFavoredChange,
  isStreaming,
}: SettingsSidebarProps) {
  const [noLimit, setNoLimit] = useState(maxResponseTokens === null)
  const [maxTokensInput, setMaxTokensInput] = useState(maxResponseTokens ?? 2048)
  const [contextLengthDisplay, setContextLengthDisplay] = useState(save?.max_context_tokens ?? 8192)

  const disabled = isStreaming

  function handleNoLimitChange(checked: boolean) {
    setNoLimit(checked)
    if (checked) {
      onMaxResponseTokensChange(null)
    } else {
      onMaxResponseTokensChange(maxTokensInput)
    }
  }

  function handleMaxTokensInputChange(value: number) {
    setMaxTokensInput(value)
    if (!noLimit) {
      onMaxResponseTokensChange(value)
    }
  }

  return (
    <div className="w-72 h-full bg-gray-900 border-r border-gray-800 overflow-y-auto flex flex-col">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200">Settings</h2>
      </div>

      <div className="px-4 py-4 space-y-6 flex-1">

        {/* Context Length */}
        <div>
          <SectionHeader>Context Length</SectionHeader>
          <input
            type="range"
            min={512}
            max={128000}
            step={512}
            value={contextLengthDisplay}
            onChange={e => setContextLengthDisplay(Number(e.target.value))}
            onPointerUp={e => onContextLengthChange(Number((e.target as HTMLInputElement).value))}
            disabled={disabled}
            className="w-full disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <div className="text-xs text-gray-400 text-right mt-1">
            {contextLengthDisplay.toLocaleString()} tokens
          </div>
        </div>

        {/* Response Reserve */}
        <div>
          <SectionHeader>Response Reserve</SectionHeader>
          <input
            type="range"
            min={128}
            max={4096}
            step={128}
            value={responseReserve}
            onChange={e => onResponseReserveChange(Number(e.target.value))}
            disabled={disabled}
            className="w-full disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <div className="text-xs text-gray-400 text-right mt-1">
            {responseReserve.toLocaleString()} tokens
          </div>
        </div>

        {/* Max Response Tokens */}
        <div>
          <SectionHeader>Max Response Tokens</SectionHeader>
          <label className="flex items-center gap-2 text-xs text-gray-300 mb-2">
            <input
              type="checkbox"
              checked={noLimit}
              onChange={e => handleNoLimitChange(e.target.checked)}
              disabled={disabled}
              className="disabled:opacity-50 disabled:cursor-not-allowed"
            />
            No limit (model default)
          </label>
          {!noLimit && (
            <input
              type="number"
              min={64}
              max={8192}
              step={64}
              value={maxTokensInput}
              onChange={e => handleMaxTokensInputChange(Number(e.target.value))}
              disabled={disabled}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
            />
          )}
        </div>

        {/* Sandbox Mode */}
        <div>
          <SectionHeader>Sandbox Mode</SectionHeader>
          <label className="flex items-center gap-2 text-xs text-gray-300">
            <input
              type="checkbox"
              checked={save?.sandbox_mode ?? false}
              onChange={onSandboxChange}
              disabled={disabled}
              className="disabled:opacity-50 disabled:cursor-not-allowed"
            />
            Director ignores beat transitions
          </label>
        </div>

        {/* Favor Response */}
        <div>
          <SectionHeader>Favor Response</SectionHeader>
          {save && save.active_character_ids.length > 0 ? (
            <div className="space-y-1.5">
              {save.active_character_ids.map(cid => {
                const char = characterIndex[cid]
                const name = char?.name ?? cid
                const isDm = char?.is_dm ?? false
                return (
                  <label key={cid} className="flex items-center gap-2 text-xs text-gray-300">
                    <input
                      type="checkbox"
                      checked={favoredIds.includes(cid)}
                      onChange={e => onFavorChange(cid, e.target.checked)}
                      disabled={disabled}
                      className="disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                    <span>
                      {name}
                      {isDm && <span className="ml-1 text-indigo-400">(DM)</span>}
                    </span>
                  </label>
                )
              })}
            </div>
          ) : (
            <div className="text-xs text-gray-600">No active characters.</div>
          )}

          <div className="mt-3 pt-2 border-t border-gray-800">
            <label className="flex items-center gap-2 text-xs text-gray-400">
              <input
                type="checkbox"
                checked={persistFavored}
                onChange={e => onPersistFavoredChange(e.target.checked)}
                disabled={disabled}
                className="disabled:opacity-50 disabled:cursor-not-allowed"
              />
              {persistFavored ? 'Persisting until unchecked' : 'Clears after each turn'}
            </label>
          </div>
        </div>

      </div>
    </div>
  )
}
