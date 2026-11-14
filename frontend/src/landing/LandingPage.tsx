import { Link } from 'react-router-dom'
import { Nav } from '../components/Nav'
import { CellPlot, EMERGING_FILL } from './components/CellPlot'
import { ALL_CELLS, METRICS, SUGGESTED_ACTIONS, TOUR_STEPS } from './data/engine'
import { SCORE_STOPS, rampColor } from '../lib/ramp'
import { fmtInt, fmtNum, fmtProb, prettyModelFeature } from '../lib/format'

const WRAP = 'mx-auto w-full max-w-6xl px-4 sm:px-6'
const H2 = 'text-3xl font-extrabold leading-tight sm:text-4xl'
const PROSE = 'max-w-[62ch] text-lg leading-relaxed text-navy/80'

const pct = (v: number) => `${(v * 100).toFixed(1)}%`

const lowHistory = METRICS.lowHistory
const BACKTEST = [
  {
    measure: 'Future blackspots found in the top 10% of the ranking',
    history: pct(lowHistory.captureTop10.history),
    model: pct(lowHistory.captureTop10.model),
  },
  {
    measure: 'Share of the top 5% that became blackspots',
    history: pct(lowHistory.precisionTop5.history),
    model: pct(lowHistory.precisionTop5.model),
  },
  { measure: 'Ranking quality (AUC)', history: fmtNum(lowHistory.auc.history), model: fmtNum(lowHistory.auc.model) },
]

const STEPS = [
  {
    title: 'Collect',
    body: `About ${fmtInt(Math.round(METRICS.houstonCrashes / 1000) * 1000)} Houston crash records from the open US Accidents dataset, cleaned and de-duplicated.`,
  },
  {
    title: 'Grid',
    body: 'The city is divided into H3 hexagons of roughly 0.1 km². Each cell gets its crash history, night and rain shares, visibility and nearby road features.',
  },
  {
    title: 'Score',
    body: `A LightGBM model is trained on data ${METRICS.trainWindow} and tested on ${METRICS.testWindow}, next to a ranking that uses past crash counts alone.`,
  },
  {
    title: 'Explain',
    body: 'SHAP values show which factors pushed each score up or down. Every cell carries a confidence label, and thin history is always labelled Low.',
  },
  {
    title: 'Review',
    body: 'Road Authorities review the map, audit list and suggested actions, then decide which safety measures to plan. Field teams deliver assigned work. WAYMARK supports their decisions; it never decides for them.',
  },
]

const BENEFITS = [
  {
    title: 'People on the street',
    body: 'An audit that happens before the third crash, in places where people walk and cycle, is prevention. One that happens after is a report.',
  },
  {
    title: 'Audit budgets',
    body: 'Audit hours are limited. A ranking that also looks beyond crash counts sends crews where a visit is more likely to change something.',
  },
  {
    title: 'Streets themselves',
    body: 'The usual fixes are lighting, crossings, drainage and calmer corridors. The same changes make walking and cycling workable.',
  },
]

const example = ALL_CELLS.find((c) => c.id === TOUR_STEPS[0].cellId) ?? ALL_CELLS.find((c) => c.factors.length > 0)

