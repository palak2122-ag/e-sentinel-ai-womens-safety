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

- `backend/main.py`: FastAPI compatibility endpoints, video upload/inference, contact, monitoring, and journey routes.
- `backend/event_engine.py`: timestamped journey event rules, simulated state, redaction, and local assistant.
- `backend/journey_engine.py`: validated journeys, browser-location observations, route-distance checks, and GPS-derived stop detection.
- `backend/risk_engine.py`: validated multi-signal scoring, unknown-data handling, escalation safeguards, cancellation, and mock notification provider.
- `backend/database.py`: SQLite schema/repository with per-operation connections, WAL, busy timeout, and parameterized transactional writes.
- `frontend/src/App.jsx`: dashboard, explicit browser-geolocation journey monitoring, cabin evidence, event timeline, trusted contacts, and local assistant.
- `frontend/vite.config.js`: local API proxy.
- `vision/`: standalone experiments; `tests/test_backend.py`: API and event-rule tests.

## API

- `GET /` and `GET /api/health`: service identity and health.
- `GET /api/safety-status`: compatibility response with `risk_level: UNKNOWN`.
- `POST /api/analyze-cabin`: multipart `file`; MP4/AVI/MOV/MKV, at most 100 MiB. Preserves existing response fields and adds frame/confidence/settings summaries, optional annotated `data:image/jpeg;base64,...` preview, and `event_created` when the observed peak changes. Counts remain frame-level detections, never unique identities.
- `GET /api/monitoring/state`: current demo mode, route illustration, simulated check-in, thresholds, and unknown risk state.
- `PUT /api/monitoring/config`: set route offset (50-10,000 m), stop duration (1-120 min), and check-in interval (5-720 min) thresholds.
- `POST /api/monitoring/check-in`: record a simulated check-in.
- `POST /api/journeys`: start a journey with a destination label and initial browser GPS fix; accepts optional planned route waypoints.
- `GET /api/journeys/active` and `GET /api/journeys?limit=20`: retrieve the active journey or recent persisted journey history and observations.
- `POST /api/journeys/{journey_id}/locations`: save a newer GPS fix and evaluate optional route deviation and sustained low movement.
- `POST /api/journeys/{journey_id}/end`: complete the active journey.
- `GET /api/events?limit=50`: list recent event records (limit 1-100).
- `GET /api/alerts?limit=50`: list persisted medium/high/critical events.
- `POST /api/events/demo`: create validated `route_deviation`, `unexpected_stop`, `missed_check_in`, `cabin_person_count_change`, or explicitly user-triggered `user_sos` events.
- `DELETE /api/events/demo`: reset demo events and simulated check-in state.
- `GET /api/incidents/summary`: generated in-memory event summary and severity counts.
- `POST /api/assistant/query`: summarize event history or explain the latest rule with the local rules assistant; no external LLM is called.
- `GET /api/contacts`, `POST /api/contacts`, `PUT /api/contacts`: view redacted contacts, add one, or replace the list. Destinations exist in process memory only.
- `POST /api/notifications/test`: exercise test mode. Always reports `delivery_confirmed: false` and `contact_notified: false`.
- `POST /api/risk/assessments`: submit camera/GPS/vehicle/check-in observations and persist score, reasons, provenance, data quality, recommendation, and escalation state.
- `GET /api/risk/assessments/latest` and `GET /api/risk/assessments?limit=50`: latest assessment and persisted history.
- `PUT /api/risk/config`: configure scoring thresholds and escalation opt-in.
- `GET /api/escalation/history?limit=50`: persisted state transitions and mock notification attempts.
- `POST /api/escalation/process`: explicitly process a pending escalation through the mock provider.
- `POST /api/escalation/cancel`: audit a user cancellation for a pending check-in/escalation.

### Multi-signal risk engine

This transparent rule-based score is a prototype, not a probability of harm, validated model, or certified vehicle safety system. Defaults: GPS deviation of at least 500 m adds 30 points; stop of at least 5 minutes adds 25; reported sudden braking adds 20; unanswered check-in at least 10 minutes overdue adds 35. A camera detection-count change adds at most 8 weak contextual points and is not proof of distress. Each active signal is weighted by reliability, then the sum is capped at 100. Bands: low 0-19, medium 20-44, high 45-69, critical 70-100.

