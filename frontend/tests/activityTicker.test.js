import assert from 'node:assert/strict'
import { activityAge, activityEntry } from '../src/lib/activityTicker.js'

const cell = { cell_id: 'cell-a', locality: 'Midtown', street_name: 'Main St' }
const checks = []

checks.push(() => {
  const event = { id: 1, event: 'incident.created', data: { cell_id: 'cell-a', kind: 'crash', at: '2026-11-14T00:00:00Z' } }
  assert.deepEqual(activityEntry(event, cell), {
    id: '1', cell_id: 'cell-a', text: 'New crash report near Midtown', at: '2026-11-14T00:00:00Z',
  })
})

checks.push(() => {
  const event = { id: 2, event: 'prediction.updated', data: {
    cell_id: 'cell-a', risk_score: 70, live_risk_score: 65, active_incident_count: 1,
    incident_overlay_points: 0, scored_at: '2026-11-14T00:01:00Z',
  } }
  assert.match(activityEntry(event, cell, 68).text, /^Score cooling near Midtown$/)
  assert.match(activityEntry(event, cell, 60).text, /^Risk score updated near Midtown$/)
})

checks.push(() => {
  const event = { id: 3, event: 'traffic.updated', data: { cell_id: 'cell-a', bucket: 'heavy', observed_at: '2026-11-14T00:02:00Z' } }
  assert.equal(activityEntry(event, cell).text, 'Heavy traffic near Midtown')
  assert.equal(activityEntry({ ...event, data: { ...event.data, note: 'private' } }, cell), null)
  assert.equal(activityEntry({ ...event, data: { ...event.data, reporter: 'private' } }, cell), null)
})

checks.push(() => {
  const event = { id: 4, event: 'incident.reviewed', data: { cell_id: 'cell-a', decision: 'confirm', at: '2026-11-14T00:03:00Z' } }
  assert.equal(activityEntry(event, cell).text, 'Incident confirmed near Midtown')
  assert.equal(activityEntry({ ...event, data: { ...event.data, review_note: 'private' } }, cell), null)
})

checks.push(() => {
  assert.equal(activityAge('2026-11-14T00:00:00Z', Date.parse('2026-11-14T00:00:08Z')), '8s ago')
  assert.equal(activityAge('2026-11-14T00:00:00Z', Date.parse('2026-11-14T00:03:00Z')), '3m ago')
  assert.equal(activityAge('2026-11-14T00:00:10Z', Date.parse('2026-11-14T00:00:00Z')), 'just now')
})

for (const check of checks) check()
console.log(`${checks.length} activity ticker checks passed`)