function ExampleCell() {
  if (!example) return null
  const max = Math.max(...example.factors.map((f) => Math.abs(f.value)), 0.001)
  return (
    <div className="border border-navy/15 bg-white">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-navy/15 px-5 py-4">
        <p className="font-mono text-sm text-navy/70">{example.id}</p>
        <Link to={`/map?cell=${example.id}`} className="text-sm font-semibold text-sign underline underline-offset-4 hover:text-sign-700">
          Open this cell on the map
        </Link>
      </div>
      <dl className="grid grid-cols-2 gap-px bg-navy/15 sm:grid-cols-4">
        {[
          ['Past crashes', fmtInt(example.pastCrashes)],
          ['Blackspot likelihood', fmtProb(example.baseline)],
          ['Rank among similar history', example.percentile >= 0.999 ? 'Top 0.1%' : `Top ${((1 - example.percentile) * 100).toFixed(1)}%`],
          ['Confidence', example.confidence],
        ].map(([label, value]) => (
          <div key={label} className="bg-white px-5 py-4">
            <dt className="text-sm text-navy/65">{label}</dt>
            <dd className="mt-1 text-2xl font-extrabold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="px-5 py-5">
        <p className="font-semibold">What moved the score</p>
        <ul className="mt-3 space-y-2.5">
          {example.factors.map((f) => {
            const raises = f.value >= 0
            return (
              <li key={f.name} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_4.5rem] items-center gap-3 text-sm">
                <span>{prettyModelFeature(f.name)}</span>
                <span className="relative h-3 bg-navy/[0.06]" aria-hidden="true">
                  <span className="absolute inset-y-0 left-1/2 w-px bg-navy/30" />
                  <span
                    className={`absolute inset-y-0 ${raises ? 'left-1/2 bg-brick' : 'right-1/2 bg-steel'}`}
                    style={{ width: `${(Math.abs(f.value) / max) * 50}%` }}
                  />
                </span>
                <span className="text-right tabular-nums text-navy/70">{raises ? 'raises' : 'lowers'}</span>
              </li>
            )
          })}
        </ul>
        <p className="mt-4 text-sm leading-relaxed text-navy/65">
          Almost no crashes here, which pulls the score down. Heavy crash counts in the cells around it push it back up.
          That combination is what the model calls emerging risk. Factors describe the model's reasoning, not causes.
        </p>
      </div>
    </div>
  )
}

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col bg-ivory">
      <a className="skip-link" href="#main">Skip to content</a>
      <Nav />
      <main id="main">
        <section className={`${WRAP} grid items-center gap-x-12 gap-y-10 pb-16 pt-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] lg:pb-24 lg:pt-20`}>
          <div>
            <h1 className="text-[clamp(2.6rem,5.4vw,4.6rem)] font-extrabold leading-[1.02] tracking-[-0.03em]">
              Find road risk before the crashes pile up.
            </h1>
            <p className={`mt-6 ${PROSE}`}>
              WAYMARK scores every 0.1 km² of Houston for the chance it becomes a crash blackspot, shows the reasons
              behind each score, and points audit teams to places that crash counts alone would miss.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-3">
              <Link
                to="/map"
                className="inline-flex min-h-12 items-center rounded-md bg-brass px-6 text-base font-bold text-navy hover:bg-brass-soft"
              >
                Open the risk map
              </Link>
              <Link to="/evidence" className="font-semibold text-sign underline underline-offset-4 hover:text-sign-700">
                See how it was tested
              </Link>
            </div>
          </div>
          <figure>
            <CellPlot />
            <figcaption className="mt-4 text-sm leading-relaxed text-navy/70">
              <span className="font-semibold text-navy">Houston as the model scores it.</span> Each mark is one of{' '}
              {fmtInt(METRICS.cellsScored)} cells.
              <span className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5">
                <span className="inline-flex items-center gap-2">
                  <span aria-hidden="true" className="h-3 w-8" style={{ background: `linear-gradient(to right, ${rampColor(SCORE_STOPS, 0.6)}, ${rampColor(SCORE_STOPS, 1)})` }} />
                  Highest-scoring tenth
                </span>
                <span className="inline-flex items-center gap-2">
                  <span aria-hidden="true" className="h-3 w-3 border border-navy" style={{ background: EMERGING_FILL }} />
                  {fmtInt(METRICS.emergingRiskCells)} emerging-risk cells
                </span>
                <span className="inline-flex items-center gap-2">
                  <span aria-hidden="true" className="h-3 w-3 bg-navy/25" />
                  Everything else
                </span>
              </span>
            </figcaption>
          </figure>
        </section>

        <section id="problem" className="scroll-mt-20 border-t border-navy/15 bg-white">
          <div className={`${WRAP} grid gap-x-12 gap-y-10 py-16 lg:grid-cols-2 lg:py-24`}>
            <div>
              <h2 className={H2}>Crash counts find yesterday's blackspots.</h2>
              <div className={`mt-5 space-y-4 ${PROSE}`}>
                <p>
                  A location is called a blackspot after {METRICS.blackspotThreshold} or more crashes in a year. Rank
                  streets by past crashes and the same corridors come up every time, shown in red on the plot.
                </p>
                <p>
                  {fmtInt(METRICS.lowHistoryCells)} of the {fmtInt(METRICS.cellsScored)} cells, about{' '}
                  {Math.round(METRICS.lowHistoryShare * 100)}%, have {METRICS.blackspotThreshold - 1} or fewer recorded
                  crashes. A history ranking puts all of them at the bottom. Little history is not the same as low risk.
                </p>
              </div>
            </div>
            <div>
              <table className="w-full border-collapse text-left">
                <caption className="mb-3 text-left font-semibold">
                  Cells with little crash history, tested on {METRICS.testWindow}
                </caption>
                <thead>
                  <tr className="border-b-2 border-navy text-sm text-navy/70">
                    <th scope="col" className="py-2 pr-4 font-semibold">Measure</th>
                    <th scope="col" className="py-2 pr-4 text-right font-semibold">Crash counts alone</th>
                    <th scope="col" className="py-2 text-right font-semibold">WAYMARK</th>
                  </tr>
                </thead>
                <tbody>
                  {BACKTEST.map((row) => (
                    <tr key={row.measure} className="border-b border-navy/15">
                      <th scope="row" className="py-3 pr-4 font-normal">{row.measure}</th>
                      <td className="py-3 pr-4 text-right tabular-nums text-navy/70">{row.history}</td>
                      <td className="py-3 text-right text-lg font-extrabold tabular-nums">{row.model}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-4 text-sm leading-relaxed text-navy/70">
                Across all cells the two rankings tie: AUC {fmtNum(METRICS.overall.aucHistory)} for crash counts and{' '}
                {fmtNum(METRICS.overall.aucModel)} for the model. The gain is only where history is thin, and the
                absolute numbers there are still modest. {fmtInt(METRICS.lowHistoryBlackspots)} low-history cells became
                blackspots in the test year.
              </p>
            </div>
          </div>
        </section>

        <section id="how" className="scroll-mt-20 border-t border-navy/15">
          <div className={`${WRAP} py-16 lg:py-24`}>
            <h2 className={H2}>How a score is made</h2>
            <ol className="mt-10 grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-5">
              {STEPS.map((step, i) => (
                <li key={step.title} className="border-t-4 border-navy pt-4">
                  <p className="text-sm font-bold tabular-nums text-navy/60">Step {i + 1}</p>
                  <h3 className="mt-1 text-xl font-extrabold">{step.title}</h3>
                  <p className="mt-2 leading-relaxed text-navy/75">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="example" className="scroll-mt-20 border-t border-navy/15 bg-white">
          <div className={`${WRAP} grid gap-x-12 gap-y-10 py-16 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:py-24`}>
            <div>
              <h2 className={H2}>What a flagged cell looks like</h2>
              <p className={`mt-5 ${PROSE}`}>
                This is a real cell from the model output: one recorded crash, low confidence, and a score far above
                other cells with the same history. It is a suggestion to look, with the reasons attached.
              </p>
              <p className="mt-6 font-semibold">Actions the tool suggests for review</p>
              <ul className="mt-2 list-disc space-y-1.5 pl-5 text-navy/80">
                {SUGGESTED_ACTIONS.map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </div>
            <ExampleCell />
          </div>
        </section>

        <section id="about" className="scroll-mt-20 border-t border-navy/15">
          <div className={`${WRAP} grid gap-x-12 gap-y-10 py-16 lg:grid-cols-2 lg:items-center lg:py-24`}>
            <div>
              <h2 className={H2}>Who this helps</h2>
              <dl className="mt-6 space-y-6">
                {BENEFITS.map((item) => (
                  <div key={item.title}>
                    <dt className="text-lg font-bold">{item.title}</dt>
                    <dd className="mt-1 max-w-[58ch] leading-relaxed text-navy/75">{item.body}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <video
                className="block aspect-video w-full bg-navy object-contain"
                autoPlay
                muted
                playsInline
                preload="metadata"
                aria-label="WAYMARK overview video"
              >
                <source src="/media/waymark-about.mp4" type="video/mp4" />
                Your browser does not support the video element.
              </video>
            </div>
          </div>
        </section>

        <section className="border-t border-navy/15 bg-navy text-ivory">
          <div className={`${WRAP} grid gap-x-12 gap-y-8 py-14 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-center`}>
            <div>
              <h2 className={H2}>Know the limits before you use it</h2>
              <ul className="mt-5 max-w-[62ch] list-disc space-y-2 pl-5 text-lg leading-relaxed text-ivory/85">
                <li>One city and one main backtest. Results are indicative.</li>
                <li>Scores are estimates from historical records. They do not predict individual crashes.</li>
                <li>What-if conditions are simulations, not forecasts.</li>
                <li>WAYMARK suggests where to look. Road Authorities decide which measures to plan, and field teams deliver assigned work.</li>
              </ul>
            </div>
            <div className="lg:justify-self-end">
              <Link to="/map" className="inline-flex min-h-12 items-center rounded-md bg-brass px-6 text-base font-bold text-navy hover:bg-brass-soft">
                Open the risk map
              </Link>
            </div>
          </div>
          <div className="border-t border-ivory/15">
            <div className={`${WRAP} flex flex-wrap justify-between gap-x-10 gap-y-2 py-6 text-sm text-ivory/70`}>
              <p>Data: US Accidents (2016 to 2023), Sobhan Moosavi et al. Grid: Uber H3. Base map: OpenStreetMap contributors.</p>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}
