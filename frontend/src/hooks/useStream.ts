import { useState, useCallback, useRef } from 'react'
import type {
  BeatTransitionEvent,
  DirectorEvent,
  ErrorEvent,
  MessageCompleteEvent,
  NoticeEvent,
  OptionsContextEvent,
  OptionsEvent,
  RegenerateEvent,
  TokenEvent,
  ValidationFailedEvent,
  ValidationWarningEvent,
} from '../types'

export type StreamStatus = 'idle' | 'streaming' | 'done' | 'error'

interface StreamCallbacks {
  onToken: (token: string, characterId?: string) => void
  onDone: () => void
  onError: (reason: string) => void
  onDirector?: (event: DirectorEvent) => void
  onBeatTransition?: (event: BeatTransitionEvent) => void
  onMessageComplete?: (event: MessageCompleteEvent) => void
  onOptions?: (event: OptionsEvent) => void
  onOptionsContext?: (event: OptionsContextEvent) => void
  onRegenerate?: (event: RegenerateEvent) => void
  onValidationWarning?: (event: ValidationWarningEvent) => void
  onValidationFailed?: (event: ValidationFailedEvent) => void
  onEndingReached?: () => void
  onNotice?: (event: NoticeEvent) => void
}

export function useStream() {
  const [status, setStatus] = useState<StreamStatus>('idle')
  const abortRef = useRef<AbortController | null>(null)

  const startStream = useCallback(async (
    url: string,
    body: Record<string, unknown>,
    callbacks: StreamCallbacks,
  ) => {
    // Cancel any in-flight stream
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setStatus('streaming')

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        throw new Error(`HTTP ${res.status}`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })

        // SSE events are delimited by blank lines (\n\n)
        const parts = buf.split('\n\n')
        buf = parts.pop() ?? ''

        for (const part of parts) {
          if (!part.trim()) continue
          const lines = part.split('\n')
          let eventType = 'message'
          let data = ''
          for (const line of lines) {
            if (line.startsWith('event: ')) eventType = line.slice(7).trim()
            else if (line.startsWith('data: ')) data = line.slice(6)
          }

          if (eventType === 'token') {
            try {
              // Stage 2: token data is JSON {character_id, text}
              const parsed = JSON.parse(data) as TokenEvent
              console.log('[sse:token]', parsed.character_id, parsed.text.slice(0, 40))
              callbacks.onToken(parsed.text, parsed.character_id)
            } catch {
              // Stage 1 fallback: raw text
              callbacks.onToken(data)
            }
          } else if (eventType === 'done') {
            console.log('[sse:done]')
            setStatus('done')
            callbacks.onDone()
          } else if (eventType === 'error') {
            let reason = 'unknown_error'
            try {
              const parsed = JSON.parse(data) as ErrorEvent
              reason = parsed.reason
            } catch { /* use default */ }
            console.log('[sse:error]', reason)
            setStatus('error')
            callbacks.onError(reason)
          } else if (eventType === 'director' && callbacks.onDirector) {
            try {
              const parsed = JSON.parse(data) as DirectorEvent
              console.log('[sse:director]', parsed)
              callbacks.onDirector(parsed)
            } catch { /* ignore parse error */ }
          } else if (eventType === 'beat_transition' && callbacks.onBeatTransition) {
            try {
              const parsed = JSON.parse(data) as BeatTransitionEvent
              console.log('[sse:beat_transition]', parsed)
              callbacks.onBeatTransition(parsed)
            } catch { /* ignore */ }
          } else if (eventType === 'message_complete' && callbacks.onMessageComplete) {
            try {
              const parsed = JSON.parse(data) as MessageCompleteEvent
              console.log('[sse:message_complete]', parsed)
              callbacks.onMessageComplete(parsed)
            } catch { /* ignore */ }
          } else if (eventType === 'options_context' && callbacks.onOptionsContext) {
            try {
              const parsed = JSON.parse(data) as OptionsContextEvent
              console.log('[sse:options_context]', parsed.context.slice(0, 80))
              callbacks.onOptionsContext(parsed)
            } catch { /* ignore */ }
          } else if (eventType === 'options' && callbacks.onOptions) {
            try {
              const parsed = JSON.parse(data) as OptionsEvent
              console.log('[sse:options]', parsed)
              callbacks.onOptions(parsed)
            } catch { /* ignore */ }
          } else if (eventType === 'regenerate' && callbacks.onRegenerate) {
            try {
              const parsed = JSON.parse(data) as RegenerateEvent
              console.log('[sse:regenerate]', parsed)
              callbacks.onRegenerate(parsed)
            } catch { /* ignore */ }
          } else if (eventType === 'validation_warning' && callbacks.onValidationWarning) {
            try {
              const parsed = JSON.parse(data) as ValidationWarningEvent
              console.log('[sse:validation_warning]', parsed)
              callbacks.onValidationWarning(parsed)
            } catch { /* ignore */ }
          } else if (eventType === 'ending_reached' && callbacks.onEndingReached) {
            console.log('[sse:ending_reached]')
            callbacks.onEndingReached()
          } else if (eventType === 'validation_failed' && callbacks.onValidationFailed) {
            try {
              const parsed = JSON.parse(data) as ValidationFailedEvent
              console.log('[sse:validation_failed]', parsed)
              callbacks.onValidationFailed(parsed)
            } catch { /* ignore */ }
          } else if (eventType === 'notice' && callbacks.onNotice) {
            try {
              const parsed = JSON.parse(data) as NoticeEvent
              console.log('[sse:notice]', parsed)
              callbacks.onNotice(parsed)
            } catch { /* ignore */ }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      setStatus('error')
      callbacks.onError('network_error')
    }
  }, [])

  const abort = useCallback(() => {
    abortRef.current?.abort()
    setStatus('idle')
  }, [])

  return { status, startStream, abort }
}
