# Hackathon Demo Script

Target: 4 minutes. This demonstrates local processing and explainable demo rules, not live monitoring or a safety prediction.

## Opening boundary

"e-Sentinel AI combines person detections from a user-uploaded cabin video with a local event timeline. The journey monitor can use browser GPS only after the user starts a journey and grants permission; route geometry is optional and manually supplied. Nothing here identifies a person, detects harassment, or decides that someone is safe."

Show the connected backend indicator and `LOCAL DEMO` badge.

## Cabin vision evidence

Upload `data/cabin_test.mp4` and run the analysis. Show processed frames, the 480 detections across frames, peak detections in one frame, confidence summary, and annotated preview. Explain that a person can appear in many frames, so detections are not unique passengers. Point out that YOLO can miss or falsely detect people, and video output never triggers SOS.

## Journey event engine

Show the schematic route and its `ILLUSTRATIVE ONLY · NOT GPS` label before starting a trip. If presenting browser GPS, explicitly start a journey, grant the browser's location request, and point out the displayed accuracy and update time. Planned waypoints must be entered by the user; route deviation is not assessed without them. Keep the trip active long enough for three accurate fixes beyond the route threshold. Show the generated observation, its browser-GPS event and linked real-source rule assessment, including score, missing signals, and recommended action. Demonstrate the explicit passenger check-in response or cancellation; neither establishes that someone is safe. End the journey and confirm it appears in history. Use the separate labelled simulation controls for a route deviation, unexpected stop, and missed check-in when live GPS is unavailable.

## Contact and notification workflow

Add a demo trusted contact using a non-sensitive address. Show the redacted destination hint and click “Test notification.” Explain the API reports `contact_notified: false` and `delivery_confirmed: false`: there is no email/SMS integration configured.

## Local assistant and SOS

Ask the local assistant why the latest event was flagged. Point out `LOCAL MODE`: the answer comes from event records and rules, with no external LLM call. Trigger SOS by hand; show the recorded critical demo event and confirmation that no contact or emergency service was called. Reset the events.

## Close

"Journey records, latest GPS fixes, observations, and the event store persist in local SQLite; contact settings are in memory and clear when the backend restarts. This prototype does not retain a breadcrumb trail and has no route planner, vehicle sensors, ETA source, real notification transport, authentication, or validated safety model. GPS and destination data are sensitive: stop the journey when monitoring is no longer wanted. Real-world use would require stronger privacy/security controls, rigorous evaluation, and authorized provider integrations."
