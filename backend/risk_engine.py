"""Explainable multi-signal scoring and safe, demo-gated escalation."""

from datetime import datetime, timezone
import os
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.database import PersistenceError, SQLiteRepository


SignalSource = Literal["real", "simulated", "unknown"]


class CameraSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool | None = None
    source: SignalSource = "unknown"
    confidence: float | None = Field(default=None, ge=0, le=1)
    person_count: int | None = Field(default=None, ge=0, le=1000)
    count_change: int | None = Field(default=None, ge=-1000, le=1000)
    pose_keypoint_observations: int | None = Field(default=None, ge=0, le=1000)
    observed_at: datetime | None = None
    reliability: float = Field(default=1, ge=0, le=1)


class LocationSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SignalSource = "unknown"
    deviation_meters: float | None = Field(default=None, ge=0, le=1_000_000)
    deviation_threshold_meters: float | None = Field(default=None, ge=1, le=100_000)
    accuracy_meters: float | None = Field(default=None, ge=0, le=100_000)
    stop_duration_minutes: float | None = Field(default=None, ge=0, le=1440)
    stop_threshold_minutes: int | None = Field(default=None, ge=1, le=240)
    approved_route_change: bool = False
    stale: bool = False
    observed_at: datetime | None = None
    reliability: float = Field(default=1, ge=0, le=1)


class VehicleSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SignalSource = "unknown"
    speed_kph: float | None = Field(default=None, ge=0, le=500)
    ignition_on: bool | None = None
    stopped_minutes: int | None = Field(default=None, ge=0, le=1440)
    sudden_braking: bool | None = None
    observed_at: datetime | None = None
    reliability: float = Field(default=1, ge=0, le=1)


class CheckInSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SignalSource = "unknown"
    responded: bool | None = None
    overdue_minutes: int | None = Field(default=None, ge=0, le=10_080)
    observed_at: datetime | None = None
    reliability: float = Field(default=1, ge=0, le=1)


class RiskAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_key: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    journey_id: str | None = Field(default=None, min_length=1, max_length=64)
    event_id: str | None = Field(default=None, min_length=1, max_length=64)
    camera: CameraSignal | None = None
    gps: LocationSignal | None = None
    vehicle: VehicleSignal | None = None
    check_in: CheckInSignal | None = None
    demo_mode: bool = True

    @model_validator(mode="after")
    def require_supplied_sensor_sources(self):
        for name in ("camera", "gps", "vehicle", "check_in"):
            signal = getattr(self, name)
            if signal and signal.source == "real" and self.demo_mode:
                raise ValueError("real signals cannot be submitted with demo_mode enabled")
        return self


class EscalationCancelRequest(BaseModel):
    assessment_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="Passenger cancelled check-in", min_length=1, max_length=200)


class EscalationProcessRequest(BaseModel):
    assessment_id: str = Field(min_length=1, max_length=64)


class RiskPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_deviation_meters: int = Field(default=500, ge=50, le=100_000)
    unexpected_stop_minutes: int = Field(default=5, ge=1, le=240)
    missed_check_in_minutes: int = Field(default=10, ge=1, le=1440)
    stale_after_seconds: int = Field(default=300, ge=30, le=86_400)
    escalation_enabled: bool = False


class MockNotificationProvider:
    """A deterministic mock. It never sends network traffic or messages."""

    name = "mock"

    def send(self, _assessment: dict, _idempotency_key: str) -> dict:
        return {
            "status": "delivered",
            "confirmed": True,
            "response": {"mock": True, "message": "Simulated delivery only; no person was contacted."},
        }


