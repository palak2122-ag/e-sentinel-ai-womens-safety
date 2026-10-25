"""Validated journey records and observations derived from browser geolocation."""

from datetime import datetime, timezone
from math import asin, atan2, cos, radians, sin, sqrt
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.database import SQLiteRepository


class JourneyNotFoundError(LookupError):
    pass


class JourneyWaypoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class LocationFix(JourneyWaypoint):
    accuracy_meters: float = Field(ge=0, le=100_000)
    speed_mps: float | None = Field(default=None, ge=0, le=100)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("observed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Location timestamp must include a timezone.")
        timestamp = value.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if timestamp.timestamp() > now.timestamp() + 30:
            raise ValueError("Location timestamp cannot be more than 30 seconds in the future.")
        if (now - timestamp).total_seconds() > 120:
            raise ValueError("Location timestamp is stale; submit a fix from the last 120 seconds.")
        return timestamp


class JourneyStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str = Field(min_length=1, max_length=200)
    start_location: LocationFix
    planned_route: list[JourneyWaypoint] = Field(default_factory=list, max_length=200)

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, value: str) -> str:
        destination = value.strip()
        if not destination:
            raise ValueError("Destination cannot be blank.")
        return destination

    @model_validator(mode="after")
    def require_complete_planned_route(self):
        if self.planned_route and len(self.planned_route) < 2:
            raise ValueError("A planned route requires at least two waypoints.")
        return self


