import { Fragment } from 'react'
import { Pane, Polyline, TileLayer, Tooltip } from 'react-leaflet'
import type { LiveTraffic, LiveTrafficCell } from '../../lib/api'

/** Congestion colours shared by the street lines, the legend and the toggle. */
const TRAFFIC_COLORS: Record<LiveTrafficCell['bucket'], string> = {
  free: '#1F9D74',
  moderate: '#E0A21B',
  heavy: '#C8372D',
}

const BUCKET_LABEL: Record<LiveTrafficCell['bucket'], string> = {
  free: 'Free flowing',
  moderate: 'Moderate congestion',
  heavy: 'Heavy congestion',
}

/** Segment readings are drawn only when the cache has them; tiles only when the server relays them. */
function trafficDrawable(data: LiveTraffic | null | undefined): boolean {
  if (!data) return false
  const segments = (data.status === 'ok' || data.status === 'stale' || data.status === 'budget_reached')
    && data.cells.some((c) => c.geometry.length >= 2)
  return data.tiles.available || segments
}

function segmentTooltip(item: LiveTrafficCell): string {
  const time = new Date(item.observed_at)
  const at = Number.isFinite(time.getTime()) ? time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'unknown time'
  return `${item.road_closure ? 'Road closure reported · ' : ''}${BUCKET_LABEL[item.bucket]}${item.status === 'stale' ? ' (stale)' : ''} · ${at}`
}

/**
 * Live traffic, display only. The flow tiles are visual-only; watched road segments sit beneath
 * the clickable H3 cells so enabling traffic does not block cell selection.
 */
export function TrafficLayer({ data }: { data: LiveTraffic }) {
  const segments = (data.status === 'ok' || data.status === 'stale' || data.status === 'budget_reached')
    ? data.cells.filter((c) => c.geometry.length >= 2)
    : []
  return (
    <>
      {data.tiles.available && (
        <Pane name="traffic-flow" style={{ zIndex: 420, pointerEvents: 'none' }}>
          <TileLayer
            url={data.tiles.url}
            minZoom={data.tiles.min_zoom}
            maxNativeZoom={data.tiles.max_zoom}
            maxZoom={20}
            // Keep basemap labels legible through the traffic overlay.
            opacity={0.58}
            attribution={data.attribution || 'Traffic data © TomTom'}
            updateWhenIdle
            keepBuffer={1}
          />
        </Pane>
      )}
      {segments.length > 0 && (
        <Pane name="traffic-segments" style={{ zIndex: 399 }}>
          {segments.map((item) => (
            <Fragment key={`traffic-${item.cell_id}`}>
              <Polyline
                positions={item.geometry}
                interactive={false}
                pathOptions={{ color: item.road_closure ? '#3B0D0B' : '#ffffff', weight: 7, opacity: 0.72, lineCap: 'round', lineJoin: 'round' }}
              />
              <Polyline
                positions={item.geometry}
                pathOptions={{
                  color: TRAFFIC_COLORS[item.bucket],
                  weight: 4,
                  opacity: item.status === 'stale' ? 0.5 : 0.82,
                  lineCap: 'round',
                  lineJoin: 'round',
                  dashArray: item.road_closure ? '1 10' : undefined,
                }}
              >
                <Tooltip sticky>{segmentTooltip(item)}</Tooltip>
              </Polyline>
            </Fragment>
          ))}
        </Pane>
      )}
    </>
  )
}

/** Toggle plus status and an inline colour key, for the map toolbar. */
export function TrafficControl({ data, on, onToggle, loading }: { data: LiveTraffic | null; on: boolean; onToggle: () => void; loading: boolean }) {
  const drawable = trafficDrawable(data)
  const status = !data
    ? (loading ? 'Checking…' : 'Unavailable')
    : data.status === 'ok' ? 'Live'
      : data.status === 'stale' ? 'Stale'
        : data.status === 'budget_reached' ? 'Budget reached'
          : data.tiles.available ? 'Live' : 'Unavailable'
  const why = !data
    ? 'Traffic status could not be loaded.'
    : !drawable
      ? (data.error === 'Traffic provider key is not configured'
        ? 'Live traffic needs a TomTom key on the server (WAYMARK_TRAFFIC_API_KEY).'
        : data.error ? `Traffic provider unavailable (${data.error}).` : 'Waiting for the first traffic reading.')
      : 'Congestion along the streets in view. Display only: it never changes risk scores.'
  return (
    <div className="flex items-center gap-1 rounded-full bg-white/95 p-1 shadow-card ring-1 ring-navy/10">
      <button
        type="button"
        aria-pressed={on}
        disabled={!drawable && !on}
        onClick={onToggle}
        title={why}
        className={`inline-flex min-h-9 items-center gap-1.5 rounded-full px-3 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-60 ${on ? 'bg-navy text-ivory' : 'text-navy hover:bg-ivory-200'}`}
      >
        <span aria-hidden="true" className="flex h-2.5 w-4 flex-col justify-between">
          <span className="h-[3px] rounded-full" style={{ background: TRAFFIC_COLORS.free }} />
          <span className="h-[3px] rounded-full" style={{ background: TRAFFIC_COLORS.heavy }} />
        </span>
        Traffic
      </button>
      {on && drawable ? (
        <span className="hidden items-center gap-2 px-1.5 text-[10px] font-semibold text-navy/75 sm:inline-flex" aria-label="Traffic colour key">
          {(['free', 'moderate', 'heavy'] as const).map((b) => (
            <span key={b} className="inline-flex items-center gap-1">
              <span aria-hidden="true" className="h-1 w-3 rounded-full" style={{ background: TRAFFIC_COLORS[b] }} />
              {b === 'free' ? 'Free' : b === 'moderate' ? 'Slow' : 'Heavy'}
            </span>
          ))}
        </span>
      ) : null}
      <span role="status" aria-live="polite" title={why} className="whitespace-nowrap pr-2 text-[10px] font-semibold text-navy/70">{status}</span>
    </div>
  )
}
