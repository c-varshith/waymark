import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { MapPin, PanelRightOpen, SlidersHorizontal } from 'lucide-react'
import { MapContainer, TileLayer, ZoomControl } from 'react-leaflet'
import { useSearchParams } from 'react-router-dom'
import { cellToParent, getResolution, gridDisk, latLngToCell } from 'h3-js'
import 'leaflet/dist/leaflet.css'
import { ApiError, api } from '../lib/api'
import type { CellDetail, CellLite, LiveConditionContext, LivePrediction, LiveSnapshot, PlaceSearchItem, PredictionOverrides, ServerEvent, WhatIf } from '../lib/api'
import { useApi } from '../hooks/useApi'
import { DEMO_CELLS } from '../lib/demo'
import { baseLayers } from '../lib/tiles'
import { Legend, FilterCard, RegionCard, WhatIfCard } from '../components/map/Controls'
import type { ColorMode, Filters, RegionTarget, Scenario } from '../components/map/Controls'
import { BaseMapSwitch } from '../components/map/BaseMapSwitch'
import { DemoTour } from '../components/map/DemoTour'
import { DetailPanel } from '../components/map/DetailPanel'
import { CrashLayer, FitOnce, FitToBounds, FlyController, HexLayer, WorldLimits } from '../components/map/MapLayers'
import type { FlyTarget, LevelInfo } from '../components/map/MapLayers'
import { Card, ErrorBanner } from '../components/ui'
import { useAuth } from '../context/auth'
import { opsApi } from '../lib/opsApi'
import { IncidentLayer } from '../components/map/IncidentLayer'
import { IncidentReportDialog } from '../components/map/IncidentReportDialog'
import { Toast } from '../components/ops/Toast'
import type { ToastData } from '../components/ops/Toast'
import { useDriverLocation } from '../hooks/useDriverLocation'
import { DriverLayer } from '../components/map/DriverLayer'
import { ActivityTicker } from '../components/map/ActivityTicker'
import { useLiveStream } from '../hooks/useLiveStream'
import { ALERT_THRESHOLD, DriverModeControl } from '../components/map/DriverModeControl'
import type { MonitorState } from '../components/map/DriverModeControl'
import { ALERT_HYSTERESIS } from '../lib/realtimeConfig'
import { TrafficControl, TrafficLayer } from '../components/map/TrafficLayer'