Missing/unknown sources, stale timestamps (default 300 seconds), reliability below 0.25, poor GPS accuracy, or future timestamps are excluded and reported as unknown. Approved route changes do not score. High risk asks for a discreet check-in. Critical risk requires at least two reliable corroborating signals and no more than two unknowns before it can enter `escalation_pending`; otherwise it remains check-in-required. Pending escalation can be cancelled before an explicit `POST /api/escalation/process` action. Escalation is disabled unless both `ESENTINEL_ESCALATION_ENABLED=true` and API policy configuration explicitly enable it. The only included provider is a network-isolated mock; its “delivered” status means simulated delivery, never a real contact notification. Unknown signals are never interpreted as evidence that a passenger is safe.

Risk weights and thresholds are configurable with `PUT /api/risk/config`. To demonstrate the mock escalation path in a local session only:

```powershell
$env:ESENTINEL_ESCALATION_ENABLED = 'true'
```

This flag does not enable SMS, email, police, or emergency-service delivery. No real provider credentials are required or read.

YOLO settings: `YOLO_CONFIDENCE` and `YOLO_IOU` default to `0.45`; `VIDEO_FRAME_STRIDE` defaults to `1`. Confidence/IoU must be 0-1; stride is at least 1. Higher stride reduces processing but can miss brief appearances.

## Event rules and data sources

Demo route offset and stop/check-in durations remain user-triggered simulation inputs. A real journey begins only after the user explicitly starts it and grants browser geolocation permission. Location fixes must be timestamped within the last 120 seconds; older or out-of-order fixes are rejected. Live route deviation is evaluated only when the user supplies at least two planned-route waypoints: fixes must meet the accuracy filter, exceed the configured threshold plus their reported accuracy, and do so for three consecutive updates. Deviation is unavailable when no route is supplied. A prolonged stop is inferred only from consecutive accurate GPS fixes with gaps no longer than 90 seconds and is labelled GPS-derived; whether it is expected is unknown, and it is not vehicle telemetry. Unusual delay/ETA monitoring is unavailable without routing or schedule data. Uploaded YOLO count changes are video observations; detections alone do not imply identity, intent, harassment, assault, or danger. Severity is an urgency label produced by transparent demo rules, not a safety score. SOS is accepted only through explicit user action.

## Privacy, reliability, limitations

- Uploaded video is processed by the local backend. The raw upload is written to an OS temporary file and deleted in request cleanup. The single annotated preview is encoded in memory and returned in the response; it is not retained. Avoid sensitive recordings without consent and authorization.
- Events, thresholds, cabin peak state, check-ins, journeys, journey observations, and risk state persist in local SQLite. A journey stores its destination label, start and latest GPS fix, optional planned route, and observations; it does not retain a breadcrumb history of every fix. Trusted-contact destinations stay in memory only and disappear on backend restart; API responses show masked destination hints. There is no authentication, production access control, or real alert provider.
- Risk assessments, escalation transitions, mock notification attempts, and risk policy persist in SQLite. Real delivery is not implemented, and the risk-engine environment gate defaults off.
- The schematic is not a map provider. Browser GPS is shared with this backend only while a user-started journey is actively monitored; closing/reloading the page stops the browser watcher, and monitoring must be resumed explicitly. A secure browser context is required (`localhost` is allowed). There is no external map, route planner, geocoder, ETA source, vehicle telemetry, or live check-in source. Route deviation requires user-supplied waypoint coordinates.
- Local assistant answers from structured event history/rules. It does not call an LLM or give emergency guidance. No external credentials or `.env.example` are needed.
- Camera person counts/keypoints cannot prove fear, harassment, assault, intent, identity, or safety. Missing camera data is unknown, not safe. The demo GPS/vehicle signals are explicitly simulated; no physical sensors are integrated.
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
