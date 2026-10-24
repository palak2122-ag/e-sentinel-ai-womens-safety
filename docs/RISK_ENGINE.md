# Risk Engine and Escalation

## Scope

The risk engine is a transparent hackathon prototype. Scores are rule totals, not probabilities, validated predictions, or certified automotive safety guidance. The backend stores risk assessments, escalation changes, and notification-attempt outcomes in the configured local SQLite database. Raw media and contact destinations are not included.

## Inputs and data quality

`POST /api/risk/assessments` accepts optional `camera`, `gps`, `vehicle`, and `check_in` objects. Each source is `real`, `simulated`, or `unknown`; requests default to demo mode. Values have bounded schemas: speed 0-500 kph, route offset 0-1,000,000 m, GPS accuracy 0-100,000 m, reliability 0-1, plus bounded durations and person counts. Real source values with `demo_mode=true` are rejected.

Signal absence, source `unknown`, stale timestamps, future/naive timestamps, poor GPS accuracy, or reliability below 0.25 produces an unknown-quality note and excludes that signal's score. Default staleness is 300 seconds. A passenger-approved route change does not incur a route deviation score.

## Default scoring

| Rule | Points before reliability |
| --- | ---: |
| Route deviation at least 500 m with usable accuracy | 30 |
| Vehicle stopped at least 5 minutes | 25 |
| Supplied sudden-braking signal is true | 20 |
| No check-in response and at least 10 minutes overdue | 35 |
| Camera frame-level count changes by at least 4 | 8 (weak contextual signal) |

Each signal's active points are multiplied by its reliability and rounded. Total score is capped at 100. Bands: low 0-19, medium 20-44, high 45-69, critical 70-100. Camera counts and pose are observations only and cannot establish distress, identity, intent, harassment, assault, or safety.

## State machine and mock delivery

- Low/medium: `monitoring`.
- High: `check_in_required` with a user-facing recommended discreet check-in.
- Critical without safeguards: `check_in_required`.
 Pending escalation does not send anything automatically. The user must explicitly invoke `POST /api/escalation/process`; cancellation remains available before processing.
Escalation requires both `ESENTINEL_ESCALATION_ENABLED=true` and `escalation_enabled: true` in `PUT /api/risk/config`. The environment flag defaults false and acts as a safety gate. The built-in `MockNotificationProvider` performs no network request and contacts nobody. Its simulated `delivered` state is not a real delivery confirmation. Attempt records use an incident-scoped idempotency key when supplied, otherwise the assessment ID.

## Endpoints

## Future integrations

A real location or vehicle provider must supply timestamps, source, reliability, and explicit consent/route-change context. A future notification provider must be separately authorized and implemented behind a provider interface using server-side environment secrets, with confirmed provider receipts before marking delivery. Do not send video, frames, face data, or unnecessary personal details to providers. No external provider, SMS/WhatsApp/email credentials, map, or emergency-service integration exists in this prototype.
