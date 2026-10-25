import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.database import PersistenceError, SQLiteRepository
from backend.event_engine import EventEngine, MonitoringConfig
from backend.journey_engine import JourneyEngine
from backend.risk_engine import RiskEngine


class FakeCapture:
    def __init__(self, _path):
        self.frames = [object() for _ in range(5)]

    def isOpened(self):
        return True

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        return False, None

    def release(self):
        pass


class UnreadableCapture:
    def __init__(self, _path):
        pass

    def isOpened(self):
        return False

    def release(self):
        pass


class FakeBoxes:
    def __init__(self, confidences):
        self.conf = SimpleNamespace(tolist=lambda: confidences)

    def __len__(self):
        return len(self.conf.tolist())


class FakeModel:
    def __init__(self):
        self.results = [
            SimpleNamespace(boxes=FakeBoxes([0.7])),
            SimpleNamespace(boxes=FakeBoxes([0.8, 0.9])),
            SimpleNamespace(boxes=FakeBoxes([])),
        ]
        self.calls = []

    def predict(self, frame, **options):
        self.calls.append(options)
        return [self.results[len(self.calls) - 1]]


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        main.event_engine = EventEngine(
            SQLiteRepository(f"{self.temp_dir.name}/test.db")
        )
        main.event_engine.initialize()
        main.risk_engine = RiskEngine(main.event_engine.repository)
        main.risk_engine.initialize()
        main.journey_engine = JourneyEngine(main.event_engine.repository)
        main.journey_engine.initialize()
        main.event_engine.reset()
        main.event_engine.configure(MonitoringConfig())
        main.event_engine.set_contacts([])
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_health_endpoint_reports_healthy(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_safety_status_contract_remains_unknown(self):
        response = self.client.get("/api/safety-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["risk_level"], "UNKNOWN")
        self.assertFalse(response.json()["monitoring_active"])

    def test_upload_rejects_unsupported_extension(self):
        response = self.client.post(
            "/api/analyze-cabin",
            files={"file": ("notes.txt", b"not a video", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("supported video", response.json()["detail"])

    def test_analysis_reports_sampled_detections_and_confidence(self):
        fake_model = FakeModel()
        with (
            patch.object(main, "FRAME_STRIDE", 2),
            patch.object(main, "get_model", return_value=fake_model),
            patch.object(main.cv2, "VideoCapture", FakeCapture),
        ):
            response = self.client.post(
                "/api/analyze-cabin",
                files={"file": ("sample.mp4", b"mock video", "video/mp4")},
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["frames_read"], 5)
        self.assertEqual(result["frames_processed"], 3)
        self.assertEqual(result["total_person_detections_across_frames"], 3)
        self.assertEqual(result["maximum_people_in_one_frame"], 2)
        self.assertAlmostEqual(result["mean_detection_confidence"], 0.8)
        self.assertAlmostEqual(result["maximum_detection_confidence"], 0.9)
        self.assertEqual(result["frame_stride"], 2)
        self.assertEqual(len(fake_model.calls), 3)
        self.assertTrue(all(call["classes"] == [0] for call in fake_model.calls))

    def test_upload_size_limit_is_enforced(self):
        with patch.object(main, "MAX_UPLOAD_BYTES", 3):
            response = self.client.post(
                "/api/analyze-cabin",
                files={"file": ("sample.mp4", b"four", "video/mp4")},
            )

        self.assertEqual(response.status_code, 413)

    def test_unreadable_video_returns_client_error(self):
        with (
            patch.object(main, "get_model", return_value=FakeModel()),
            patch.object(main.cv2, "VideoCapture", UnreadableCapture),
        ):
            response = self.client.post(
                "/api/analyze-cabin",
                files={"file": ("broken.mp4", b"not really a video", "video/mp4")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not read", response.json()["detail"])

    def test_demo_route_event_applies_threshold_and_records_rule(self):
        response = self.client.post(
            "/api/events/demo",
            json={"event_type": "route_deviation", "route_deviation_meters": 700},
        )

        self.assertEqual(response.status_code, 200, response.text)
        event = response.json()
        self.assertEqual(event["severity"], "medium")
        self.assertIn("500 m", event["rule"])
        self.assertTrue(event["is_demo"])
        self.assertIsNotNone(event["occurred_at"])
        self.assertEqual(self.client.get("/api/events").json()["events"][0]["id"], event["id"])

    def test_alert_api_reads_persisted_qualifying_events(self):
        created = self.client.post(
            "/api/events/demo",
            json={"event_type": "user_sos"},
        ).json()

        response = self.client.get("/api/alerts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["alerts"][0]["id"], created["id"])

    def test_demo_event_below_threshold_is_rejected(self):
        response = self.client.post(
            "/api/events/demo",
            json={"event_type": "unexpected_stop", "stop_minutes": 2},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("below", response.json()["detail"])

    def test_event_api_validation_rejects_out_of_range_values(self):
        response = self.client.post(
            "/api/events/demo",
            json={"event_type": "route_deviation", "route_deviation_meters": -1},
        )

        self.assertEqual(response.status_code, 422)

    def test_config_and_incident_summary_are_available(self):
        config_response = self.client.put(
            "/api/monitoring/config",
            json={
                "route_deviation_threshold_meters": 800,
                "unexpected_stop_threshold_minutes": 7,
                "check_in_interval_minutes": 45,
            },
        )
        self.assertEqual(config_response.status_code, 200)
        self.assertEqual(
            config_response.json()["configuration"]["route_deviation_threshold_meters"],
            800,
        )
        self.client.post(
            "/api/events/demo",
            json={"event_type": "route_deviation", "route_deviation_meters": 900},
        )
        summary = self.client.get("/api/incidents/summary").json()
        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["safety_assessment"], "UNKNOWN")
        self.assertEqual(summary["severity_counts"]["medium"], 1)

    def test_assistant_uses_events_and_labels_local_mode(self):
        self.client.post(
            "/api/events/demo",
            json={"event_type": "unexpected_stop", "stop_minutes": 10},
        )
        response = self.client.post(
            "/api/assistant/query", json={"question": "Why was this flagged?"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "local_rules_assistant")
        self.assertFalse(response.json()["provider_called"])
        self.assertIn("at least 5 min", response.json()["answer"])

    def test_contact_data_is_redacted_and_test_notification_is_not_delivered(self):
        contacts = self.client.put(
            "/api/contacts",
            json={"contacts": [{"name": "Trusted", "channel": "email", "destination": "trusted@example.com"}]},
        )
        self.assertEqual(contacts.status_code, 200)
        result = contacts.json()["contacts"][0]
        self.assertNotIn("trusted@example.com", str(result))
        self.assertTrue(result["destination_hint"].endswith(".com"))

        notification = self.client.post(
            "/api/notifications/test", json={"contact_id": result["id"]}
        )
        self.assertEqual(notification.status_code, 200)
        self.assertFalse(notification.json()["delivery_confirmed"])
        self.assertFalse(notification.json()["contact_notified"])

    def test_reset_clears_events(self):
        self.client.post(
            "/api/events/demo",
            json={"event_type": "missed_check_in", "overdue_minutes": 35},
        )
        response = self.client.delete("/api/events/demo")

        self.assertTrue(response.json()["events_cleared"])
        self.assertEqual(self.client.get("/api/events").json()["events"], [])

    def test_demo_reset_preserves_non_demo_cabin_observation(self):
        main.event_engine.record_cabin_peak(1)
        main.event_engine.record_cabin_peak(3)

        response = self.client.delete("/api/events/demo")

        self.assertTrue(response.json()["non_demo_events_preserved"])
        events = self.client.get("/api/events").json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "cabin_person_count_change")

    def test_write_failure_returns_service_unavailable_without_success(self):
        with patch.object(main.event_engine.repository, "save_event", side_effect=PersistenceError("private db path")):
            response = self.client.post(
                "/api/events/demo",
                json={"event_type": "route_deviation", "route_deviation_meters": 700},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Event could not be saved."})

    def test_simulated_check_in_updates_monitoring_state(self):
        response = self.client.post("/api/monitoring/check-in")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["check_in_status"], "simulated_checked_in")
        self.assertTrue(response.json()["is_demo"])
        self.assertEqual(
            self.client.get("/api/monitoring/state").json()["check_in_status"],
            "simulated_checked_in",
        )

    def test_journey_start_location_and_status_are_persisted(self):
        started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        response = self.client.post(
            "/api/journeys",
            json={
                "destination": "  Home  ",
                "start_location": {
                    "latitude": 37.5,
                    "longitude": -122.2,
                    "accuracy_meters": 12,
                    "observed_at": started_at.isoformat(),
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        journey = response.json()
        self.assertEqual(journey["destination"], "Home")
        self.assertEqual(journey["status"], "active")
        self.assertEqual(journey["start_location"]["latitude"], 37.5)
        self.assertIsNone(journey["ended_at"])
        active = self.client.get("/api/journeys/active").json()["journey"]
        self.assertEqual(active["journey_id"], journey["journey_id"])
        self.assertEqual(
            self.client.get("/api/journeys").json()["journeys"][0]["status"],
            "active",
        )

    def test_journey_rejects_invalid_coordinates_and_second_active_trip(self):
        invalid = self.client.post(
            "/api/journeys",
            json={
                "destination": "Home",
                "start_location": {
                    "latitude": 91,
                    "longitude": 0,
                    "accuracy_meters": 10,
                },
            },
        )
        self.assertEqual(invalid.status_code, 422)

        now = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        payload = {
            "destination": "Home",
            "start_location": {
                "latitude": 37,
                "longitude": -122,
                "accuracy_meters": 10,
                "observed_at": now,
            },
        }
        self.assertEqual(self.client.post("/api/journeys", json=payload).status_code, 200)
        duplicate = self.client.post("/api/journeys", json=payload)
        self.assertEqual(duplicate.status_code, 409)

    def test_journey_rejects_stale_start_and_location_fixes(self):
        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        stale_start = self.client.post(
            "/api/journeys",
            json={
                "destination": "Home",
                "start_location": {
                    "latitude": 37,
                    "longitude": -122,
                    "accuracy_meters": 10,
                    "observed_at": stale_time,
                },
            },
        )
        self.assertEqual(stale_start.status_code, 422)
        self.assertIn("stale", stale_start.json()["detail"][0]["msg"])

        fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        journey = self.client.post(
            "/api/journeys",
            json={
                "destination": "Home",
                "start_location": {
                    "latitude": 37,
                    "longitude": -122,
                    "accuracy_meters": 10,
                    "observed_at": fresh_time,
                },
            },
        ).json()
        stale_update = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations",
            json={
                "latitude": 37.001,
                "longitude": -122,
                "accuracy_meters": 10,
                "observed_at": stale_time,
            },
        )
        self.assertEqual(stale_update.status_code, 422)
        self.assertIn("stale", stale_update.json()["detail"][0]["msg"])

    def test_journey_rejects_duplicate_and_valid_out_of_order_fixes(self):
        started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        journey = self.client.post(
            "/api/journeys",
            json={
                "destination": "Home",
                "start_location": {
                    "latitude": 37,
                    "longitude": -122,
                    "accuracy_meters": 10,
                    "observed_at": started_at.isoformat(),
                },
            },
        ).json()
        fix_time = started_at + timedelta(seconds=10)
        first_fix = {
            "latitude": 37,
            "longitude": -122,
            "accuracy_meters": 10,
            "speed_mps": 2,
            "observed_at": fix_time.isoformat(),
        }
        accepted = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations", json=first_fix
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)

        duplicate = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations", json=first_fix
        )
        out_of_order = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations",
            json={
                **first_fix,
                "observed_at": (fix_time - timedelta(seconds=1)).isoformat(),
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(out_of_order.status_code, 409)
        self.assertIn("newer", duplicate.json()["detail"])
        self.assertIn("newer", out_of_order.json()["detail"])

    def test_missing_and_completed_journey_location_responses(self):
        missing_id = "missing-journey"
        missing_location = self.client.post(
            f"/api/journeys/{missing_id}/locations",
            json={
                "latitude": 37,
                "longitude": -122,
                "accuracy_meters": 10,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        missing_end = self.client.post(f"/api/journeys/{missing_id}/end")
        self.assertEqual(missing_location.status_code, 404)
        self.assertEqual(missing_end.status_code, 404)

        started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        journey = self.client.post(
            "/api/journeys",
            json={
                "destination": "Home",
                "start_location": {
                    "latitude": 37,
                    "longitude": -122,
                    "accuracy_meters": 10,
                    "observed_at": started_at.isoformat(),
                },
            },
        ).json()
        ended = self.client.post(f"/api/journeys/{journey['journey_id']}/end")
        self.assertEqual(ended.status_code, 200)
        update_after_end = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations",
            json={
                "latitude": 37.001,
                "longitude": -122,
                "accuracy_meters": 10,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        second_end = self.client.post(f"/api/journeys/{journey['journey_id']}/end")
        self.assertEqual(update_after_end.status_code, 409)
        self.assertEqual(second_end.status_code, 409)

    def test_resetting_demo_events_preserves_real_journey(self):
        started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        journey = self.client.post(
            "/api/journeys",
            json={
                "destination": "Home",
                "start_location": {
                    "latitude": 37,
                    "longitude": -122,
                    "accuracy_meters": 10,
                    "observed_at": started_at.isoformat(),
                },
            },
        ).json()

        self.client.delete("/api/events/demo")

        active = self.client.get("/api/journeys/active").json()["journey"]
        self.assertEqual(active["journey_id"], journey["journey_id"])

    def test_route_deviation_requires_three_accurate_fixes_and_is_persisted(self):
        started_at = datetime.now(timezone.utc) - timedelta(seconds=100)
        journey = self.client.post(
            "/api/journeys",
            json={
                "destination": "Destination",
                "start_location": {
                    "latitude": 0,
                    "longitude": 0,
                    "accuracy_meters": 8,
                    "observed_at": started_at.isoformat(),
                },
                "planned_route": [
                    {"latitude": 0, "longitude": 0},
                    {"latitude": 0, "longitude": 0.02},
                ],
            },
        ).json()

        small_variation = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations",
            json={
                "latitude": 0.0001,
                "longitude": 0.01,
                "accuracy_meters": 10,
                "speed_mps": 2,
                "observed_at": (started_at + timedelta(seconds=10)).isoformat(),
            },
        )
        self.assertEqual(small_variation.status_code, 200, small_variation.text)
        self.assertEqual(small_variation.json()["new_observations"], [])

        poor_accuracy = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations",
            json={
                "latitude": 0.01,
                "longitude": 0.01,
                "accuracy_meters": 150,
                "speed_mps": 2,
                "observed_at": (started_at + timedelta(seconds=20)).isoformat(),
            },
        )
        self.assertEqual(poor_accuracy.status_code, 200, poor_accuracy.text)
        self.assertEqual(poor_accuracy.json()["new_observations"], [])

        for second in (30, 40):
            response = self.client.post(
                f"/api/journeys/{journey['journey_id']}/locations",
                json={
                    "latitude": 0.01,
                    "longitude": 0.01,
                    "accuracy_meters": 10,
                    "speed_mps": 2,
                    "observed_at": (started_at + timedelta(seconds=second)).isoformat(),
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["new_observations"], [])

        response = self.client.post(
            f"/api/journeys/{journey['journey_id']}/locations",
            json={
                "latitude": 0.01,
                "longitude": 0.01,
                "accuracy_meters": 10,
                "speed_mps": 2,
                "observed_at": (started_at + timedelta(seconds=50)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        observations = response.json()["new_observations"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["observation_type"], "route_deviation")
        self.assertEqual(observations[0]["consecutive_fixes"], 3)
        reloaded = self.client.get("/api/journeys/active").json()["journey"]
        self.assertEqual(reloaded["observations"][0]["id"], observations[0]["id"])
        tracking_state = main.journey_engine.repository.get_journey_tracking_state(
            journey["journey_id"]
        )
        self.assertEqual(tracking_state["route_deviation_streak"], 3)
        self.assertTrue(tracking_state["route_deviation_active"])
        self.assertEqual(
            tracking_state["current_location"]["observed_at"],
            observations[0]["occurred_at"],
        )

    def test_concurrent_route_fixes_serialize_detector_state_and_observation(self):
        started_at = datetime.now(timezone.utc) - timedelta(seconds=80)
        journey = self.client.post(
            "/api/journeys",
            json={
                "destination": "Destination",
                "start_location": {
                    "latitude": 0,
                    "longitude": 0,
                    "accuracy_meters": 8,
                    "observed_at": started_at.isoformat(),
                },
                "planned_route": [
                    {"latitude": 0, "longitude": 0},
                    {"latitude": 0, "longitude": 0.02},
                ],
            },
        ).json()
        for second in (10, 20):
            seeded = self.client.post(
                f"/api/journeys/{journey['journey_id']}/locations",
                json={
                    "latitude": 0.01,
                    "longitude": 0.01,
                    "accuracy_meters": 10,
                    "speed_mps": 2,
                    "observed_at": (started_at + timedelta(seconds=second)).isoformat(),
                },
            )
            self.assertEqual(seeded.status_code, 200, seeded.text)
            self.assertEqual(seeded.json()["new_observations"], [])

        barrier = Barrier(2)

        def submit_fix(second):
            with TestClient(main.app) as client:
                barrier.wait()
                return client.post(
                    f"/api/journeys/{journey['journey_id']}/locations",
                    json={
                        "latitude": 0.01,
                        "longitude": 0.01,
                        "accuracy_meters": 10,
                        "speed_mps": 2,
                        "observed_at": (
                            started_at + timedelta(seconds=second)
                        ).isoformat(),
                    },
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit_fix, (30, 40)))

        successful_responses = [
            response for response in responses if response.status_code == 200
        ]
        self.assertGreaterEqual(len(successful_responses), 1)
        self.assertIn(
            sorted(response.status_code for response in responses),
            ([200, 200], [200, 409]),
        )
        generated_observations = [
            observation
            for response in successful_responses
            for observation in response.json()["new_observations"]
        ]
        self.assertEqual(len(generated_observations), 1)

        repository = main.journey_engine.repository
        persisted = repository.get_journey_tracking_state(journey["journey_id"])
        observations = repository.list_journey_observations(journey["journey_id"])
        accepted_times = [
            (started_at + timedelta(seconds=second)).isoformat()
            for second, response in zip((30, 40), responses)
            if response.status_code == 200
        ]
        self.assertEqual(
            persisted["route_deviation_streak"], 2 + len(successful_responses)
        )
        self.assertTrue(persisted["route_deviation_active"])
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["consecutive_fixes"], 3)
        self.assertEqual(
            persisted["current_location"]["observed_at"], max(accepted_times)
        )
        self.assertIn(observations[0]["occurred_at"], accepted_times)

    def test_gps_derived_stop_requires_threshold_duration_and_journey_can_end(self):
        main.event_engine.configure(
            MonitoringConfig(unexpected_stop_threshold_minutes=1)
        )
        started_at = datetime.now(timezone.utc) - timedelta(seconds=100)
        journey = self.client.post(
            "/api/journeys",
            json={
                "destination": "Office",
                "start_location": {
                    "latitude": 37.5,
                    "longitude": -122.2,
                    "accuracy_meters": 5,
                    "speed_mps": 0,
                    "observed_at": started_at.isoformat(),
                },
            },
        ).json()

        last_response = None
        for second in (30, 60, 90):
            last_response = self.client.post(
                f"/api/journeys/{journey['journey_id']}/locations",
                json={
                    "latitude": 37.5,
                    "longitude": -122.2,
                    "accuracy_meters": 5,
                    "speed_mps": 0,
                    "observed_at": (started_at + timedelta(seconds=second)).isoformat(),
                },
            )
            self.assertEqual(last_response.status_code, 200, last_response.text)
            if second < 90:
                self.assertEqual(last_response.json()["new_observations"], [])
        self.assertEqual(len(last_response.json()["new_observations"]), 1)
        self.assertEqual(
            last_response.json()["new_observations"][0]["observation_type"],
            "unexpected_stop",
        )
        self.assertEqual(
            last_response.json()["new_observations"][0]["expectedness"],
            "unknown",
        )

        ended = self.client.post(f"/api/journeys/{journey['journey_id']}/end")
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(ended.json()["status"], "completed")
        self.assertIsNotNone(ended.json()["ended_at"])
        self.assertIsNone(self.client.get("/api/journeys/active").json()["journey"])

    def test_contacts_can_be_added_without_exposing_destination(self):
        first = self.client.post(
            "/api/contacts",
            json={"name": "A", "channel": "email", "destination": "a@example.org"},
        )
        second = self.client.post(
            "/api/contacts",
            json={"name": "B", "channel": "sms", "destination": "+15551234567"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        listed = self.client.get("/api/contacts").json()["contacts"]
        self.assertEqual(len(listed), 2)
        self.assertNotIn("a@example.org", str(listed))
        self.assertNotIn("+15551234567", str(listed))

    def test_risk_assessment_api_contract_and_missing_signals(self):
        response = self.client.post("/api/risk/assessments", json={"demo_mode": True})

        self.assertEqual(response.status_code, 200, response.text)
        assessment = response.json()
        self.assertEqual(assessment["risk_level"], "low")
        self.assertEqual(assessment["risk_score"], 0)
        self.assertEqual(len(assessment["signals_used"]), 4)
        self.assertEqual(assessment["source"], "unknown")
        self.assertTrue(assessment["is_demo"])
        self.assertTrue(assessment["data_quality"]["missing_or_uncertain"])
        self.assertIn("assessment_id", self.client.get("/api/risk/assessments/latest").json())

    def test_risk_input_rejects_impossible_sensor_values(self):
        response = self.client.post(
            "/api/risk/assessments",
            json={"demo_mode": False, "vehicle": {"source": "real", "speed_kph": -5}},
        )

        self.assertEqual(response.status_code, 422)

    def test_critical_scenario_uses_mock_escalation_and_incident_idempotency(self):
        main.risk_engine.escalation_enabled = True
        payload = {
            "demo_mode": True,
            "incident_key": "scenario-critical-001",
            "gps": {"source": "simulated", "deviation_meters": 1500},
            "vehicle": {"source": "simulated", "stopped_minutes": 12, "sudden_braking": True},
            "check_in": {"source": "simulated", "responded": False, "overdue_minutes": 25},
        }
        first = self.client.post("/api/risk/assessments", json=payload)
        second = self.client.post("/api/risk/assessments", json=payload)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["risk_level"], "critical")
        self.assertEqual(first.json()["escalation_state"], "escalation_pending")
        processed = self.client.post(
            "/api/escalation/process",
            json={"assessment_id": first.json()["assessment_id"]},
        )
        self.assertEqual(processed.status_code, 200)
        self.assertEqual(processed.json()["state"], "notified")
        self.assertEqual(processed.json()["notification"]["provider"], "mock")
        self.assertIn("no person was contacted", processed.json()["notification"]["provider_response"]["message"])
        repeated = self.client.post(
            "/api/escalation/process",
            json={"assessment_id": second.json()["assessment_id"]},
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["notification"]["id"], processed.json()["notification"]["id"])
        self.assertEqual(len(self.client.get("/api/escalation/history").json()["notifications"]), 1)
        history = self.client.get("/api/escalation/history").json()
        self.assertTrue(history["history"])
        self.assertFalse(history["real_delivery_enabled"])

    def test_high_risk_can_be_cancelled_and_recorded(self):
        assessment = self.client.post(
            "/api/risk/assessments",
            json={
                "demo_mode": True,
                "gps": {"source": "simulated", "deviation_meters": 800},
                "check_in": {"source": "simulated", "responded": False, "overdue_minutes": 12},
            },
        )
        self.assertEqual(assessment.status_code, 200, assessment.text)
        self.assertEqual(assessment.json()["escalation_state"], "check_in_required")
        cancelled = self.client.post(
            "/api/escalation/cancel",
            json={"assessment_id": assessment.json()["assessment_id"], "reason": "Passenger confirmed"},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertFalse(cancelled.json()["real_notification_sent"])

    def test_critical_escalation_can_be_cancelled_before_mock_processing(self):
        main.risk_engine.escalation_enabled = True
        response = self.client.post(
            "/api/risk/assessments",
            json={
                "demo_mode": True,
                "gps": {"source": "simulated", "deviation_meters": 1000},
                "vehicle": {"source": "simulated", "stopped_minutes": 15, "sudden_braking": True},
                "check_in": {"source": "simulated", "responded": False, "overdue_minutes": 20},
            },
        )
        self.assertEqual(response.json()["escalation_state"], "escalation_pending")
        cancelled = self.client.post(
            "/api/escalation/cancel",
            json={"assessment_id": response.json()["assessment_id"]},
        )
        self.assertEqual(cancelled.json()["state"], "cancelled")

    def test_demo_reset_clears_simulated_risk_but_keeps_real_risk(self):
        simulated = self.client.post(
            "/api/risk/assessments", json={"demo_mode": True}
        ).json()
        real = self.client.post(
            "/api/risk/assessments",
            json={"demo_mode": False, "gps": {"source": "real", "deviation_meters": 30}},
        ).json()

        reset = self.client.delete("/api/events/demo")
        remaining = self.client.get("/api/risk/assessments").json()["assessments"]

        self.assertTrue(reset.json()["demo_risk_assessments_cleared"])
        self.assertEqual([item["assessment_id"] for item in remaining], [real["assessment_id"]])
        self.assertNotIn(simulated["assessment_id"], str(remaining))


class EventEngineTests(unittest.TestCase):
    def test_rule_rejects_route_signal_below_custom_threshold(self):
        engine = EventEngine()
        engine.configure(main.MonitoringConfig(route_deviation_threshold_meters=1000))
        with self.assertRaisesRegex(ValueError, "below"):
            engine.create_demo_event(
                main.DemoEventRequest(
                    event_type="route_deviation", route_deviation_meters=999
                )
            )


if __name__ == "__main__":
    unittest.main()