class RiskEngine:
    def __init__(self, repository: SQLiteRepository, *, escalation_enabled: bool | None = None,
                 provider=None, stale_after_seconds: int = 300):
        self.repository = repository
        self._environment_escalation_enabled = (
            os.getenv("ESENTINEL_ESCALATION_ENABLED", "false").casefold() == "true"
            if escalation_enabled is None else escalation_enabled
        )
        self.escalation_enabled = self._environment_escalation_enabled
        self.provider = provider or MockNotificationProvider()
        self.stale_after_seconds = stale_after_seconds
        self.policy = RiskPolicyConfig(stale_after_seconds=stale_after_seconds)

    def initialize(self) -> None:
        stored = self.repository.load_risk_policy()
        if stored:
            self.policy = RiskPolicyConfig.model_validate(stored)
            self.stale_after_seconds = self.policy.stale_after_seconds
            self.escalation_enabled = self._environment_escalation_enabled and self.policy.escalation_enabled

    def configure(self, policy: RiskPolicyConfig) -> dict:
        self.repository.save_risk_policy(policy.model_dump())
        self.policy = policy
        self.stale_after_seconds = policy.stale_after_seconds
        self.escalation_enabled = self._environment_escalation_enabled and policy.escalation_enabled
        return {
            **policy.model_dump(),
            "escalation_enabled": self.escalation_enabled,
            "provider": self.provider.name,
            "environment_gate_enabled": self._environment_escalation_enabled,
            "real_delivery_enabled": False,
        }

    def current_config(self) -> dict:
        return {
            **self.policy.model_dump(),
            "escalation_enabled": self.escalation_enabled,
            "provider": self.provider.name,
            "environment_gate_enabled": self._environment_escalation_enabled,
            "real_delivery_enabled": False,
        }

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _fresh_signal(self, signal, now: datetime) -> tuple[bool, str | None]:
        if signal is None:
            return False, "not supplied"
        if signal.source == "unknown":
            return False, "source unknown"
        if signal.reliability < 0.25:
            return False, "reliability below 0.25"
        if signal.observed_at is not None:
            timestamp = signal.observed_at
            if timestamp.tzinfo is None:
                return False, "timestamp has no timezone"
            age = (now - timestamp.astimezone(timezone.utc)).total_seconds()
            if age > self.stale_after_seconds:
                return False, "stale observation"
            if age < -30:
                return False, "timestamp is in the future"
        if getattr(signal, "stale", False):
            return False, "marked stale"
        return True, None

    def assess(self, request: RiskAssessmentRequest) -> dict:
        now = self._utc_now()
        score = 0
        reasons: list[dict] = []
        signals_used: list[dict] = []
        missing: list[str] = []
        corroborating: set[str] = set()

        def consider(name: str, signal, checks: list[tuple[bool, int, str]], *, weak=False):
            nonlocal score
            usable, unavailable_reason = self._fresh_signal(signal, now)
            if not usable:
                missing.append(f"{name}: {unavailable_reason}")
                signals_used.append({"name": name, "source": "unknown", "status": "unknown", "reliability": None})
                return
            signals_used.append({
                "name": name,
                "source": signal.source,
                "status": "available",
                "reliability": signal.reliability,
                "observed_at": signal.observed_at.isoformat() if signal.observed_at else None,
            })
            signal_points = 0
            signal_reasons = []
            for active, points, explanation in checks:
                if active:
                    applied = round(points * signal.reliability)
                    signal_points += applied
                    signal_reasons.append({"signal": name, "points": applied, "explanation": explanation})
            score += signal_points
            reasons.extend(signal_reasons)
            if signal_points >= 15 and not weak:
                corroborating.add(name)

        camera = request.camera
        if camera is not None and (camera.available is False or (
            camera.confidence is not None and camera.confidence < 0.25
        )):
            consider("camera", None, [])
            if camera.confidence is not None and camera.confidence < 0.25:
                missing.append("camera: low detection confidence; observation is uncertain")
        else:
            consider("camera", camera, [
                (camera is not None and camera.count_change is not None and abs(camera.count_change) >= 4,
                 8, "A change in frame-level person detections was observed; this is a weak context signal only."),
                (camera is not None and camera.confidence is not None and camera.confidence < 0.25,
                 0, "Camera confidence is low; visual status is uncertain."),
            ], weak=True)
            if camera is not None and camera.confidence is not None and camera.confidence < 0.25:
                missing.append("camera: low detection confidence; observation is uncertain")

        gps = request.gps
        gps_checks = []
        deviation_threshold = (
            gps.deviation_threshold_meters
            if gps is not None and gps.deviation_threshold_meters is not None
            else self.policy.route_deviation_meters
        )
        stop_threshold = (
            gps.stop_threshold_minutes
            if gps is not None and gps.stop_threshold_minutes is not None
            else self.policy.unexpected_stop_minutes
        )
        if gps is not None and gps.approved_route_change:
            gps_checks.append((False, 0, "Passenger-approved route change; no deviation penalty."))
        elif gps is not None and gps.accuracy_meters is not None and gps.accuracy_meters > 100:
            missing.append("gps: accuracy too poor for route scoring")
            gps = None
        else:
            gps_checks.append((gps is not None and gps.deviation_meters is not None and gps.deviation_meters >= deviation_threshold,
                               30, f"Route deviation is at least {deviation_threshold:g} m."))
        gps_checks.append((
            gps is not None and gps.stop_duration_minutes is not None
            and gps.stop_duration_minutes >= stop_threshold,
            25,
            f"GPS-derived low movement persisted for at least {stop_threshold} minutes; stop context is unknown.",
        ))
        consider("gps", gps, gps_checks)

        vehicle = request.vehicle
        consider("vehicle", vehicle, [
            (vehicle is not None and vehicle.stopped_minutes is not None and vehicle.stopped_minutes >= self.policy.unexpected_stop_minutes,
             25, f"Vehicle stop duration is at least {self.policy.unexpected_stop_minutes} minutes."),
            (vehicle is not None and vehicle.sudden_braking is True,
             20, "Sudden braking was reported by the supplied vehicle signal."),
        ])
        check_in = request.check_in
        consider("check_in", check_in, [
            (check_in is not None and check_in.responded is False and (check_in.overdue_minutes or 0) >= self.policy.missed_check_in_minutes,
             35, f"Passenger check-in is unanswered and at least {self.policy.missed_check_in_minutes} minutes overdue."),
        ])
        score = min(100, score)
        level = "low" if score < 20 else "medium" if score < 45 else "high" if score < 70 else "critical"
        reasons.insert(0, {"signal": "scoring", "points": score, "explanation": "Transparent weighted rule total; not a probability of harm."})

        if level in ("low", "medium"):
            escalation_state = "monitoring"
            action = "Continue monitoring; no external alert is configured."
        elif level == "high":
            escalation_state = "check_in_required"
            action = "Request a discreet passenger check-in; do not assume safety if unanswered."
        else:
            safe_to_escalate = len(corroborating) >= 2 and len(missing) <= 2
            if self.escalation_enabled and safe_to_escalate:
                escalation_state = "escalation_pending"
                action = "Critical score with corroborating signals; mock escalation policy will run."
            else:
                escalation_state = "check_in_required"
                action = "Critical score, but escalation is disabled or lacks corroboration; request a check-in."

        supplied_sources = [
            getattr(request, name).source
            for name in ("camera", "gps", "vehicle", "check_in")
            if getattr(request, name) is not None
        ]
        assessment_source = (
            "real" if "real" in supplied_sources
            else "simulated" if "simulated" in supplied_sources
            else "unknown"
        )
        assessment = {
            "assessment_id": str(uuid4()),
            "incident_key": request.incident_key,
            "journey_id": request.journey_id,
            "event_id": request.event_id,
            "timestamp": now.isoformat(),
            "risk_score": score,
            "risk_level": level,
            "reasons": reasons,
            "signals_used": signals_used,
            "data_quality": {"missing_or_uncertain": missing, "corroborating_signal_count": len(corroborating)},
            "recommended_action": action,
            "source": assessment_source,
            "is_demo": request.demo_mode,
            "disclaimer": "Rule-based prototype score, not a probability or validated prediction of harm.",
        }
        if camera is not None and camera.pose_keypoint_observations is not None:
            signals_used.append({
                "name": "pose_keypoints",
                "source": camera.source,
                "status": "observed_not_scored",
                "observation_count": camera.pose_keypoint_observations,
                "reliability": camera.reliability,
            })
            reasons.append({
                "signal": "pose_keypoints",
                "points": 0,
                "explanation": "Pose keypoints are not interpreted as distress, intent, or danger and do not affect this score.",
            })

        saved, initial_state, created = self.repository.save_assessment_with_initial_state(
            assessment, escalation_state, action
        )
        assessment.update(saved)
        current_state = initial_state
        if not created:
            current_state = self.repository.latest_escalation_for(
                assessment["assessment_id"]
            ) or initial_state
        assessment["escalation_state"] = current_state["state"]
        assessment["escalation_history"] = [current_state]
        assessment["is_duplicate"] = not created
        notification = None
        if not created and assessment.get("incident_key"):
            notification = self.repository.get_notification_by_key(
                f"incident:{assessment['incident_key']}"
            )
        assessment["notification"] = notification
        return assessment

    def assess_journey_observation(
        self, journey_id: str, observation: dict, event_id: str
    ) -> dict:
        observation_type = observation["observation_type"]
        details = observation
        if observation_type == "route_deviation":
            gps = LocationSignal(
                source="real",
                deviation_meters=details["deviation_meters"],
                deviation_threshold_meters=details["threshold_meters"],
                accuracy_meters=details["accuracy_meters"],
                observed_at=datetime.fromisoformat(observation["occurred_at"]),
            )
        elif observation_type == "unexpected_stop":
            gps = LocationSignal(
                source="real",
                accuracy_meters=details.get("accuracy_meters", 0),
                stop_duration_minutes=details["stationary_minutes"],
                stop_threshold_minutes=details["threshold_minutes"],
                observed_at=datetime.fromisoformat(observation["occurred_at"]),
            )
        else:
            raise ValueError("Unsupported journey observation type.")

        request = RiskAssessmentRequest(
            incident_key=f"journeyobs:{observation['id']}",
            journey_id=journey_id,
            event_id=event_id,
            gps=gps,
            demo_mode=False,
        )
        return self.assess(request)

    def process_pending(self, assessment_id: str) -> dict:
        assessment = self.repository.get_risk_assessment(assessment_id)
        if assessment is None:
            raise ValueError("Risk assessment was not found.")
        latest = self.repository.latest_escalation_for(assessment_id)
        if latest is not None and latest["state"] in {"notified", "delivery_failed"}:
            notification_key = (
                f"incident:{assessment['incident_key']}"
                if assessment.get("incident_key")
                else f"risk:{assessment_id}"
            )
            attempt = self.repository.get_notification_by_key(notification_key)
            return {
                "assessment_id": assessment_id,
                "state": latest["state"],
                "notification": attempt,
                "transition": latest,
                "real_notification_sent": False,
                "idempotent_replay": True,
            }
        if latest is None or latest["state"] != "escalation_pending":
            raise ValueError("This assessment has no pending escalation to process.")
        notification = self._notify(assessment)
        if notification["status"] == "delivered":
            state = "notified"
        elif notification["status"] == "failed":
            state = "delivery_failed"
        else:
            state = "escalation_pending"
        record = None
        if state != "escalation_pending":
            outcome = (
                "simulated_delivered"
                if notification["provider"] == "mock" and notification["status"] == "delivered"
                else notification["status"]
            )
            record, _ = self.repository.transition_escalation_state(
                assessment_id,
                state,
                "Mock provider result; no real contact was contacted.",
                assessment["is_demo"],
                {"escalation_pending"},
                event_type="demo_escalation_delivery" if assessment["is_demo"] else "risk_escalation_delivery",
                provider_mode=notification["provider"],
                delivery_outcome=outcome,
            )
        return {
            "assessment_id": assessment_id,
            "state": state,
            "notification": notification,
            "transition": record,
            "real_notification_sent": False,
        }

    def request_demo_escalation(self, assessment_id: str) -> dict:
        assessment = self.repository.get_risk_assessment(assessment_id)
        if assessment is None:
            raise ValueError("Risk assessment was not found.")
        if not assessment["is_demo"]:
            raise ValueError("Only explicitly simulated assessments can use the demo escalation workflow.")
        if assessment["risk_level"] not in {"high", "critical"}:
            raise ValueError("The demo escalation workflow is available only for high or critical rule-based assessments.")
        record, created = self.repository.transition_escalation_state(
            assessment_id,
            "escalation_pending",
            "User explicitly requested the simulated escalation workflow.",
            True,
            {"check_in_required", "escalation_pending"},
            event_type="demo_escalation_requested",
            provider_mode="mock",
        )
        return {
            "assessment_id": assessment_id,
            "state": "escalation_pending",
            "transition": record,
            "idempotent_replay": not created,
            "real_notification_sent": False,
        }

    def respond_to_check_in(self, assessment_id: str) -> dict:
        assessment = self.repository.get_risk_assessment(assessment_id)
        if assessment is None:
            raise ValueError("Risk assessment was not found.")
        latest = self.repository.latest_escalation_for(assessment_id)
        if latest is None or latest["state"] not in {
            "check_in_required", "escalation_pending"
        }:
            raise ValueError("This assessment is not waiting for a passenger check-in.")
        occurred_at = self._utc_now().isoformat()
        self.repository.save_passenger_check_in(occurred_at, assessment_id)
        transition, _ = self.repository.transition_escalation_state(
            assessment_id,
            "monitoring",
            "Passenger explicitly responded to the check-in; this is not a safety determination.",
            assessment["is_demo"],
            {"check_in_required", "escalation_pending"},
            event_type="passenger_check_in",
            provider_mode="none",
        )
        return {
            "assessment_id": assessment_id,
            "check_in_status": "passenger_responded",
            "occurred_at": occurred_at,
            "escalation_state": "monitoring",
            "transition": transition,
            "safety_assessment": "UNKNOWN",
        }

    def _notify(self, assessment: dict) -> dict:
        key = f"incident:{assessment['incident_key']}" if assessment.get("incident_key") else f"risk:{assessment['assessment_id']}"
        attempt, should_send = self.repository.reserve_notification_attempt({
            "id": str(uuid4()),
            "assessment_id": assessment["assessment_id"],
            "idempotency_key": key,
            "provider": self.provider.name,
            "attempted_at": self._utc_now().isoformat(),
        })
        if not should_send:
            return attempt
        try:
            result = self.provider.send(assessment, key)
            status = result.get("status") if result.get("status") in {
                "delivered", "failed", "skipped_not_configured"
            } else "failed"
            provider_response = result.get("response", {})
            if status == "delivered" and result.get("confirmed") is not True:
                status = "failed"
                provider_response = {"mock": self.provider.name == "mock", "confirmed": False}
                error = "Provider did not confirm delivery."
            else:
                error = None
        except Exception:
            status = "failed"
            provider_response = {"mock": self.provider.name == "mock"}
            error = "Notification provider failed; details were withheld."
        return self.repository.finish_notification_attempt(
            attempt["id"], status, provider_response, error
        )

    def cancel(self, assessment_id: str, reason: str) -> dict:
        assessment = self.repository.get_risk_assessment(assessment_id)
        if assessment is None:
            raise ValueError("Risk assessment was not found.")
        latest = self.repository.latest_escalation_for(assessment_id)
        if latest is None or latest["assessment_id"] != assessment_id or latest["state"] not in {
            "check_in_required", "escalation_pending"
        }:
            raise ValueError("This assessment has no cancellable pending escalation.")
        record, created = self.repository.transition_escalation_state(
            assessment_id,
            "cancelled",
            reason,
            assessment["is_demo"],
            {"check_in_required", "escalation_pending"},
            event_type="escalation_cancelled",
            provider_mode="none",
        )
        return {
            "state": "cancelled",
            "record": record,
            "idempotent_replay": not created,
            "real_notification_sent": False,
        }

    def escalation_history(self, limit: int = 50) -> list[dict]:
        records = self.repository.list_escalations(limit)
        notifications = self.repository.list_notifications(limit)
        by_assessment = {}
        for notification in notifications:
            by_assessment.setdefault(notification["assessment_id"], []).append(notification)
        for record in records:
            record["notifications"] = by_assessment.get(record["assessment_id"], [])
        return records

    def latest(self) -> dict | None:
        items = self.repository.list_risk_assessments(1)
        if not items:
            return None
        assessment = items[0]
        escalation = self.repository.latest_escalation_for(
            assessment["assessment_id"]
        )
        assessment["escalation_state"] = (
            escalation["state"] if escalation and escalation["assessment_id"] == assessment["assessment_id"]
            else "monitoring"
        )
        return assessment

    def get_assessment(self, assessment_id: str) -> dict | None:
        assessment = self.repository.get_risk_assessment(assessment_id)
        if assessment is None:
            return None
        escalation = self.repository.latest_escalation_for(assessment_id)
        assessment["escalation_state"] = escalation["state"] if escalation else "monitoring"
        return assessment

    def history(self, limit: int = 50) -> list[dict]:
        return self.repository.list_risk_assessments(limit)