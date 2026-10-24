# Local SQLite Storage

## Location and setup

The backend initializes SQLite during FastAPI lifespan startup. The default file is `data/e_sentinel.db`; set `ESENTINEL_DB_PATH` before starting Uvicorn to choose another absolute or project-root-relative path. The parent directory is created as needed. Initialization is idempotent and never drops tables.

## Schema

- `safety_events`: event ID, constrained type/severity, UTC timestamp, observed summary, source, demo flag, status, explanation, trigger reason, and JSON details. Indexes support newest-first and demo filtering.
- `monitoring_config`: one JSON configuration row keyed by the singleton ID.
- `check_in_history`: simulated check-in status/timestamps, marked demo.
- `app_state`: persisted current check-in and last cabin peak values.

Event reads retain the existing response fields and add `status` when retrieved from SQLite. Alerts are derived from persisted medium/high/critical events. Deleting demo events removes only rows marked `is_demo = 1`; uploaded-video observations are retained. Demo check-in history and current demo-only peak/check-in state are cleared by the same reset action. Rule configuration is not cleared.

Trusted-contact destinations intentionally remain in memory only and are excluded from SQLite. Raw videos, annotated preview frames, secrets, and unnecessary personal information are never stored in the database.

## Connection and transaction behavior

Each repository operation opens and closes its own SQLite connection. WAL mode and an 8-second busy timeout support concurrent local requests. Writes use parameterized SQL in explicit immediate transactions; failures roll back and propagate as a persistence error, mapped by API routes to sanitized service-unavailable responses.

## Backup and operational boundary

The `.db`, `.db-wal`, and `.db-shm` files are ignored by Git. Stop the backend before copying the database file directly. For live backup, use SQLite's online backup API or an equivalent supported tool so uncheckpointed WAL data is preserved. This file is local to a single development instance and is not a production multi-user database service.