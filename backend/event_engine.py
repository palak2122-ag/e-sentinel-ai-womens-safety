from datetime import datetime, timezone
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.database import SQLiteRepository


EventType = Literal[
    "route_deviation",
    "unexpected_stop",
    "missed_check_in",
    "cabin_person_count_change",
    "user_sos",
]


class DemoEventRequest(BaseModel):
    event_type: EventType
    route_deviation_meters: float | None = Field(default=None, ge=0, le=100_000)
    stop_minutes: int | None = Field(default=None, ge=1, le=1440)
    overdue_minutes: int | None = Field(default=None, ge=1, le=1440)
    previous_count: int | None = Field(default=None, ge=0, le=1000)
    current_count: int | None = Field(default=None, ge=0, le=1000)
    note: str | None = Field(default=None, max_length=200)


class MonitoringConfig(BaseModel):
    route_deviation_threshold_meters: int = Field(default=500, ge=50, le=10_000)
    unexpected_stop_threshold_minutes: int = Field(default=5, ge=1, le=120)
    check_in_interval_minutes: int = Field(default=30, ge=5, le=720)


class ContactInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    channel: Literal["email", "sms"]
    destination: str = Field(min_length=3, max_length=160)


class ContactListInput(BaseModel):
    contacts: list[ContactInput] = Field(max_length=5)


class AssistantQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class NotificationTestRequest(BaseModel):
    contact_id: str | None = None


