import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.database import PersistenceError, SQLiteRepository
from backend.event_engine import EventEngine, MonitoringConfig


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