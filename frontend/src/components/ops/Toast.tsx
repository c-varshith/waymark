import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { FlaskConical, TriangleAlert, X } from 'lucide-react'

export interface ToastData {
  id: number
  message: ReactNode
  variant?: 'default' | 'alert'
  title?: string
  actionLabel?: string
  onAction?: () => void
  /** Alert only: live risk score, 0-100, shown as a badge. */
  score?: number
  /** Alert only: where the alert applies (street, locality or cell ID). */
  location?: string
  /** Alert only: true when the position that triggered it was a simulated test position. */
  simulated?: boolean
}

const DEFAULT_MS = 9000
const ALERT_MS = 15000

/** Dismissible notice at the bottom of the screen. Moves on by itself; hovering or focusing it holds it open. */
export function Toast({ toast, onClose }: { toast: ToastData | null; onClose: () => void }) {
  const isAlert = toast?.variant === 'alert'
  const reduceMotion = useReducedMotion()
  const [held, setHeld] = useState(false)
  const duration = isAlert ? ALERT_MS : DEFAULT_MS
  const toastId = toast?.id ?? null

  useEffect(() => {
    if (toastId === null || held) return
    const t = setTimeout(onClose, duration)
    return () => clearTimeout(t)
  }, [toastId, held, duration, onClose])

  useEffect(() => setHeld(false), [toastId])

  return (
    <div
      aria-live={isAlert ? 'assertive' : 'polite'}
      role={isAlert ? 'alert' : 'status'}
      className="pointer-events-none fixed inset-x-0 bottom-4 z-[2000] flex justify-center px-3 sm:px-4"
    >
      <AnimatePresence mode="wait">
        {toast && (
          <motion.div
            key={toast.id}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            onMouseEnter={() => setHeld(true)}
            onMouseLeave={() => setHeld(false)}
            onFocusCapture={() => setHeld(true)}
            onBlurCapture={() => setHeld(false)}
            className={`pointer-events-auto relative overflow-hidden rounded-xl bg-navy text-ivory shadow-card ${
              isAlert
                ? 'flex w-full max-w-xl items-start gap-3 border border-brick/70 p-4 sm:p-5'
                : 'flex max-w-xl items-center gap-3 px-4 py-3'
            }`}
          >
            {isAlert && (
              <span
                className="mt-0.5 flex size-10 shrink-0 items-center justify-center rounded-full bg-brick/20 text-[#F28A75]"
                aria-hidden="true"
              >
                <TriangleAlert size={22} strokeWidth={2.25} />
              </span>
            )}
            <div className={`min-w-0 flex-1 ${isAlert ? 'space-y-1.5' : ''}`}>
              {isAlert && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-bold text-[#F28A75]">Proximity alert</span>
                  {toast.simulated && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-brass px-2 py-0.5 text-xs font-bold text-navy">
                      <FlaskConical aria-hidden="true" size={11} />
                      Simulated location
                    </span>
                  )}
                </div>
              )}
              {isAlert && toast.title && (
                <div className="flex items-start justify-between gap-3">
                  <div className="text-base font-bold leading-snug sm:text-lg">{toast.title}</div>
                  {typeof toast.score === 'number' && (
                    <div
                      className="shrink-0 rounded-lg bg-brick px-2.5 py-1 text-center leading-none text-white"
                      aria-label={`Live risk score ${Math.round(toast.score)} out of 100`}
                    >
                      <div className="text-lg font-bold tabular-nums">{Math.round(toast.score)}</div>
                      <div className="mt-0.5 text-[10px] font-semibold text-white/80">of 100</div>
                    </div>
                  )}
                </div>
              )}
              {isAlert && toast.location && <div className="truncate text-xs text-white/70">{toast.location}</div>}
              <div className={isAlert ? 'text-sm leading-relaxed text-white/85' : 'text-sm'}>
                {isAlert && <span className="font-semibold text-white">Main factors: </span>}
                {toast.message}
              </div>
              {toast.actionLabel && (
                <button
                  type="button"
                  onClick={() => {
                    toast.onAction?.()
                    onClose()
                  }}
                  className="mt-2 rounded-md bg-brass px-3 py-1.5 text-sm font-semibold text-navy hover:brightness-95"
                >
                  {toast.actionLabel}
                </button>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label={isAlert ? 'Dismiss proximity alert' : 'Dismiss notice'}
              className="-mr-1 shrink-0 rounded-md p-2 text-white/70 hover:bg-white/10 hover:text-white"
            >
              <X aria-hidden="true" size={16} />
            </button>
            {!reduceMotion && !held && (
              <motion.span
                aria-hidden="true"
                key={`bar-${toast.id}`}
                initial={{ scaleX: 1 }}
                animate={{ scaleX: 0 }}
                transition={{ duration: duration / 1000, ease: 'linear' }}
                className={`absolute inset-x-0 bottom-0 h-0.5 origin-left ${isAlert ? 'bg-brick' : 'bg-brass'}`}
              />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
