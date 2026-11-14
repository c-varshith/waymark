/**
 * Convert the public realtime event contract into a safe, cell-level label.
 * Strict key checks deliberately keep note, reason, reporter, and review text out.
 */
const EVENT_FIELDS = Object.freeze({
  'incident.created': ['cell_id', 'kind', 'at'],
  'incident.reviewed': ['cell_id', 'decision', 'at'],
  'prediction.updated': ['cell_id', 'risk_score', 'live_risk_score', 'active_incident_count', 'incident_overlay_points', 'scored_at'],
  'traffic.updated': ['cell_id', 'bucket', 'observed_at'],
})

function hasExactKeys(data, fields) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false
  const keys = Object.keys(data).sort()
  return keys.length === fields.length && keys.every((key, index) => key === [...fields].sort()[index])
}

export function activityEntry(event, cell, previousScore) {
  if (!event || !cell || typeof cell.cell_id !== 'string') return null
  const fields = EVENT_FIELDS[event.event]
  if (!fields || !hasExactKeys(event.data, fields) || event.data.cell_id !== cell.cell_id) return null

  const place = [cell.locality, cell.street_name].find((value) => typeof value === 'string' && value.trim()) || 'this area'
  const data = event.data
  let text
  let at
  if (event.event === 'incident.created') {
    if (!['crash', 'near_miss'].includes(data.kind)) return null
    text = `New ${data.kind === 'crash' ? 'crash report' : 'near-miss report'} near ${place}`
    at = data.at
  } else if (event.event === 'incident.reviewed') {
    if (!['confirm', 'reject'].includes(data.decision)) return null
    text = `Incident ${data.decision === 'confirm' ? 'confirmed' : 'rejected'} near ${place}`
    at = data.at
  } else if (event.event === 'prediction.updated') {
    const score = Number(data.live_risk_score)
    if (!Number.isFinite(score)) return null
    text = Number.isFinite(previousScore) && score < previousScore - 0.05
      ? `Score cooling near ${place}`
      : `Risk score updated near ${place}`
    at = data.scored_at
  } else {
    if (!['free', 'moderate', 'heavy'].includes(data.bucket)) return null
    text = `${data.bucket[0].toUpperCase()}${data.bucket.slice(1)} traffic near ${place}`
    at = data.observed_at
  }
  if (typeof at !== 'string' || !Number.isFinite(Date.parse(at))) return null
  return { id: String(event.id ?? `${event.event}-${at}-${cell.cell_id}`), cell_id: cell.cell_id, text, at }
}

export function activityAge(value, now = Date.now()) {
  const age = Math.max(0, Math.floor((now - Date.parse(value)) / 1000))
  if (age < 5) return 'just now'
  if (age < 60) return `${age}s ago`
  const minutes = Math.floor(age / 60)
  if (minutes < 60) return `${minutes}m ago`
  return `${Math.floor(minutes / 60)}h ago`
}