class JourneyEngine:
    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    def initialize(self) -> None:
        self.repository.initialize()

    @staticmethod
    def _location_payload(location: LocationFix) -> dict:
        return {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "accuracy_meters": location.accuracy_meters,
            "speed_mps": location.speed_mps,
            "observed_at": location.observed_at.isoformat(),
        }

    def _with_observations(self, journey: dict | None) -> dict | None:
        if journey is None:
            return None
        journey["observations"] = self.repository.list_journey_observations(
            journey["journey_id"]
        )
        return journey

    def start(self, request: JourneyStartRequest) -> dict:
        journey = self.repository.create_journey({
            "journey_id": str(uuid4()),
            "destination": request.destination,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "start_location": self._location_payload(request.start_location),
            "planned_route": [point.model_dump() for point in request.planned_route],
        })
        return self._with_observations(journey)

    def active(self) -> dict | None:
        return self._with_observations(self.repository.get_active_journey())

    def get(self, journey_id: str) -> dict:
        journey = self.repository.get_journey(journey_id)
        if journey is None:
            raise JourneyNotFoundError("Journey was not found.")
        return self._with_observations(journey)

    def history(self, limit: int = 50) -> list[dict]:
        return [
            self._with_observations(journey)
            for journey in self.repository.list_journeys(limit)
        ]

    def record_location(
        self,
        journey_id: str,
        location: LocationFix,
        *,
        route_deviation_threshold_meters: int,
        unexpected_stop_threshold_minutes: int,
    ) -> dict:
        current = self._location_payload(location)
        try:
            updated, observations = self.repository.save_journey_location(
                journey_id,
                current,
                lambda state: self._calculate_detector_state(
                    journey_id,
                    state,
                    location,
                    current,
                    route_deviation_threshold_meters,
                    unexpected_stop_threshold_minutes,
                ),
            )
        except LookupError as exc:
            raise JourneyNotFoundError("Journey was not found.") from exc
        updated["observations"] = self.repository.list_journey_observations(journey_id)
        updated["new_observations"] = observations
        return updated

    def _calculate_detector_state(
        self,
        journey_id: str,
        state: dict,
        location: LocationFix,
        current: dict,
        route_deviation_threshold_meters: int,
        unexpected_stop_threshold_minutes: int,
    ) -> dict:
        previous = state["current_location"]
        observations = []
        route_deviation_streak = state["route_deviation_streak"]
        route_deviation_active = state["route_deviation_active"]
        stationary_since = state["stationary_since"]
        stationary_fix_count = state["stationary_fix_count"]
        stop_observed = state["stop_observed"]

        route = state["planned_route"]
        accuracy_limit = min(100, max(25, route_deviation_threshold_meters / 4))
        if len(route) >= 2 and current["accuracy_meters"] <= accuracy_limit:
            deviation = self.distance_to_route(current, route)
            if deviation > route_deviation_threshold_meters + current["accuracy_meters"]:
                route_deviation_streak += 1
                if route_deviation_streak >= 3 and not route_deviation_active:
                    severity = (
                        "high"
                        if deviation >= route_deviation_threshold_meters * 2
                        else "medium"
                    )
                    observations.append(self._observation(
                        journey_id,
                        "route_deviation",
                        current["observed_at"],
                        f"GPS position was {deviation:.0f} m from the planned route.",
                        {
                            "severity": severity,
                            "deviation_meters": round(deviation, 1),
                            "threshold_meters": route_deviation_threshold_meters,
                            "accuracy_meters": current["accuracy_meters"],
                            "consecutive_fixes": route_deviation_streak,
                            "source": "browser_geolocation",
                        },
                    ))
                    route_deviation_active = True
            else:
                route_deviation_streak = 0
                if deviation <= route_deviation_threshold_meters:
                    route_deviation_active = False
        else:
            route_deviation_streak = 0

        prior_time = datetime.fromisoformat(previous["observed_at"])
        current_time = location.observed_at
        elapsed_seconds = (current_time - prior_time).total_seconds()
        speed = current["speed_mps"]
        if speed is None and elapsed_seconds > 0:
            speed = self.distance_between(current, previous) / elapsed_seconds

        if (
            speed is not None
            and current["accuracy_meters"] <= 50
            and previous["accuracy_meters"] <= 50
        ):
            if speed <= 0.8:
                if stationary_since is None or elapsed_seconds > 90:
                    stationary_since = current["observed_at"]
                    stationary_fix_count = 1
                    stop_observed = False
                else:
                    stationary_fix_count += 1
                stationary_duration = (
                    current_time - datetime.fromisoformat(stationary_since)
                ).total_seconds() / 60
                if (
                    stationary_duration >= unexpected_stop_threshold_minutes
                    and stationary_fix_count >= 3
                    and not stop_observed
                ):
                    observations.append(self._observation(
                        journey_id,
                        "unexpected_stop",
                        current["observed_at"],
                        f"GPS-derived movement stayed below 0.8 m/s for {stationary_duration:.0f} min; expectedness is unknown.",
                        {
                            "severity": "medium",
                            "stationary_minutes": round(stationary_duration, 1),
                            "consecutive_fixes": stationary_fix_count,
                            "threshold_minutes": unexpected_stop_threshold_minutes,
                            "accuracy_meters": current["accuracy_meters"],
                            "expectedness": "unknown",
                            "source": "browser_geolocation",
                        },
                    ))
                    stop_observed = True
            else:
                stationary_since = None
                stationary_fix_count = 0
                stop_observed = False
        else:
            stationary_since = None
            stationary_fix_count = 0
            stop_observed = False

        return {
            "route_deviation_streak": route_deviation_streak,
            "route_deviation_active": route_deviation_active,
            "stationary_since": stationary_since,
            "stationary_fix_count": stationary_fix_count,
            "stop_observed": stop_observed,
            "observations": observations,
        }

    def end(self, journey_id: str) -> dict:
        journey = self.repository.end_journey(
            journey_id, datetime.now(timezone.utc).isoformat()
        )
        if journey is None:
            raise JourneyNotFoundError("Journey was not found.")
        return self._with_observations(journey)

    @staticmethod
    def _observation(
        journey_id: str,
        observation_type: Literal["route_deviation", "unexpected_stop"],
        occurred_at: str,
        summary: str,
        details: dict,
    ) -> dict:
        return {
            "id": str(uuid4()),
            "journey_id": journey_id,
            "observation_type": observation_type,
            "occurred_at": occurred_at,
            "summary": summary,
            **details,
        }

    @staticmethod
    def distance_between(first: dict, second: dict) -> float:
        earth_radius_meters = 6_371_000
        latitude_one = radians(first["latitude"])
        latitude_two = radians(second["latitude"])
        delta_latitude = latitude_two - latitude_one
        delta_longitude = radians(second["longitude"] - first["longitude"])
        haversine = (
            sin(delta_latitude / 2) ** 2
            + cos(latitude_one) * cos(latitude_two) * sin(delta_longitude / 2) ** 2
        )
        haversine = max(0, min(1, haversine))
        return earth_radius_meters * 2 * atan2(
            sqrt(haversine), sqrt(max(0, 1 - haversine))
        )

    def is_exact_duplicate_location(self, journey_id: str, location: LocationFix) -> bool:
        state = self.repository.get_journey_tracking_state(journey_id)
        if state is None or state["status"] != "active":
            return False
        current = state["current_location"]
        return all((
            current["latitude"] == location.latitude,
            current["longitude"] == location.longitude,
            current["accuracy_meters"] == location.accuracy_meters,
            current["speed_mps"] == location.speed_mps,
            current["observed_at"] == location.observed_at.isoformat(),
        ))

    @classmethod
    def distance_to_route(cls, location: dict, route: list[dict]) -> float:
        point_latitude = radians(location["latitude"])
        point_longitude = radians(location["longitude"])
        closest = float("inf")
        earth_radius_meters = 6_371_000

        for start, end in zip(route, route[1:]):
            start_latitude = radians(start["latitude"])
            start_longitude = radians(start["longitude"])
            end_latitude = radians(end["latitude"])
            end_longitude = radians(end["longitude"])
            delta_longitude = end_longitude - start_longitude

            segment_haversine = (
                sin((end_latitude - start_latitude) / 2) ** 2
                + cos(start_latitude) * cos(end_latitude)
                * sin(delta_longitude / 2) ** 2
            )
            segment_angle = 2 * asin(sqrt(max(0, min(1, segment_haversine))))
            if segment_angle < 1e-12:
                closest = min(
                    closest,
                    cls.distance_between(location, start),
                )
                continue

            point_haversine = (
                sin((point_latitude - start_latitude) / 2) ** 2
                + cos(start_latitude) * cos(point_latitude)
                * sin((point_longitude - start_longitude) / 2) ** 2
            )
            point_angle = 2 * asin(sqrt(max(0, min(1, point_haversine))))
            bearing_to_point = atan2(
                sin(point_longitude - start_longitude) * cos(point_latitude),
                cos(start_latitude) * sin(point_latitude)
                - sin(start_latitude) * cos(point_latitude)
                * cos(point_longitude - start_longitude),
            )
            bearing_to_end = atan2(
                sin(delta_longitude) * cos(end_latitude),
                cos(start_latitude) * sin(end_latitude)
                - sin(start_latitude) * cos(end_latitude) * cos(delta_longitude),
            )
            cross_track = asin(max(
                -1,
                min(
                    1,
                    sin(point_angle) * sin(bearing_to_point - bearing_to_end),
                ),
            ))
            along_track = atan2(
                sin(point_angle) * cos(bearing_to_point - bearing_to_end),
                cos(point_angle),
            )
            if 0 <= along_track <= segment_angle:
                distance = abs(cross_track) * earth_radius_meters
            else:
                distance = min(
                    cls.distance_between(location, start),
                    cls.distance_between(location, end),
                )
            closest = min(closest, distance)
        return closest
