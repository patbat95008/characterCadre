import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  reset = () => {
    this.setState({ error: null })
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const isDev = import.meta.env.DEV

    return (
      <div className="min-h-screen flex items-center justify-center p-6 bg-gray-950 text-gray-100">
        <div className="max-w-lg w-full space-y-4 border border-red-800 bg-gray-900 rounded p-6">
          <h1 className="text-lg font-semibold text-red-300">Something broke.</h1>
          <p className="text-sm text-gray-400">
            The game UI hit an error and stopped rendering. Try reloading; if it persists,
            check the browser console.
          </p>
          {isDev && (
            <pre className="text-[11px] text-amber-300 bg-black/40 rounded p-3 overflow-auto max-h-64 whitespace-pre-wrap">
              {error.stack ?? error.message}
            </pre>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => window.location.reload()}
              className="bg-blue-600 hover:bg-blue-500 text-white text-sm px-4 py-2 rounded"
            >
              Reload
            </button>
            <button
              onClick={this.reset}
              className="border border-gray-700 hover:border-gray-500 text-gray-300 text-sm px-4 py-2 rounded"
            >
              Try to recover
            </button>
          </div>
        </div>
      </div>
    )
  }
}
