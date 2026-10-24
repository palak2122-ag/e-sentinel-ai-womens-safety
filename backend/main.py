
from fastapi import FastAPI
from datetime import datetime, timezone

app = FastAPI(
    title="e-Sentinel AI",
    description="AI-assisted women's journey safety prototype",
    version="0.1.0",
)


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
        "risk_level": "SAFE",
        "message": "Demo status only — live monitoring is not active.",
        "monitoring_active": False,
    }
