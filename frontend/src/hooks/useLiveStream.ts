import { useEffect, useRef, useState } from 'react'
import { api, openEventStream } from '../lib/api'
import type { LiveSnapshot, ServerEvent } from '../lib/api'
import { CLIENT_WATCHDOG_SECONDS, POLL_FALLBACK_SECONDS } from '../lib/realtimeConfig'

const EVENTS = ['incident.created', 'incident.reviewed', 'prediction.updated', 'context.changed', 'resync', 'heartbeat'] as const
export type LiveStreamState = 'connecting' | 'live' | 'reconnecting' | 'offline'

export function useLiveStream(onEvent: (event: ServerEvent) => void, onSnapshot: (snapshot: LiveSnapshot) => void) {
  const [state, setState] = useState<LiveStreamState>('connecting')
  const stateRef = useRef(state)
  stateRef.current = state
  const [lastEventAt, setLastEventAt] = useState<number | null>(null)
  const eventCallback = useRef(onEvent)
  const snapshotCallback = useRef(onSnapshot)
  useEffect(() => { eventCallback.current = onEvent }, [onEvent])
  useEffect(() => { snapshotCallback.current = onSnapshot }, [onSnapshot])

  useEffect(() => {
    let stopped = false
    let activeController: AbortController | null = null
    let lastEventId: number | null = null
    let retry = 0
    let watchdog: number | null = null
    let retryTimer: number | null = null
    let retryResolve: (() => void) | null = null
    let visibilityHandler: (() => void) | null = null
    let visibilityResolve: (() => void) | null = null
    const updateCursor = (id: number | null) => {
      if (id === null) return
      lastEventId = Math.max(lastEventId ?? 0, id)
    }
    const snapshot = async () => {
      try {
        const value = await api.liveSnapshot()
        if (stopped) return false
        updateCursor(value.version)
        snapshotCallback.current(value)
        return true
      } catch {
        return false
      }
    }
    const clearWatchdog = () => {
      if (watchdog !== null) window.clearTimeout(watchdog)
      watchdog = null
    }
    const armWatchdog = (controller: AbortController) => {
      clearWatchdog()
      watchdog = window.setTimeout(() => controller.abort(), CLIENT_WATCHDOG_SECONDS * 1000)
    }
    const waitUntilVisible = () => new Promise<void>((resolve) => {
      if (document.visibilityState === 'visible') return resolve()
      const visible = () => {
        if (document.visibilityState !== 'visible') return
        document.removeEventListener('visibilitychange', visible)
        visibilityHandler = null
        visibilityResolve = null
        resolve()
      }
      visibilityHandler = visible
      visibilityResolve = resolve
      document.addEventListener('visibilitychange', visible)
    })
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') activeController?.abort()
    }
    document.addEventListener('visibilitychange', onVisibility)

    const run = async () => {
      while (!stopped) {
        await waitUntilVisible()
        if (stopped) break
        if (!activeController) await snapshot()
        const controller = new AbortController()
        activeController = controller
        setState(retry === 0 ? 'connecting' : 'reconnecting')
        armWatchdog(controller)
        try {
          await openEventStream('/api/stream', (event: ServerEvent) => {
            updateCursor(event.id)
            setLastEventAt(Date.now())
            setState('live')
            retry = 0
            armWatchdog(controller)
            if (event.event === 'resync') {
              void snapshot().then(() => eventCallback.current(event))
            } else {
              eventCallback.current(event)
            }
          }, { signal: controller.signal, lastEventId, allowedEvents: EVENTS })
          if (!controller.signal.aborted) throw new Error('Stream closed')
        } catch {
          if (stopped) break
          if (document.visibilityState === 'hidden') continue
          setState('offline')
          retry += 1
          const base = Math.min(30_000, 1000 * (2 ** Math.min(retry - 1, 5)))
          const delay = base * (0.8 + Math.random() * 0.4)
          await new Promise<void>((resolve) => {
            retryResolve = resolve
            retryTimer = window.setTimeout(() => {
              retryTimer = null
              retryResolve = null
              resolve()
            }, delay)
          })
        } finally {
          clearWatchdog()
          if (activeController === controller) activeController = null
        }
      }
    }
    void run()

    const poll = window.setInterval(() => {
      if (!stopped && stateRef.current !== 'live' && document.visibilityState === 'visible') void snapshot()
    }, POLL_FALLBACK_SECONDS * 1000)
    return () => {
      stopped = true
      activeController?.abort()
      clearWatchdog()
      if (retryTimer !== null) window.clearTimeout(retryTimer)
      retryTimer = null
      retryResolve?.()
      retryResolve = null
      if (visibilityHandler) document.removeEventListener('visibilitychange', visibilityHandler)
      visibilityHandler = null
      visibilityResolve?.()
      visibilityResolve = null
      window.clearInterval(poll)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  // state intentionally is not a dependency: stateRef tracks it for the fallback poll.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { state, lastEventAt }
}
