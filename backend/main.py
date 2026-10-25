
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import tempfile
from uuid import NAMESPACE_URL, uuid5

import cv2
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from ultralytics import YOLO

from backend.database import PersistenceError
from backend.event_engine import (
    AssistantQuestion,
    ContactInput,
    ContactListInput,
    DemoEventRequest,
    MonitoringConfig,
    NotificationTestRequest,
    event_engine,
)
from backend.journey_engine import (
    JourneyEngine,
    JourneyNotFoundError,
    JourneyStartRequest,
    LocationFix,
)
from backend.risk_engine import (
    EscalationCancelRequest,
    EscalationProcessRequest,
    RiskAssessmentRequest,
    RiskEngine,
    RiskPolicyConfig,
)

risk_engine = RiskEngine(event_engine.repository)
journey_engine = JourneyEngine(event_engine.repository)
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app):
    logger.info(
        "Starting e-Sentinel backend: module=%s pid=%s model_path=%s model_exists=%s",
        __name__,
        os.getpid(),
        MODEL_PATH,
        MODEL_PATH.is_file(),
    )
    event_engine.initialize()
    risk_engine.initialize()
    journey_engine.initialize()
    yield


app = FastAPI(
    title="e-Sentinel AI",
    description="AI-assisted women's journey safety prototype",
    version="0.2.0",
    lifespan=lifespan,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = Path(os.getenv("ESENTINEL_MODEL_PATH", str(PROJECT_ROOT / "yolov8n.pt"))).expanduser()
if not MODEL_PATH.is_absolute():
    MODEL_PATH = PROJECT_ROOT / MODEL_PATH
MODEL_PATH = MODEL_PATH.resolve()
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


def _read_float_setting(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1.")
    return value


YOLO_CONFIDENCE = _read_float_setting("YOLO_CONFIDENCE", 0.45)
YOLO_IOU = _read_float_setting("YOLO_IOU", 0.45)
FRAME_STRIDE = int(os.getenv("VIDEO_FRAME_STRIDE", "1"))
if FRAME_STRIDE < 1:
    raise ValueError("VIDEO_FRAME_STRIDE must be at least 1.")

# Load the model only when cabin analysis is first requested.
_model = None


def get_model():
    global _model

    if _model is None:
        if not MODEL_PATH.is_file():
            logger.error("YOLO weights file not found at resolved path: %s", MODEL_PATH)
            raise HTTPException(
                status_code=500,
                detail=(
                    f"YOLO weights file was not found at {MODEL_PATH}. "
                    "Set ESENTINEL_MODEL_PATH or place yolov8n.pt in the project root."
                ),
            )
        _model = YOLO(str(MODEL_PATH))

    return _model


@app.get("/")
def home():
    return {
        "project": "e-Sentinel AI",
        "message": "Women's Safety System is running!",
        "status": "online",
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backend_module": __name__,
        "process_id": os.getpid(),
        "model_available": MODEL_PATH.is_file(),
    }


@app.get("/api/safety-status")
def safety_status():
    return {
        "risk_level": "UNKNOWN",
        "message": (
            "Demo status only. No live safety monitoring "
            "or confirmed risk assessment is active."
        ),
        "monitoring_active": False,
    }


@app.post("/api/risk/assessments")
def create_risk_assessment(request: RiskAssessmentRequest):
    try:
        return risk_engine.assess(request)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Risk assessment could not be saved.") from exc


@app.get("/api/risk/assessments/latest")
def latest_risk_assessment():
    try:
        return risk_engine.latest()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Risk assessment history is unavailable.") from exc


@app.get("/api/risk/assessments")
def risk_assessment_history(limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"assessments": risk_engine.history(limit)}
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Risk assessment history is unavailable.") from exc


@app.put("/api/risk/config")
def configure_risk_policy(policy: RiskPolicyConfig):
    try:
        return risk_engine.configure(policy)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Risk policy could not be saved.") from exc


@app.get("/api/risk/config")
def current_risk_policy():
    try:
        return risk_engine.current_config()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Risk policy is unavailable.") from exc


@app.get("/api/escalation/history")
def escalation_history(limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {
            "history": risk_engine.escalation_history(limit),
            "notifications": risk_engine.repository.list_notifications(limit),
            "contact_notification_tests": risk_engine.repository.list_contact_notification_tests(limit),
            "real_delivery_enabled": False,
        }
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Escalation history is unavailable.") from exc


@app.post("/api/escalation/demo")
def request_demo_escalation(request: EscalationProcessRequest):
    try:
        return risk_engine.request_demo_escalation(request.assessment_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Demo escalation could not be saved.") from exc


@app.post("/api/escalation/cancel")
def cancel_escalation(request: EscalationCancelRequest):
    try:
        return risk_engine.cancel(request.assessment_id, request.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Escalation state could not be saved.") from exc


@app.post("/api/escalation/process")
def process_pending_escalation(request: EscalationProcessRequest):
    try:
        return risk_engine.process_pending(request.assessment_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Escalation attempt could not be saved.") from exc


@app.post("/api/risk/assessments/{assessment_id}/check-in")
def respond_to_risk_check_in(assessment_id: str):
    try:
        response = risk_engine.respond_to_check_in(assessment_id)
        event_engine.sync_check_in_state(
            response["check_in_status"], response["occurred_at"]
        )
        return response
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Check-in response could not be saved.") from exc


@app.get("/api/monitoring/state")
def monitoring_state():
    try:
        return event_engine.monitoring_state()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Persistent local storage is unavailable.") from exc


@app.post("/api/journeys")
def start_journey(request: JourneyStartRequest):
    try:
        return journey_engine.start(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Journey could not be saved.") from exc


@app.get("/api/journeys/active")
def active_journey():
    try:
        return {"journey": journey_engine.active()}
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Journey data is unavailable.") from exc


@app.get("/api/journeys")
def journey_history(limit: int = Query(default=20, ge=1, le=100)):
    try:
        return {"journeys": journey_engine.history(limit)}
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Journey history is unavailable.") from exc


@app.post("/api/journeys/{journey_id}/locations")
def record_journey_location(journey_id: str, location: LocationFix):
    try:
        try:
            result = journey_engine.record_location(
                journey_id,
                location,
                route_deviation_threshold_meters=(
                    event_engine.config.route_deviation_threshold_meters
                ),
                unexpected_stop_threshold_minutes=(
                    event_engine.config.unexpected_stop_threshold_minutes
                ),
            )
            idempotent_retry = False
        except ValueError:
            if not journey_engine.is_exact_duplicate_location(journey_id, location):
                raise
            result = journey_engine.get(journey_id)
            result["new_observations"] = []
            idempotent_retry = True

        linked_results = []
        processed_observation_ids = set()
        pending_observations = event_engine.repository.list_unlinked_journey_observations(
            journey_id
        )
        for observation in pending_observations:
            event_id = str(uuid5(NAMESPACE_URL, f"e-sentinel:journey-observation:{observation['id']}"))
            assessment = risk_engine.assess_journey_observation(
                journey_id, observation, event_id
            )
            event_type = observation["observation_type"]
            rule = (
                f"GPS route deviation crossed {observation['threshold_meters']} m "
                f"for {observation['consecutive_fixes']} accurate fixes."
                if event_type == "route_deviation"
                else f"GPS-derived low movement exceeded {observation['threshold_minutes']} minutes."
            )
            safety_event = event_engine.repository.save_event_once({
                "id": event_id,
                "event_type": event_type,
                "severity": observation.get("severity", "medium"),
                "occurred_at": observation["occurred_at"],
                "source": "browser_geolocation",
                "is_demo": False,
                "status": "open",
                "observed_fact": observation["summary"],
                "inferred_signal": (
                    "Observed browser GPS data met a detector rule; it does not establish danger or intent."
                ),
                "rule": rule,
                "journey_id": journey_id,
                "observation_id": observation["id"],
                "risk_assessment_id": assessment["assessment_id"],
                "unknown": "Passenger safety and the cause/context of this observation remain unknown.",
            })
            linked_observation = event_engine.repository.link_journey_observation(
                observation["id"], assessment["assessment_id"], event_id
            )
            linked_results.append({
                "observation": linked_observation,
                "assessment": assessment,
                "event": safety_event,
            })
            processed_observation_ids.add(observation["id"])

        if idempotent_retry:
            for observation in event_engine.repository.list_journey_observations_at(
                journey_id, location.observed_at.isoformat()
            ):
                if observation["id"] in processed_observation_ids:
                    continue
                if not observation["risk_assessment_id"] or not observation["safety_event_id"]:
                    continue
                assessment = risk_engine.get_assessment(observation["risk_assessment_id"])
                safety_event = event_engine.repository.get_event(observation["safety_event_id"])
                if assessment is not None and safety_event is not None:
                    linked_results.append({
                        "observation": observation,
                        "assessment": assessment,
                        "event": safety_event,
                    })

        new_observations = result.get("new_observations", [])
        persisted_journey = journey_engine.get(journey_id)
        persisted_journey["new_observations"] = new_observations
        persisted_journey["idempotent_retry"] = idempotent_retry
        persisted_journey["linked_results"] = linked_results
        persisted_journey["risk_results"] = [item["assessment"] for item in linked_results]
        return persisted_journey
    except JourneyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Journey location could not be saved.") from exc


@app.post("/api/journeys/{journey_id}/end")
def end_journey(journey_id: str):
    try:
        return journey_engine.end(journey_id)
    except JourneyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Journey could not be completed.") from exc


@app.get("/api/events")
def recent_events(limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"events": event_engine.list_events(limit), "mode": "local_demo"}
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Persistent local storage is unavailable.") from exc


@app.get("/api/alerts")
def recent_alerts(limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"alerts": event_engine.repository.list_alerts(limit), "mode": "local_demo"}
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Persistent local storage is unavailable.") from exc


@app.post("/api/events/demo")
def create_demo_event(request: DemoEventRequest):
    try:
        return event_engine.create_demo_event(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Event could not be saved.") from exc


@app.delete("/api/events/demo")
def reset_demo_events():
    try:
        return event_engine.reset()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Demo data could not be reset.") from exc


@app.put("/api/monitoring/config")
def configure_monitoring(config: MonitoringConfig):
    try:
        return event_engine.configure(config)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Monitoring settings could not be saved.") from exc


@app.get("/api/incidents/summary")
def incident_summary():
    try:
        return event_engine.summary()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Incident data is unavailable.") from exc


@app.post("/api/assistant/query")
def assistant_query(question: AssistantQuestion):
    try:
        return event_engine.ask(question.question)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Event data is unavailable to the assistant.") from exc


@app.get("/api/contacts")
def trusted_contacts():
    try:
        return {
            "contacts": event_engine.redacted_contacts(),
            "storage": "sqlite",
            "notification_tests": event_engine.repository.list_contact_notification_tests(20),
        }
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Trusted contacts are unavailable.") from exc


@app.put("/api/contacts")
def save_trusted_contacts(request: ContactListInput):
    try:
        contacts = event_engine.set_contacts(request.contacts)
        return {"contacts": contacts, "storage": "sqlite"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Trusted contacts could not be saved.") from exc


@app.post("/api/contacts")
def add_trusted_contact(contact: ContactInput):
    try:
        saved_contact = event_engine.add_contact(contact)
        return {"contact": saved_contact, "storage": "sqlite"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Trusted contact could not be saved.") from exc


@app.put("/api/contacts/{contact_id}")
def update_trusted_contact(contact_id: str, contact: ContactInput):
    try:
        updated_contact = event_engine.update_contact(contact_id, contact)
        return {"contact": updated_contact, "storage": "sqlite"}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Trusted contact could not be updated.") from exc


@app.delete("/api/contacts/{contact_id}")
def delete_trusted_contact(contact_id: str):
    try:
        event_engine.delete_contact(contact_id)
        return {"deleted": True, "storage": "sqlite"}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Trusted contact could not be deleted.") from exc


@app.post("/api/monitoring/check-in")
def simulate_check_in():
    try:
        return event_engine.check_in()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Check-in could not be saved.") from exc


@app.post("/api/notifications/test")
def test_notification(request: NotificationTestRequest):
    try:
        return event_engine.test_notification(request.contact_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Mock notification test could not be recorded.") from exc


@app.post("/api/analyze-cabin")
def analyze_cabin(file: UploadFile = File(...)):
    allowed_extensions = {".mp4", ".avi", ".mov", ".mkv"}
    suffix = Path(file.filename or "").suffix.lower()

    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Upload a supported video: MP4, AVI, MOV, or MKV.",
        )

    if file.size == 0:
        raise HTTPException(status_code=400, detail="The uploaded video is empty.")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Video must be smaller than 100 MB.",
        )

    temp_path = None
    cap = None

    try:
        # Save the uploaded video temporarily.
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix
        ) as temp_file:
            temp_path = Path(temp_file.name)
            total_bytes = 0

            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Video must be smaller than 100 MB.",
                    )

                temp_file.write(chunk)

        model = get_model()
        cap = cv2.VideoCapture(str(temp_path))

        if not cap.isOpened():
            raise HTTPException(
                status_code=400,
                detail="Could not read the uploaded video.",
            )

        frames_read = 0
        frames_processed = 0
        total_person_detections = 0
        max_people_in_frame = 0
        confidence_total = 0.0
        confidence_count = 0
        highest_confidence = None
        preview_data_url = None

        while True:
            success, frame = cap.read()
            if not success:
                break

            frames_read += 1
            if (frames_read - 1) % FRAME_STRIDE:
                continue

            # COCO class 0 = person.
            result = model.predict(
                frame,
                classes=[0],
                verbose=False,
                conf=YOLO_CONFIDENCE,
                iou=YOLO_IOU,
            )[0]

            people = (
                len(result.boxes)
                if result.boxes is not None
                else 0
            )

            frames_processed += 1
            total_person_detections += people
            if people and hasattr(result, "plot"):
                annotated_frame = result.plot()
                frame_height, frame_width = annotated_frame.shape[:2]
                if frame_width > 720:
                    scaled_height = int(frame_height * 720 / frame_width)
                    annotated_frame = cv2.resize(
                        annotated_frame,
                        (720, scaled_height),
                        interpolation=cv2.INTER_AREA,
                    )
                encoded, jpeg = cv2.imencode(".jpg", annotated_frame)
                if encoded:
                    preview_data_url = (
                        "data:image/jpeg;base64,"
                        + base64.b64encode(jpeg.tobytes()).decode("ascii")
                    )
            if result.boxes is not None and result.boxes.conf is not None:
                confidences = result.boxes.conf.tolist()
                confidence_total += sum(confidences)
                confidence_count += len(confidences)
                frame_highest = max(confidences, default=None)
                if frame_highest is not None:
                    highest_confidence = max(
                        highest_confidence or 0.0,
                        frame_highest,
                    )

            if people > max_people_in_frame:
                max_people_in_frame = people
                print(
                    f"Frame {frames_processed}: "
                    f"{people} person detections"
                )

        if frames_processed == 0:
            raise HTTPException(
                status_code=400,
                detail="No readable frames found in the video.",
            )

        cabin_event = event_engine.record_cabin_peak(max_people_in_frame)
        return {
            "status": "analysis_complete",
            "filename": file.filename,
            "frames_read": frames_read,
            "frames_processed": frames_processed,
            "total_person_detections_across_frames": (
                total_person_detections
            ),
            "maximum_people_in_one_frame": max_people_in_frame,
            "frame_stride": FRAME_STRIDE,
            "confidence_threshold": YOLO_CONFIDENCE,
            "iou_threshold": YOLO_IOU,
            "mean_detection_confidence": (
                confidence_total / confidence_count
                if confidence_count
                else None
            ),
            "maximum_detection_confidence": highest_confidence,
            "annotated_preview": preview_data_url,
            "event_created": cabin_event,
            "detection_type": "YOLO person detection",
            "harassment_detected": None,
            "safety_assessment": (
                "Not determined. Person detection alone cannot "
                "establish harassment or whether someone is safe."
            ),
        }

    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Cabin event could not be saved.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Video analysis failed. Check the video format and backend logs.",
        ) from exc
    finally:
        if cap is not None:
            cap.release()

        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

        file.file.close()
