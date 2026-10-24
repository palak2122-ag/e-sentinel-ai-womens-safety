# e-Sentinel AI

Local-first hackathon MVP combining uploaded-video person detection with an explainable, resettable journey-event demo. It is not an emergency service or a validated personal-safety predictor.

## Requirements and setup

- Python 3.10+ and Node.js/npm.
- Existing project weights `yolov8n.pt` at the project root. The backend lazily loads this file and does not download another model.
- Python packages from `requirements.txt`; frontend packages from `frontend/package.json`.
- SQLite is created at `data/e_sentinel.db` by default. Set `ESENTINEL_DB_PATH` before backend startup to select another absolute path or project-root-relative path.

From the project root, use two PowerShell terminals.

Backend:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open the URL Vite prints (normally `http://localhost:5173`). The Vite `/api` proxy forwards requests to `http://127.0.0.1:8000`. API docs: `http://127.0.0.1:8000/docs`.

If `.venv` does not exist, create it with `py -m venv .venv`, activate it, then run `pip install -r requirements.txt`. Use Python and package pins supported by the host if an existing pin is unavailable. To select the database from PowerShell, set `$env:ESENTINEL_DB_PATH = 'data/e_sentinel.db'` before launching Uvicorn. No `.env` is required. There is no external map, LLM, email, or SMS provider configured, so no API credentials are needed for the demo.

## Complete demo

1. Start the backend and frontend; confirm system status is connected.
2. Upload `data/cabin_test.mp4`, run analysis, and inspect counts, confidence, and the annotated sampled frame.
3. In Journey monitor, simulate a route offset, unexpected stop, and missed check-in. Adjust thresholds and inspect each event's rule in the timeline.
4. Record a simulated passenger check-in and compare the displayed status.
5. Add a trusted contact, inspect the redacted destination, then run its test notification. The response explicitly confirms nothing was sent.
6. Ask the local assistant to summarize events or explain the latest rule.
7. Trigger SOS explicitly; confirm it creates a critical demo event but does not contact anyone. Reset demo events.

See [HACKATHON_DEMO.md](HACKATHON_DEMO.md) for the short judge script.

## Architecture

- `backend/main.py`: FastAPI compatibility endpoints, video upload/inference, contact and monitoring routes.
- `backend/event_engine.py`: timestamped event rules, simulated journey state, redaction, local assistant, test-only notifications.
- `backend/database.py`: SQLite schema/repository with per-operation connections, WAL, busy timeout, and parameterized transactional writes.
- `frontend/src/App.jsx`: dashboard, route illustration, cabin evidence, timeline, trusted contacts, and local assistant.
- `frontend/vite.config.js`: local API proxy.
- `vision/`: standalone experiments; `tests/test_backend.py`: API and event-rule tests.

## API

- `GET /` and `GET /api/health`: service identity and health.
- `GET /api/safety-status`: compatibility response with `risk_level: UNKNOWN`.
- `POST /api/analyze-cabin`: multipart `file`; MP4/AVI/MOV/MKV, at most 100 MiB. Preserves existing response fields and adds frame/confidence/settings summaries, optional annotated `data:image/jpeg;base64,...` preview, and `event_created` when the observed peak changes. Counts remain frame-level detections, never unique identities.
- `GET /api/monitoring/state`: current demo mode, route illustration, simulated check-in, thresholds, and unknown risk state.
- `PUT /api/monitoring/config`: set route offset (50-10,000 m), stop duration (1-120 min), and check-in interval (5-720 min) thresholds.
- `POST /api/monitoring/check-in`: record a simulated check-in.
- `GET /api/events?limit=50`: list recent event records (limit 1-100).
- `GET /api/alerts?limit=50`: list persisted medium/high/critical events.
- `POST /api/events/demo`: create validated `route_deviation`, `unexpected_stop`, `missed_check_in`, `cabin_person_count_change`, or explicitly user-triggered `user_sos` events.
- `DELETE /api/events/demo`: reset demo events and simulated check-in state.
- `GET /api/incidents/summary`: generated in-memory event summary and severity counts.
- `POST /api/assistant/query`: summarize event history or explain the latest rule with the local rules assistant; no external LLM is called.
- `GET /api/contacts`, `POST /api/contacts`, `PUT /api/contacts`: view redacted contacts, add one, or replace the list. Destinations exist in process memory only.
- `POST /api/notifications/test`: exercise test mode. Always reports `delivery_confirmed: false` and `contact_notified: false`.

YOLO settings: `YOLO_CONFIDENCE` and `YOLO_IOU` default to `0.45`; `VIDEO_FRAME_STRIDE` defaults to `1`. Confidence/IoU must be 0-1; stride is at least 1. Higher stride reduces processing but can miss brief appearances.

## Event rules and data sources

Route offset and stop/check-in durations are user-triggered simulation inputs compared with editable session thresholds. Route deviation can only use a valid route/location stream; this MVP has no location provider, so the UI uses a schematic, labelled route and simulation data only. Uploaded YOLO count changes are video observations; detections alone do not imply identity, intent, harassment, assault, or danger. Severity is an urgency label produced by transparent demo rules, not a safety score. SOS is accepted only through explicit user action.

## Privacy, reliability, limitations

- Uploaded video is processed by the local backend. The raw upload is written to an OS temporary file and deleted in request cleanup. The single annotated preview is encoded in memory and returned in the response; it is not retained. Avoid sensitive recordings without consent and authorization.
- Events, thresholds, cabin peak state, and simulated check-in state persist in SQLite. Trusted-contact destinations stay in memory only and disappear on backend restart; API responses show masked destination hints. There is no authentication, production access control, or real alert provider.
- The map is not a map provider and displays no real coordinates. No GPS, routing, vehicle telemetry, or live check-in source is connected.
- Local assistant answers from structured event history/rules. It does not call an LLM or give emergency guidance. No external credentials or `.env.example` are needed.
- Notification test mode does not send email/SMS. The SOS demo does not notify contacts or emergency services. No automated detection can trigger SOS.
- Model errors and video codec differences can affect outputs. False positives and false negatives remain possible. `safety_assessment` remains unknown.

## Validation commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Push-Location frontend; npm run lint; Pop-Location
Push-Location frontend; npm run build; Pop-Location
```

## Database location and backup

The default database file is `data/e_sentinel.db`. `ESENTINEL_DB_PATH` can point to an absolute path or a path relative to the project root. SQLite `-wal` and `-shm` sidecars can exist while the backend is running; database files and sidecars are ignored by Git. Stop the backend before making a simple file-copy backup; use SQLite's online backup API/tool for a live database so WAL data is included.

This database belongs to one local development instance, not a production multi-user service. There is no authentication, encryption-at-rest configuration, managed backup process, or migration framework. Startup uses idempotent table creation and does not drop existing data. Raw video, frames, contacts, and credentials are not stored in SQLite.

## Troubleshooting

- Backend offline: start Uvicorn and open `/api/health`.
- Missing weights: put the supplied `yolov8n.pt` in the repository root; do not commit it.
- Unreadable upload: try a valid supported video under 100 MiB; codecs differ by OS.
- Slow video: set `VIDEO_FRAME_STRIDE=2` or more before launching the backend.
- Frontend dependency issue: run `npm install` in `frontend/`.
