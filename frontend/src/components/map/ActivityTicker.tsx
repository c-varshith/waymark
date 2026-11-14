import { useEffect, useRef, useState } from 'react'
import type { CellLite, ServerEvent } from '../../lib/api'
import { activityAge, activityEntry } from '../../lib/activityTicker.js'

interface Props {
  event: ServerEvent | null
  cellsById: ReadonlyMap<string, CellLite>
  onSelect: (cellId: string) => void
}

export function ActivityTicker({ event, cellsById, onSelect }: Props) {
  const [entries, setEntries] = useState<ReturnType<typeof activityEntry>[]>([])
  const [announcement, setAnnouncement] = useState('')
  const [now, setNow] = useState<number | null>(null)
  const seenEvent = useRef<string | number | null>(null)
  const scores = useRef(new Map<string, number>())
  const pendingAnnouncement = useRef('')
  const announcementTimer = useRef<number | null>(null)

  useEffect(() => {
    if (!event) return
    const cellId = typeof event.data.cell_id === 'string' ? event.data.cell_id : ''
    const eventKey = event.id ?? `${event.event}:${cellId}:${String(event.data.at ?? event.data.scored_at ?? event.data.observed_at ?? '')}`
    if (seenEvent.current === eventKey) return
    seenEvent.current = eventKey
    const cell = cellsById.get(cellId)
    if (!cell) return
    const previous = scores.current.get(cellId) ?? cell.risk_score
    const entry = activityEntry(event, cell, previous)
    if (!entry) return
    if (event.event === 'prediction.updated') {
      const score = Number(event.data.live_risk_score)
      if (Number.isFinite(score)) scores.current.set(cellId, score)
    }
    setEntries((current) => [entry, ...current.filter((item) => item?.id !== entry.id)].slice(0, 8))
    pendingAnnouncement.current = entry.text
    if (announcementTimer.current === null) {
      announcementTimer.current = window.setTimeout(() => {
        announcementTimer.current = null
        setAnnouncement(pendingAnnouncement.current)
      }, 2500)
    }
  }, [event, cellsById])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => () => {
    if (announcementTimer.current !== null) window.clearTimeout(announcementTimer.current)
  }, [])

  if (entries.length === 0) return null
  return <section aria-label="Recent map activity" className="w-[min(20rem,calc(100vw-1.5rem))] overflow-hidden rounded-xl border border-navy/15 bg-white/95 shadow-card backdrop-blur-sm">
    <div className="flex items-center justify-between border-b border-navy/10 px-3 py-2">
      <h2 className="text-xs font-extrabold uppercase tracking-wide text-navy">Recent activity</h2>
      <span className="text-[10px] font-semibold text-navy/60">Cell-level updates</span>
    </div>
    <ol aria-live="off" className="max-h-36 divide-y divide-navy/5 overflow-y-auto">
      {entries.map((item) => item && <li key={item.id}>
        <button type="button" onClick={() => onSelect(item.cell_id)} className="flex min-h-9 w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs text-navy hover:bg-ivory-100 focus-visible:outline-2 focus-visible:outline-brass">
          <span className="min-w-0 truncate font-semibold">{item.text}</span>
          <time className="shrink-0 text-[10px] text-navy/60" dateTime={item.at}>{activityAge(item.at, now ?? Date.parse(item.at))}</time>
        </button>
      </li>)}
    </ol>
    <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</p>
  </section>
}
