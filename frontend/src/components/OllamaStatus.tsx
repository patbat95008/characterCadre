import { useEffect, useState } from 'react'

type Status = 'unknown' | 'ok' | 'model_not_loaded' | 'unreachable'

const LABELS: Record<Status, string> = {
  unknown: 'Checking Ollama…',
  ok: 'Ollama ready',
  model_not_loaded: 'Model loading…',
  unreachable: 'Ollama unreachable',
}

export default function OllamaStatus() {
  const [status, setStatus] = useState<Status>('unknown')

  useEffect(() => {
    async function check() {
      try {
        const res = await fetch('/api/health/ollama')
        if (res.ok) {
          setStatus('ok')
        } else {
          const body = await res.json().catch(() => ({}))
          setStatus(body.status === 'model_not_loaded' ? 'model_not_loaded' : 'unreachable')
        }
      } catch {
        setStatus('unreachable')
      }
    }

    check()
    const id = setInterval(check, 5000)
    return () => clearInterval(id)
  }, [])

  const color =
    status === 'ok'
      ? 'bg-green-500'
      : status === 'unknown'
      ? 'bg-gray-500'
      : 'bg-red-500'

  return (
    <div className="fixed top-3 right-3 z-50 group">
      <div className={`w-3 h-3 rounded-full ${color} cursor-default`} />
      <div className="absolute right-0 top-5 hidden group-hover:block bg-gray-800 text-gray-100 text-xs rounded px-2 py-1 whitespace-nowrap shadow-lg">
        {LABELS[status]}
      </div>
    </div>
  )
}
