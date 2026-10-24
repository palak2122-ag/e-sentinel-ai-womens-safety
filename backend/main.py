
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

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
from backend.risk_engine import (
    EscalationCancelRequest,
    EscalationProcessRequest,
    RiskAssessmentRequest,
    RiskEngine,
    RiskPolicyConfig,
)

risk_engine = RiskEngine(event_engine.repository)


@asynccontextmanager
async def lifespan(_app):
    event_engine.initialize()
    risk_engine.initialize()
    yield


app = FastAPI(
    title="e-Sentinel AI",
    description="AI-assisted women's journey safety prototype",
    version="0.2.0",
    lifespan=lifespan,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = PROJECT_ROOT / "yolov8n.pt"
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
        if not MODEL_PATH.exists():
            raise HTTPException(
                status_code=500,
                detail="YOLO model file yolov8n.pt was not found.",
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
            "real_delivery_enabled": False,
        }
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Escalation history is unavailable.") from exc


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


@app.get("/api/monitoring/state")
def monitoring_state():
    try:
        return event_engine.monitoring_state()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Persistent local storage is unavailable.") from exc


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
    return {"contacts": event_engine.redacted_contacts(), "storage": "in_memory_only"}


@app.put("/api/contacts")
def save_trusted_contacts(request: ContactListInput):
    try:
        contacts = event_engine.set_contacts(request.contacts)
        return {"contacts": contacts, "storage": "in_memory_only"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/contacts")
def add_trusted_contact(contact: ContactInput):
    try:
        saved_contact = event_engine.add_contact(contact)
        return {"contact": saved_contact, "storage": "in_memory_only"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
