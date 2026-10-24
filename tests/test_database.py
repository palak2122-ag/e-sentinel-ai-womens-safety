from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from backend.database import SQLiteRepository


class SQLiteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "nested" / "events.db"
        self.repository = SQLiteRepository(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_event(self, *, occurred_at=None, is_demo=True, event_type="route_deviation"):
        timestamp = occurred_at or datetime.now(timezone.utc).isoformat()
        return {
            "id": str(uuid4()),
            "event_type": event_type,
            "severity": "medium",
            "occurred_at": timestamp,
            "observed_fact": "Simulated route offset: 700 m.",
            "source": "demo_simulation" if is_demo else "uploaded_video_yolo",
            "is_demo": is_demo,
            "inferred_signal": "A configured threshold was crossed.",
            "rule": "Flag at or above 500 m.",
            "unknown": "Personal safety remains unknown.",
            "note": "test details",
        }

    def test_initialization_creates_tables_and_is_repeatable(self):
        self.repository.initialize()
        self.repository.initialize()

        connection = sqlite3.connect(self.db_path)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        connection.close()

        self.assertTrue(
            {"safety_events", "monitoring_config", "check_in_history", "app_state"}
            <= tables
        )

    def test_event_survives_new_repository_instance_and_preserves_fields(self):
        event = self.make_event()
        self.repository.save_event(event)

        restarted_repository = SQLiteRepository(self.db_path)
        restored = restarted_repository.list_events()[0]

        for field in (
            "id",
            "event_type",
            "severity",
            "occurred_at",
            "observed_fact",
            "source",
            "is_demo",
            "inferred_signal",
            "rule",
            "unknown",
            "note",
        ):
            self.assertEqual(restored[field], event[field])

    def test_events_are_returned_newest_first(self):
        now = datetime.now(timezone.utc)
        older = self.make_event(occurred_at=(now - timedelta(minutes=1)).isoformat())
        newer = self.make_event(occurred_at=now.isoformat())
        self.repository.save_event(older)
        self.repository.save_event(newer)

        events = self.repository.list_events()

        self.assertEqual([event["id"] for event in events], [newer["id"], older["id"]])

    def test_alerts_are_derived_from_persisted_severity(self):
        low = self.make_event()
        low["severity"] = "info"
        alert = self.make_event(event_type="unexpected_stop")
        self.repository.save_event(low)
        self.repository.save_event(alert)

        self.assertEqual([event["id"] for event in self.repository.list_alerts()], [alert["id"]])

    def test_demo_reset_preserves_non_demo_events(self):
        demo_event = self.make_event()
        observed_event = self.make_event(is_demo=False, event_type="cabin_person_count_change")
        self.repository.save_event(demo_event)
        self.repository.save_event(observed_event)

        deleted_count = self.repository.reset_demo_state()
        remaining = self.repository.list_events()

        self.assertEqual(deleted_count, 1)
        self.assertEqual([event["id"] for event in remaining], [observed_event["id"]])

    def test_configuration_and_check_in_state_survive_restart(self):
        config = {
            "route_deviation_threshold_meters": 900,
            "unexpected_stop_threshold_minutes": 8,
            "check_in_interval_minutes": 45,
        }
        self.repository.save_config(config)
        timestamp = datetime.now(timezone.utc).isoformat()
        self.repository.save_check_in(timestamp, "simulated_checked_in")

        fresh = SQLiteRepository(self.db_path)
        self.assertEqual(fresh.load_config(), config)
        self.assertEqual(
            fresh.load_check_in(),
            {"status": "simulated_checked_in", "occurred_at": timestamp},
        )

    def test_parameterized_details_preserve_unicode_and_json(self):
        event = self.make_event()
        event["note"] = "safe demo: user's choice"
        self.repository.save_event(event)

        restored = self.repository.list_events()[0]
        self.assertEqual(restored["note"], event["note"])
        connection = sqlite3.connect(self.db_path)
        details = connection.execute(
            "SELECT details_json FROM safety_events WHERE id = ?", (event["id"],)
        ).fetchone()[0]
        connection.close()
        self.assertEqual(json.loads(details)["note"], event["note"])

    def test_risk_assessment_escalation_and_notification_survive_restart(self):
        assessment = {
            "assessment_id": "assessment-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "risk_score": 72,
            "risk_level": "critical",
            "reasons": [{"signal": "gps", "points": 30, "explanation": "Simulated route deviation."}],
            "signals_used": [{"name": "gps", "source": "simulated", "status": "available"}],
            "data_quality": {"missing_or_uncertain": []},
            "recommended_action": "Run mock workflow.",
            "source": "simulated",
            "is_demo": True,
        }
        self.repository.save_risk_assessment(assessment)
        state = self.repository.save_escalation_state("assessment-1", "escalation_pending", "Corroborated", True)
        attempt = {
            "id": "attempt-1",
            "assessment_id": "assessment-1",
            "idempotency_key": "incident:demo-1",
            "provider": "mock",
            "status": "delivered",
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "provider_response": {"mock": True},
            "error_detail": None,
        }
        self.repository.save_notification_attempt(attempt)

        fresh = SQLiteRepository(self.db_path)
        self.assertEqual(fresh.list_risk_assessments()[0]["risk_score"], 72)
        self.assertEqual(fresh.list_escalations()[0]["id"], state["id"])
        self.assertEqual(fresh.get_notification_by_key("incident:demo-1")["id"], "attempt-1")

    def test_demo_reset_removes_demo_risk_records_but_preserves_real_records(self):
        base = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "risk_score": 20,
            "risk_level": "medium",
            "reasons": [],
            "signals_used": [],
            "data_quality": {},
            "recommended_action": "Monitor",
            "source": "simulated",
        }
        demo_assessment = {**base, "assessment_id": "demo-assessment", "is_demo": True}
        real_assessment = {
            **base,
            "assessment_id": "real-assessment",
            "source": "real",
            "is_demo": False,
        }
        self.repository.save_risk_assessment(demo_assessment)
        self.repository.save_risk_assessment(real_assessment)
        self.repository.save_escalation_state("demo-assessment", "monitoring", "Demo", True)
        self.repository.save_escalation_state("real-assessment", "monitoring", "Real", False)

        self.repository.reset_demo_state()

        assessments = self.repository.list_risk_assessments()
        transitions = self.repository.list_escalations()
        self.assertEqual([item["assessment_id"] for item in assessments], ["real-assessment"])
        self.assertEqual([item["assessment_id"] for item in transitions], ["real-assessment"])

    def test_parallel_writes_use_independent_connections_safely(self):
        events = [self.make_event() for _ in range(24)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self.repository.save_event, events))

        saved = self.repository.list_events(100)
        self.assertEqual(len(saved), len(events))
        self.assertEqual({event["id"] for event in saved}, {event["id"] for event in events})


if __name__ == "__main__":
    unittest.main()