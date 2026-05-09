interface Props {
  values: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  rows?: number
}

export default function StringList({ values, onChange, placeholder, rows = 2 }: Props) {
  return (
    <div className="space-y-2">
      {values.map((v, idx) => (
        <div key={idx} className="flex gap-2 items-start">
          <textarea
            value={v}
            onChange={e =>
              onChange(values.map((x, i) => (i === idx ? e.target.value : x)))
            }
            rows={rows}
            placeholder={placeholder}
            className="flex-1 bg-gray-800 text-sm rounded px-2 py-1.5 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
          />
          <button
            type="button"
            onClick={() => onChange(values.filter((_, i) => i !== idx))}
            className="text-xs text-red-400 hover:text-red-300 px-1 mt-1"
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...values, ''])}
        className="text-xs text-blue-300 hover:text-blue-200 border border-blue-800 hover:border-blue-600 rounded px-3 py-1"
      >
        + Add
      </button>
    </div>
  )
}
