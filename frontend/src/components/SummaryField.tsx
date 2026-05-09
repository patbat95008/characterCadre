import { useState } from 'react'

interface Props {
  label: string
  value: string
  helper?: string
  onChange: (next: string) => void
  onRegenerate: () => Promise<string>
  edited?: boolean
  disabled?: boolean
}

export default function SummaryField({
  label,
  value,
  helper,
  onChange,
  onRegenerate,
  edited,
  disabled,
}: Props) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function handleRegen() {
    setBusy(true)
    setErr(null)
    try {
      const next = await onRegenerate()
      onChange(next)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <label className="text-xs uppercase tracking-wide text-gray-400">{label}</label>
        <div className="flex items-center gap-2">
          {edited && (
            <span className="text-[10px] uppercase bg-amber-900 text-amber-200 px-1.5 py-0.5 rounded">
              edited
            </span>
          )}
          <button
            type="button"
            onClick={handleRegen}
            disabled={busy || disabled}
            className="text-xs px-2 py-0.5 rounded border border-gray-700 hover:border-blue-500 hover:text-blue-300 text-gray-400 disabled:opacity-40"
          >
            {busy ? 'Generating…' : '↻ Regenerate'}
          </button>
        </div>
      </div>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        className="w-full bg-gray-800 text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:opacity-50"
      />
      {helper && !err && <p className="text-[11px] text-gray-500">{helper}</p>}
      {err && <p className="text-[11px] text-red-400">{err}</p>}
    </div>
  )
}
