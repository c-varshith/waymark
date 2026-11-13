import { useEffect, useRef } from 'react'
import { HOUSTON_CELLS, PLOT_HEIGHT, PLOT_WIDTH } from '../data/houstonCells'
import { SCORE_STOPS, rampColor } from '../../lib/ramp'

/** Scores at or above this are drawn in the map's own colour ramp; everything below stays grey. */
export const HIGH_SCORE = 90
export const EMERGING_FILL = '#f5b700'
const INK = '#1e2327'

function hexPath(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number) {
  ctx.beginPath()
  for (let k = 0; k < 6; k++) {
    const a = ((60 * k - 90) * Math.PI) / 180
    const x = cx + r * Math.cos(a)
    const y = cy + r * Math.sin(a)
    if (k === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  }
  ctx.closePath()
}

/**
 * Every scored Houston cell, drawn once from the exported model output. No tiles, no animation:
 * the road network shows up by itself because cells only exist where crashes were recorded.
 */
export function CellPlot({ className = '' }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const draw = () => {
      const width = canvas.clientWidth
      if (!width) return
      const height = (width * PLOT_HEIGHT) / PLOT_WIDTH
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const s = (width / PLOT_WIDTH) * dpr
      const r = Math.max(1.1 * dpr, 2.9 * s)
      ctx.clearRect(0, 0, canvas.width, canvas.height)

      // Three passes so the cells that matter are never painted over: grey, then high scores, then emerging.
      for (let i = 0; i < HOUSTON_CELLS.length; i += 4) {
        const score = HOUSTON_CELLS[i + 2]
        if (score >= HIGH_SCORE || HOUSTON_CELLS[i + 3]) continue
        ctx.fillStyle = `rgba(30, 35, 39, ${0.14 + (score / 100) * 0.22})`
        hexPath(ctx, HOUSTON_CELLS[i] * s, HOUSTON_CELLS[i + 1] * s, r)
        ctx.fill()
      }
      for (let i = 0; i < HOUSTON_CELLS.length; i += 4) {
        const score = HOUSTON_CELLS[i + 2]
        if (score < HIGH_SCORE || HOUSTON_CELLS[i + 3]) continue
        ctx.fillStyle = rampColor(SCORE_STOPS, 0.6 + ((score - HIGH_SCORE) / (100 - HIGH_SCORE)) * 0.4)
        hexPath(ctx, HOUSTON_CELLS[i] * s, HOUSTON_CELLS[i + 1] * s, r)
        ctx.fill()
      }
      ctx.lineWidth = Math.max(1, 0.9 * dpr)
      ctx.strokeStyle = INK
      ctx.fillStyle = EMERGING_FILL
      for (let i = 0; i < HOUSTON_CELLS.length; i += 4) {
        if (!HOUSTON_CELLS[i + 3]) continue
        hexPath(ctx, HOUSTON_CELLS[i] * s, HOUSTON_CELLS[i + 1] * s, r * 1.35)
        ctx.fill()
        ctx.stroke()
      }
    }
    draw()
    const observer = new ResizeObserver(draw)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  return (
    <canvas
      ref={ref}
      role="img"
      aria-label="Plot of all 8,275 scored cells in Houston. Most are grey; the highest-scoring tenth are orange to red along the main corridors; 242 emerging-risk cells are marked in yellow, mostly away from those corridors."
      className={`block w-full ${className}`}
      style={{ aspectRatio: `${PLOT_WIDTH} / ${PLOT_HEIGHT}` }}
    />
  )
}