const BASE_LAYERS = baseLayers()
const HOUSTON: [number, number] = [29.7604, -95.3698]
const DEFAULT_FILTERS: Filters = { confidence: 'All', maxPast: Number.POSITIVE_INFINITY, emergingOnly: false }
const NO_SCENARIO: Scenario = { night: false, rain: false, lowVis: false }
const DESKTOP = '(min-width: 768px)'
/** Below this width the filters column and the details column do not both fit beside the map. */
const NARROW = '(max-width: 1279px)'
function mediaMatches(query: string): boolean {
  return typeof window !== 'undefined' && window.matchMedia(query).matches
}
type MapMode = 'baseline' | 'live' | 'what-if'
/** How often the cell under the driver is re-scored while nothing else changes. */
const ALERT_RECHECK_MS = 30_000
interface ScenarioBatch {
  comparison: WhatIf
  predictionsByCell: Map<string, LivePrediction>
}
function LiveContextChip({ context, loading, mapLoading, error }: { context: LiveConditionContext | null; loading: boolean; mapLoading: boolean; error: string | null }) {
  const status = context?.status ?? 'unavailable'
  const conditions = context?.active_conditions.length ? context.active_conditions.join(' · ') : 'clear conditions'
  const observed = context?.observed_at ? new Date(context.observed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null
  const dot = status === 'ok' ? 'bg-teal' : status === 'stale' ? 'bg-brass' : 'bg-brick'
  return <div role="status" aria-live="polite" title={context?.caveat} className="max-w-[min(84vw,26rem)] rounded-lg border border-navy/15 bg-white/95 px-3 py-2 text-[11px] font-semibold text-navy shadow-card">
    <div className="flex items-center gap-1.5"><span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
      {status === 'ok' ? `${context?.source ?? 'Open-Meteo'} · ${conditions}${observed ? ` · ${observed}` : ''}${error ? ' · scores stale' : ''}` : status === 'stale' ? `${context?.source ?? 'Open-Meteo'} stale · last ${observed ?? 'reading'}` : 'Live weather unavailable'}
      {mapLoading && <span className="font-normal text-navy/65">· rescoring map</span>}
    </div>
    {(status !== 'ok' || error) && <p className="mt-0.5 font-normal text-brick">{error || (loading ? 'Loading provider status…' : 'Live scoring is paused; stored baseline scores remain visible.')}</p>}
  </div>
}
type LiveMapPrediction = Pick<LivePrediction, 'risk_score' | 'live_risk_score' | 'active_incident_count' | 'incident_overlay_points'>

function liveMapPrediction(prediction: LivePrediction): LiveMapPrediction {
  return {
    risk_score: prediction.risk_score,
    live_risk_score: prediction.live_risk_score,
    active_incident_count: prediction.active_incident_count,
    incident_overlay_points: prediction.incident_overlay_points,
  }
}

function isModelInferenceFailure(error: unknown): boolean {
  if (!(error instanceof ApiError) || ![422, 503].includes(error.status)) return false
  return /live inference (?:is unavailable|failed)|no model feature row/i.test(error.message)
}

export default function MapPage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const selectedId = params.get('cell')
  // Side panes. Below xl there is not room for both next to a usable map, so opening one closes the other.
  const [leftPaneOpen, setLeftPaneOpen] = useState(() => mediaMatches(DESKTOP) && !(selectedId && mediaMatches(NARROW)))
  const [rightPaneOpen, setRightPaneOpen] = useState(true)

  const cellsQ = useApi(api.cells, [], { keepPrevious: true })
  const summaryQ = useApi(api.summary, [])
  const contextQ = useApi(api.liveContext, [])
  const modelHealthQ = useApi(api.modelHealth, [])
  const trafficQ = useApi(api.liveTraffic, [], { keepPrevious: true })
  const reloadLiveContext = contextQ.reload
  const reloadTraffic = trafficQ.reload
  const [trafficLayerOn, setTrafficLayerOn] = useState(false)
  const [mapMode, setMapMode] = useState<MapMode>('baseline')
  const [contextRevision, setContextRevision] = useState(0)
  const [lastLivePredictions, setLastLivePredictions] = useState<Map<string, LivePrediction>>(() => new Map())
  const [liveBatchError, setLiveBatchError] = useState<string | null>(null)
  const [liveBatchLoading, setLiveBatchLoading] = useState(false)
  const contextRefreshTimer = useRef<number | null>(null)
  const cells = useMemo(() => cellsQ.data ?? [], [cellsQ.data])
  const cellsById = useMemo(() => new Map(cells.map((c) => [c.cell_id, c])), [cells])
  const scoredResolution = useMemo(() => cells.length ? getResolution(cells[0].cell_id) : 9, [cells])

  // ---- filters
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const maxPastLimit = useMemo(() => cells.reduce((m, c) => Math.max(m, c.n_past_crashes), 0), [cells])
  const filtered = useMemo(
    () =>
      cells.filter(
        (c) =>
          (filters.confidence === 'All' || c.confidence === filters.confidence) &&
          c.n_past_crashes <= filters.maxPast &&
          (!filters.emergingOnly || c.emerging_risk),
      ),
    [cells, filters],
  )
  const [region, setRegion] = useState<{ id: string; res: number } | null>(null)
  const [placeSearch, setPlaceSearch] = useState<PlaceSearchItem | null>(null)
  // Region view: only the cells whose H3 parent is the chosen region.
  const regionCells = useMemo(() => {
    if (!region) return filtered
    return filtered.filter((c) => {
      try {
        return cellToParent(c.cell_id, region.res) === region.id
      } catch {
        return false
      }
    })
  }, [filtered, region])
  const regionIds = useMemo(() => (region ? new Set(regionCells.map((c) => c.cell_id)) : null), [region, regionCells])
  const placeIds = useMemo(() => (placeSearch ? new Set(placeSearch.cell_ids) : null), [placeSearch])
  const displayedCells = useMemo(
    () => (placeIds ? filtered.filter((c) => placeIds.has(c.cell_id)) : region ? regionCells : filtered),
    [filtered, placeIds, region, regionCells],
  )

  // What stays vivid: the selected region, otherwise just the selected cell. Everything else fades.
  const highlightIds = useMemo(
    () => regionIds ?? (selectedId ? new Set([selectedId]) : null),
    [regionIds, selectedId],
  )

  // ---- what-if scenario
  const [scenario, setScenario] = useState<Scenario>(NO_SCENARIO)
  const [incidentRefreshKey, setIncidentRefreshKey] = useState(0)
  const [incidentLayerRefreshKey, setIncidentLayerRefreshKey] = useState(0)
  const [snapshotScoredAt, setSnapshotScoredAt] = useState<string | null>(null)
  const [tickerEvent, setTickerEvent] = useState<ServerEvent | null>(null)
  const [pulseEvent, setPulseEvent] = useState<{ cellId: string; nonce: number; at: number } | null>(null)
  const pulseNonce = useRef(0)
  const scenarioActive = mapMode === 'what-if' && (scenario.night || scenario.rain || scenario.lowVis)
  const predictionOverrides = useMemo<PredictionOverrides>(() => ({
    ...(scenario.rain ? { weather_condition: 'rain' as const } : {}),
    ...(scenario.night ? { lighting: 'night' as const } : {}),
    ...(scenario.lowVis ? { low_visibility: true } : {}),
  }), [scenario.rain, scenario.night, scenario.lowVis])
  const scenarioLabel = [scenario.night && 'Night', scenario.rain && 'Rain', scenario.lowVis && 'Low visibility']
    .filter(Boolean)
    .join(' + ')
  const whatIfQ = useApi<ScenarioBatch | null>(
    async (signal) => {
      if (!scenarioActive) return null
      const predictions = await api.predictBatch(cells.map((cell) => cell.cell_id), predictionOverrides, signal)
      const predictionsByCell = new Map(predictions.map((prediction) => [prediction.cell_id, prediction]))
      const byCell = new Map(predictions.map((prediction) => [prediction.cell_id, {
        baseline: cellsById.get(prediction.cell_id)?.risk_score ?? null,
        scenario: prediction.risk_score,
      }]))
      let maxAbsDelta = 0
      for (const value of byCell.values()) {
        if (value.baseline !== null && value.scenario !== null) maxAbsDelta = Math.max(maxAbsDelta, Math.abs(value.scenario - value.baseline))
      }
      return { comparison: { byCell, maxAbsDelta }, predictionsByCell }
    },
    [mapMode, scenario.night, scenario.rain, scenario.lowVis, cells, cellsById],
    { keepPrevious: true },
  )
  const scenarioBatch = scenarioActive ? whatIfQ.data : null
  const whatIf = scenarioBatch?.comparison ?? null
  const liveBatchQ = useApi<LivePrediction[] | null>(
    async (signal) => mapMode === 'live' ? api.predictBatch(cells.map((cell) => cell.cell_id), {}, signal, true) : null,
    [mapMode, cells, contextRevision],
    { keepPrevious: true },
  )
  useEffect(() => {
    if (mapMode !== 'live') return
    setLiveBatchLoading(liveBatchQ.loading)
    if (liveBatchQ.data) {
      setLastLivePredictions(new Map(liveBatchQ.data.map((prediction) => [prediction.cell_id, prediction])))
    }
    if (liveBatchQ.error) {
      setLiveBatchError(liveBatchQ.error)
    } else if (liveBatchQ.data) {
      setLiveBatchError(null)
    }
  }, [mapMode, liveBatchQ.data, liveBatchQ.error, liveBatchQ.loading])
  const [incidentPredictions, setIncidentPredictions] = useState<Map<string, LiveMapPrediction>>(() => new Map())

  const scenarioStats = useMemo(() => {
    if (!whatIf) return { all: null as number | null, emerging: null as number | null }
    let sumAll = 0
    let nAll = 0
    for (const w of whatIf.byCell.values()) {
      if (w.baseline === null || w.scenario === null) continue
      sumAll += w.scenario - w.baseline
      nAll++
    }
    let sumEm = 0
    let nEm = 0
    for (const c of cells) {
      if (!c.emerging_risk) continue
      const w = whatIf.byCell.get(c.cell_id)
      if (!w || w.baseline === null || w.scenario === null) continue
      sumEm += w.scenario - w.baseline
      nEm++
    }
    return { all: nAll ? sumAll / nAll : null, emerging: nEm ? sumEm / nEm : null }
  }, [whatIf, cells])

  // ---- base map (Light / Dark / Satellite), remembered in this browser
  const [baseName, setBaseName] = useState<string>(() => {
    try {
      const saved = localStorage.getItem('waymark.basemap')
      if (saved && BASE_LAYERS.some((l) => l.name === saved)) return saved
    } catch {
      /* storage unavailable */
    }
    return BASE_LAYERS[0].name
  })
  const base = BASE_LAYERS.find((l) => l.name === baseName) ?? BASE_LAYERS[0]
  const pickBase = (name: string) => {
    setBaseName(name)
    try {
      localStorage.setItem('waymark.basemap', name)
    } catch {
      /* storage unavailable */
    }
  }

  // ---- hexagon colouring
  const [opacity, setOpacity] = useState(0.35)
  const [colorMode, setColorMode] = useState<ColorMode>('score')
  const maxIncidentCount = useMemo(() => cells.reduce((max, c) => Math.max(max, (c.incident_pending_count ?? 0) + (c.incident_confirmed_count ?? 0)), 1), [cells])
  const [level, setLevel] = useState<LevelInfo>({ grouped: true, points: false })
  // null until the first zoomed-in view asks the backend; false = crash points not loaded yet
  const [crashesAvailable, setCrashesAvailable] = useState<boolean | null>(null)
  /** Colour value per cell: scenario change (-1..1), or a 0..1 score; null draws grey. */
  const valueFor = useCallback(
    (c: CellLite): number | null => {
      if (scenarioActive && whatIfQ.error) {
        const fallbackIncident = incidentPredictions.get(c.cell_id)
        return Math.min(100, c.risk_score + (fallbackIncident?.incident_overlay_points ?? 0)) / 100
      }
      if (whatIf) {
        const scenarioPrediction = scenarioBatch?.predictionsByCell.get(c.cell_id)
        const reportedPrediction = incidentPredictions.get(c.cell_id)
        const activeIncidentCount = reportedPrediction?.active_incident_count ?? scenarioPrediction?.active_incident_count ?? 0
        const modelScore = scenarioPrediction?.risk_score ?? reportedPrediction?.risk_score ?? c.risk_score
        const overlayPoints = reportedPrediction?.incident_overlay_points ?? scenarioPrediction?.incident_overlay_points ?? 0
        const w = whatIf.byCell.get(c.cell_id)
        if (!w || w.baseline === null || w.scenario === null) return null
        const scenarioScore = activeIncidentCount > 0
          ? Math.min(100, modelScore + overlayPoints)
          : w.scenario
        return Math.max(-1, Math.min(1, (scenarioScore - w.baseline) / (whatIf.maxAbsDelta || 1)))
      }
      if (mapMode === 'live') {
        const prediction = lastLivePredictions.get(c.cell_id)
        if (!prediction) return null
        const latestIncidentOverlay = incidentPredictions.get(c.cell_id)?.incident_overlay_points
        return Math.min(100, prediction.risk_score + (latestIncidentOverlay ?? prediction.incident_overlay_points)) / 100
      }
      if (colorMode === 'emerging') return c.emerging_risk ? (c.history_percentile ?? 0) / 100 : null
      if (colorMode === 'adjusted') return (c.adjusted_risk_score ?? c.risk_score) / 100
      if (colorMode === 'projection') return (c.projected_risk_score ?? c.adjusted_risk_score ?? c.risk_score) / 100
      if (colorMode === 'incidents') return Math.log1p((c.incident_pending_count ?? 0) + (c.incident_confirmed_count ?? 0)) / Math.log1p(maxIncidentCount)
      const livePrediction = incidentPredictions.get(c.cell_id)
      if (livePrediction) return Math.min(100, c.risk_score + livePrediction.incident_overlay_points) / 100
      return c.risk_score / 100
    },
    [whatIf, scenarioBatch, incidentPredictions, lastLivePredictions, mapMode, colorMode, maxIncidentCount, scenarioActive, whatIfQ.error],
  )
  const tooltipFor = useCallback(
    (c: CellLite) =>
      `${c.cell_id}${c.street_name || c.locality ? `, ${[c.street_name, c.locality].filter(Boolean).join(', ')}` : ''}, ${c.n_past_crashes} past crashes` +
      (c.history_percentile !== null
        ? `, riskier than ${Math.round(c.history_percentile)}% of cells with similar history`
        : '') +
      (colorMode === 'adjusted' && c.adjusted_risk_score !== null
        ? `, baseline ${c.risk_score.toFixed(1)} to verified adjusted ${c.adjusted_risk_score.toFixed(1)}`
        : '') +
      (colorMode === 'projection'
        ? `, baseline ${c.risk_score.toFixed(1)} to progress projection ${(c.projected_risk_score ?? c.risk_score).toFixed(1)} (estimate)`
        : '') +
      (colorMode === 'incidents'
        ? `, ${c.incident_confirmed_count ?? 0} confirmed and ${c.incident_pending_count ?? 0} pending reports`
        : ''),
    [colorMode],
  )

  // ---- selection and navigation
  const [flyTarget, setFlyTarget] = useState<FlyTarget | null>(null)
  const pendingFly = useRef<string | null>(selectedId)
  const flyNonce = useRef(0)
  const [fitEnabled] = useState(!selectedId)

  const selectCell = useCallback(
    (id: string | null, fly = true) => {
      // Picking a single cell highlights just that cell, so leave region view.
      if (id && region) setRegion(null)
      if (id) setPlaceSearch(null)
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev)
          if (id) p.set('cell', id)
          else p.delete('cell')
          return p
        },
        { replace: id === null },
      )
      if (id && fly) {
        const c = cellsById.get(id)
        if (c) setFlyTarget({ lat: c.lat, lng: c.lng, nonce: ++flyNonce.current })
        else pendingFly.current = id
      }
      if (id) {
        setRightPaneOpen(true)
        if (mediaMatches(NARROW)) setLeftPaneOpen(false)
      }
    },
    [cellsById, setParams, region],
  )

  const detailQ = useApi<CellDetail | null>(
    (signal) => (selectedId ? api.cell(selectedId, signal) : Promise.resolve(null)),
    [selectedId],
  )
  const reloadDetail = detailQ.reload
  const detail = detailQ.data
  const selectedPredictionQ = useApi<LivePrediction | null>(
    (signal) => !selectedId ? Promise.resolve(null)
      : mapMode === 'live' ? api.predictLive(selectedId, signal)
        : api.predict(selectedId, mapMode === 'what-if' ? predictionOverrides : {}, signal),
    [selectedId, mapMode, predictionOverrides, incidentRefreshKey, contextRevision],
    { keepPrevious: false },
  )
  const selectedPrediction = selectedPredictionQ.data

  useEffect(() => {
    const refreshWhenVisible = () => {
      if (document.visibilityState !== 'visible') return
      if (selectedId) detailQ.reload()
    }
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => document.removeEventListener('visibilitychange', refreshWhenVisible)
    // The live stream reconnects and snapshots on visibility; avoid reloading the full cell set here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (detail && pendingFly.current === detail.cell_id) {
      pendingFly.current = null
      setFlyTarget({ lat: detail.lat, lng: detail.lng, nonce: ++flyNonce.current })
    }
  }, [detail])

  const selectedPos = useMemo<[number, number] | null>(() => {
    if (detail) return [detail.lat, detail.lng]
    const c = selectedId ? cellsById.get(selectedId) : undefined
    return c ? [c.lat, c.lng] : null
  }, [detail, selectedId, cellsById])

  // ---- demo mode
  const [demoStep, setDemoStep] = useState<number | null>(null)
  const goStep = useCallback(
    (n: number) => {
      setDemoStep(n)
      selectCell(DEMO_CELLS[n])
    },
    [selectCell],
  )
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      const typing = !!el && ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)
      if (e.key === 'Escape') {
        if (demoStep !== null) setDemoStep(null)
        else if (selectedId) selectCell(null)
        else if (region) setRegion(null)
      } else if (demoStep !== null && !typing) {
        if (e.key === 'ArrowRight' && demoStep < DEMO_CELLS.length - 1) goStep(demoStep + 1)
        if (e.key === 'ArrowLeft' && demoStep > 0) goStep(demoStep - 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [demoStep, selectedId, selectCell, goStep, region])

  // Where the "Region report" card sends you: the open region, the searched locality, or the area around the cell.
  const reportTarget = useMemo<RegionTarget | null>(() => {
    if (region) return { kind: 'h3_parent', key: region.id, label: `region ${region.id} (H3 resolution ${region.res})` }
    if (placeSearch) return { kind: 'locality', key: `${placeSearch.kind}:${placeSearch.name}`, label: placeSearch.name }
    if (selectedId) {
      try {
        return { kind: 'h3_parent', key: cellToParent(selectedId, 7), label: 'the area around this cell' }
      } catch {
        return null
      }
    }
    return null
  }, [region, placeSearch, selectedId])

  const [pickIncidentMode, setPickIncidentMode] = useState(false)
  const [incidentPoint, setIncidentPoint] = useState<[number, number] | null>(null)
  const isTrafficPolice = user?.role === 'community'
  const demoDetail = demoStep !== null && detail?.cell_id === DEMO_CELLS[demoStep] ? detail : null
  const [toast, setToast] = useState<ToastData | null>(null)
  const toastId = useRef(0)
  const notify = useCallback((message: ToastData['message'], options?: Omit<ToastData, 'id' | 'message'>) => {
    setToast({ id: ++toastId.current, message, ...options })
  }, [])
  const closeToast = useCallback(() => setToast(null), [])

  // ---- driver mode: position -> H3 cell -> live score -> proximity alert
  const [locationAlertsEnabled, setLocationAlertsEnabled] = useState(false)
  const [pickSimMode, setPickSimMode] = useState(false)
  const driver = useDriverLocation(locationAlertsEnabled)
  const [monitor, setMonitor] = useState<MonitorState>({ kind: 'idle' })
  const [recheckTick, setRecheckTick] = useState(0)
  /** Cells already alerted on during this visit; a cell is forgotten once it leaves the watch ring or cools down. */
  const alertedCells = useRef<Set<string>>(new Set())
  const lastRealtimeAlertRefresh = useRef(0)
  const selectCellRef = useRef(selectCell)
  useEffect(() => {
    selectCellRef.current = selectCell
  }, [selectCell])

  const locationCellId = useMemo(() => {
    if (!driver.position) return null
    try {
      return latLngToCell(driver.position.lat, driver.position.lng, scoredResolution)
    } catch {
      return null
    }
  }, [driver.position, scoredResolution])

  const applyLiveSnapshot = useCallback((snapshot: LiveSnapshot) => {
    setSnapshotScoredAt(snapshot.scored_at)
    setIncidentPredictions(new Map(snapshot.overlays.map((item) => [item.cell_id, {
      risk_score: item.risk_score,
      live_risk_score: item.live_risk_score,
      active_incident_count: item.active_incident_count,
      incident_overlay_points: item.incident_overlay_points,
    }])))
    setIncidentLayerRefreshKey((n) => n + 1)
    if (selectedId) reloadDetail()
  }, [selectedId, reloadDetail])
  const handleLiveEvent = useCallback((event: ServerEvent) => {
    const cellId = typeof event.data.cell_id === 'string' ? event.data.cell_id : null
    if (cellId && ['incident.created', 'incident.reviewed', 'prediction.updated', 'traffic.updated'].includes(event.event)) {
      setTickerEvent(event)
    }
    if (cellId && (event.event === 'incident.created' || event.event === 'prediction.updated')) {
      setPulseEvent({ cellId, nonce: ++pulseNonce.current, at: Date.now() })
    }
    if (event.event === 'traffic.updated') {
      reloadTraffic()
      if (mapMode === 'live' && modelHealthQ.data?.traffic_lift_applied) {
        if (contextRefreshTimer.current !== null) window.clearTimeout(contextRefreshTimer.current)
        contextRefreshTimer.current = window.setTimeout(() => {
          contextRefreshTimer.current = null
          setContextRevision((n) => n + 1)
        }, 2000)
      }
      return
    }
    if (event.event === 'prediction.updated' && cellId) {
      if (typeof event.data.scored_at === 'string') setSnapshotScoredAt(event.data.scored_at)
      const count = Number(event.data.active_incident_count) || 0
      if (count <= 0) {
        setIncidentPredictions((current) => {
          if (!current.has(cellId)) return current
          const next = new Map(current)
          next.delete(cellId)
          return next
        })
      } else {
        const prediction: LiveMapPrediction = {
          risk_score: Number(event.data.risk_score) || 0,
          live_risk_score: Number(event.data.live_risk_score) || 0,
          active_incident_count: count,
          incident_overlay_points: Number(event.data.incident_overlay_points) || 0,
        }
        setIncidentPredictions((current) => new Map(current).set(cellId, prediction))
      }
      if (selectedId === cellId) setIncidentRefreshKey((n) => n + 1)
      if (locationAlertsEnabled && locationCellId) {
        try {
          const inWatchRing = gridDisk(locationCellId, 1).includes(cellId)
          if (inWatchRing && Date.now() - lastRealtimeAlertRefresh.current >= 20_000) {
            lastRealtimeAlertRefresh.current = Date.now()
            setIncidentRefreshKey((n) => n + 1)
          }
        } catch { /* ignore invalid or different resolution cells */ }
      }
      return
    }
    if ((event.event === 'incident.created' || event.event === 'incident.reviewed') && cellId) {
      setIncidentLayerRefreshKey((n) => n + 1)
      if (selectedId === cellId) reloadDetail()
      return
    }
    if (event.event === 'context.changed') {
      reloadLiveContext()
      if (contextRefreshTimer.current !== null) window.clearTimeout(contextRefreshTimer.current)
      contextRefreshTimer.current = window.setTimeout(() => {
        contextRefreshTimer.current = null
        setContextRevision((n) => n + 1)
      }, 2000)
      if (selectedId) setIncidentRefreshKey((n) => n + 1)
    }
  }, [selectedId, locationAlertsEnabled, locationCellId, reloadDetail, reloadLiveContext, reloadTraffic,
    mapMode, modelHealthQ.data?.traffic_lift_applied])
  const liveStream = useLiveStream(handleLiveEvent, applyLiveSnapshot)

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') reloadTraffic()
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [reloadTraffic])

  useEffect(() => {
    if (selectedId) void api.watchTrafficCell(selectedId).catch(() => undefined)
  }, [selectedId])

  useEffect(() => () => {
    if (contextRefreshTimer.current !== null) window.clearTimeout(contextRefreshTimer.current)
  }, [])

  const stopLocationAlerts = useCallback(() => {
    setLocationAlertsEnabled(false)
    setPickSimMode(false)
    setMonitor({ kind: 'idle' })
    alertedCells.current.clear()
  }, [])

  useEffect(() => {
    if (driver.status !== 'denied') return
    stopLocationAlerts()
    notify('Location permission was denied. Enable it in browser settings to use proximity alerts.')
  }, [driver.status, stopLocationAlerts, notify])

  // Re-score on a timer while the tab is visible, so a score that changes server-side still raises an alert.
  useEffect(() => {
    if (!locationAlertsEnabled) return
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') setRecheckTick((n) => n + 1)
    }, ALERT_RECHECK_MS)
    return () => clearInterval(timer)
  }, [locationAlertsEnabled])

  useEffect(() => {
    if (!locationAlertsEnabled || !locationCellId || cells.length === 0) return
    // Watch the cell under the driver and its six neighbours, limited to cells the model has scored.
    let ring: string[]
    try {
      ring = gridDisk(locationCellId, 1).filter((id) => cellsById.has(id))
    } catch {
      ring = []
    }
    if (ring.length === 0) {
      alertedCells.current.clear()
      setMonitor({ kind: 'outside' })
      return
    }
    const controller = new AbortController()
    setMonitor((current) => (current.kind === 'scored' ? current : { kind: 'checking' }))
    api.predictBatch(ring, mapMode === 'what-if' ? predictionOverrides : {}, controller.signal, mapMode !== 'what-if')
      .then(async (predictions) => {
        if (controller.signal.aborted) return
        const here = predictions.find((p) => p.cell_id === locationCellId)
        setMonitor(here ? { kind: 'scored', score: here.live_risk_score, checkedAt: Date.now() } : { kind: 'outside' })

        const scores = new Map(predictions.map((p) => [p.cell_id, p.live_risk_score]))
        for (const id of alertedCells.current) {
          const score = scores.get(id)
          if (score === undefined || score < ALERT_THRESHOLD - ALERT_HYSTERESIS) alertedCells.current.delete(id)
        }
        const hot = new Set(predictions.filter((p) => p.live_risk_score >= ALERT_THRESHOLD).map((p) => p.cell_id))
        // The driver's own cell first, then the highest-scoring neighbour.
        const fresh = predictions
          .filter((p) => hot.has(p.cell_id) && !alertedCells.current.has(p.cell_id))
          .sort((a, b) => Number(b.cell_id === locationCellId) - Number(a.cell_id === locationCellId) || b.live_risk_score - a.live_risk_score)
        const top = fresh[0]
        if (!top) return
        for (const p of fresh) alertedCells.current.add(p.cell_id)

        const inCell = top.cell_id === locationCellId
        const cell = cellsById.get(top.cell_id)
        // The batch endpoint returns scores only; ask for this one cell again to get its factors.
        const contributors = top.shap_contributors.length
          ? top.shap_contributors
          : await (mapMode === 'what-if' ? api.predict(top.cell_id, predictionOverrides, controller.signal) : api.predictLive(top.cell_id, controller.signal)).then((p) => p.shap_contributors).catch(() => [])
        if (controller.signal.aborted) return
        const factors = contributors
          .filter((item) => item.direction === 'raises')
          .slice(0, 3)
          .map((item) => item.label || item.feature)
        if (top.active_incident_count > 0) {
          factors.unshift(`${top.active_incident_count} active incident report${top.active_incident_count === 1 ? '' : 's'}`)
        }
        const title = inCell ? 'Caution: you are in a high-risk zone' : 'Caution: high-risk zone nearby'
        const body = factors.join(', ') || 'Current model factors unavailable.'
        const simulated = driver.source === 'simulated'
        notify(body, {
          variant: 'alert',
          title,
          score: top.live_risk_score,
          location: [cell?.street_name, cell?.locality].filter(Boolean).join(', ') || `Cell ${top.cell_id}`,
          simulated,
          actionLabel: 'View this cell',
          onAction: () => selectCellRef.current(top.cell_id),
        })
        // A system notification only when the tab is in the background; the toast covers the foreground.
        if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
          try {
            new Notification(`WAYMARK: ${title}`, {
              body: `${simulated ? '[Simulated location] ' : ''}Live risk ${Math.round(top.live_risk_score)}/100. ${body}`,
              tag: `waymark-${top.cell_id}`,
            })
          } catch {
            /* some mobile browsers only allow notifications from a service worker */
          }
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setMonitor({ kind: 'error' })
      })
    return () => controller.abort()
  }, [locationAlertsEnabled, locationCellId, predictionOverrides, mapMode, incidentRefreshKey, recheckTick, cells.length, cellsById, driver.source, notify])

  const toggleLocationAlerts = () => {
    if (locationAlertsEnabled) {
      stopLocationAlerts()
      return
    }
    if (!driver.simActive && !('geolocation' in navigator)) {
      notify('This browser does not support location based safety alerts.')
      return
    }
    // Asked here because browsers only show the permission prompt in response to a click.
    if ('Notification' in window && Notification.permission === 'default') {
      void Notification.requestPermission().catch(() => undefined)
    }
    setLocationAlertsEnabled(true)
  }
  const placeSimLocation = (lat: number, lng: number) => {
    driver.setSimPosition({ lat, lng })
    setPickSimMode(false)
    alertedCells.current.clear()
  }

  const openLeftPane = useCallback(() => {
    setLeftPaneOpen(true)
    if (mediaMatches(NARROW)) setRightPaneOpen(false)
  }, [])
  const collapseLeftPane = useCallback(() => {
    setLeftPaneOpen(false)
    setRightPaneOpen(true)
  }, [])
  const showDetails = useCallback(() => {
    setRightPaneOpen(true)
    if (mediaMatches(NARROW)) setLeftPaneOpen(false)
  }, [])
  const detailsVisible = !!selectedId && rightPaneOpen
  // Keep Leaflet's zoom buttons and attribution clear of the details pane / sheet.
  const overlayVars = {
    '--wm-right': detailsVisible ? '27.25rem' : '0px',
    '--wm-bottom': detailsVisible && !leftPaneOpen ? 'calc(min(58dvh, 560px) + 0.5rem)' : '0px',
    '--wm-zoom-gutter': '3rem',
  } as CSSProperties

  const searchPlaces = useCallback(
    (query: string, prefix = false, signal?: AbortSignal) => api.places(query, signal, prefix),
    [],
  )
  const selectPlace = (place: PlaceSearchItem) => {
    setRegion(null)
    setFilters(DEFAULT_FILTERS)
    setScenario(NO_SCENARIO)
    setDemoStep(null)
    setPlaceSearch(place)
    if (selectedId) selectCell(null, false)
  }

  return (
    <div className="wm-map relative h-[calc(100dvh-4rem)] min-h-[320px] w-full overflow-hidden" style={overlayVars} role="region" aria-label="Map of scored cells in Houston">
      <p className="sr-only">
        Map markers cannot be reached with the keyboard. Use the locality search field, or the Audit list page, to open
        a cell.
      </p>

      <MapContainer
        center={HOUSTON}
        zoom={10}
        preferCanvas
        zoomControl={false}
        className="absolute inset-0 z-0"
        scrollWheelZoom
        maxBoundsViscosity={1}
      >
        <TileLayer
          key={base.name}
          url={base.url}
          attribution={base.attribution}
          maxZoom={base.maxZoom}
          noWrap
          bounds={[
            [-85.05, -180],
            [85.05, 180],
          ]}
        />
        <WorldLimits />
        <ZoomControl position="bottomright" />
        <FitToBounds
          bounds={placeSearch ? [[placeSearch.south, placeSearch.west], [placeSearch.north, placeSearch.east]] : null}
        />
        {/* Live inference updates cell polygons; historical crash points remain their own independent layer. */}
        <HexLayer
          cells={displayedCells}
          highlightIds={highlightIds}
          fillOpacity={opacity}
          forceNative={!!region}
          onPickRegion={(id, res, point) => {
            if (pickSimMode && point) { placeSimLocation(point[0], point[1]); return }
            if (pickIncidentMode && point) { setIncidentPoint(point); setPickIncidentMode(false); return }
            setPlaceSearch(null)
            setRegion({ id, res })
            selectCell(null, false)
          }}
          valueFor={valueFor}
          scenarioMode={!!whatIf}
          tooltipFor={tooltipFor}
          selectedId={selectedId}
          selected={selectedPos}
          pulseEvent={pulseEvent}
          onSelect={(id, point) => {
            if (pickSimMode && point) { placeSimLocation(point[0], point[1]); return }
            if (pickIncidentMode && point) { setIncidentPoint(point); setPickIncidentMode(false); return }
            selectCell(id)
          }}
          onLevel={setLevel}
          centerPoints={crashesAvailable === false}
        />
        <CrashLayer enabled={!level.grouped} onAvailable={setCrashesAvailable} cellIds={placeIds ?? highlightIds} satellite={base.name === "Satellite"} />
        {trafficLayerOn && trafficQ.data && <TrafficLayer data={trafficQ.data} />}
        <IncidentLayer enabled={true} pickMode={pickIncidentMode} refreshKey={incidentLayerRefreshKey}
          onPick={(lat, lng) => { if (pickIncidentMode) { setIncidentPoint([lat, lng]); setPickIncidentMode(false) } }} />
        <DriverLayer position={driver.position} source={driver.source} pickMode={pickSimMode} onPick={placeSimLocation} />
        <FlyController target={flyTarget} />
        <FitOnce cells={cells} enabled={fitEnabled} />
      </MapContainer>

      {/*
        Overlay layout. Three columns that can never overlap each other:
        left = filters and legend, centre = map toolbar and status chips, right = cell details.
        Below md the side columns become sheets over the map.
      */}
      <div className="pointer-events-none absolute inset-0 z-[1000] flex gap-3 p-2 md:p-4">
        {leftPaneOpen && (
          <aside
            aria-label="Map filters and legend"
            className="wm-scroll pointer-events-auto flex flex-col gap-3 overflow-y-auto overscroll-contain max-md:absolute max-md:inset-2 max-md:z-30 max-md:rounded-2xl max-md:bg-ivory-100 max-md:p-2 max-md:shadow-card md:w-[22rem] md:shrink-0 md:pb-1"
          >
            <div className="shrink-0 rounded-xl border border-navy/10 bg-white/95 p-2 shadow-card">
              <div className="grid grid-cols-3 gap-1" role="group" aria-label="Map scoring mode">
                {(['baseline', 'live', 'what-if'] as const).map((mode) => (
                  <button key={mode} type="button" aria-pressed={mapMode === mode}
                    onClick={() => { setMapMode(mode); if (mode !== 'what-if') setScenario(NO_SCENARIO) }}
                    className={`min-h-9 rounded-lg px-2 text-xs font-bold ${mapMode === mode ? 'bg-navy text-ivory' : 'bg-ivory-200 text-navy hover:bg-ivory'}`}>
                    {mode === 'what-if' ? 'What-if' : mode === 'live' ? 'Live' : 'Baseline'}
                  </button>
                ))}
              </div>
              {mapMode === 'live' && (
                <div className="mt-2 space-y-1.5">
                  <LiveContextChip context={contextQ.data} loading={contextQ.loading} mapLoading={liveBatchLoading} error={liveBatchError} />
                  <p className="px-1 text-[11px] font-bold text-navy/75">
                    {modelHealthQ.data?.traffic_lift_applied
                      ? `Experimental traffic effect applied in Live mode · ${modelHealthQ.data.traffic_lift_verdict ?? 'validated'}`
                      : modelHealthQ.data?.traffic_lift_loaded
                        ? `Traffic effect ${modelHealthQ.data.traffic_lift_verdict ?? 'not validated'}; not applied (experimental flag off or validation not passed)`
                        : 'Traffic context only; no historical traffic lift is loaded'}
                  </p>
                </div>
              )}
              <details className="mt-1 text-xs text-navy/70"><summary className="min-h-7 cursor-pointer font-semibold">How live context works</summary>
                <p className="pb-1">Live mode shows the latest cached Houston-centre weather conditions and per-cell reasons. Weather remains context only. An experimental traffic effect can adjust Live scores only when a historical artifact passes its held-out validation and the server flag is enabled. Live provider readings are not used as training history or persisted. Rain associations mean historically more crashes per hour in rain here; they do not show that rain causes crashes. Readings are city-wide context, not street-level measurements. An unavailable live inference is shown as unavailable.</p>
              </details>
            </div>
            <div className="shrink-0">
              <FilterCard
                onCollapse={collapseLeftPane}
                summary={summaryQ.data}
                filters={filters}
                onChange={setFilters}
                maxPastLimit={maxPastLimit}
                shown={displayedCells.length}
                total={cells.length}
                onPlaceSearch={searchPlaces}
                onPlaceSelect={selectPlace}
              />
            </div>
            <div className="shrink-0">
              <WhatIfCard
                scenario={scenario}
                onChange={(next) => { setScenario(next); setMapMode(next.night || next.rain || next.lowVis ? 'what-if' : 'baseline') }}
                loading={whatIfQ.loading}
                error={whatIfQ.error}
                meanAll={scenarioStats.all}
                meanEmerging={scenarioStats.emerging}
                active={scenarioActive}
              />
            </div>
            {!selectedId && (
              <div className="shrink-0">
                <RegionCard target={reportTarget} />
              </div>
            )}
            <div className="shrink-0">
              <Legend
                scenarioMode={!!whatIf}
                colorMode={colorMode}
                onColorMode={setColorMode}
                opacity={opacity}
                onOpacity={setOpacity}
                placeholderWeights={summaryQ.data?.placeholder_weights ?? true}
                creditCap={summaryQ.data?.credit_cap ?? null}
              />
            </div>
          </aside>
        )}

        {/* Centre column: toolbar on top, status at the bottom. Only the controls themselves take clicks. */}
        <div className="relative flex min-w-0 flex-1 flex-col justify-between gap-2">
          <div className="flex flex-wrap items-start gap-2 *:pointer-events-auto">
            {!leftPaneOpen && (
              <button
                type="button"
                onClick={openLeftPane}
                aria-expanded={false}
                aria-label="Show map filters and legend"
                title="Filters and legend"
                className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-navy px-3 py-2 text-sm font-semibold text-ivory shadow-card hover:bg-navy-800"
              >
                <SlidersHorizontal aria-hidden="true" className="h-4 w-4" /> Filters
              </button>
            )}
            {region ? (
              <button
                type="button"
                onClick={() => setRegion(null)}
                className="inline-flex min-h-10 max-w-full items-center truncate rounded-full bg-brass px-3 py-1.5 text-xs font-semibold text-navy shadow-card hover:brightness-95 sm:text-sm"
              >
                <span className="truncate">{regionCells.length} cells in this region</span><span className="ml-1.5 underline">Show all</span>
              </button>
            ) : placeSearch ? (
              <button
                type="button"
                onClick={() => setPlaceSearch(null)}
                title={`${placeSearch.name}, ${displayedCells.length} cells, Clear`}
                className="inline-flex min-h-10 max-w-full items-center rounded-full bg-brass px-3 py-1.5 text-xs font-semibold text-navy shadow-card hover:brightness-95 sm:text-sm"
              >
                <span className="truncate">{placeSearch.name}, {displayedCells.length} cells</span><span className="ml-1.5 underline">Clear</span>
              </button>
            ) : null}
            <div className="ml-auto flex max-w-full flex-wrap items-start justify-end gap-2 *:pointer-events-auto">
              <BaseMapSwitch names={BASE_LAYERS.map((l) => l.name)} value={base.name} onChange={pickBase} />
              <TrafficControl data={trafficQ.data} loading={trafficQ.loading} on={trafficLayerOn} onToggle={() => setTrafficLayerOn((on) => !on)} affectsScores={modelHealthQ.data?.traffic_lift_applied ?? false} />
              <DriverModeControl
                enabled={locationAlertsEnabled}
                onToggle={toggleLocationAlerts}
                status={driver.status}
                source={driver.source}
                monitor={monitor}
                simAllowed={driver.simAllowed}
                simActive={driver.simActive}
                pickingSim={pickSimMode}
                onPickSim={() => { setPickIncidentMode(false); setPickSimMode((v) => !v) }}
                onClearSim={() => { driver.setSimPosition(null); setPickSimMode(false); setMonitor({ kind: 'idle' }); alertedCells.current.clear() }}
              />
              {isTrafficPolice && <button type="button" onClick={() => { setPickSimMode(false); setPickIncidentMode((v) => !v) }} aria-pressed={pickIncidentMode}
                aria-label={pickIncidentMode ? 'Cancel incident location selection' : 'Report a crash or near miss'}
                className={`inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold shadow-card md:min-h-9 sm:text-sm ${pickIncidentMode ? 'bg-brass text-navy' : 'bg-navy text-ivory hover:bg-navy-800'}`}>
                <MapPin aria-hidden="true" className="h-3.5 w-3.5" /> {pickIncidentMode ? 'Cancel report' : 'Report incident'}
              </button>}
              {selectedId && !rightPaneOpen && (
                <button type="button" onClick={showDetails}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-xs font-semibold text-navy shadow-card ring-1 ring-navy/10 hover:bg-ivory sm:text-sm">
                  <PanelRightOpen aria-hidden="true" className="h-4 w-4" /> Cell details
                </button>
              )}
            </div>
          </div>

          {/* Loading / error / empty states sit in the middle of the visible map. */}
          {cellsQ.loading && !cellsQ.data && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-xl border border-navy/10 bg-white/95 px-4 py-2 shadow-card" role="status" aria-live="polite">
              <span className="inline-flex items-center gap-2 whitespace-nowrap text-sm font-semibold text-navy"><span aria-hidden="true" className="h-2.5 w-2.5 animate-pulse rounded-full bg-brass" />Loading map cells…</span>
            </div>
          )}
          {cellsQ.error && (
            <div className="pointer-events-auto absolute inset-x-0 top-1/3 mx-auto w-[min(40rem,100%)]">
              <ErrorBanner message={cellsQ.error} onRetry={cellsQ.reload} />
            </div>
          )}
          {!cellsQ.loading && !cellsQ.error && displayedCells.length === 0 && (
            <div className="pointer-events-auto absolute left-1/2 top-1/2 w-[min(26rem,100%)] -translate-x-1/2 -translate-y-1/2">
              <Card className="p-5 text-center">
                <p className="font-serif text-xl font-bold">
                  {cells.length === 0 ? 'No cells in the database yet' : placeSearch ? `No scored cells found in ${placeSearch.name}` : 'No cells match these filters'}
                </p>
                <p className="mt-1 text-navy/75">
                  {cells.length === 0
                    ? 'This backend has no scored cells loaded. Import the project dataset into its database, then reload this page.'
                    : placeSearch ? 'Try another locality or clear the search.' : 'Loosen a filter to see cells again.'}
                </p>
                {cells.length > 0 && !placeSearch && (
                  <button
                    type="button"
                    onClick={() => setFilters(DEFAULT_FILTERS)}
                    className="mt-3 rounded-lg bg-navy px-4 py-2 font-semibold text-ivory hover:bg-navy-800"
                  >
                    Reset filters
                  </button>
                )}
              </Card>
            </div>
          )}

          <div className="flex flex-col items-start gap-2 *:pointer-events-auto" style={{ marginRight: 'var(--wm-zoom-gutter, 0px)' }}>
            {pickSimMode && <p role="status" className="rounded-full bg-brass px-3 py-2 text-xs font-semibold text-navy shadow-card">Tap the map to set the simulated test location</p>}
            {pickIncidentMode && <p role="status" className="rounded-full bg-navy px-3 py-2 text-xs font-semibold text-ivory shadow-card">Tap the map to place the report</p>}
            {demoStep !== null && (
              <div className="w-full max-w-xl">
                <DemoTour
                  step={demoStep}
                  detail={demoDetail}
                  loading={!demoDetail && detailQ.loading}
                  error={detailQ.error}
                  onPrev={() => goStep(Math.max(0, demoStep - 1))}
                  onNext={() => goStep(Math.min(DEMO_CELLS.length - 1, demoStep + 1))}
                  onExit={() => setDemoStep(null)}
                />
              </div>
            )}
            {!(selectedId && rightPaneOpen) && <ActivityTicker event={tickerEvent} cellsById={cellsById} onSelect={selectCell} />}
            <div className="flex max-w-full flex-wrap items-center gap-2">
              <div role="status" aria-live="polite" className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-white/95 px-3 py-2 text-[11px] font-semibold text-navy shadow-card">
                <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${liveStream.state === 'live' ? 'bg-teal' : liveStream.state === 'offline' ? 'bg-brick' : 'bg-brass'}`} />
                <span className="truncate">
                  {liveStream.state === 'live' ? 'Live updates' : liveStream.state === 'offline'
                    ? `Offline: scores from ${snapshotScoredAt ? new Date(snapshotScoredAt).toLocaleTimeString() : 'last snapshot'}`
                    : liveStream.state === 'reconnecting' ? 'Reconnecting to live updates' : 'Connecting to live updates'}
                </span>
              </div>
              <div className="inline-flex max-w-full items-center rounded-full bg-navy/90 px-3 py-2 text-[11px] font-semibold text-ivory shadow-card max-sm:hidden">
                <span className="truncate">
                  {level.points
                    ? crashesAvailable === false ? 'Single cells (crash points not loaded)' : 'Cells and crash points'
                    : level.grouped ? 'Grouped cells: zoom in to open' : 'Single cells: zoom in for crashes'}
                </span>
              </div>
            </div>
          </div>
        </div>

        {selectedId && rightPaneOpen && (
          <div className="pointer-events-none flex flex-col gap-3 max-md:absolute max-md:inset-x-2 max-md:bottom-2 max-md:z-20 max-md:h-[min(58dvh,560px)] md:w-[26rem] md:shrink-0">
            {reportTarget && (
              <div className="pointer-events-auto shrink-0">
                <RegionCard target={reportTarget} compact />
              </div>
            )}
            <div className="pointer-events-auto min-h-0 flex-1">
              <DetailPanel
                key={selectedId}
                cellId={selectedId}
                detail={detail}
                loading={detailQ.loading}
                error={detailQ.error}
                onRetry={detailQ.reload}
                onClose={() => selectCell(null)}
                onHide={() => setRightPaneOpen(false)}
                scenario={scenarioActive && selectedPrediction ? {
                  baseline: detail?.baseline_prob ?? null,
                  scenario: selectedPrediction.risk_probability,
                } : null}
                scenarioLabel={scenarioLabel}
                livePrediction={selectedPrediction}
                livePredictionLoading={selectedPredictionQ.loading}
                livePredictionError={selectedPredictionQ.error}
                scoreMode={mapMode}
                conditionLiftApplied={modelHealthQ.data?.condition_lift_applied ?? false}
              />
            </div>
          </div>
        )}
      </div>

      {incidentPoint && <IncidentReportDialog point={incidentPoint} onClose={() => setIncidentPoint(null)} onSubmit={async (body) => {
        let cellId: string
        let mapPrediction: LiveMapPrediction | null = null
        try {
          const response = await opsApi.createLiveIncident(body)
          cellId = response.incident.cell_id
          let prediction = response.prediction
          if (scenarioActive) {
            try { prediction = await api.predict(cellId, predictionOverrides) } catch { /* Keep the successful incident response if scenario refresh fails. */ }
          }
          mapPrediction = liveMapPrediction(prediction)
        } catch (error) {
          if (!isModelInferenceFailure(error)) throw error

          // Preserve report submission when model inference is unavailable without inventing a score contribution.
          const report = await opsApi.createIncident(body)
          cellId = report.cell_id
          notify('Report saved. Its score effect will appear once live scoring is back.')
        }
        if (mapPrediction) {
          setIncidentPredictions((current) => new Map(current).set(cellId, mapPrediction!))
        }
        setIncidentLayerRefreshKey((n) => n + 1)
        if (selectedId === cellId) setIncidentRefreshKey((n) => n + 1)
        cellsQ.reload()
        if (selectedId) detailQ.reload()
        setColorMode('score')
      }} />}

      <Toast toast={toast} onClose={closeToast} />
    </div>
  )
}
