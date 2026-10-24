from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from backend.database import SQLiteRepository
from backend.risk_engine import RiskAssessmentRequest, RiskEngine


class FailingProvider:
    name = "mock_failure"

    def send(self, _assessment, _key):
        raise RuntimeError("sensitive provider detail")


class UnconfiguredProvider:
    name = "mock_unconfigured"

    def send(self, _assessment, _key):
        return {"status": "skipped_not_configured", "response": {"mock": True}}


class RiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SQLiteRepository(Path(self.temp_dir.name) / "risk.db")
        self.repository.initialize()
        self.engine = RiskEngine(self.repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def assess(self, payload):
        return self.engine.assess(RiskAssessmentRequest.model_validate(payload))

    def test_low_and_medium_scores(self):
        low = self.assess({"demo_mode": True})
        medium = self.assess({
            "demo_mode": True,
            "vehicle": {"source": "simulated", "stopped_minutes": 6},
        })

        self.assertEqual(low["risk_level"], "low")
        self.assertEqual(medium["risk_level"], "medium")
        self.assertEqual(medium["risk_score"], 25)

    def test_high_score_from_unanswered_checkin(self):
        assessment = self.assess({
            "demo_mode": True,
            "check_in": {"source": "simulated", "responded": False, "overdue_minutes": 15},
            "vehicle": {"source": "simulated", "stopped_minutes": 6},
        })

        self.assertEqual(assessment["risk_level"], "high")
        self.assertEqual(assessment["escalation_state"], "check_in_required")

    def test_critical_requires_multiple_reliable_signals_and_feature_flag(self):
        engine = RiskEngine(self.repository, escalation_enabled=False)
        payload = {
            "demo_mode": True,
            "gps": {"source": "simulated", "deviation_meters": 1000},
            "vehicle": {"source": "simulated", "stopped_minutes": 15, "sudden_braking": True},
            "check_in": {"source": "simulated", "responded": False, "overdue_minutes": 20},
        }
        assessment = engine.assess(RiskAssessmentRequest.model_validate(payload))

        self.assertEqual(assessment["risk_level"], "critical")
        self.assertEqual(assessment["escalation_state"], "check_in_required")
        self.assertIsNone(assessment["notification"])

    def test_enabled_critical_scenario_calls_only_mock_provider(self):
        engine = RiskEngine(self.repository, escalation_enabled=True)
        assessment = engine.assess(RiskAssessmentRequest.model_validate({
            "demo_mode": True,
            "incident_key": "test-critical",
            "gps": {"source": "simulated", "deviation_meters": 1000},
            "vehicle": {"source": "simulated", "stopped_minutes": 15, "sudden_braking": True},
            "check_in": {"source": "simulated", "responded": False, "overdue_minutes": 20},
        }))

        self.assertEqual(assessment["escalation_state"], "escalation_pending")
        self.assertIsNone(assessment["notification"])

        processed = engine.process_pending(assessment["assessment_id"])
        self.assertEqual(processed["state"], "notified")
        self.assertEqual(processed["notification"]["provider"], "mock")
        self.assertFalse(processed["notification"]["provider_response"].get("real_contacted", False))
        self.assertFalse(processed["real_notification_sent"])

    def test_stale_low_reliability_and_missing_inputs_are_unknown(self):
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assessment = self.assess({
            "demo_mode": True,
            "gps": {"source": "simulated", "deviation_meters": 5000, "observed_at": stale_time},
            "vehicle": {"source": "simulated", "stopped_minutes": 30, "reliability": 0.1},
        })

        self.assertEqual(assessment["risk_score"], 0)
        self.assertEqual(assessment["risk_level"], "low")
        self.assertIn("stale observation", " ".join(assessment["data_quality"]["missing_or_uncertain"]))
        self.assertIn("reliability below", " ".join(assessment["data_quality"]["missing_or_uncertain"]))

    def test_approved_route_change_and_ordinary_stop_do_not_score(self):
        assessment = self.assess({
            "demo_mode": True,
            "gps": {"source": "simulated", "deviation_meters": 5000, "approved_route_change": True},
            "vehicle": {"source": "simulated", "stopped_minutes": 2, "speed_kph": 0},
        })

        self.assertEqual(assessment["risk_score"], 0)
        self.assertEqual(assessment["risk_level"], "low")

    def test_low_confidence_camera_is_unknown_and_pose_is_never_scored(self):
        assessment = self.assess({
            "demo_mode": True,
            "camera": {
                "source": "simulated",
                "available": True,
                "confidence": 0.1,
                "count_change": 20,
                "pose_keypoint_observations": 4,
            },
        })

        self.assertEqual(assessment["risk_score"], 0)
        self.assertIn("low detection confidence", " ".join(assessment["data_quality"]["missing_or_uncertain"]))
        pose = next(item for item in assessment["signals_used"] if item["name"] == "pose_keypoints")
        self.assertEqual(pose["status"], "observed_not_scored")
        pose_reason = next(item for item in assessment["reasons"] if item["signal"] == "pose_keypoints")
        self.assertEqual(pose_reason["points"], 0)

    def test_failed_and_unconfigured_provider_states_are_audited(self):
        payload = RiskAssessmentRequest.model_validate({
            "demo_mode": True,
            "gps": {"source": "simulated", "deviation_meters": 1000},
            "vehicle": {"source": "simulated", "stopped_minutes": 15, "sudden_braking": True},
            "check_in": {"source": "simulated", "responded": False, "overdue_minutes": 20},
        })
        failed_engine = RiskEngine(self.repository, escalation_enabled=True, provider=FailingProvider())
        failed = failed_engine.assess(payload)
        failed = failed_engine.process_pending(failed["assessment_id"])
        self.assertEqual(failed["state"], "delivery_failed")
        self.assertNotIn("sensitive", failed["notification"].get("error_detail", ""))

        skipped_engine = RiskEngine(self.repository, escalation_enabled=True, provider=UnconfiguredProvider())
        skipped = skipped_engine.assess(payload)
        skipped = skipped_engine.process_pending(skipped["assessment_id"])
        self.assertEqual(skipped["notification"]["status"], "skipped_not_configured")
        self.assertEqual(skipped["state"], "escalation_pending")


if __name__ == "__main__":
    unittest.main()