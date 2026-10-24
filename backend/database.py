"""Small SQLite repository for local event and monitoring state."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Iterator


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
                        is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1))
                    );
                    CREATE TABLE IF NOT EXISTS app_state (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL
                    );
                    """
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
            connection.execute("DELETE FROM check_in_history WHERE is_demo = 1")
            connection.execute(
                "DELETE FROM app_state WHERE key IN ('check_in', 'last_cabin_peak')"
            )
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

    def save_check_in(self, occurred_at: str, status: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO check_in_history (occurred_at, status, is_demo) VALUES (?, ?, 1)",
                (occurred_at, status),
            )
            connection.execute(
                """
                INSERT INTO app_state (key, value_json) VALUES ('check_in', ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (json.dumps({"status": status, "occurred_at": occurred_at}),),
            )
            connection.commit()

    def load_check_in(self) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key = 'check_in'"
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def load_last_cabin_peak(self) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key = 'last_cabin_peak'"
            ).fetchone()
        return json.loads(row["value_json"]) if row else None


# Configured by ESENTINEL_DB_PATH, or data/e_sentinel.db by default.
