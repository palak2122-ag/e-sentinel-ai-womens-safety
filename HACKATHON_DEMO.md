# Hackathon Demo Script

Target: 4 minutes. This demonstrates local processing and explainable demo rules, not live monitoring or a safety prediction.

## Opening boundary

"e-Sentinel AI combines person detections from a user-uploaded cabin video with a local event timeline. Journey route and check-in signals are simulations. Nothing here identifies a person, detects harassment, or decides that someone is safe."

Show the connected backend indicator and `LOCAL DEMO` badge.

## Cabin vision evidence

Upload `data/cabin_test.mp4` and run the analysis. Show processed frames, the 480 detections across frames, peak detections in one frame, confidence summary, and annotated preview. Explain that a person can appear in many frames, so detections are not unique passengers. Point out that YOLO can miss or falsely detect people, and video output never triggers SOS.

## Journey event engine

Show the schematic route and its `SIMULATED ROUTE` label. Change the route-offset threshold. Create a route-deviation event, an unexpected-stop event, and a missed-check-in event. Open a timeline item's “Why flagged?” details and explain its observed input, threshold rule, inferred signal, and unknowns. Record a simulated check-in, then reset the demo events.

## Contact and notification workflow

Add a demo trusted contact using a non-sensitive address. Show the redacted destination hint and click “Test notification.” Explain the API reports `contact_notified: false` and `delivery_confirmed: false`: there is no email/SMS integration configured.

## Local assistant and SOS

Ask the local assistant why the latest event was flagged. Point out `LOCAL MODE`: the answer comes from event records and rules, with no external LLM call. Trigger SOS by hand; show the recorded critical demo event and confirmation that no contact or emergency service was called. Reset the events.

## Close

"The event store and contact settings are in memory and clear when the backend restarts. There is no GPS provider, persistent database, real notification transport, authentication, or validated safety model. Those require consent, privacy and security review, rigorous evaluation, and explicit provider integration before real-world use."
