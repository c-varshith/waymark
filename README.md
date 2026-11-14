<div align="center">

# WAYMARK

### Predictive safety intelligence for safer streets

Explore road-risk patterns, understand the signals behind each score, and help teams prioritize locations for human review.

[![Live demo](https://img.shields.io/badge/LIVE%20DEMO-Open%20WAYMARK-f28b54?style=for-the-badge)](https://waymark-annb.onrender.com)
[![Source repository](https://img.shields.io/badge/GITHUB-Source%20repository-30343b?style=for-the-badge&logo=github)](https://github.com/c-varshith/waymark)

![React](https://img.shields.io/badge/React-19-149eca?logo=react&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-6-3178c6?logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![LightGBM](https://img.shields.io/badge/ML-LightGBM-4c8c4a?logo=lightgbm&logoColor=white)
![Maps](https://img.shields.io/badge/Maps-Leaflet%20%2B%20H3-356b58?logo=leaflet&logoColor=white)

[Screenshots](#screenshots) · [Features](#features) · [Quick start](#quick-start) · [Architecture](#architecture) · [Model and data](#model-and-data) · [FAQ](#frequently-asked-questions) · [API](#api) · [Responsible use](#limitations-and-responsible-use)

</div>

> **Decision support only.** WAYMARK helps identify places that may merit investigation. Scores are estimates from historical records—not crash forecasts, causal findings, or engineering determinations. People make the final decisions.

## Overview

WAYMARK divides the Houston study area into H3 hexagonal cells of roughly 0.1 km² and assigns each cell a 0–100 relative risk score. The interface brings the map, model evidence, what-if scenarios, incident reports, and human-reviewed safety measures into one workspace.

The project is a predictive safety intelligence layer: it is designed to support analysis alongside existing mapping and transportation tools, rather than replace them.

## Screenshots

<p align="center">
  <img src="frontend/images/main.png" alt="WAYMARK landing page showing Houston road-risk overview" width="49%" />
  <img src="frontend/images/map.png" alt="WAYMARK interactive Houston risk map with cell details" width="49%" />
</p>

| At a glance | Details |
| --- | --- |
| Pilot city | Houston, Texas |
| Spatial grid | H3 resolution 9 |
| Model | Packaged three-model LightGBM ensemble with SHAP explanations |
| Backend | FastAPI, SQLAlchemy, SQLite for local development; PostgreSQL supported |
| Frontend | React, TypeScript, Vite, Leaflet, and H3 |
| Map tiles | OpenStreetMap by default; optional MapTiler styles |
| Assistant | Optional Groq or Anthropic integration, with a rule-based fallback |
| Workspaces | Road Authorities — planning; Road Authority field teams — delivery; User |

## Features

- **Explore risk cells:** browse H3 scores, crash history, confidence, and emerging-risk locations on the interactive map.
- **Understand a score:** inspect SHAP contributors and supporting historical factors for a selected cell.
- **Compare scenarios:** explore night, rain, and low-visibility inputs. These are simulations, not live weather forecasts.
- **Review model evidence:** inspect backtest metrics, robustness comparisons, and data-quality checks with their caveats.
- **Prioritize field work:** search and export the emerging-risk audit list; generate region reports with PDF and JSON downloads.
- **Track safety measures:** Road Authorities review report-backed suggestions, add selected actions to the plan, and monitor delivery; field teams record implementation progress and attach evidence for review. Suggestions can be added from Analysis or AI chat and then appear in Progress. Adjusted scores and progress projections are illustrative overlays; they do not retrain or rerun the model.
- **Report incidents and get alerts:** Users submit crash or near-miss reports and can enable browser-based proximity alerts for nearby high-risk cells. The live inference API returns a separate, capped incident overlay; it does not alter the trained model score.
- **View live context:** Open-Meteo weather and TomTom traffic are fetched and cached server-side. Weather associations are context-only by default. Traffic is a display layer by default; an experimental score adjustment requires a licensed historical archive, a passing held-out validation, a packaged lift artifact, and explicit `WAYMARK_TRAFFIC_LIFT=1` opt-in. Live provider readings are not training history. See [`docs/traffic_lift.md`](docs/traffic_lift.md).
- **Ask questions:** use the optional report-aware assistant for structured suggestions and discussion.

### Workspaces and responsibilities

- **User:** submits crash and near-miss reports and may opt in to browser proximity alerts.
- **Road Authorities — planning:** reviews reports and analysis, chooses suggested safety measures, and tracks the resulting plan.
- **Road Authority field teams — delivery:** updates assigned measure progress and submits evidence for review.

AI-generated actions are recommendations for human review. Adding an action to Progress records it as planned work; it does not mean the work has been approved, completed, or proven effective.

## Quick start

### Requirements

- Python 3.12 (the pinned project runtime; see `.python-version`)
- Node.js 20 or later and npm
- The packaged model files in `backend/ml_artifacts/` and feature store in `out/`

### 1. Set up the backend

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

Copy the root environment example and set a private, stable JWT secret for your local environment:

```bash
cp .env.example .env
```

The local SQLite database is intentionally excluded from Git because it can contain account and operational data. Create a development database from the included exports with:

```bash
python pipeline/load_db.py
```

The command stops if `waymark.db` already exists. Review [`pipeline/load_db.py`](pipeline/load_db.py) and its `--help` output before rebuilding; `--force` deletes the existing database and its operational data.

Start the API from the project root:

```bash
.venv/bin/uvicorn backend.main:app --reload
```

The API runs at <http://127.0.0.1:8000>. Interactive documentation is available at <http://127.0.0.1:8000/docs>.

### 2. Set up the frontend

In a second terminal:

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

Open <http://localhost:5173>. In local development, Vite proxies `/api` requests to the backend at `127.0.0.1:8000`.

The default map uses OpenStreetMap and does not need an API key. To use MapTiler, set `VITE_TILE_PROVIDER=maptiler` and a restricted browser key in `frontend/.env.local`. Keep all assistant and database credentials on the backend.

## Architecture

Training and validation happen offline. One FastAPI service loads the packaged model and cell features into memory, polls external providers into server-side caches, handles reports and measures, and serves the React application through REST and Server-Sent Events (SSE). Provider credentials stay on the server. The browser does not call weather or traffic providers directly.

```mermaid
flowchart LR
    subgraph OFFLINE[Offline data preparation and training]
        CSV[US Accidents CSV<br/>Houston study subset<br/>2016–2023 crash records]
        FE[H3 resolution 9<br/>Crash history and fields recorded at crashes<br/>18 packaged model features<br/>No OSM features in current model]
        TRAIN[Train and backtest<br/>Three LightGBM models<br/>SHAP explainer]
        ART[Runtime package<br/>model.pkl · explainer.pkl<br/>feature_schema.json<br/>H3 cell feature store]
        CSV --> FE --> TRAIN --> ART
    end

    subgraph RENDER[Render · single FastAPI backend service]
        API[REST API<br/>Inference · reports · incidents<br/>measures · evidence · assistant]
        RT[In-memory runtime<br/>Ensemble scoring and SHAP<br/>cell-level incident overlay]
        DB[(SQLite locally<br/>PostgreSQL in deployment)]
        FILES[(Private report and evidence files<br/>local storage or Supabase Storage)]
        STREAM[SSE event stream<br/>cell-level live updates]
        WEATHER[Open-Meteo<br/>background poller + cache]
        TRAFFIC[TomTom Traffic Flow<br/>server-side tile proxy + cache]
        ASSIST[Optional Groq or Anthropic<br/>report-grounded answers<br/>rule-based fallback]
        CONDITIONS[Weather condition context<br/>ranking flag off by default]
        TRAFFIC_LIFT[Experimental traffic lift<br/>Live mode only; off by default<br/>requires validated archive + artifact + opt-in]
        API --> RT
        API <--> DB
        API <--> FILES
        API -->|SSE route| STREAM
        RT -->|score and incident events| STREAM
        WEATHER -->|polled readings| RT
        TRAFFIC -->|polled readings / tile requests| RT
        ASSIST <--> API
        CONDITIONS -. loaded artifact .-> RT
        TRAFFIC_LIFT -. optional artifact + flag .-> RT
    end

    subgraph CLIENT[Render · React static frontend]
        UI[React · Leaflet · H3<br/>Risk map and analysis]
        ROLES[Road Authorities — planning<br/>Road Authority field teams — delivery<br/>User]
        UI --- ROLES
    end

    ART -->|startup load| RT
    API <-->|JSON requests / responses| UI
    STREAM -->|cell-level live events| UI
    UI -->|User incident reports| API
```

The checked-in runtime feature schema contains 18 crash-history and crash-record features. The repository has separate OSM extraction utilities, but the packaged live model does **not** currently use OSM-derived road-network features. Weather associations are loaded for live context, but `WAYMARK_CONDITION_LIFT_APPLIES_TO_RANKING` defaults to `false`. TomTom traffic remains a display layer unless a separately licensed archive passes validation, a lift artifact is packaged, and `WAYMARK_TRAFFIC_LIFT=1` is explicitly set. The experimental traffic lift applies only to Live mode; it never changes Baseline scores. The current repository does not include a traffic-lift artifact, so traffic does not currently adjust scores.

### Repository layout

| Path | Purpose |
| --- | --- |
| `frontend/` | React application, map, landing page, and API client |
| `backend/` | FastAPI routes, inference runtime, authentication, reports, measures, and evidence workflows |
| `backend/ml_artifacts/` | Packaged LightGBM models, SHAP explainer, and feature schema |
| `pipeline/` | Data validation, exports, database loading, and supporting ingestion utilities |
| `blackspot_pipeline.py` | Feature engineering, training, and model evaluation pipeline |
| `out/` | Generated feature store, scores, evaluation outputs, and pipeline exports |
| `supabase/` | SQL schema and versioned database migrations |
| `contracts/` | API contract examples and snapshots |
| `docs/` | Realtime inference changelog and manual verification steps |
| `data/` | Runtime report and evidence files in local development |

For frontend-specific routes and behavior, see [`frontend/README.md`](frontend/README.md).

## Model and data

The packaged three-model LightGBM ensemble was trained using the Houston-area subset of the US Accidents dataset (2016–2023), attributed to Sobhan Moosavi and collaborators. Its 18 inputs describe cell-level crash history and fields recorded on accident rows, such as junction, crossing, visibility, and weather context. OSM extraction and comparison utilities exist in `pipeline/external/`, but OSM features are not in the checked-in runtime schema or current model. Adding OSM features requires rebuilding the feature store, retraining, and evaluating a new model package. The raw source dataset is not included in Git. Review the original dataset's terms and confirm provenance before redistributing or using derived data operationally.

At startup, FastAPI loads the packaged LightGBM ensemble, SHAP explainer, feature schema, and H3 feature store into memory. It averages the three models' positive-class probabilities and maps the result to a relative 0–100 percentile score. The inference API supports single-cell scoring and batch scoring with optional scenario overrides. It does not update model weights during requests.

- `risk_probability` is the model's positive-class probability.
- `risk_score` is the probability's percentile against the saved reference distribution, on a 0–100 scale. It is **not calibrated** and should not be interpreted as a percentage chance of a crash.
- Single-cell inference includes SHAP contributors. Batch inference omits SHAP for speed.
- Scenario overrides change selected model inputs; they are not live weather observations.
- Incident overlays are separate from model predictions: active crash reports add 20 points and near-miss reports add 10, capped at 100. The overlay is process-local and intended for a single API worker; persisted reports rebuild it at startup.

The data and model are Houston-specific. Review the evidence and quality pages before interpreting the map. For the current inference performance results and manual checks, see [`docs/CHANGELOG-realtime.md`](docs/CHANGELOG-realtime.md) and [`docs/manual-checks/phase-1.md`](docs/manual-checks/phase-1.md).

## Frequently asked questions

### Why use the US Accidents dataset?

It provides a large historical set of crash records with locations, timestamps, and related conditions. Those fields support the project's core workflow: grouping crashes into map cells, engineering features, and evaluating scores against later historical outcomes. The project uses the Houston-area subset for its pilot. The dataset is not a complete or authoritative record of every crash; reporting coverage, missing values, and collection practices can affect what it represents. See the [dataset and attribution notes](#model-and-data).

### Why Houston?

Houston is the current pilot area for building and evaluating the full mapping and inference workflow in one place. The model artifacts and cell feature store are Houston-specific. A different city needs suitable local data, new feature generation, and its own evaluation before its scores should be used.

### Why use H3 hexagons?

H3 gives the app consistent cell IDs and a regular grid for aggregating crash history and displaying scores. The current grid uses resolution 9, with cells averaging roughly 0.1 km². The hexagons are an analysis unit; they do not imply that risk is uniform across every street inside a cell.

### Why include simulations?

The rain, night, and low-visibility controls let someone compare how selected model inputs affect a cell's score. They are controlled what-if scenarios, not a forecast or a claim about current conditions. A simulation should be read as “what the model returns with these inputs,” not “what will happen on this road.”

### Why is the score 0–100 instead of a crash probability?

The displayed score ranks a model probability against a saved reference distribution. It is useful for comparing cells in this project, but it is not calibrated as a percentage chance of a crash. The API's `risk_probability` is also a model output, not a validated individual-road forecast. See [Model and data](#model-and-data).

### Why LightGBM and SHAP?

LightGBM is used to score the project's structured, tabular cell features efficiently. SHAP provides a breakdown of which model inputs contributed to a selected cell's output. These contributions describe the model's calculation; they do not establish that a factor caused crashes or that changing it will prevent them.

### Does the current model use OpenStreetMap road features?

No. The repository contains utilities to collect and compare OSM features, but the checked-in model schema contains crash-history and accident-record fields only. OSM-derived road structure would require building a new feature store, retraining the model, and validating the resulting package before it could be used for scoring.

### What does “Live” mode update?

Live mode reads cached Open-Meteo weather context and TomTom traffic data, and reports provider freshness or availability. Weather conditions are explanatory context and do not change the ranking by default. Traffic is a map display layer; its optional experimental score adjustment is off by default and currently has no traffic-lift artifact in this repository. Enabling it requires a licensed historical archive, a passing held-out validation, a packaged artifact, and explicit backend opt-in. User incident reports are stored separately and appear as a decaying overlay; they do not retrain the model. Live provider readings are not saved as training history. See [`docs/traffic_lift.md`](docs/traffic_lift.md).

### Why not train continuously from reports or live traffic?

Changing model weights while people are using the map would make scores harder to reproduce and review. WAYMARK serves packaged model weights; incoming incident reports update a separate temporary overlay. Live traffic readings also cannot be treated as historical training data under the current provider terms review. Any future traffic effect must use a separately licensed archive and pass the documented validation gate.

### Can I use the current model for another city or operational decisions?

Not without additional work. The current feature store and evaluation are specific to Houston and the available historical records. This prototype is decision support for human review, not a substitute for local crash data, engineering judgment, or a safety assessment. See [Limitations and responsible use](#limitations-and-responsible-use).

## API

FastAPI's OpenAPI reference is available at `/docs` when the backend is running. Main route groups include:

| Area | Routes |
| --- | --- |
| Health | `GET /api/health`, `GET /api/health/model` |
| Cells and crash points | `GET /api/cells`, `GET /api/cells/{cell_id}`, `GET /api/crashes` |
| Live inference and context | `POST /api/predict`, `POST /api/predict/batch`, `GET /api/live/context`, `GET /api/stream` (SSE) |
| Scenarios and evidence | `GET /api/whatif`, `GET /api/metrics`, `GET /api/validation` |
| Reports and regions | `/api/reports`, `GET /api/regions/resolve`, `GET /api/places` |
| Incidents | `POST /api/incident`, `/api/incidents` |
| Measures and evidence files | `/api/measures`, `/api/evidence` |
| Assistant | `/api/chat/sessions` and session message routes |

Authenticated routes enforce role-based permissions. The API and schemas define the source of truth for each request and response.

## Configuration

Backend settings can be supplied as environment variables or in the root `.env` file. The main optional production integrations are:

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string. Leave unset for local SQLite development. |
| `WAYMARK_JWT_SECRET` | Stable, private secret for signing authentication tokens. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Private backend access to Supabase Storage. Never expose the service-role key in frontend settings. |
| `GROQ_API_KEY` or `ANTHROPIC_API_KEY` | Optional assistant provider credentials. Without a provider, the app uses its rule-based fallback. |
| `WAYMARK_ML_ARTIFACT_DIR` | Model artifact directory; defaults to `backend/ml_artifacts/`. |
| `WAYMARK_ML_FEATURE_STORE` | H3 feature CSV; defaults to `out/cell_features.csv`. |
| `WAYMARK_DATA_DIR` | Local directory for generated reports and evidence files. |
| `WAYMARK_CONDITION_LIFT_APPLIES_TO_RANKING` | Defaults to `false`; weather-condition associations remain context-only unless explicitly enabled. |
| `WAYMARK_TRAFFIC_API_KEY` | Optional server-side TomTom key for live traffic. Never put it in a `VITE_*` variable. |
| `WAYMARK_TRAFFIC_LIFT` | Defaults to `false`; enables experimental traffic adjustment in Live mode only when a validated artifact is present. |

See [`.env.example`](.env.example) and [`frontend/.env.example`](frontend/.env.example) for the full set of supported values. Do not commit secrets or production environment files.

## Data pipeline

The training and data preparation workflow is separate from online inference:

1. Run `blackspot_pipeline.py` with an authorized source dataset to engineer features, train/evaluate the model, and generate artifacts.
2. Validate and export database-ready outputs with the scripts in `pipeline/`.
3. Load the generated tables into a local database with `python pipeline/load_db.py` when building a fresh development dataset.

Use each script's `--help` output to review its inputs and write behavior. Back up the database and `data/` before performing data migrations or rebuilds. The app's live inference endpoints load fixed model weights and update scenario inputs or the process-local incident overlay. Live weather and traffic are separately polled into server-side caches; they do not become training data. Weather is context-only by default, and traffic is display-only unless the experimental traffic-lift gate is deliberately enabled.

## Deployment

The public demo uses a Render-hosted React frontend and a separate Render FastAPI web service. Supabase PostgreSQL and private Supabase Storage can provide persistent production data and evidence files; local development uses SQLite and local files.

- Deploy the frontend as a Render Static Site with `frontend/` as the root directory and `dist` as the publish directory. Add the SPA rewrite required for direct client-side routes such as `/map` and `/region/...`.
- Deploy the backend from the repository root with `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
- Set `VITE_API_BASE` to the backend's public origin and configure the backend CORS allowlist to include the frontend origin.
- Set the database, storage, and JWT secrets in the backend's hosting environment. Never put server credentials in `VITE_*` variables.
- Apply the appropriate SQL migrations before connecting a production backend. Keep persistent database and file backups.

Refer to the [frontend deployment notes](frontend/README.md#render-deployment) and the SQL files under [`supabase/`](supabase/) before deploying.

## Development checks

Run from the project root:

```bash
python -m pytest
cd frontend
npm run lint
npm run build
```

## Limitations and responsible use

- WAYMARK is a prototype for human decision support, not an automated audit, enforcement, or dispatch system.
- Results are based on one city and one historical dataset. Performance may not transfer to other regions, time periods, or reporting conditions.
- Crash records can contain missing fields, under-reporting, and changes in reporting practices. Review data-quality warnings and confidence information.
- Model associations do not prove causes, and suggested actions do not guarantee a safety improvement.
- Adjusted measure weights are placeholders until reviewed by a qualified road-safety domain owner.
- Assistant responses are suggestions for human review, not engineering, legal, or safety certification.

## Attribution

The crash data is derived from the US Accidents dataset (2016–2023), attributed to Sobhan Moosavi and collaborators. Map imagery may include OpenStreetMap contributors and optional third-party tile providers. Follow the applicable attribution and usage terms when deploying or redistributing the application.
