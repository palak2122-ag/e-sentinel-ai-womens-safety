from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from backend.database import PersistenceError, SQLiteRepository


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
            {
                "safety_events",
                "monitoring_config",
                "check_in_history",
                "app_state",
                "journeys",
                "journey_observations",
                "journey_locations",
            }
            <= tables
        )

    def test_initialization_adds_audit_fields_without_dropping_existing_transitions(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE escalation_history (
                        id TEXT PRIMARY KEY,
                        assessment_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        is_demo INTEGER NOT NULL
                    )"""
                )
                connection.execute(
                    """INSERT INTO escalation_history
                    VALUES ('legacy-transition', 'legacy-assessment', 'monitoring',
                            '2025-01-01T00:00:00+00:00', 'Preserved', 1)"""
                )

        self.repository.initialize()

        transition = self.repository.list_escalation_history()[0]
        self.assertEqual(transition["id"], "legacy-transition")
        self.assertEqual(transition["reason"], "Preserved")
        self.assertEqual(transition["event_type"], "risk_assessment")
        self.assertEqual(transition["provider_mode"], "none")
        self.assertEqual(transition["delivery_outcome"], "not_attempted")

    def test_journey_location_and_observation_survive_repository_restart(self):
        started = datetime.now(timezone.utc) - timedelta(minutes=2)
        first_fix = {
            "latitude": 37.5,
            "longitude": -122.2,
            "accuracy_meters": 10,
            "speed_mps": 2,
            "observed_at": started.isoformat(),
        }
        self.repository.create_journey({
            "journey_id": "journey-1",
            "destination": "Office",
            "started_at": started.isoformat(),
            "start_location": first_fix,
            "planned_route": [
                {"latitude": 37.5, "longitude": -122.2},
                {"latitude": 37.6, "longitude": -122.1},
            ],
        })
        observation_time = (started + timedelta(minutes=1)).isoformat()
        observation = {
            "id": "observation-1",
            "journey_id": "journey-1",
            "observation_type": "route_deviation",
            "occurred_at": observation_time,
            "summary": "GPS position was outside the planned route.",
            "severity": "medium",
            "deviation_meters": 700,
        }
        self.repository.save_journey_location(
            "journey-1",
            {
                "latitude": 37.51,
                "longitude": -122.19,
                "accuracy_meters": 8,
                "speed_mps": 2,
                "observed_at": observation_time,
            },
            lambda _state: {
                "route_deviation_streak": 3,
                "route_deviation_active": True,
                "stationary_since": None,
                "stationary_fix_count": 0,
                "stop_observed": False,
                "observations": [observation],
            },
        )

        restarted = SQLiteRepository(self.db_path)
        journey = restarted.get_active_journey()
        saved_observation = restarted.list_journey_observations("journey-1")[0]
        saved_locations = restarted.list_journey_locations("journey-1")
        self.assertEqual(journey["current_location"]["latitude"], 37.51)
        self.assertEqual(journey["planned_route"][1]["longitude"], -122.1)
        self.assertEqual(saved_observation["id"], "observation-1")
        self.assertEqual(saved_observation["deviation_meters"], 700)
        self.assertEqual(len(saved_locations), 2)
        self.assertEqual(saved_locations[-1]["latitude"], 37.51)

    def test_journey_location_and_detector_state_roll_back_on_observation_failure(self):
        started = datetime.now(timezone.utc) - timedelta(seconds=30)
        first_fix = {
            "latitude": 37.5,
            "longitude": -122.2,
            "accuracy_meters": 10,
            "speed_mps": 2,
            "observed_at": started.isoformat(),
        }
        self.repository.create_journey({
            "journey_id": "journey-rollback",
            "destination": "Office",
            "started_at": started.isoformat(),
            "start_location": first_fix,
            "planned_route": [],
        })
        attempted_time = (started + timedelta(seconds=10)).isoformat()

        with self.assertRaises(PersistenceError):
            self.repository.save_journey_location(
                "journey-rollback",
                {
                    **first_fix,
                    "latitude": 37.6,
                    "observed_at": attempted_time,
                },
                lambda _state: {
                    "route_deviation_streak": 1,
                    "route_deviation_active": False,
                    "stationary_since": None,
                    "stationary_fix_count": 0,
                    "stop_observed": False,
                    "observations": [{
                        "id": "invalid-observation",
                        "journey_id": "journey-rollback",
                        "observation_type": "unsupported",
                        "occurred_at": attempted_time,
                        "summary": "This insert should violate the observation type constraint.",
                    }],
                },
            )

        persisted = self.repository.get_journey_tracking_state("journey-rollback")
        self.assertEqual(persisted["current_location"]["observed_at"], started.isoformat())
        self.assertEqual(persisted["route_deviation_streak"], 0)
        self.assertEqual(self.repository.list_journey_observations("journey-rollback"), [])

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

    def test_demo_reset_preserves_real_assessment_check_in_state(self):
        timestamp = datetime.now(timezone.utc).isoformat()
        self.repository.save_passenger_check_in(timestamp, "assessment-real")
        self.repository.save_check_in(timestamp, "simulated_checked_in")
        self.repository.save_passenger_check_in(timestamp, "assessment-real")

        self.repository.reset_demo_state()

        check_in = self.repository.load_check_in()
        self.assertEqual(check_in["status"], "passenger_responded")
        connection = sqlite3.connect(self.db_path)
        try:
            latest_response = connection.execute(
                """SELECT assessment_id, is_demo FROM check_in_history
                WHERE status = 'passenger_responded' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(latest_response, ("assessment-real", 0))

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

    def test_contacts_and_mock_test_audit_survive_repository_restart(self):
        contact = self.repository.add_contact({
            "id": "trusted-1",
            "name": "Trusted",
            "channel": "email",
            "destination": "trusted@example.org",
        })
        self.repository.save_contact_notification_test(
            contact["id"],
            "mock",
            "simulated_not_sent",
            {"contact_notified": False, "delivery_confirmed": False},
        )

        fresh = SQLiteRepository(self.db_path)
        fresh.initialize()

        self.assertEqual(fresh.list_contacts()[0]["destination"], "trusted@example.org")
        audit = fresh.list_contact_notification_tests()[0]
        self.assertEqual(audit["contact_id"], contact["id"])
        self.assertEqual(audit["outcome"], "simulated_not_sent")
        self.assertFalse(audit["response"]["contact_notified"])

    def test_escalation_transition_is_recorded_once_and_has_delivery_details(self):
        assessment = {
            "assessment_id": "transition-assessment",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "risk_score": 80,
            "risk_level": "critical",
            "reasons": [],
            "signals_used": [],
            "data_quality": {},
            "recommended_action": "Explicit demo action.",
            "source": "simulated",
            "is_demo": True,
        }
        self.repository.save_risk_assessment(assessment)
        self.repository.save_escalation_state(
            "transition-assessment", "check_in_required", "Initial state", True
        )

        transition, created = self.repository.transition_escalation_state(
            "transition-assessment",
            "escalation_pending",
            "User requested demo workflow.",
            True,
            {"check_in_required", "escalation_pending"},
            event_type="demo_escalation_requested",
            provider_mode="mock",
        )
        repeated, repeated_created = self.repository.transition_escalation_state(
            "transition-assessment",
            "escalation_pending",
            "Repeated request.",
            True,
            {"check_in_required", "escalation_pending"},
            event_type="demo_escalation_requested",
            provider_mode="mock",
        )

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(repeated["id"], transition["id"])
        self.assertEqual(transition["event_type"], "demo_escalation_requested")
        self.assertEqual(transition["provider_mode"], "mock")
        self.assertEqual(transition["delivery_outcome"], "not_attempted")
        self.assertEqual(len(self.repository.list_escalations()), 2)

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