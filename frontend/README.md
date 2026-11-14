# WAYMARK Web Application

The `frontend/` directory contains the primary WAYMARK web application. It combines the product landing page with the operational workspace for exploring road-risk estimates, reviewing model evidence, producing reports, and managing road-safety measures.

## Technology

- React 19, TypeScript, Vite, and Tailwind CSS v4
- React Router for client-side navigation
- Leaflet and H3 for geospatial map visualization
- Recharts for analytical charts
- Framer Motion, GSAP, and Lenis for interface motion and scrolling
- Lucide React for interface icons

Dependency versions and commands are defined in `package.json` and `package-lock.json`.

## Local development

Install dependencies from this directory:

```bash
npm install
```

Start the FastAPI backend from the project root in a separate terminal:

```bash
uvicorn backend.main:app --reload
```

Then start Vite from `frontend/`:

```bash
npm run dev
```

The app is served at <http://localhost:5173>. During development, Vite forwards `/api` requests to `http://127.0.0.1:8000`. Override the target with `VITE_PROXY_TARGET` in `.env.local` if needed. API documentation is available from the backend at <http://127.0.0.1:8000/docs>.

## Routes

| Route | Purpose |
| --- | --- |
| `/` | Product landing page with product context, evidence, limitations, and calls to action. |
| `/map` | Interactive H3 risk map, local search, filters, cell details, and what-if scenarios. |
| `/evidence` | Backtest metrics, low-history performance, robustness comparisons, and caveats. |
| `/data-quality` | Dataset validation checks and crash count summaries. |
| `/audit-list` | Searchable, sortable emerging-risk cell list with CSV download. |
| `/about` | Methodology, map interpretation, limitations, and attribution. |
| `/auth` | Sign-in and account creation. |
| `/settings` | Signed-in profile and password settings. |
| `/incident-reports` | Road Authorities planning queue for reviewing User reports. |
| `/region/:regionId` | Region analysis and measure progress workspace, with a floating Assistant chat. |

A cell can be opened directly, for example `/map?cell=89446ca89d7ffff`.

## Map behavior

- Cells are H3 polygons colored by risk score, emerging-risk status, or the selected scenario comparison.
- Zoomed-out views group cells into larger H3 regions; zoomed-in views show individual cells. Crash points appear when available in the database and at a sufficiently close zoom level.
- Filters include confidence, maximum past crash count, and emerging-risk status. Locality search narrows the displayed region.
- The detail panel explains the selected cell's score and displays estimated blackspot likelihood, crash history, confidence, and available model factors.
- Scenario controls compare the baseline against night, rain, and low-visibility input conditions. The panel is labeled as a simulation, not a live forecast.
- The adjusted-risk mode visualizes an overlay based only on verified measures. The current effect weights are placeholders and must be treated as illustrative.
- Progress projection visualizes an estimate based on active measure stages; it is separate from verified adjusted risk and does not change the trained model score.
- Incident activity shows pending and confirmed crash or near-miss reports separately from model risk. Users can report incidents from the map; Road Authorities planning review them.
- Users can opt in to browser location-based proximity alerts for high-risk cells. These are on-device alerts while the site is open, not notifications triggered by other users' reports.
- OpenStreetMap is the default tile provider. MapTiler is optional for alternate street and satellite styles.

## Region workspace

The region page has two tabs and a floating Assistant button:

- **Analysis:** create or open a region report, review summary statistics and hotspot cells, and download PDF or JSON files.
- **Progress:** follow measures planned by Road Authorities through field implementation, evidence submission, and review. Evidence images can be uploaded and viewed in the measure details.
- **Assistant:** open the bottom-right chat button to ask questions, attach reports, and review structured recommendation cards. Expand the compact chat to a larger window; report hand-off opens the chat with the selected report attached.

Authentication and role permissions are enforced by the backend. Existing API role IDs remain `planner`, `engineer`, and `community`, displayed as Road Authorities — planning, Road Authority field teams — delivery, and User. Users can report incidents and use proximity alerts; only the planning role can create or assign regional measures and review incident reports.

