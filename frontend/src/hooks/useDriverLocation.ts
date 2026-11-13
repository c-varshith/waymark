import { useCallback, useEffect, useMemo, useState } from 'react'

/**
 * Driver position for proximity alerts.
 *
 * Two sources, never mixed:
 *  - "gps": the real browser/device position (navigator.geolocation.watchPosition).
 *  - "simulated": a test coordinate picked on the map. It survives reloads and closing DevTools,
 *    which makes the proximity-alert demo repeatable without Chrome's Sensors override.
 *
 * While a simulated position is active the GPS watcher is not started at all, so the real
 * position is neither read nor sent anywhere. Simulation is only available in development
 * builds, or when a build explicitly opts in with VITE_ENABLE_SIM_LOCATION=1.
 */

export type LocationSource = 'gps' | 'simulated'
export type LocationStatus = 'off' | 'locating' | 'tracking' | 'denied' | 'unavailable'

export interface DriverPosition {
  lat: number
  lng: number
  /** Reported accuracy radius in metres; null for a simulated position. */
  accuracy: number | null
}

export interface LatLng {
  lat: number
  lng: number
}

export const SIM_LOCATION_ALLOWED: boolean =
  import.meta.env.DEV || import.meta.env.VITE_ENABLE_SIM_LOCATION === '1'

const SIM_KEY = 'waymark.simLocation'

function readSim(): LatLng | null {
  if (!SIM_LOCATION_ALLOWED) return null
  try {
    const raw = localStorage.getItem(SIM_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const { lat, lng } = parsed as Record<string, unknown>
    if (typeof lat !== 'number' || typeof lng !== 'number') return null
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null
    return { lat, lng }
  } catch {
    return null
  }
}

function writeSim(value: LatLng | null) {
  try {
    if (value) localStorage.setItem(SIM_KEY, JSON.stringify(value))
    else localStorage.removeItem(SIM_KEY)
  } catch {
    /* storage unavailable: the simulated position still works for this page load */
  }
}

type GpsStatus = Exclude<LocationStatus, 'off'>

export interface DriverLocation {
  position: DriverPosition | null
  source: LocationSource
  status: LocationStatus
  simAllowed: boolean
  simActive: boolean
  /** Sets (or, with null, clears) the simulated position. Ignored when simulation is not allowed. */
  setSimPosition: (value: LatLng | null) => void
}

export function useDriverLocation(enabled: boolean): DriverLocation {
  const [sim, setSim] = useState<LatLng | null>(readSim)
  const [gps, setGps] = useState<DriverPosition | null>(null)
  const [gpsStatus, setGpsStatus] = useState<GpsStatus>('locating')
  const simActive = SIM_LOCATION_ALLOWED && sim !== null

  useEffect(() => {
    if (!enabled || simActive) return
    if (!('geolocation' in navigator)) {
      setGpsStatus('unavailable')
      return
    }
    setGpsStatus('locating')
    const watchId = navigator.geolocation.watchPosition(
      ({ coords }) => {
        setGps({ lat: coords.latitude, lng: coords.longitude, accuracy: coords.accuracy ?? null })
        setGpsStatus('tracking')
      },
      (error) => {
        // A timeout or a temporary loss of signal is not a reason to switch alerts off:
        // the watcher keeps running and recovers by itself. Only a denied permission is final.
        if (error.code === error.PERMISSION_DENIED) {
          setGps(null)
          setGpsStatus('denied')
        } else {
          setGpsStatus((current) => (current === 'tracking' ? 'tracking' : 'unavailable'))
        }
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 },
    )
    return () => {
      navigator.geolocation.clearWatch(watchId)
      setGps(null)
      // Start clean next time, so a past denial does not switch alerts straight off again.
      setGpsStatus('locating')
    }
  }, [enabled, simActive])

  const setSimPosition = useCallback((value: LatLng | null) => {
    if (!SIM_LOCATION_ALLOWED) return
    writeSim(value)
    setSim(value)
  }, [])

  const position = useMemo<DriverPosition | null>(() => {
    if (!enabled) return null
    if (simActive && sim) return { lat: sim.lat, lng: sim.lng, accuracy: null }
    return gps
  }, [enabled, simActive, sim, gps])

  return {
    position,
    source: simActive ? 'simulated' : 'gps',
    status: !enabled ? 'off' : simActive ? 'tracking' : gpsStatus,
    simAllowed: SIM_LOCATION_ALLOWED,
    simActive,
    setSimPosition,
  }
}
