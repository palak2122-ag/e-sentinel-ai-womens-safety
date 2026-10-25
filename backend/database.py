"""Small SQLite repository for local event and monitoring state."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Callable, Iterator
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "e_sentinel.db"


class PersistenceError(RuntimeError):
    """Raised when local persistence cannot complete safely."""


class SQLiteRepository:
    def __init__(self, db_path: str | Path | None = None):
        configured_path = db_path or os.getenv("ESENTINEL_DB_PATH")
        if configured_path is None:
            self.db_path = DEFAULT_DB_PATH
        else:
            path = Path(configured_path).expanduser()
            self.db_path = path if path.is_absolute() else PROJECT_ROOT / path
        self._initialization_lock = Lock()
        self._initialized = False

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = None
        try:
            connection = sqlite3.connect(
                str(self.db_path), timeout=8.0, isolation_level=None
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 8000")
            yield connection
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise PersistenceError("Local data could not be saved or read.") from exc
        finally:
            if connection is not None:
                connection.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            connection = None
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(
                    str(self.db_path), timeout=8.0, isolation_level=None
                )
                connection.execute("PRAGMA busy_timeout = 8000")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS safety_events (
                        id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL CHECK (event_type IN (
                            'route_deviation', 'unexpected_stop', 'missed_check_in',
                            'cabin_person_count_change', 'user_sos'
                        )),
                        severity TEXT NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
                        occurred_at TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        source TEXT NOT NULL,
                        is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
                        status TEXT NOT NULL DEFAULT 'open',
                        explanation TEXT NOT NULL,
                        trigger_reason TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS idx_safety_events_occurred_at
                        ON safety_events (occurred_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_safety_events_demo_time
                        ON safety_events (is_demo, occurred_at DESC);
                    CREATE TABLE IF NOT EXISTS monitoring_config (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        config_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS check_in_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
                        assessment_id TEXT
                    );
                    CREATE TABLE IF NOT EXISTS app_state (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS journeys (
                        id TEXT PRIMARY KEY,
                        destination TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('active', 'completed')),
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        start_latitude REAL NOT NULL,
                        start_longitude REAL NOT NULL,
                        start_accuracy_meters REAL NOT NULL,
                        start_observed_at TEXT NOT NULL,
                        current_latitude REAL NOT NULL,
                        current_longitude REAL NOT NULL,
                        current_accuracy_meters REAL NOT NULL,
                        current_observed_at TEXT NOT NULL,
                        current_speed_mps REAL,
                        planned_route_json TEXT NOT NULL,
                        route_deviation_streak INTEGER NOT NULL DEFAULT 0,
                        route_deviation_active INTEGER NOT NULL DEFAULT 0,
                        stationary_since TEXT,
                        stationary_fix_count INTEGER NOT NULL DEFAULT 0,
                        stop_observed INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_journeys_started_at
                        ON journeys (started_at DESC);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_journeys_one_active
                        ON journeys (status) WHERE status = 'active';
                    CREATE TABLE IF NOT EXISTS journey_observations (
                        id TEXT PRIMARY KEY,
                        journey_id TEXT NOT NULL REFERENCES journeys(id) ON DELETE CASCADE,
                        observation_type TEXT NOT NULL CHECK (observation_type IN (
                            'route_deviation', 'unexpected_stop'
                        )),
                        occurred_at TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        risk_assessment_id TEXT,
                        safety_event_id TEXT
                    );
                    CREATE TABLE IF NOT EXISTS journey_locations (
                        journey_id TEXT NOT NULL REFERENCES journeys(id) ON DELETE CASCADE,
                        observed_at TEXT NOT NULL,
                        latitude REAL NOT NULL,
                        longitude REAL NOT NULL,
                        accuracy_meters REAL NOT NULL,
                        speed_mps REAL,
                        PRIMARY KEY (journey_id, observed_at)
                    );
                    CREATE INDEX IF NOT EXISTS idx_journey_locations_time
                        ON journey_locations (journey_id, observed_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_journey_observations_time
                        ON journey_observations (journey_id, occurred_at DESC);
                    CREATE TABLE IF NOT EXISTS risk_assessments (
                        assessment_id TEXT PRIMARY KEY,
                        incident_key TEXT,
                        journey_id TEXT,
                        event_id TEXT,
                        occurred_at TEXT NOT NULL,
                        risk_score INTEGER NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
                        risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
                        reasons_json TEXT NOT NULL,
                        signals_json TEXT NOT NULL,
                        data_quality_json TEXT NOT NULL,
                        recommended_action TEXT NOT NULL,
                        source TEXT NOT NULL CHECK (source IN ('real', 'simulated', 'unknown')),
                        is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1))
                    );
                    CREATE TABLE IF NOT EXISTS risk_policy (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        policy_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS trusted_contacts (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        channel TEXT NOT NULL CHECK (channel IN ('email', 'sms')),
                        destination TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS contact_notification_tests (
                        id TEXT PRIMARY KEY,
                        contact_id TEXT,
                        provider TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        attempted_at TEXT NOT NULL,
                        response_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_contact_notification_tests_time
                        ON contact_notification_tests (attempted_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_risk_assessments_time
                        ON risk_assessments (occurred_at DESC);
                    CREATE TABLE IF NOT EXISTS escalation_history (
                        id TEXT PRIMARY KEY,
                        assessment_id TEXT NOT NULL REFERENCES risk_assessments(assessment_id),
                        state TEXT NOT NULL CHECK (state IN ('monitoring', 'check_in_required', 'escalation_pending', 'notified', 'delivery_failed', 'cancelled')),
                        occurred_at TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
                        event_type TEXT NOT NULL DEFAULT 'risk_assessment',
                        provider_mode TEXT NOT NULL DEFAULT 'none',
                        delivery_outcome TEXT NOT NULL DEFAULT 'not_attempted'
                    );
                    CREATE INDEX IF NOT EXISTS idx_escalation_time
                        ON escalation_history (occurred_at DESC);
                    CREATE TABLE IF NOT EXISTS notification_attempts (
                        id TEXT PRIMARY KEY,
                        assessment_id TEXT NOT NULL REFERENCES risk_assessments(assessment_id),
                        idempotency_key TEXT NOT NULL UNIQUE,
                        provider TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed', 'skipped_not_configured')),
                        attempted_at TEXT NOT NULL,
                        provider_response TEXT NOT NULL,
                        error_detail TEXT
                    );
                    """
                )
                risk_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(risk_assessments)")
                }
                if "incident_key" not in risk_columns:
                    connection.execute("ALTER TABLE risk_assessments ADD COLUMN incident_key TEXT")
                if "journey_id" not in risk_columns:
                    connection.execute("ALTER TABLE risk_assessments ADD COLUMN journey_id TEXT")
                if "event_id" not in risk_columns:
                    connection.execute("ALTER TABLE risk_assessments ADD COLUMN event_id TEXT")
                escalation_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(escalation_history)")
                }
                for column, default in (
                    ("event_type", "risk_assessment"),
                    ("provider_mode", "none"),
                    ("delivery_outcome", "not_attempted"),
                ):
                    if column not in escalation_columns:
                        connection.execute(
                            f"ALTER TABLE escalation_history ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'"
                        )
                journey_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(journeys)")
                }
                if "stationary_fix_count" not in journey_columns:
                    connection.execute(
                        "ALTER TABLE journeys ADD COLUMN stationary_fix_count INTEGER NOT NULL DEFAULT 0"
                    )
                check_in_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(check_in_history)")
                }
                if "assessment_id" not in check_in_columns:
                    connection.execute("ALTER TABLE check_in_history ADD COLUMN assessment_id TEXT")
                journey_observation_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(journey_observations)")
                }
                if "risk_assessment_id" not in journey_observation_columns:
                    connection.execute(
                        "ALTER TABLE journey_observations ADD COLUMN risk_assessment_id TEXT"
                    )
                if "safety_event_id" not in journey_observation_columns:
                    connection.execute(
                        "ALTER TABLE journey_observations ADD COLUMN safety_event_id TEXT"
                    )
                connection.execute(
                    """CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_journey_incident
                    ON risk_assessments (journey_id, incident_key)
                    WHERE journey_id IS NOT NULL AND incident_key IS NOT NULL"""
                )
                self._initialized = True
            except (OSError, sqlite3.Error) as exc:
                raise PersistenceError("Local data storage could not be initialized.") from exc
            finally:
                if connection is not None:
                    connection.close()

    @staticmethod
    def _event_details(event: dict) -> str:
        base_fields = {
            "id", "event_type", "severity", "occurred_at", "observed_fact",
            "source", "is_demo", "status", "inferred_signal", "rule",
        }
        details = {key: value for key, value in event.items() if key not in base_fields}
        return json.dumps(details, ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: dict) -> None:
        connection.execute(
            """
            INSERT INTO safety_events (
                id, event_type, severity, occurred_at, summary, source,
                is_demo, status, explanation, trigger_reason, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"], event["event_type"], event["severity"],
                event["occurred_at"], event["observed_fact"], event["source"],
                int(event["is_demo"]), event.get("status", "open"),
                event["inferred_signal"], event["rule"],
                SQLiteRepository._event_details(event),
            ),
        )

    def save_event_once(self, event: dict) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO safety_events (
                    id, event_type, severity, occurred_at, summary, source,
                    is_demo, status, explanation, trigger_reason, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], event["event_type"], event["severity"],
                    event["occurred_at"], event["observed_fact"], event["source"],
                    int(event["is_demo"]), event.get("status", "open"),
                    event["inferred_signal"], event["rule"],
                    self._event_details(event),
                ),
            )
            row = connection.execute(
                "SELECT * FROM safety_events WHERE id = ?", (event["id"],)
            ).fetchone()
            connection.commit()
        return self._event_response(row)

    @staticmethod
    def _event_response(row: sqlite3.Row) -> dict:
        event = json.loads(row["details_json"])
        event.update(
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "severity": row["severity"],
                "occurred_at": row["occurred_at"],
                "observed_fact": row["summary"],
                "source": row["source"],
                "is_demo": bool(row["is_demo"]),
                "status": row["status"],
                "inferred_signal": row["explanation"],
                "rule": row["trigger_reason"],
            }
        )
        return event

    def save_event(self, event: dict, check_in_status: str | None = None) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_event(connection, event)
            if check_in_status is not None:
                connection.execute(
                    "INSERT INTO check_in_history (occurred_at, status, is_demo) VALUES (?, ?, 1)",
                    (event["occurred_at"], check_in_status),
                )
                connection.execute(
                    """
                    INSERT INTO app_state (key, value_json) VALUES ('check_in', ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                    """,
                    (json.dumps({"status": check_in_status, "occurred_at": event["occurred_at"]}),),
                )
            connection.commit()
        return event

    def save_cabin_observation(self, event: dict | None, count: int) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if event is not None:
                self._insert_event(connection, event)
            connection.execute(
                """
                INSERT INTO app_state (key, value_json) VALUES ('last_cabin_peak', ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (json.dumps(count),),
            )
            connection.commit()

    def list_events(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM safety_events ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._event_response(row) for row in rows]

    def get_event(self, event_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM safety_events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._event_response(row) if row else None

    def list_alerts(self, limit: int = 100) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM safety_events
                WHERE severity IN ('medium', 'high', 'critical')
                ORDER BY occurred_at DESC, rowid DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._event_response(row) for row in rows]

    def reset_demo_state(self) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM safety_events WHERE is_demo = 1")
            deleted = cursor.rowcount
            connection.execute(
                """DELETE FROM notification_attempts WHERE assessment_id IN
                (SELECT assessment_id FROM risk_assessments WHERE is_demo = 1)"""
            )
            connection.execute(
                """DELETE FROM escalation_history WHERE assessment_id IN
                (SELECT assessment_id FROM risk_assessments WHERE is_demo = 1)"""
            )
            connection.execute("DELETE FROM risk_assessments WHERE is_demo = 1")
            connection.execute("DELETE FROM check_in_history WHERE is_demo = 1")
            check_in_state = connection.execute(
                "SELECT value_json FROM app_state WHERE key = 'check_in'"
            ).fetchone()
            if check_in_state is not None:
                saved_check_in = json.loads(check_in_state["value_json"])
                is_demo_check_in = saved_check_in.get(
                    "is_demo",
                    str(saved_check_in.get("status", "")).startswith("simulated_"),
                )
                if is_demo_check_in:
                    connection.execute("DELETE FROM app_state WHERE key = 'check_in'")
            connection.execute("DELETE FROM app_state WHERE key = 'last_cabin_peak'")
            connection.commit()
        return deleted

    def save_config(self, config: dict) -> None:
        serialized = json.dumps(config, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO monitoring_config (id, config_json) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET config_json = excluded.config_json
                """,
                (serialized,),
            )
            connection.commit()

    def load_config(self) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT config_json FROM monitoring_config WHERE id = 1"
            ).fetchone()
        return json.loads(row["config_json"]) if row else None

    def save_check_in(
        self, occurred_at: str, status: str, assessment_id: str | None = None,
        *, is_demo: bool = True,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO check_in_history
                (occurred_at, status, is_demo, assessment_id) VALUES (?, ?, ?, ?)""",
                (occurred_at, status, int(is_demo), assessment_id),
            )
            connection.execute(
                """
                INSERT INTO app_state (key, value_json) VALUES ('check_in', ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (json.dumps({
                    "status": status,
                    "occurred_at": occurred_at,
                    "assessment_id": assessment_id,
                    "is_demo": is_demo,
                }),),
            )
            connection.commit()

    def save_passenger_check_in(self, occurred_at: str, assessment_id: str) -> None:
        status = "passenger_responded"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO check_in_history
                (occurred_at, status, is_demo, assessment_id) VALUES (?, ?, 0, ?)""",
                (occurred_at, status, assessment_id),
            )
            connection.execute(
                """INSERT INTO app_state (key, value_json) VALUES ('check_in', ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json""",
                (json.dumps({
                    "status": status,
                    "occurred_at": occurred_at,
                    "assessment_id": assessment_id,
                    "is_demo": False,
                }),),
            )
            connection.commit()

    def load_check_in(self) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key = 'check_in'"
            ).fetchone()
        if row is None:
            return None
        saved = json.loads(row["value_json"])
        return {"status": saved["status"], "occurred_at": saved["occurred_at"]}

    def load_last_cabin_peak(self) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key = 'last_cabin_peak'"
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    @staticmethod
    def _journey_response(row: sqlite3.Row) -> dict:
        return {
            "journey_id": row["id"],
            "destination": row["destination"],
            "status": row["status"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "start_location": {
                "latitude": row["start_latitude"],
                "longitude": row["start_longitude"],
                "accuracy_meters": row["start_accuracy_meters"],
                "observed_at": row["start_observed_at"],
            },
            "current_location": {
                "latitude": row["current_latitude"],
                "longitude": row["current_longitude"],
                "accuracy_meters": row["current_accuracy_meters"],
                "speed_mps": row["current_speed_mps"],
                "observed_at": row["current_observed_at"],
            },
            "planned_route": json.loads(row["planned_route_json"]),
        }

    @staticmethod
    def _journey_observation_response(row: sqlite3.Row) -> dict:
        observation = json.loads(row["details_json"])
        observation.update({
            "id": row["id"],
            "journey_id": row["journey_id"],
            "observation_type": row["observation_type"],
            "occurred_at": row["occurred_at"],
            "summary": row["summary"],
            "risk_assessment_id": row["risk_assessment_id"],
            "safety_event_id": row["safety_event_id"],
        })
        return observation

    def create_journey(self, journey: dict) -> dict:
        location = journey["start_location"]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT id FROM journeys WHERE status = 'active'"
            ).fetchone()
            if active is not None:
                connection.rollback()
                raise ValueError("An active journey already exists.")
            connection.execute(
                """
                INSERT INTO journeys (
                    id, destination, status, started_at, start_latitude,
                    start_longitude, start_accuracy_meters, start_observed_at,
                    current_latitude, current_longitude, current_accuracy_meters,
                    current_observed_at, current_speed_mps, planned_route_json
                ) VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    journey["journey_id"], journey["destination"], journey["started_at"],
                    location["latitude"], location["longitude"], location["accuracy_meters"],
                    location["observed_at"], location["latitude"], location["longitude"],
                    location["accuracy_meters"], location["observed_at"],
                    location.get("speed_mps"),
                    json.dumps(journey["planned_route"], separators=(",", ":")),
                ),
            )
            connection.execute(
                """INSERT INTO journey_locations (
                    journey_id, observed_at, latitude, longitude, accuracy_meters, speed_mps
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    journey["journey_id"], location["observed_at"],
                    location["latitude"], location["longitude"],
                    location["accuracy_meters"], location.get("speed_mps"),
                ),
            )
            row = connection.execute(
                "SELECT * FROM journeys WHERE id = ?", (journey["journey_id"],)
            ).fetchone()
            connection.commit()
        return self._journey_response(row)

    def get_journey(self, journey_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone()
        return self._journey_response(row) if row else None

    def get_journey_tracking_state(self, journey_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            **self._journey_response(row),
            "route_deviation_streak": row["route_deviation_streak"],
            "route_deviation_active": bool(row["route_deviation_active"]),
            "stationary_since": row["stationary_since"],
            "stationary_fix_count": row["stationary_fix_count"],
            "stop_observed": bool(row["stop_observed"]),
        }

    def get_active_journey(self) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM journeys WHERE status = 'active'"
            ).fetchone()
        return self._journey_response(row) if row else None

    def list_journeys(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM journeys ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._journey_response(row) for row in rows]

    def list_journey_observations(self, journey_id: str, limit: int = 100) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM journey_observations
                WHERE journey_id = ?
                ORDER BY occurred_at DESC, rowid DESC LIMIT ?
                """,
                (journey_id, limit),
            ).fetchall()
        return [self._journey_observation_response(row) for row in rows]

    def list_unlinked_journey_observations(self, journey_id: str) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM journey_observations
                WHERE journey_id = ? AND risk_assessment_id IS NULL
                ORDER BY occurred_at, rowid""",
                (journey_id,),
            ).fetchall()
        return [self._journey_observation_response(row) for row in rows]

    def list_journey_observations_at(
        self, journey_id: str, occurred_at: str
    ) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM journey_observations
                WHERE journey_id = ? AND occurred_at = ?
                ORDER BY rowid""",
                (journey_id, occurred_at),
            ).fetchall()
        return [self._journey_observation_response(row) for row in rows]

    def link_journey_observation(
        self, observation_id: str, risk_assessment_id: str, safety_event_id: str
    ) -> dict | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE journey_observations
                SET risk_assessment_id = COALESCE(risk_assessment_id, ?),
                    safety_event_id = COALESCE(safety_event_id, ?)
                WHERE id = ?""",
                (risk_assessment_id, safety_event_id, observation_id),
            )
            row = connection.execute(
                "SELECT * FROM journey_observations WHERE id = ?", (observation_id,)
            ).fetchone()
            connection.commit()
        return self._journey_observation_response(row) if row else None

    def list_journey_locations(self, journey_id: str, limit: int = 500) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT journey_id, observed_at, latitude, longitude,
                accuracy_meters, speed_mps FROM journey_locations
                WHERE journey_id = ? ORDER BY observed_at, rowid LIMIT ?""",
                (journey_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_journey_location(
        self,
        journey_id: str,
        location: dict,
        calculate_detector_state: Callable[[dict], dict],
    ) -> tuple[dict, list[dict]]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LookupError("Journey was not found.")
            if row["status"] != "active":
                connection.rollback()
                raise ValueError("Journey is not active.")
            if location["observed_at"] <= row["current_observed_at"]:
                connection.rollback()
                raise ValueError("Location timestamp must be newer than the last saved fix.")
            state = {
                **self._journey_response(row),
                "route_deviation_streak": row["route_deviation_streak"],
                "route_deviation_active": bool(row["route_deviation_active"]),
                "stationary_since": row["stationary_since"],
                "stationary_fix_count": row["stationary_fix_count"],
                "stop_observed": bool(row["stop_observed"]),
            }
            detector_state = calculate_detector_state(state)
            observations = detector_state["observations"]
            connection.execute(
                """
                UPDATE journeys SET
                    current_latitude = ?, current_longitude = ?,
                    current_accuracy_meters = ?, current_observed_at = ?,
                    current_speed_mps = ?, route_deviation_streak = ?,
                    route_deviation_active = ?, stationary_since = ?,
                    stationary_fix_count = ?, stop_observed = ?
                WHERE id = ?
                """,
                (
                    location["latitude"], location["longitude"],
                    location["accuracy_meters"], location["observed_at"],
                    location.get("speed_mps"),
                    detector_state["route_deviation_streak"],
                    int(detector_state["route_deviation_active"]),
                    detector_state["stationary_since"],
                    detector_state["stationary_fix_count"],
                    int(detector_state["stop_observed"]), journey_id,
                ),
            )
            connection.execute(
                """INSERT INTO journey_locations (
                    journey_id, observed_at, latitude, longitude, accuracy_meters, speed_mps
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    journey_id, location["observed_at"], location["latitude"],
                    location["longitude"], location["accuracy_meters"],
                    location.get("speed_mps"),
                ),
            )
            for observation in observations:
                connection.execute(
                    """
                    INSERT INTO journey_observations (
                        id, journey_id, observation_type, occurred_at, summary, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation["id"], journey_id, observation["observation_type"],
                        observation["occurred_at"], observation["summary"],
                        json.dumps(
                            {key: value for key, value in observation.items()
                             if key not in {"id", "journey_id", "observation_type", "occurred_at", "summary"}},
                            separators=(",", ":"),
                        ),
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone()
            connection.commit()
        return self._journey_response(updated), observations

    def end_journey(self, journey_id: str, ended_at: str) -> dict | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE journeys SET status = 'completed', ended_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (ended_at, journey_id),
            )
            if cursor.rowcount == 0:
                exists = connection.execute(
                    "SELECT id FROM journeys WHERE id = ?", (journey_id,)
                ).fetchone()
                connection.rollback()
                if exists is None:
                    return None
                raise ValueError("Journey is not active.")
            row = connection.execute(
                "SELECT * FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone()
            connection.commit()
        return self._journey_response(row)

    def save_risk_assessment(self, assessment: dict) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO risk_assessments (
                    assessment_id, incident_key, journey_id, event_id,
                    occurred_at, risk_score, risk_level,
                    reasons_json, signals_json, data_quality_json, recommended_action,
                    source, is_demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assessment["assessment_id"], assessment.get("incident_key"),
                    assessment.get("journey_id"), assessment.get("event_id"),
                    assessment["timestamp"],
                    assessment["risk_score"], assessment["risk_level"],
                    json.dumps(assessment["reasons"]),
                    json.dumps(assessment["signals_used"]),
                    json.dumps(assessment["data_quality"]),
                    assessment["recommended_action"], assessment["source"],
                    int(assessment["is_demo"]),
                ),
            )
            connection.commit()

    def save_assessment_with_initial_state(
        self, assessment: dict, state: str, reason: str
    ) -> tuple[dict, dict, bool]:
        """Save an assessment and its first escalation state atomically and idempotently."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = None
            if assessment.get("journey_id") and assessment.get("incident_key"):
                existing = connection.execute(
                    """SELECT * FROM risk_assessments
                    WHERE journey_id = ? AND incident_key = ?""",
                    (assessment["journey_id"], assessment["incident_key"]),
                ).fetchone()
            if existing is not None:
                saved_assessment = self._risk_response(existing)
                escalation = connection.execute(
                    """SELECT * FROM escalation_history WHERE assessment_id = ?
                    ORDER BY occurred_at DESC, rowid DESC LIMIT 1""",
                    (saved_assessment["assessment_id"],),
                ).fetchone()
                connection.commit()
                return saved_assessment, dict(escalation), False

            connection.execute(
                """INSERT INTO risk_assessments (
                    assessment_id, incident_key, journey_id, event_id,
                    occurred_at, risk_score, risk_level, reasons_json, signals_json,
                    data_quality_json, recommended_action, source, is_demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assessment["assessment_id"], assessment.get("incident_key"),
                    assessment.get("journey_id"), assessment.get("event_id"),
                    assessment["timestamp"], assessment["risk_score"],
                    assessment["risk_level"], json.dumps(assessment["reasons"]),
                    json.dumps(assessment["signals_used"]),
                    json.dumps(assessment["data_quality"]),
                    assessment["recommended_action"], assessment["source"],
                    int(assessment["is_demo"]),
                ),
            )
            escalation = {
                "id": str(uuid4()),
                "assessment_id": assessment["assessment_id"],
                "state": state,
                "occurred_at": assessment["timestamp"],
                "reason": reason,
                "is_demo": assessment["is_demo"],
                "event_type": "risk_assessment",
                "provider_mode": "mock" if assessment["is_demo"] else "none",
                "delivery_outcome": "not_attempted",
            }
            connection.execute(
                """INSERT INTO escalation_history (
                    id, assessment_id, state, occurred_at, reason, is_demo,
                    event_type, provider_mode, delivery_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(escalation.values()),
            )
            row = connection.execute(
                "SELECT * FROM risk_assessments WHERE assessment_id = ?",
                (assessment["assessment_id"],),
            ).fetchone()
            connection.commit()
        return self._risk_response(row), escalation, True

    def get_risk_assessment_by_incident(
        self, journey_id: str, incident_key: str
    ) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM risk_assessments
                WHERE journey_id = ? AND incident_key = ?""",
                (journey_id, incident_key),
            ).fetchone()
        return self._risk_response(row) if row else None

    def save_risk_policy(self, policy: dict) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO risk_policy (id, policy_json) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET policy_json = excluded.policy_json""",
                (json.dumps(policy, separators=(",", ":")),),
            )
            connection.commit()

    def load_risk_policy(self) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT policy_json FROM risk_policy WHERE id = 1"
            ).fetchone()
        return json.loads(row["policy_json"]) if row else None

    @staticmethod
    def _risk_response(row: sqlite3.Row) -> dict:
        return {
            "assessment_id": row["assessment_id"],
            "incident_key": row["incident_key"],
            "journey_id": row["journey_id"],
            "event_id": row["event_id"],
            "timestamp": row["occurred_at"],
            "risk_score": row["risk_score"],
            "risk_level": row["risk_level"],
            "reasons": json.loads(row["reasons_json"]),
            "signals_used": json.loads(row["signals_json"]),
            "data_quality": json.loads(row["data_quality_json"]),
            "recommended_action": row["recommended_action"],
            "source": row["source"],
            "is_demo": bool(row["is_demo"]),
        }

    def list_risk_assessments(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM risk_assessments ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._risk_response(row) for row in rows]

    def save_escalation_state(
        self, assessment_id: str, state: str, reason: str, is_demo: bool
    ) -> dict:
        record = {
            "id": str(uuid4()),
            "assessment_id": assessment_id,
            "state": state,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "is_demo": is_demo,
            "event_type": "risk_assessment",
            "provider_mode": "mock" if is_demo else "none",
            "delivery_outcome": "not_attempted",
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO escalation_history (
                    id, assessment_id, state, occurred_at, reason, is_demo,
                    event_type, provider_mode, delivery_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(record.values()),
            )
            connection.commit()
        return record

    def transition_escalation_state(
        self,
        assessment_id: str,
        state: str,
        reason: str,
        is_demo: bool,
        allowed_states: set[str],
        *,
        event_type: str,
        provider_mode: str,
        delivery_outcome: str = "not_attempted",
    ) -> tuple[dict, bool]:
        record = {
            "id": str(uuid4()),
            "assessment_id": assessment_id,
            "state": state,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "is_demo": is_demo,
            "event_type": event_type,
            "provider_mode": provider_mode,
            "delivery_outcome": delivery_outcome,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """SELECT * FROM escalation_history
                WHERE assessment_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1""",
                (assessment_id,),
            ).fetchone()
            if latest is not None and latest["state"] == state:
                connection.commit()
                return dict(latest), False
            if latest is None or latest["state"] not in allowed_states:
                connection.rollback()
                raise ValueError("The escalation state changed before this action could be recorded.")
            connection.execute(
                """INSERT INTO escalation_history (
                    id, assessment_id, state, occurred_at, reason, is_demo,
                    event_type, provider_mode, delivery_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(record.values()),
            )
            connection.commit()
        return record, True

    def latest_escalation(self) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM escalation_history ORDER BY occurred_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def latest_escalation_for(self, assessment_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM escalation_history
                WHERE assessment_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1""",
                (assessment_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_escalation_history(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM escalation_history ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_notification_attempt(self, attempt: dict) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO notification_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt["id"], attempt["assessment_id"], attempt["idempotency_key"],
                    attempt["provider"], attempt["status"], attempt["attempted_at"],
                    json.dumps(attempt["provider_response"]), attempt.get("error_detail"),
                ),
            )
            connection.commit()
        return attempt

    def reserve_notification_attempt(self, attempt: dict) -> tuple[dict, bool]:
        """Atomically reserve an idempotency key before invoking a provider."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """INSERT OR IGNORE INTO notification_attempts
                VALUES (?, ?, ?, ?, 'pending', ?, '{}', NULL)""",
                (
                    attempt["id"], attempt["assessment_id"], attempt["idempotency_key"],
                    attempt["provider"], attempt["attempted_at"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM notification_attempts WHERE idempotency_key = ?",
                (attempt["idempotency_key"],),
            ).fetchone()
            connection.commit()
        saved = dict(row)
        saved["provider_response"] = json.loads(saved["provider_response"])
        return saved, cursor.rowcount == 1

    def finish_notification_attempt(self, attempt_id: str, status: str,
                                    provider_response: dict, error_detail: str | None) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE notification_attempts
                SET status = ?, provider_response = ?, error_detail = ? WHERE id = ?""",
                (status, json.dumps(provider_response), error_detail, attempt_id),
            )
            row = connection.execute(
                "SELECT * FROM notification_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            connection.commit()
        result = dict(row)
        result["provider_response"] = json.loads(result["provider_response"])
        return result

    def get_notification_by_key(self, idempotency_key: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM notification_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["provider_response"] = json.loads(result["provider_response"])
        return result

    def list_notifications(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM notification_attempts ORDER BY attempted_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["provider_response"] = json.loads(item["provider_response"])
            result.append(item)
        return result

    def get_risk_assessment(self, assessment_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM risk_assessments WHERE assessment_id = ?",
                (assessment_id,),
            ).fetchone()
        return self._risk_response(row) if row else None

    def list_escalations(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM escalation_history ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _contact_response(row: sqlite3.Row) -> dict:
        return dict(row)

    def list_contacts(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM trusted_contacts ORDER BY created_at, rowid"
            ).fetchall()
        return [self._contact_response(row) for row in rows]

    def replace_contacts(self, contacts: list[dict]) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        saved = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM trusted_contacts")
            for contact in contacts:
                record = {
                    **contact,
                    "created_at": now,
                    "updated_at": now,
                }
                connection.execute(
                    """INSERT INTO trusted_contacts (
                        id, name, channel, destination, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    tuple(record[key] for key in (
                        "id", "name", "channel", "destination", "created_at", "updated_at"
                    )),
                )
                saved.append(record)
            connection.commit()
        return saved

    def add_contact(self, contact: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        record = {
            **contact,
            "created_at": now,
            "updated_at": now,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute(
                "SELECT COUNT(*) FROM trusted_contacts"
            ).fetchone()[0]
            if count >= 5:
                connection.rollback()
                raise ValueError("You can save up to five trusted contacts.")
            connection.execute(
                """INSERT INTO trusted_contacts (
                    id, name, channel, destination, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                tuple(record[key] for key in (
                    "id", "name", "channel", "destination", "created_at", "updated_at"
                )),
            )
            connection.commit()
        return record

    def update_contact(self, contact_id: str, contact: dict) -> dict | None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE trusted_contacts
                SET name = ?, channel = ?, destination = ?, updated_at = ?
                WHERE id = ?""",
                (
                    contact["name"], contact["channel"], contact["destination"],
                    updated_at, contact_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM trusted_contacts WHERE id = ?", (contact_id,)
            ).fetchone()
            connection.commit()
        return self._contact_response(row) if cursor.rowcount else None

    def delete_contact(self, contact_id: str) -> bool:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM trusted_contacts WHERE id = ?", (contact_id,)
            )
            connection.commit()
        return cursor.rowcount > 0

    def save_contact_notification_test(
        self, contact_id: str | None, provider: str, outcome: str, response: dict
    ) -> dict:
        record = {
            "id": str(uuid4()),
            "contact_id": contact_id,
            "provider": provider,
            "outcome": outcome,
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "response": response,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO contact_notification_tests (
                    id, contact_id, provider, outcome, attempted_at, response_json
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record["id"], record["contact_id"], record["provider"],
                    record["outcome"], record["attempted_at"],
                    json.dumps(record["response"], separators=(",", ":")),
                ),
            )
            connection.commit()
        return record

    def list_contact_notification_tests(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM contact_notification_tests
                ORDER BY attempted_at DESC, rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                **{key: row[key] for key in (
                    "id", "contact_id", "provider", "outcome", "attempted_at"
                )},
                "response": json.loads(row["response_json"]),
            }
            for row in rows
        ]


# Configured by ESENTINEL_DB_PATH, or data/e_sentinel.db by default.