class EventEngine:
    """Event rules and non-sensitive session state backed by SQLite."""

    def __init__(self, repository: SQLiteRepository | None = None):
        self._lock = RLock()
        self.repository = repository or SQLiteRepository()
        self.config = MonitoringConfig()
        self.contacts: list[dict] = []
        self.last_cabin_peak: int | None = None
        self.check_in_status = "simulated_pending"
        self.checked_in_at: str | None = None

    def initialize(self) -> None:
        self.repository.initialize()
        config = self.repository.load_config()
        check_in = self.repository.load_check_in()
        last_peak = self.repository.load_last_cabin_peak()
        with self._lock:
            if config is not None:
                self.config = MonitoringConfig.model_validate(config)
            if check_in is not None:
                self.check_in_status = check_in["status"]
                self.checked_in_at = check_in["occurred_at"]
            self.last_cabin_peak = last_peak

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_demo_event(
        self,
        request: DemoEventRequest,
        cabin_peak_to_save: int | None = None,
    ) -> dict:
        event_type = request.event_type
        severity = "info"
        fact = ""
        rule = "User explicitly created this demo event."
        inference = "No safety conclusion is made."

        if event_type == "route_deviation":
            distance = request.route_deviation_meters
            if distance is None:
                raise ValueError("route_deviation_meters is required for this event.")
            threshold = self.config.route_deviation_threshold_meters
            fact = f"Simulated route offset: {distance:g} m."
            rule = f"Flag when simulated route offset is at least {threshold} m."
            if distance < threshold:
                raise ValueError(f"Route offset is below the configured {threshold} m threshold.")
            severity = "high" if distance >= threshold * 2 else "medium"
            inference = "A simulated route offset crossed the configured threshold; no live route was checked."
        elif event_type == "unexpected_stop":
            minutes = request.stop_minutes
            if minutes is None:
                raise ValueError("stop_minutes is required for this event.")
            threshold = self.config.unexpected_stop_threshold_minutes
            fact = f"Simulated stop duration: {minutes} min."
            rule = f"Flag when simulated stop duration is at least {threshold} min."
            if minutes < threshold:
                raise ValueError(f"Stop duration is below the configured {threshold} min threshold.")
            severity = "high" if minutes >= threshold * 3 else "medium"
            inference = "A simulated stop exceeded the configured duration; no vehicle telemetry was checked."
        elif event_type == "missed_check_in":
            minutes = request.overdue_minutes
            if minutes is None:
                raise ValueError("overdue_minutes is required for this event.")
            threshold = self.config.check_in_interval_minutes
            fact = f"Simulated check-in is {minutes} min overdue."
            rule = f"Flag when a simulated check-in is at least {threshold} min overdue."
            if minutes < threshold:
                raise ValueError(f"Check-in is below the configured {threshold} min interval.")
            severity = "high" if minutes >= threshold * 2 else "medium"
            inference = "A simulated check-in is overdue; this does not establish that a person is unsafe."
        elif event_type == "cabin_person_count_change":
            previous = request.previous_count
            current = request.current_count
            if previous is None or current is None:
                raise ValueError("previous_count and current_count are required for this event.")
            change = abs(current - previous)
            fact = f"Frame-level person detections changed from {previous} to {current}."
            rule = "A change of at least two frame-level detections is marked medium; smaller changes are informational."
            severity = "medium" if change >= 2 else "info"
            inference = "This compares detections in frames; it does not identify or count unique people."
        elif event_type == "user_sos":
            fact = "The user explicitly activated the SOS demo control."
            rule = "SOS events are only created after explicit user action."
            severity = "critical"
            inference = "This is a demo event; no contact or emergency service was notified."

        event = {
            "id": str(uuid4()),
            "event_type": event_type,
            "severity": severity,
            "occurred_at": self._now(),
            "source": {
                "user_sos": "user_demo",
                "cabin_person_count_change": "uploaded_video_yolo",
            }.get(event_type, "demo_simulation"),
            "is_demo": event_type != "cabin_person_count_change",
            "observed_fact": fact,
            "rule": rule,
            "inferred_signal": inference,
            "unknown": "Harassment, intent, identity, and personal safety are unknown.",
            "note": request.note,
        }
        check_in_status = "simulated_overdue" if event_type == "missed_check_in" else None
        if cabin_peak_to_save is not None:
            self.repository.save_cabin_observation(event, cabin_peak_to_save)
        else:
            self.repository.save_event(event, check_in_status=check_in_status)
        with self._lock:
            if check_in_status is not None:
                self.check_in_status = check_in_status
                self.checked_in_at = None
            if cabin_peak_to_save is not None:
                self.last_cabin_peak = cabin_peak_to_save
        return event

    def record_cabin_peak(self, current_count: int) -> dict | None:
        with self._lock:
            previous = self.last_cabin_peak
        if previous is None or previous == current_count:
            self.repository.save_cabin_observation(None, current_count)
            with self._lock:
                self.last_cabin_peak = current_count
            return None
        return self.create_demo_event(
            DemoEventRequest(
                event_type="cabin_person_count_change",
                previous_count=previous,
                current_count=current_count,
            ),
            cabin_peak_to_save=current_count,
        )

    def list_events(self, limit: int = 50) -> list[dict]:
        return self.repository.list_events(limit)

    def reset(self) -> dict:
        deleted = self.repository.reset_demo_state()
        with self._lock:
            self.last_cabin_peak = None
            self.check_in_status = "simulated_pending"
            self.checked_in_at = None
        return {
            "status": "reset",
            "events_cleared": True,
            "deleted_demo_events": deleted,
            "demo_data_only": True,
            "non_demo_events_preserved": True,
            "demo_risk_assessments_cleared": True,
            "demo_escalation_records_cleared": True,
        }

    def configure(self, config: MonitoringConfig) -> dict:
        self.repository.save_config(config.model_dump())
        with self._lock:
            self.config = config
        return self.monitoring_state()

    def monitoring_state(self) -> dict:
        latest_events = self.repository.list_events(1)
        with self._lock:
            recent = latest_events[0] if latest_events else None
            config = self.config.model_dump()
        count = len(self.repository.list_events(200))
        return {
            "monitoring_mode": "local_demo",
            "is_live_monitoring": False,
            "location_provider": "simulated",
            "location_label": "Simulated route only; no device location is read.",
            "map_route": [
                {"x": 8, "y": 68},
                {"x": 25, "y": 47},
                {"x": 46, "y": 57},
                {"x": 66, "y": 30},
                {"x": 91, "y": 42},
            ],
            "current_waypoint": 3,
            "check_in_status": self.check_in_status,
            "checked_in_at": self.checked_in_at,
            "configuration": config,
            "event_count": count,
            "latest_event": recent,
            "risk_level": "UNKNOWN",
            "risk_explanation": "Rules classify demo event urgency only; they do not predict personal safety.",
        }

    def summary(self) -> dict:
        events = self.list_events(200)
        severity_counts = {name: 0 for name in ("info", "low", "medium", "high", "critical")}
        for event in events:
            severity_counts[event["severity"]] += 1
        if not events:
            summary = "No events recorded in this demo session."
        else:
            latest = events[0]
            summary = (
                f"{len(events)} demo event(s) recorded. Latest: "
                f"{latest['event_type'].replace('_', ' ')} ({latest['severity']}) "
                f"at {latest['occurred_at']}. {latest['observed_fact']}"
            )
        return {
            "summary": summary,
            "event_count": len(events),
            "severity_counts": severity_counts,
            "events": events[:10],
            "generated_at": self._now(),
            "mode": "local_demo",
            "safety_assessment": "UNKNOWN",
        }

    def ask(self, question: str) -> dict:
        normalized = question.casefold()
        summary = self.summary()
        events = summary["events"]
        if any(word in normalized for word in ("why", "rule", "flag", "reason")) and events:
            event = events[0]
            answer = f"{event['event_type'].replace('_', ' ').title()} was classified {event['severity']}. {event['observed_fact']} Rule: {event['rule']} Unknown: {event['unknown']}"
            sources = [event["id"]]
        elif any(word in normalized for word in ("event", "happened", "summary", "status")):
            answer = summary["summary"]
            sources = [event["id"] for event in events[:5]]
        elif not events:
            answer = "There are no events in the current demo session. You can create a labelled simulation from Journey monitoring. No live location or safety data is connected."
            sources = []
        else:
            answer = "I can summarize recent events or explain the latest rule. " + summary["summary"] + " Safety remains unknown; no live location provider is connected."
            sources = [event["id"] for event in events[:5]]
        return {
            "answer": answer,
            "mode": "local_rules_assistant",
            "provider_called": False,
            "event_sources": sources,
            "disclaimer": "Uses this local demo's structured state only; it is not emergency guidance.",
        }

    def set_contacts(self, contacts: list[ContactInput]) -> list[dict]:
        normalized = []
        for contact in contacts:
            destination = contact.destination.strip()
            if contact.channel == "email" and ("@" not in destination or "." not in destination.split("@")[-1]):
                raise ValueError("Enter a valid email address for each email contact.")
            if contact.channel == "sms" and sum(character.isdigit() for character in destination) < 7:
                raise ValueError("Enter a phone number with at least 7 digits for each SMS contact.")
            normalized.append({
                "id": str(uuid4()),
                "name": contact.name.strip(),
                "channel": contact.channel,
                "destination": destination,
            })
        with self._lock:
            self.contacts = normalized
            return self.redacted_contacts()

    def add_contact(self, contact: ContactInput) -> dict:
        if not contact.name.strip() or not contact.destination.strip():
            raise ValueError("Contact name and destination cannot be blank.")
        current = self.set_contacts(
            [
                ContactInput(
                    name=item["name"],
                    channel=item["channel"],
                    destination=item["destination"],
                )
                for item in self.contacts
            ] + [contact]
        )
        return current[-1]

    def check_in(self) -> dict:
        checked_in_at = self._now()
        self.repository.save_check_in(checked_in_at, "simulated_checked_in")
        with self._lock:
            self.check_in_status = "simulated_checked_in"
            self.checked_in_at = checked_in_at
        return {
            "check_in_status": self.check_in_status,
            "checked_in_at": self.checked_in_at,
            "is_demo": True,
            "message": "Simulated check-in recorded; no location or contact was used.",
        }

    def redacted_contacts(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": contact["id"],
                    "name": contact["name"],
                    "channel": contact["channel"],
                    "destination_hint": "***" + contact["destination"][-4:],
                }
                for contact in self.contacts
            ]

    def test_notification(self, contact_id: str | None) -> dict:
        with self._lock:
            known = contact_id is None or any(
                contact["id"] == contact_id for contact in self.contacts
            )
        if not known:
            raise ValueError("Trusted contact was not found.")
        return {
            "status": "test_only",
            "provider_configured": False,
            "delivery_confirmed": False,
            "contact_notified": False,
            "message": "Test mode only: no email, SMS, contact, or emergency service was contacted.",
        }


event_engine = EventEngine()