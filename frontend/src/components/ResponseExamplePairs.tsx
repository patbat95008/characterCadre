interface Pair {
  user: string
  char: string
}

interface Props {
  pairs: Pair[]
  onChange: (pairs: Pair[]) => void
}

export default function ResponseExamplePairs({ pairs, onChange }: Props) {
  function update(idx: number, key: 'user' | 'char', value: string) {
    onChange(
      pairs.map((p, i) => (i === idx ? { ...p, [key]: value } : p)),
    )
  }

  function remove(idx: number) {
    onChange(pairs.filter((_, i) => i !== idx))
  }

  function add() {
    onChange([...pairs, { user: '', char: '' }])
  }

  return (
    <div className="space-y-3">
      {pairs.map((pair, idx) => (
        <div key={idx} className="border border-gray-800 rounded p-3 space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-[10px] uppercase tracking-wide text-gray-500">
              Example #{idx + 1}
            </span>
            <button
              type="button"
              onClick={() => remove(idx)}
              className="text-xs text-red-400 hover:text-red-300"
            >
              Remove
            </button>
          </div>
          <textarea
            value={pair.user}
            onChange={e => update(idx, 'user', e.target.value)}
            placeholder="What {{user}} says…"
            rows={2}
            className="w-full bg-gray-800 text-sm rounded px-2 py-1.5 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
          />
          <textarea
            value={pair.char}
            onChange={e => update(idx, 'char', e.target.value)}
            placeholder="How the character responds…"
            rows={3}
            className="w-full bg-gray-800 text-sm rounded px-2 py-1.5 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
          />
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="text-xs text-blue-300 hover:text-blue-200 border border-blue-800 hover:border-blue-600 rounded px-3 py-1"
      >
        + Add example
      </button>
    </div>
  )
}
