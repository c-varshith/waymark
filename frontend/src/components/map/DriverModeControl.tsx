import { Crosshair, FlaskConical, LocateFixed, Navigation } from 'lucide-react'
import type { LocationSource, LocationStatus } from '../../hooks/useDriverLocation'

export type MonitorState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'outside' }
  | { kind: 'error' }
  | { kind: 'scored'; score: number; checkedAt: number }

export const ALERT_THRESHOLD = 80

const BTN =
  'inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold shadow-card md:min-h-9 sm:text-sm'

function statusText(status: LocationStatus, monitor: MonitorState): { text: string; tone: 'calm' | 'warn' | 'alert' } {
  if (status === 'locating') return { text: 'Finding your location…', tone: 'calm' }
  if (status === 'denied') return { text: 'Location permission denied', tone: 'warn' }
  if (status === 'unavailable') return { text: 'Location unavailable', tone: 'warn' }
  switch (monitor.kind) {
    case 'checking':
      return { text: 'Scoring this cell…', tone: 'calm' }
    case 'outside':
      return { text: 'Outside the scored area', tone: 'warn' }
    case 'error':
      return { text: 'Live scoring unavailable, retrying', tone: 'warn' }
    case 'scored':
      return monitor.score > ALERT_THRESHOLD
        ? { text: `Live risk ${Math.round(monitor.score)}, high`, tone: 'alert' }
        : { text: `Live risk ${Math.round(monitor.score)}`, tone: 'calm' }
    default:
      return { text: 'Waiting for a position', tone: 'calm' }
  }
}

/**
 * Proximity-alert switch plus a live status readout. When simulation is allowed (development or an
 * explicit demo build) it also offers a test position, and says so in plain words while it is active.
 */
export function DriverModeControl({
  enabled,
  onToggle,
  status,
  source,
  monitor,
  simAllowed,
  simActive,
  pickingSim,
  onPickSim,
  onClearSim,
}: {
  enabled: boolean
  onToggle: () => void
  status: LocationStatus
  source: LocationSource
  monitor: MonitorState
  simAllowed: boolean
  simActive: boolean
  pickingSim: boolean
  onPickSim: () => void
  onClearSim: () => void
}) {
  const { text, tone } = statusText(status, monitor)
  const dot = tone === 'alert' ? 'bg-brick' : tone === 'warn' ? 'bg-brass' : 'bg-emerald-500'
  return (
    <div className="order-2 flex max-w-full flex-col items-end gap-1.5 md:order-1">
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <button
          type="button"
          onClick={onToggle}
          aria-pressed={enabled}
          title="Your position is converted to an H3 cell in the browser. Only the cell ID is sent for scoring."
          className={`${BTN} ${enabled ? 'bg-brass text-navy hover:brightness-95' : 'bg-white text-navy hover:bg-ivory'}`}
        >
          <Navigation aria-hidden="true" className="h-3.5 w-3.5" />
          {enabled ? 'Stop alerts' : 'Proximity alerts'}
        </button>
        {enabled && simAllowed && (
          <button
            type="button"
            onClick={onPickSim}
            aria-pressed={pickingSim}
            title="Pick a test position on the map. It is remembered on this device until you clear it."
            className={`${BTN} ${pickingSim ? 'bg-brass text-navy' : 'bg-navy text-ivory hover:bg-navy-800'}`}
          >
            <Crosshair aria-hidden="true" className="h-3.5 w-3.5" />
            {pickingSim ? 'Cancel' : simActive ? 'Move test location' : 'Set test location'}
          </button>
        )}
        {enabled && simActive && (
          <button type="button" onClick={onClearSim} className={`${BTN} bg-white text-navy hover:bg-ivory`}>
            <LocateFixed aria-hidden="true" className="h-3.5 w-3.5" />
            Use real GPS
          </button>
        )}
      </div>
      {enabled && (
        <div
          role="status"
          aria-live="polite"
          className="flex max-w-[min(80vw,22rem)] items-center gap-2 rounded-full bg-navy/90 px-3 py-1.5 text-xs font-semibold text-ivory shadow-card"
        >
          <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${dot} ${monitor.kind === 'checking' || status === 'locating' ? 'animate-pulse' : ''}`} />
          <span className="truncate">{text}</span>
          {source === 'simulated' && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-brass px-2 py-0.5 text-xs font-bold text-navy">
              <FlaskConical aria-hidden="true" className="h-3 w-3" />
              Simulated
            </span>
          )}
        </div>
      )}
    </div>
  )
}