## API integration and mock mode

`src/lib/api.ts` and `src/lib/opsApi.ts` centralize API access and response parsing. The main map uses endpoints including:

- `GET /api/summary`, `GET /api/cells`, and `GET /api/cells/{cell_id}`
- `GET /api/crashes`, `GET /api/places`, and `GET /api/regions/resolve`
- `GET /api/whatif`, `GET /api/validation`, `GET /api/metrics`, and `GET /api/audit-list`

The region workspace uses authenticated routes for login, signup, reports, chat sessions, measures, progress, and evidence. The current contract is available at the backend's `/docs` endpoint; `contracts/openapi_stub.json` is a checked-in snapshot.

Set `VITE_USE_MOCKS=1` in `.env.local` to use in-memory fixtures for report, assistant, and measure workflows without the corresponding backend endpoints. The map still needs the API and database. Restart Vite after changing environment variables.

## Environment variables

Copy `.env.example` to `.env.local` for frontend-specific settings. Do not place Groq or Anthropic credentials in frontend variables; those keys must remain in the backend environment.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE` | Empty | Public API origin for a separately hosted production frontend. Leave empty in local development. |
| `VITE_PROXY_TARGET` | `http://127.0.0.1:8000` | Backend origin used by Vite's development and preview proxies. |
| `VITE_TILE_PROVIDER` | `osm` | Base map provider: OpenStreetMap or MapTiler. |
| `VITE_TILE_KEY` | Empty | Browser-visible MapTiler key when MapTiler is enabled. Restrict it by domain. |
| `VITE_TILE_STREET_STYLE` | `streets-v4` | Optional MapTiler street style ID. |
| `VITE_TILE_SATELLITE_STYLE` | `hybrid` | Optional MapTiler satellite style ID. |
| `VITE_USE_MOCKS` | Empty | Set to `1` to use in-memory report, assistant, and action-plan fixtures. |
| `VITE_WAYMARK_APP_URL` | `http://localhost:5173` | WAYMARK application URL used by the standalone `main_page` project. |

The Vite development and preview ports, along with proxy behavior, are configured in `vite.config.ts`.

## Build, preview, and deployment

```bash
npx tsc -b
npm run lint
npm run build
npm run preview
```

`npm run build` type-checks the TypeScript projects and creates the production bundle in `dist/`. `npm run preview` serves that bundle locally on port 4173.

### Render deployment

Create a Render **Static Site** connected to this repository and configure:

| Setting | Value |
| --- | --- |
| Root directory | `frontend` |
| Install command | `npm ci` |
| Build command | `npm run build` |
| Output directory | `dist` |

Set `VITE_API_BASE` in the Render static site's environment variables to the public Render API origin, for example `https://waymark-api.onrender.com`. Do not append `/api`; the API client adds route paths. This value is embedded into the frontend at build time, so redeploy after changing it. Keep backend API keys and JWT secrets out of frontend environment variables.

This app uses client-side routing. Configure a Render rewrite from `/*` to `/index.html` with status `200` to support direct links and refreshes on routes such as `/map` and `/region/:regionId`.

Set the frontend's production origin in the backend service's `WAYMARK_CORS_ORIGINS`. Restrict any browser-visible MapTiler keys to approved domains.

Alternatively, build `dist/` and run the backend from the project root. FastAPI serves `frontend/dist/` when it exists and supports the frontend's client-side routes.

The root [project README](../README.md#deployment) documents the Render API setup, persistent storage requirements, and deployment verification steps.

## Accessibility and interaction

The interface includes a skip link, visible focus states, keyboard-operable controls, and reduced-motion support. The map has keyboard-accessible search and controls; map polygons themselves are not tab stops, so use cell search or the audit list to navigate by identifier. The demo tour can be controlled by keyboard, and Escape closes its overlay or selected detail panel.

## Further reading

See the [project README](../README.md) for backend setup, API and data pipeline details, database safety, roles, and model limitations.
