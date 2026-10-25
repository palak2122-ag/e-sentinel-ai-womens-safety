
import { useEffect, useRef, useState } from 'react'
import './App.css'

async function api(path, options = {}) {
  const { headers, ...requestOptions } = options
  const response = await fetch(path, {
    ...requestOptions,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`)
  return data
}

function locationErrorMessage(error) {
  if (error.code === 1) return 'Location permission was denied. Allow location access in your browser to monitor this journey.'
  if (error.code === 2) return 'Your device could not determine a location. Check GPS/location services and try again.'
  if (error.code === 3) return 'Location lookup timed out. Check device location services and retry.'
  return 'Browser geolocation is unavailable in this browser.'
}

function positionPayload(position) {
  return {
    latitude: position.coords.latitude,
    longitude: position.coords.longitude,
    accuracy_meters: position.coords.accuracy,
    speed_mps: position.coords.speed,
    observed_at: new Date(position.timestamp).toISOString(),
  }
}

function observationLabel(type) {
  return type === 'unexpected_stop' ? 'prolonged GPS stop' : type.replaceAll('_', ' ')
}

export default function App() {
  const [online, setOnline] = useState(false)
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [sos, setSos] = useState(false)
  const [journey, setJourney] = useState(null)
  const [activeJourney, setActiveJourney] = useState(null)
  const [journeyHistory, setJourneyHistory] = useState([])
  const [destination, setDestination] = useState('')
  const [plannedRouteText, setPlannedRouteText] = useState('')
  const [locationError, setLocationError] = useState('')
  const [locationMonitoring, setLocationMonitoring] = useState(false)
  const [thresholds, setThresholds] = useState(null)
  const [events, setEvents] = useState([])
  const [eventsError, setEventsError] = useState('')
  const [workspaceLoaded, setWorkspaceLoaded] = useState(false)
  const [riskAssessment, setRiskAssessment] = useState(null)
  const [riskHistory, setRiskHistory] = useState([])
  const [escalations, setEscalations] = useState([])
  const [riskError, setRiskError] = useState('')
  const [journeyDataError, setJourneyDataError] = useState('')
  const [discreetMode, setDiscreetMode] = useState(false)
  const [contacts, setContacts] = useState([])
  const [contactsError, setContactsError] = useState('')
  const [contactForm, setContactForm] = useState({ name: '', channel: 'email', destination: '' })
  const [assistantQuestion, setAssistantQuestion] = useState('Summarize recent events')
  const [assistantAnswer, setAssistantAnswer] = useState(null)
  const [notificationMessage, setNotificationMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [busyAction, setBusyAction] = useState('')
  const fileRef = useRef(null)
  const locationWatchRef = useRef(null)
  const locationRequestInFlightRef = useRef(false)
  const lastLocationSentAtRef = useRef(0)
  const lastSavedLocationTimestampRef = useRef(0)

  async function refreshWorkspace() {
    const [
      stateResult,
      eventResult,
      contactResult,
      latestRiskResult,
      riskResult,
      escalationResult,
      activeJourneyResult,
      journeyHistoryResult,
    ] = await Promise.allSettled([
        api('/api/monitoring/state'),
        api('/api/events?limit=30'),
        api('/api/contacts'),
        api('/api/risk/assessments/latest'),
        api('/api/risk/assessments?limit=10'),
        api('/api/escalation/history?limit=20'),
        api('/api/journeys/active'),
        api('/api/journeys?limit=5'),
    ])

    if (stateResult.status === 'fulfilled') {
      const state = stateResult.value
      setJourney(state)
      setThresholds((current) => current ?? state.configuration)
    }
    setOnline(stateResult.status === 'fulfilled')

    if (eventResult.status === 'fulfilled' && Array.isArray(eventResult.value.events)) {
      setEvents(eventResult.value.events)
      setEventsError('')
    } else {
      setEventsError(eventResult.status === 'rejected'
        ? eventResult.reason.message || 'Saved events are unavailable.'
        : 'The backend returned an invalid event list.')
    }

    if (contactResult.status === 'fulfilled' && Array.isArray(contactResult.value.contacts)) {
      setContacts(contactResult.value.contacts)
      setContactsError('')
    } else {
      setContactsError(contactResult.status === 'rejected'
        ? contactResult.reason.message || 'Trusted contacts are unavailable.'
        : 'The backend returned an invalid contact list.')
    }

    if (latestRiskResult.status === 'fulfilled') {
      setRiskAssessment(latestRiskResult.value ?? null)
    }
    if (riskResult.status === 'fulfilled' && Array.isArray(riskResult.value.assessments)) {
      setRiskHistory(riskResult.value.assessments)
    }
    const riskFailure = [latestRiskResult, riskResult].find((result) => result.status === 'rejected')
    setRiskError(riskFailure
      ? riskFailure.reason.message || 'Risk data is unavailable.'
      : riskResult.status === 'fulfilled' && !Array.isArray(riskResult.value.assessments)
        ? 'The backend returned an invalid risk history.'
        : '')

    if (escalationResult.status === 'fulfilled' && Array.isArray(escalationResult.value.history)) {
      setEscalations(escalationResult.value.history)
    } else if (escalationResult.status === 'rejected') {
      setRiskError((current) => current || escalationResult.reason.message || 'Escalation history is unavailable.')
    }

    if (activeJourneyResult.status === 'fulfilled') {
      const activeJourneyData = activeJourneyResult.value
      setActiveJourney(activeJourneyData?.journey ?? null)
      if (activeJourneyData?.journey?.current_location?.observed_at) {
        lastSavedLocationTimestampRef.current = Math.max(
          lastSavedLocationTimestampRef.current,
          Date.parse(activeJourneyData.journey.current_location.observed_at),
        )
      }
    }
    if (journeyHistoryResult.status === 'fulfilled' && Array.isArray(journeyHistoryResult.value.journeys)) {
      setJourneyHistory(journeyHistoryResult.value.journeys)
    }
    const journeyFailure = [activeJourneyResult, journeyHistoryResult].find((result) => result.status === 'rejected')
    setJourneyDataError(journeyFailure
      ? journeyFailure.reason.message || 'Journey history is unavailable.'
      : journeyHistoryResult.status === 'fulfilled' && !Array.isArray(journeyHistoryResult.value.journeys)
        ? 'The backend returned an invalid journey history.'
        : '')
    setWorkspaceLoaded(true)
  }

  function selectVideo(nextFile) {
    setError('')
    setResult(null)
    if (!nextFile) {
      setFile(null)
      return
    }

    const allowedExtensions = ['.mp4', '.avi', '.mov', '.mkv']
    const extension = nextFile.name.toLowerCase().slice(nextFile.name.lastIndexOf('.'))
    if (!allowedExtensions.includes(extension)) {
      setFile(null)
      setError('Choose an MP4, AVI, MOV, or MKV video.')
      return
    }
    if (nextFile.size > 100 * 1024 * 1024) {
      setFile(null)
      setError('The video must be smaller than 100 MB.')
      return
    }
    setFile(nextFile)
  }

  function toggleJourneyEvent(eventName) {
    const payloads = {
      unexpectedStop: { event_type: 'unexpected_stop', stop_minutes: (journey?.configuration?.unexpected_stop_threshold_minutes ?? 5) + 3 },
      routeDeviation: { event_type: 'route_deviation', route_deviation_meters: (journey?.configuration?.route_deviation_threshold_meters ?? 500) + 200 },
      missedCheckIn: { event_type: 'missed_check_in', overdue_minutes: (journey?.configuration?.check_in_interval_minutes ?? 30) + 5 },
    }
    runAction(eventName, async () => {
      await api('/api/events/demo', { method: 'POST', body: JSON.stringify(payloads[eventName]) })
      await refreshWorkspace()
      setNotificationMessage(`${eventName.replaceAll(/([A-Z])/g, ' $1').toLowerCase()} simulation recorded in the event timeline.`)
    })
  }

  useEffect(() => {
    Promise.resolve().then(refreshWorkspace)
    const timer = setInterval(refreshWorkspace, 10000)
    return () => {
      clearInterval(timer)
      if (locationWatchRef.current !== null && navigator.geolocation) {
        navigator.geolocation.clearWatch(locationWatchRef.current)
      }
    }
  }, [])

  async function runAction(actionName, action) {
    setActionError('')
    setNotificationMessage('')
    setBusyAction(actionName)
    try {
      await action()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setBusyAction('')
    }
  }

  function startLocationWatch(journeyId) {
    if (!navigator.geolocation) {
      setLocationError('This browser does not provide geolocation.')
      return
    }
    if (locationWatchRef.current !== null) return

    setLocationError('')
    try {
      locationWatchRef.current = navigator.geolocation.watchPosition(async (position) => {
        const now = Date.now()
        if (position.timestamp <= lastSavedLocationTimestampRef.current) return
        if (now - lastLocationSentAtRef.current < 10000 || locationRequestInFlightRef.current) return

        locationRequestInFlightRef.current = true
        lastLocationSentAtRef.current = now
        try {
          const saved = await api(`/api/journeys/${encodeURIComponent(journeyId)}/locations`, {
            method: 'POST',
            body: JSON.stringify(positionPayload(position)),
          })
          if (locationWatchRef.current !== null) {
            setActiveJourney(saved)
            lastSavedLocationTimestampRef.current = position.timestamp
            setLocationError('')
          }
        } catch (err) {
          if (locationWatchRef.current !== null) {
            setLocationError(`Latest location could not be saved: ${err.message}`)
          }
        } finally {
          locationRequestInFlightRef.current = false
        }
      }, (error) => {
        setLocationError(locationErrorMessage(error))
        if (error.code === 1 && locationWatchRef.current !== null) {
          navigator.geolocation.clearWatch(locationWatchRef.current)
          locationWatchRef.current = null
          setLocationMonitoring(false)
        }
      }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 })
      setLocationMonitoring(true)
    } catch {
      setLocationMonitoring(false)
      setLocationError('Browser location monitoring could not start. Check location permission and device settings.')
    }
  }

  function parsePlannedRoute() {
    const lines = plannedRouteText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    const points = lines.map((line) => {
      const parts = line.split(',').map((value) => value.trim())
      const values = parts.map(Number)
      if (parts.length !== 2 || parts.some((value) => !value) || !values.every(Number.isFinite)) {
        throw new Error('Enter each route waypoint as latitude, longitude on its own line.')
      }
      const [latitude, longitude] = values
      if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
        throw new Error('Route coordinates must use valid latitude and longitude ranges.')
      }
      return { latitude, longitude }
    })
    if (points.length === 1) throw new Error('A planned route needs at least two waypoints.')
    return points
  }

  function startJourney(event) {
    event.preventDefault()
    runAction('journey-start', async () => {
      if (!navigator.geolocation) throw new Error('This browser does not provide geolocation.')
      const plannedRoute = parsePlannedRoute()
      const position = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(
          resolve,
          (error) => reject(new Error(locationErrorMessage(error))),
          { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 },
        )
      })
      const started = await api('/api/journeys', {
        method: 'POST',
        body: JSON.stringify({
          destination,
          start_location: positionPayload(position),
          planned_route: plannedRoute,
        }),
      })
      setActiveJourney(started)
      lastSavedLocationTimestampRef.current = position.timestamp
      lastLocationSentAtRef.current = Date.now()
      startLocationWatch(started.journey_id)
      await refreshWorkspace()
      setNotificationMessage('Journey started. Browser location is monitored only while this journey is active.')
    })
  }

  function resumeLocationMonitoring() {
    if (!activeJourney) return
    lastLocationSentAtRef.current = 0
    startLocationWatch(activeJourney.journey_id)
  }

  function endJourney() {
    if (!activeJourney) return
    runAction('journey-end', async () => {
      if (locationWatchRef.current !== null && navigator.geolocation) {
        navigator.geolocation.clearWatch(locationWatchRef.current)
        locationWatchRef.current = null
      }
      setLocationMonitoring(false)
      const ended = await api(`/api/journeys/${encodeURIComponent(activeJourney.journey_id)}/end`, {
        method: 'POST',
      })
      setActiveJourney(ended)
      setLocationError('')
      await refreshWorkspace()
      setNotificationMessage('Journey ended. Browser location monitoring has stopped.')
    })
  }

  async function analyze(event) {
    event.preventDefault()
    if (!file) {
      setError('Please select a cabin video first.')
      return
    }

    setLoading(true)
    setError('')
    setResult(null)

    try {
      const form = new FormData()
      form.append('file', file)

      const response = await fetch('/api/analyze-cabin', { method: 'POST', body: form })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Video analysis failed')
      setResult(data)
      await refreshWorkspace()
    } catch (err) {
      setError(`${err.message}. Check that the backend is running on port 8000.`)
    } finally {
      setLoading(false)
    }
  }

  function triggerSOS() {
    runAction('sos', async () => {
      await api('/api/events/demo', { method: 'POST', body: JSON.stringify({ event_type: 'user_sos' }) })
      setSos(true)
      setNotificationMessage('Demo SOS recorded locally. No contact or emergency service was contacted.')
      await refreshWorkspace()
    })
  }

  function saveContacts(event) {
    event.preventDefault()
    runAction('contacts', async () => {
      const result = await api('/api/contacts', { method: 'POST', body: JSON.stringify(contactForm) })
      setContacts((current) => [...current, result.contact])
      setContactForm({ name: '', channel: 'email', destination: '' })
      setNotificationMessage('Trusted contact saved in memory for this backend session; address is redacted.')
    })
  }

  function saveThresholds(event) {
    event.preventDefault()
    runAction('config', async () => {
      const result = await api('/api/monitoring/config', {
        method: 'PUT',
        body: JSON.stringify({
          route_deviation_threshold_meters: Number(thresholds.route_deviation_threshold_meters),
          unexpected_stop_threshold_minutes: Number(thresholds.unexpected_stop_threshold_minutes),
          check_in_interval_minutes: Number(thresholds.check_in_interval_minutes),
        }),
      })
      setJourney(result)
      setNotificationMessage('Demo rule thresholds updated for this backend session.')
    })
  }

  function checkIn() {
    runAction('checkin', async () => {
      await api('/api/monitoring/check-in', { method: 'POST' })
      await refreshWorkspace()
      setNotificationMessage('Simulated passenger check-in recorded; no location was shared.')
    })
  }

  function simulateRiskScenario(scenario) {
    const common = { demo_mode: true }
    const scenarios = {
      route: { ...common, gps: { source: 'simulated', deviation_meters: 900, accuracy_meters: 18, reliability: 0.9 } },
      stop: { ...common, vehicle: { source: 'simulated', stopped_minutes: 12, ignition_on: true, reliability: 0.9 } },
      combined: {
        ...common,
        gps: { source: 'simulated', deviation_meters: 1400, accuracy_meters: 15, reliability: 0.95 },
        vehicle: { source: 'simulated', stopped_minutes: 18, sudden_braking: true, reliability: 0.9 },
        check_in: { source: 'simulated', responded: false, overdue_minutes: 25, reliability: 0.9 },
      },
      unknown: { ...common, camera: { available: false, source: 'unknown' } },
    }
    runAction(`risk-${scenario}`, async () => {
      const assessment = await api('/api/risk/assessments', { method: 'POST', body: JSON.stringify(scenarios[scenario]) })
      setRiskAssessment(assessment)
      await refreshWorkspace()
      setNotificationMessage(`Simulated risk assessment: ${assessment.risk_level} (${assessment.risk_score}/100). No real contacts were notified.`)
    })
  }

  function cancelPendingAssessment() {
    if (!riskAssessment?.assessment_id) return
    runAction('risk-cancel', async () => {
      const cancelled = await api('/api/escalation/cancel', {
        method: 'POST',
        body: JSON.stringify({ assessment_id: riskAssessment.assessment_id, reason: 'Passenger used discreet check-in control' }),
      })
      setNotificationMessage(`Discreet cancellation recorded (${cancelled.state}). No real notification was sent.`)
      await refreshWorkspace()
    })
  }

  function processPendingEscalation() {
    if (!riskAssessment?.assessment_id) return
    runAction('risk-process', async () => {
      const result = await api('/api/escalation/process', {
        method: 'POST',
        body: JSON.stringify({ assessment_id: riskAssessment.assessment_id }),
      })
      setNotificationMessage(`Mock escalation state: ${result.state}. No real contact or emergency service was contacted.`)
      await refreshWorkspace()
    })
  }

  function testNotification(contactId) {
    runAction('notification', async () => {
      const result = await api('/api/notifications/test', {
        method: 'POST',
        body: JSON.stringify({ contact_id: contactId }),
      })
      setNotificationMessage(result.message)
    })
  }

  function askAssistant(event) {
    event.preventDefault()
    runAction('assistant', async () => {
      const result = await api('/api/assistant/query', {
        method: 'POST',
        body: JSON.stringify({ question: assistantQuestion }),
      })
      setAssistantAnswer(result)
    })
  }

  function resetEvents() {
    runAction('reset', async () => {
      await api('/api/events/demo', { method: 'DELETE' })
      setSos(false)
      setAssistantAnswer(null)
      await refreshWorkspace()
    })
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">S</div>
          <div>
            <strong>e-Sentinel AI</strong>
            <small>WOMEN'S JOURNEY SAFETY</small>
          </div>
        </div>

        <p className="nav-title">WORKSPACE</p>
        <a className="nav active" href="#overview">▦ &nbsp; Overview</a>
        <a className="nav" href="#journey">⌖ &nbsp; Journey monitor</a>
        <a className="nav" href="#cabin">▣ &nbsp; Cabin analysis</a>
        <a className="nav" href="#timeline">◷ &nbsp; Event timeline</a>
        <a className="nav" href="#contacts">♡ &nbsp; Trusted contacts</a>
        <a className="nav" href="#assistant">✳ &nbsp; Local assistant</a>
        <a className="nav" href="#emergency">! &nbsp; Emergency SOS</a>

        <div className="privacy">
          <span>✳</span>
          <strong>Privacy-first demo</strong>
          <p>Cabin videos are processed by your local backend and are not uploaded to a cloud service by this app.</p>
          <small>PROTOTYPE · v0.2.0</small>
        </div>
      </aside>

      <main className="main">
        <header className="header" id="overview">
          <div>
            <p className="eyebrow">SAFETY WORKSPACE / OVERVIEW</p>
            <h1>Journey safety dashboard</h1>
            <p className="muted">Monitor demo signals and analyze cabin footage.</p>
          </div>
          <div className="header-indicators">
            <span className="mode-badge">LOCAL DEMO</span>
            <div className="connection" role="status" aria-live="polite">
              <span className={`dot ${online ? 'green' : ''}`} />
              {online ? 'Systems connected' : 'Backend offline'}
            </div>
          </div>
        </header>

        <section className="hero">
          <div className="eyebrow">E-SENTINEL MONITOR</div>
          <h2>Journey monitoring, with clear evidence.</h2>
          <p>Local video analysis · opted-in browser GPS · user-controlled alerts</p>
          <div className="hero-symbol">✳</div>
        </section>

        <section className="stats">
          <article className="card">
            <p>Backend status</p>
            <h3 className={online ? 'green-text' : ''}>{online ? 'Online' : 'Offline'}</h3>
            <small>FastAPI health check</small>
          </article>
          <article className="card">
            <p>Frames processed</p>
            <h3>{result?.frames_processed ?? '—'}</h3>
            <small>Latest cabin video</small>
          </article>
          <article className="card">
            <p>Peak frame detections</p>
            <h3>{result?.maximum_people_in_one_frame ?? '—'}</h3>
            <small>Maximum in one frame</small>
          </article>
          <article className="card">
            <p>Rule-based risk score</p>
            <h3 className="amber-text">{riskAssessment ? `${riskAssessment.risk_score}/100` : '—'}</h3>
            <small>{riskAssessment ? `${riskAssessment.risk_level.toUpperCase()} · simulated assessment` : 'No assessment yet'}</small>
          </article>
        </section>

        <section className="risk-panel panel" aria-labelledby="risk-title">
          <div className="panel-title"><div><p className="eyebrow">MULTI-SIGNAL RULE ENGINE</p><h2 id="risk-title">Current assessment</h2></div><span className={`risk-pill risk-${riskAssessment?.risk_level ?? 'unknown'}`}>{riskAssessment?.risk_level?.toUpperCase() ?? 'UNKNOWN'}</span></div>
          {riskError && <p className="error" role="alert">Risk data unavailable: {riskError}</p>}
          {!riskAssessment ? <p className="empty-inline">No assessment available. Inputs not submitted are unknown, never treated as safe.</p> : <>
            <div className="risk-score-row"><strong>{riskAssessment.risk_score}</strong><span>/ 100 · prototype rule score</span><small>{riskAssessment.source?.toUpperCase()}</small></div>
            <div className="signal-cards">
              {riskAssessment.signals_used.map((signal) => <div className="signal-card" key={signal.name}><span>{signal.name.replaceAll('_', ' ')}</span><b className={`source-${signal.source}`}>{signal.source.toUpperCase()}</b><small>{signal.status}{signal.reliability != null ? ` · reliability ${Math.round(signal.reliability * 100)}%` : ''}</small></div>)}
            </div>
            <div className="risk-details"><div><strong>Reasons</strong>{riskAssessment.reasons.map((reason, index) => <p key={`${reason.signal}-${index}`}>{reason.explanation} {reason.points > 0 ? `(+${reason.points})` : ''}</p>)}</div><div><strong>Data quality</strong>{riskAssessment.data_quality.missing_or_uncertain.length ? <ul>{riskAssessment.data_quality.missing_or_uncertain.map((item) => <li key={item}>{item}</li>)}</ul> : <p>No missing signals reported.</p>}</div></div>
            <p className="risk-action"><strong>Recommended:</strong> {riskAssessment.recommended_action}</p>
            <p className="risk-unknown-notice">Unknown signals are not evidence of safety. This score describes only the supplied observations.</p>
            <p className="fine-print">{riskAssessment.disclaimer}</p>
          </>}
          <div className="risk-demo-controls" aria-label="Simulated risk scenarios">
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('route')}>Simulate route deviation</button>
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('stop')}>Simulate prolonged stop</button>
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('combined')}>Simulate corroborated critical</button>
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('unknown')}>Simulate camera unavailable</button>
          </div>
          <div className="discreet-row"><label><input type="checkbox" checked={discreetMode} onChange={(event) => setDiscreetMode(event.target.checked)} /> Discreet check-in controls</label><div className="escalation-actions">{riskAssessment?.escalation_state === 'escalation_pending' && <button type="button" disabled={!online || !!busyAction} onClick={processPendingEscalation}>Run mock escalation</button>}<button type="button" disabled={!online || !!busyAction || !riskAssessment || !['check_in_required', 'escalation_pending'].includes(riskAssessment.escalation_state)} onClick={cancelPendingAssessment}>{discreetMode ? "I'm OK · cancel pending" : 'Cancel pending demo escalation'}</button></div></div>
          {riskAssessment?.notification && <p className="notice" role="status">Mock provider: {riskAssessment.notification.status}. {riskAssessment.notification.provider_response?.message} Real contact notification: no.</p>}
        </section>

        {actionError && <p className="error" role="alert">{actionError}</p>}
        {notificationMessage && <p className="notice" role="status" aria-live="polite">{notificationMessage}</p>}

        <section className="columns">
          <article className="panel map-panel" id="journey">
            <div className="panel-title">
              <div><p className="eyebrow">JOURNEY MONITOR</p><h2>Journey and location</h2></div>
              <span className="tag">{activeJourney?.status === 'active' ? 'ACTIVE JOURNEY' : 'NO ACTIVE JOURNEY'}</span>
            </div>
            <p className="muted description">
              {activeJourney?.status === 'active'
                ? 'Location is sent from this device while monitoring is running. After a reload, resume monitoring explicitly; no map service receives coordinates.'
                : 'Start a journey to request browser location permission and save a current position.'}
            </p>
            {activeJourney?.status === 'active' ? (
              <>
                <div className="live-location-card" aria-live="polite">
                  <div className="live-location-heading"><strong>Current device location</strong><span>{locationMonitoring ? 'MONITORING' : 'PAUSED'}</span></div>
                  <p>{activeJourney.current_location.latitude.toFixed(5)}, {activeJourney.current_location.longitude.toFixed(5)}</p>
                  <small>Accuracy ±{Math.round(activeJourney.current_location.accuracy_meters)} m · Updated {new Date(activeJourney.current_location.observed_at).toLocaleString()}</small>
                </div>
                <div className="journey-details">
                  <div><span>Status</span><strong>{activeJourney.status}</strong></div>
                  <div><span>Started</span><strong>{new Date(activeJourney.started_at).toLocaleString()}</strong></div>
                  <div><span>Start</span><strong>{activeJourney.start_location.latitude.toFixed(5)}, {activeJourney.start_location.longitude.toFixed(5)}</strong></div>
                  <div><span>Destination</span><strong>{activeJourney.destination}</strong></div>
                  <div><span>Planned route</span><strong>{activeJourney.planned_route.length >= 2 ? `${activeJourney.planned_route.length} user-supplied waypoints` : 'Not supplied'}</strong></div>
                  <div><span>Route deviation</span><strong>{activeJourney.planned_route.length >= 2 ? `Checking after 3 accurate fixes (threshold ${journey?.configuration?.route_deviation_threshold_meters ?? 500} m)` : 'Unavailable without planned waypoints'}</strong></div>
                </div>
                {activeJourney.planned_route.length >= 2 && <details className="route-waypoints">
                  <summary>View planned route waypoints</summary>
                  <ol>{activeJourney.planned_route.map((point, index) => (
                    <li key={`${point.latitude}-${point.longitude}-${index}`}>{point.latitude.toFixed(5)}, {point.longitude.toFixed(5)}</li>
                  ))}</ol>
                </details>}
                {locationError && <p className="error" role="alert">{locationError}</p>}
                <div className="map-actions">
                  {!locationMonitoring && <button type="button" disabled={!!busyAction} onClick={resumeLocationMonitoring}>Resume location monitoring</button>}
                  <button type="button" disabled={!online || !!busyAction} onClick={endJourney}>End journey</button>
                </div>
              </>
            ) : (
              <div className="map-board" role="img" aria-label="Illustrative route only, no current GPS location">
                <div className="map-grid" />
                <svg className="route-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                  <polyline points={(journey?.map_route ?? []).map((point) => `${point.x},${point.y}`).join(' ')} />
                </svg>
                {(journey?.map_route ?? []).map((point, index) => (
                  <span key={`${point.x}-${point.y}`} className={`route-point ${index === journey.current_waypoint ? 'current' : ''}`} style={{ left: `${point.x}%`, top: `${point.y}%` }} aria-hidden="true" />
                ))}
                <span className="map-caption">ILLUSTRATIVE ONLY · NOT GPS</span>
              </div>
            )}
            <div className="map-state-row"><span>Provider</span><strong>{activeJourney?.status === 'active' ? 'Browser geolocation' : 'Not connected'}</strong><span>Safety status</span><strong>Unknown</strong></div>
            {journeyDataError && <p className="error" role="alert">Journey data unavailable: {journeyDataError}</p>}
            {activeJourney?.status !== 'active' && <form className="journey-form" onSubmit={startJourney}>
              <label>Destination label<input value={destination} onChange={(event) => setDestination(event.target.value)} maxLength={200} required placeholder="For example, home or office" /></label>
              <label>Optional planned route waypoints<textarea value={plannedRouteText} onChange={(event) => setPlannedRouteText(event.target.value)} rows="3" placeholder={'One latitude, longitude pair per line\nInclude start, intermediate, and destination points'} /></label>
              <small>Waypoints are user-supplied coordinates; no route planner or map service is connected. Location requires browser permission and a secure context (localhost is supported).</small>
              <button className="primary" type="submit" disabled={!online || !!busyAction}>{busyAction === 'journey-start' ? 'Starting journey...' : 'Start journey and share location'}</button>
            </form>}
            {activeJourney?.observations?.length > 0 && <div className="journey-observations">
              <strong>Journey observations</strong>
              <ol>{activeJourney.observations.slice(0, 5).map((observation) => (
                <li key={observation.id}><span>{observationLabel(observation.observation_type)}</span><p>{observation.summary}</p><small>{new Date(observation.occurred_at).toLocaleString()}</small></li>
              ))}</ol>
            </div>}
            {journeyHistory.length > 0 && <div className="journey-history">
              <strong>Recent journeys</strong>
              <ul>{journeyHistory.map((item) => (
                <li key={item.journey_id}>
                  <div className="journey-history-heading"><span><b>{item.destination}</b><small>{new Date(item.started_at).toLocaleString()}</small></span><em>{item.status}</em></div>
                  <details>
                    <summary>Journey details · {item.observations.length} observation(s)</summary>
                    <p>Start: {item.start_location.latitude.toFixed(5)}, {item.start_location.longitude.toFixed(5)} · {item.planned_route.length} planned waypoint(s)</p>
                    {item.observations.map((observation) => <p key={observation.id}>{observationLabel(observation.observation_type)} · {observation.summary}</p>)}
                  </details>
                </li>
              ))}</ul>
            </div>}
            <p className="checkin-state">Passenger check-in: <strong>{journey?.check_in_status?.replaceAll('_', ' ') ?? 'loading'}</strong></p>
            <div className="map-actions">
              <button type="button" disabled={!online || !!busyAction} onClick={checkIn}>Simulate check-in</button>
              <button type="button" disabled={!online || !!busyAction} onClick={() => toggleJourneyEvent('missedCheckIn')}>Simulate missed check-in</button>
            </div>
            <p className="demo-label">SIMULATED EVENTS · NO GPS OR VEHICLE DATA</p>
            <div className="simulation-controls">
              <button type="button" disabled={!online || !!busyAction} onClick={() => toggleJourneyEvent('unexpectedStop')}>Simulate unexpected stop</button>
              <button type="button" disabled={!online || !!busyAction} onClick={() => toggleJourneyEvent('routeDeviation')}>Simulate route deviation</button>
              <button type="button" disabled={!online || !!busyAction} onClick={() => toggleJourneyEvent('missedCheckIn')}>Simulate missed check-in</button>
              <button type="button" className="reset-control" disabled={!online || !!busyAction} onClick={resetEvents}>Reset demo events</button>
            </div>
            {thresholds && <form className="threshold-form" onSubmit={saveThresholds}>
              <strong>Explainable demo thresholds</strong>
              <label>Route offset (m)<input type="number" min="50" max="10000" value={thresholds.route_deviation_threshold_meters} onChange={(event) => setThresholds({ ...thresholds, route_deviation_threshold_meters: event.target.value })} /></label>
              <label>Stop duration (min)<input type="number" min="1" max="120" value={thresholds.unexpected_stop_threshold_minutes} onChange={(event) => setThresholds({ ...thresholds, unexpected_stop_threshold_minutes: event.target.value })} /></label>
              <label>Check-in interval (min)<input type="number" min="5" max="720" value={thresholds.check_in_interval_minutes} onChange={(event) => setThresholds({ ...thresholds, check_in_interval_minutes: event.target.value })} /></label>
              <button type="submit" disabled={!online || !!busyAction}>Save thresholds</button>
            </form>}
          </article>

          <article className="panel" id="cabin">
            <div className="panel-title">
              <div>
                <p className="eyebrow">COMPUTER VISION</p>
                <h2>Cabin video analysis</h2>
              </div>
              <span className="tag">YOLO</span>
            </div>
            <p className="muted description">
              Select a video to count detected people frame by frame.
            </p>

            <form onSubmit={analyze}>
              <button
                className="upload"
                type="button"
                onClick={() => fileRef.current?.click()}
              >
                <span className="upload-icon">↑</span>
                <strong>{file ? file.name : 'Choose a cabin video'}</strong>
                <small>MP4, AVI, MOV or MKV · Up to 100 MB</small>
              </button>
              <input
                ref={fileRef}
                className="hidden"
                type="file"
                accept=".mp4,.avi,.mov,.mkv,video/mp4,video/quicktime,video/x-msvideo,video/x-matroska"
                aria-label="Choose a cabin video"
                onChange={(event) => {
                  selectVideo(event.target.files?.[0] ?? null)
                  event.target.value = ''
                }}
              />
              <button className="primary" disabled={loading || !online || !file}>
                {loading ? 'Analyzing video...' : 'Run cabin analysis'}
              </button>
            </form>

            {error && <p className="error" role="alert">{error}</p>}
            {loading && (
              <p className="progress" role="status" aria-live="polite">
                Processing video locally. Longer videos may take a little while.
              </p>
            )}

            {result && (
              <div className="result" aria-live="polite" role="status">
                <strong>Analysis complete</strong>
                <div className="result-row">
                  <span>Frames analyzed</span>
                  <b>{result.frames_processed}</b>
                </div>
                <div className="result-row">
                  <span>Person detections across analyzed frames</span>
                  <b>{result.total_person_detections_across_frames}</b>
                </div>
                <div className="result-row">
                  <span>Maximum detections in one frame</span>
                  <b>{result.maximum_people_in_one_frame}</b>
                </div>
                <div className="result-row">
                  <span>Detection confidence (mean / maximum)</span>
                  <b>
                    {result.mean_detection_confidence == null
                      ? 'Not available'
                      : `${(result.mean_detection_confidence * 100).toFixed(1)}% / ${(result.maximum_detection_confidence * 100).toFixed(1)}%`}
                  </b>
                </div>
                <div className="result-row">
                  <span>Analysis settings</span>
                  <b>
                    {`Confidence ${result.confidence_threshold ?? '—'} · IoU ${result.iou_threshold ?? '—'} · every ${result.frame_stride ?? 1} frame(s)`}
                  </b>
                </div>
                {result.event_created && <p className="fine-print">A cabin-count change was added to the event timeline as a video observation.</p>}
                {result.annotated_preview && <figure className="preview-figure"><img src={result.annotated_preview} alt="YOLO-annotated frame from the uploaded cabin video" /><figcaption>Annotated frame from uploaded video · local analysis · not identity recognition</figcaption></figure>}
                <p className="warning">{result.safety_assessment}</p>
                <p className="fine-print">
                  Counts are frame-level detections, not unique people. False positives and missed detections are possible.
                </p>
              </div>
            )}
          </article>

        </section>

        <section className="columns lower-grid">
          <article className="panel" id="timeline">
            <div className="panel-title"><div><p className="eyebrow">AUDIT TRAIL</p><h2>Safety event timeline</h2></div><span className="tag">{events.length} EVENTS</span></div>
            {eventsError ? <div className="error" role="alert">Saved events are unavailable: {eventsError}</div> : !workspaceLoaded ? <div className="empty-state" role="status">Loading saved events...</div> : events.length === 0 ? <div className="empty-state">No events yet. Cabin observations and simulated journey events will appear here.</div> : (
              <ol className="timeline-list">
                {events.slice(0, 12).map((event) => (
                  <li key={event.id} className="timeline-item">
                    <span className={`severity-mark severity-${event.severity}`} aria-hidden="true" />
                    <div className="timeline-copy">
                      <div className="timeline-heading"><strong>{event.event_type.replaceAll('_', ' ')}</strong><span className={`severity-label severity-text-${event.severity}`}>{event.severity}</span></div>
                      <p>{event.observed_fact}</p>
                      <small>{new Date(event.occurred_at).toLocaleString()} · {event.is_demo ? 'SIMULATED' : 'VIDEO OBSERVATION'}</small>
                      <details><summary>Why flagged?</summary><p>{event.rule} {event.inferred_signal} Unknown: {event.unknown}</p></details>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </article>

          <article className="panel" id="assistant">
            <div className="panel-title"><div><p className="eyebrow">AI ASSISTANT</p><h2>Ask about this session</h2></div><span className="tag">LOCAL MODE</span></div>
            <p className="muted description">Answers are generated from this backend session's event records and rules. No external model is called.</p>
            <form className="assistant-form" onSubmit={askAssistant}>
              <label htmlFor="assistant-question">Question</label>
              <textarea id="assistant-question" rows="3" maxLength={500} value={assistantQuestion} onChange={(event) => setAssistantQuestion(event.target.value)} />
              <button className="primary" disabled={!online || !!busyAction || !assistantQuestion.trim()}>{busyAction === 'assistant' ? 'Thinking...' : 'Ask local assistant'}</button>
            </form>
            {assistantAnswer && <div className="assistant-answer" role="status"><strong>Local rules assistant</strong><p>{assistantAnswer.answer}</p><small>{assistantAnswer.disclaimer}</small></div>}
            <div className="note"><strong>Safety remains unknown</strong><p>The assistant summarizes recorded facts and rule thresholds. It is not a risk predictor or emergency advisor.</p></div>
          </article>
        </section>

        <section className="panel escalation-panel">
          <div className="panel-title"><div><p className="eyebrow">AUDITABLE STATE MACHINE</p><h2>Escalation and delivery history</h2></div><span className="tag">MOCK PROVIDER ONLY</span></div>
          {escalations.length === 0 ? <p className="empty-inline">No escalation state changes recorded.</p> : <ol className="timeline-list">{escalations.slice(0, 8).map((record) => <li key={record.id} className="timeline-item"><span className="severity-mark severity-info" aria-hidden="true" /><div className="timeline-copy"><div className="timeline-heading"><strong>{record.state.replaceAll('_', ' ')}</strong><small>{new Date(record.occurred_at).toLocaleString()}</small></div><p>{record.reason}</p>{record.notifications?.map((attempt) => <small key={attempt.id}>Notification: {attempt.status} · {attempt.provider} · no real destination configured</small>)}</div></li>)}</ol>}
          <p className="fine-print">Demo mode cannot send to trusted contacts, police, or emergency services. “Notified” refers only to the mock provider's simulated result.</p>
        </section>

        <section className="panel risk-history-panel">
          <div className="panel-title"><div><p className="eyebrow">ASSESSMENT HISTORY</p><h2>Recent risk assessments</h2></div><span className="tag">{riskHistory.length} RECORDS</span></div>
          {riskHistory.length === 0 ? <p className="empty-inline">No risk assessments recorded.</p> : <ol className="risk-history-list">{riskHistory.slice(0, 8).map((assessment) => <li key={assessment.assessment_id}><time>{new Date(assessment.timestamp).toLocaleString()}</time><strong className={`source-${assessment.source}`}>{assessment.risk_level.toUpperCase()}</strong><span>{assessment.risk_score}/100</span><small>{assessment.source.toUpperCase()}</small></li>)}</ol>}
        </section>

        <section className="panel contacts-panel" id="contacts">
          <div className="panel-title"><div><p className="eyebrow">USER-CONTROLLED SETTINGS</p><h2>Trusted contacts</h2></div><span className="tag">IN-MEMORY ONLY</span></div>
          <p className="muted description">Contact entries are held in backend memory for this session only. No message provider is configured.</p>
          {contactsError && <p className="error" role="alert">Trusted contacts unavailable: {contactsError}</p>}
          {contacts.length === 0 ? <p className="empty-inline">No trusted contacts configured.</p> : <ul className="contact-list">{contacts.map((contact) => <li key={contact.id}><span><strong>{contact.name}</strong><small>{contact.channel.toUpperCase()} · {contact.destination_hint}</small></span><button type="button" disabled={!!busyAction} onClick={() => testNotification(contact.id)}>Test notification</button></li>)}</ul>}
          <form className="contact-form" onSubmit={saveContacts}>
            <label>Name<input required maxLength={80} value={contactForm.name} onChange={(event) => setContactForm({ ...contactForm, name: event.target.value })} /></label>
            <label>Channel<select value={contactForm.channel} onChange={(event) => setContactForm({ ...contactForm, channel: event.target.value })}><option value="email">Email</option><option value="sms">SMS</option></select></label>
            <label>Address or number<input required maxLength={160} autoComplete="off" value={contactForm.destination} onChange={(event) => setContactForm({ ...contactForm, destination: event.target.value })} /></label>
            <button className="primary" disabled={!online || !!busyAction}>Save contact</button>
          </form>
          <p className="fine-print">Test mode confirms the UI workflow only. It never sends email or SMS and never reports delivery.</p>
        </section>

        <section className="sos-panel" id="emergency">
          <div className="sos-icon">!</div>
          <div className="sos-text">
            <h2>{sos ? 'Demo SOS activated' : 'Emergency action demo'}</h2>
            <p>{sos ? 'Demo event recorded. No contacts or emergency services were contacted.' : 'Explicit user action only. No real emergency alerts are configured.'}</p>
          </div>
          <button
            className={sos ? 'secondary' : 'sos-button'}
            type="button"
            aria-pressed={sos}
            onClick={sos ? () => setSos(false) : triggerSOS}
            disabled={!online || !!busyAction}
          >
            {sos ? 'Reset demo' : 'Trigger demo SOS'}
          </button>
        </section>

        <footer>
          <span>e-Sentinel AI · Hackathon prototype</span>
          <span>Demo only · Not an emergency service</span>
        </footer>
      </main>
    </div>
  )
}
