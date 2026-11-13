import { Circle, CircleMarker, Tooltip, useMapEvents } from 'react-leaflet'
import type { DriverPosition, LocationSource } from '../../hooks/useDriverLocation'

const GPS_COLOR = '#1D6FD8'
const SIM_COLOR = '#B7791F'

/**
 * Shows where the alert monitor thinks the driver is, and (in pick mode) lets a click on the map
 * set the simulated test position. A simulated position is always labelled on the map itself.
 */
export function DriverLayer({
  position,
  source,
  pickMode,
  onPick,
}: {
  position: DriverPosition | null
  source: LocationSource
  pickMode: boolean
  onPick: (lat: number, lng: number) => void
}) {
  useMapEvents({
    click: (e) => {
      if (pickMode) onPick(e.latlng.lat, e.latlng.lng)
    },
  })
  if (!position) return null
  const simulated = source === 'simulated'
  const color = simulated ? SIM_COLOR : GPS_COLOR
  const center: [number, number] = [position.lat, position.lng]
  return (
    <>
      {position.accuracy !== null && position.accuracy > 15 && (
        <Circle
          center={center}
          radius={Math.min(position.accuracy, 2000)}
          interactive={false}
          pathOptions={{ color, weight: 1, opacity: 0.5, fillColor: color, fillOpacity: 0.08 }}
        />
      )}
      <CircleMarker
        center={center}
        radius={8}
        interactive={false}
        pathOptions={{
          color: '#ffffff',
          weight: 3,
          fillColor: color,
          fillOpacity: 1,
          dashArray: simulated ? '3 3' : undefined,
        }}
      >
        <Tooltip permanent direction="top" offset={[0, -10]} opacity={0.95}>
          {simulated ? 'Simulated location' : 'You are here'}
        </Tooltip>
      </CircleMarker>
    </>
  )
}
