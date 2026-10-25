# Local SQLite Storage

## Location and setup

FastAPI initializes SQLite during lifespan startup. The default file is `data/e_sentinel.db`; set `ESENTINEL_DB_PATH` to an absolute or project-root-relative path to override it. The parent directory is created as needed. Schema creation and additive column migrations are repeatable and do not drop tables.

## Schema

- `safety_events`: constrained event type/severity, timestamp, summary, provenance, demo flag, status, explanation, trigger reason, and structured JSON details.
- `monitoring_config` and `risk_policy`: singleton JSON records for event thresholds and risk policy.
- `check_in_history`: simulated check-ins and assessment-linked passenger responses, with source/demo metadata.
- `app_state`: current check-in summary and the last cabin peak used for detector comparisons.
- `journeys`: active/completed journey metadata, destination label, start/current fix, optional user-supplied waypoints, and route/stop detector state. At most one journey may be active.
- `journey_locations`: every accepted timestamped browser GPS fix with coordinates, accuracy, and optional speed.
- `journey_observations`: route-deviation and GPS-derived stop observations with stable IDs and links to risk assessment and safety event records.
- `risk_assessments`: explainable score, reasons, signals, quality, recommendation, provenance, journey ID, incident key, and linked event ID.
- `escalation_history` and `notification_attempts`: state changes and idempotent mock-provider attempts.

`GET /api/events` preserves the event response fields; `GET /api/alerts` derives alerts from persisted event severity. Demo reset deletes demo events, assessments, escalations, notification attempts, and simulated check-in state, but preserves browser-GPS journey observations and linked real-source records. It also preserves any non-demo passenger check-in state and the risk/event threshold configuration.

Trusted-contact destinations remain in memory only. Raw videos and annotated frames are never stored in SQLite. Browser coordinates are sensitive data and are stored only after explicit journey start; ending a journey stops future browser monitoring but retains its persisted history. The application stores every accepted fix locally, so protect or remove the local database according to the user's retention needs.

## Transaction and retry behavior

Each repository operation opens and closes its own SQLite connection. WAL mode, an 8-second busy timeout, parameterized SQL, and explicit transactions support local concurrency. Journey state, a GPS fix, and any new detector observations commit atomically. Risk assessment plus initial escalation state also commit atomically. Event and observation links are persisted afterward; if linking fails, the location endpoint returns an error and an exact retry resumes unlinked observations using deterministic observation/incident IDs. Repeated assessments use the `(journey_id, incident_key)` uniqueness constraint to avoid duplicates.

## Backup and operational boundary

The `.db`, `.db-wal`, and `.db-shm` files are ignored by Git. Stop the backend before copying the database file directly; for live backups, use SQLite's online backup API so WAL data is included. This is a single local development database, not a production multi-user service, and it has no authentication or encryption-at-rest configuration.