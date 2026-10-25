
import { useEffect, useRef, useState } from 'react'
import './App.css'

function readableApiError(detail, fallback = 'Request failed') {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === 'string') return item
      const field = Array.isArray(item.loc) ? item.loc.join('.') : ''
      return [field, item.msg].filter(Boolean).join(': ') || JSON.stringify(item)
    }).join('; ')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.msg || JSON.stringify(detail)
  }
  return fallback
}

async function api(path, options = {}) {
  const { headers, ...requestOptions } = options
  const response = await fetch(path, {
    ...requestOptions,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(
      readableApiError(data.detail, `Request failed (${response.status})`),
    )
  }
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

function eventSourceLabel(event) {
  if (event.source === 'browser_geolocation') return 'BROWSER GPS'
  if (event.source === 'uploaded_video_yolo') return 'CABIN VIDEO'
  if (event.is_demo) return 'SIMULATED'
  return event.source?.replaceAll('_', ' ').toUpperCase() ?? 'RECORDED'
}

function signalAvailability(signal) {
  if (signal?.available === false || signal?.status === 'unavailable' || signal?.status === 'not_available') return 'UNAVAILABLE'
  if (!signal || signal.source === 'unknown' || signal.status === 'unknown') return 'UNKNOWN'
  if (signal.source === 'simulated') return 'SIMULATED'
  return 'AVAILABLE'
}

function formatTimestamp(timestamp) {
  if (!timestamp) return 'Not available'
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? 'Not available' : date.toLocaleString()
}

function greetingForTime() {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

const assistantSuggestions = [
  'Why was this risk score generated?',
  'Summarize the latest events.',
  'Which signals are unavailable?',
  'What should I check next?',
]

function locationAccuracyNotice(journey, routeDeviationThreshold) {
  if (!journey) return null

  const accuracy = journey.current_location.accuracy_meters
  const insufficientDetectors = []
  if (
    journey.planned_route.length >= 2
    && accuracy > Math.min(100, Math.max(25, routeDeviationThreshold / 4))
  ) {
    insufficientDetectors.push('route-deviation checks')
  }
  if (accuracy > 50) insufficientDetectors.push('GPS-derived stop checks')
  if (insufficientDetectors.length === 0) return null

  return `This fix is stored, but its accuracy is insufficient for ${insufficientDetectors.join(' and ')}. No observation or risk assessment is inferred from an unreliable fix.`
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
  const [editingContactId, setEditingContactId] = useState('')
  const [assistantQuestion, setAssistantQuestion] = useState('Summarize recent events')
  const [assistantAnswer, setAssistantAnswer] = useState(null)
  const [notificationMessage, setNotificationMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [busyAction, setBusyAction] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [activeNav, setActiveNav] = useState('overview')
  const [eventSeverityFilter, setEventSeverityFilter] = useState('all')
  const [eventTypeFilter, setEventTypeFilter] = useState('all')
  const [eventSourceFilter, setEventSourceFilter] = useState('all')
  const [eventSearch, setEventSearch] = useState('')
  const fileRef = useRef(null)
  const locationWatchRef = useRef(null)
  const locationRequestInFlightRef = useRef(false)
  const lastLocationSentAtRef = useRef(0)
  const lastSavedLocationTimestampRef = useRef(0)
  const gpsAccuracyNotice = locationAccuracyNotice(
    activeJourney,
    journey?.configuration?.route_deviation_threshold_meters ?? 500,
  )
  const visibleEvents = events.filter((event) => {
    const matchesSeverity = eventSeverityFilter === 'all' || event.severity === eventSeverityFilter
    const matchesType = eventTypeFilter === 'all' || event.event_type === eventTypeFilter
    const matchesSource = eventSourceFilter === 'all'
      || (event.is_demo ? 'simulated' : 'recorded') === eventSourceFilter
    const searchText = `${event.event_type} ${event.observed_fact} ${eventSourceLabel(event)}`.toLowerCase()
    return matchesSeverity && matchesType && matchesSource && searchText.includes(eventSearch.trim().toLowerCase())
  })
  const riskScore = riskAssessment?.risk_score
  const latestAssessmentSignals = riskAssessment?.signals_used ?? []
  const signalCards = [
    { name: 'Camera', icon: '◫', key: 'camera' },
    { name: 'GPS', icon: '⌖', key: 'gps' },
    { name: 'Vehicle signals', icon: '◈', key: 'vehicle' },
    { name: 'Passenger check-in', icon: '◉', key: 'check_in' },
  ].map((item) => ({
    ...item,
    signal: latestAssessmentSignals.find((signal) => signal.name === item.key),
  }))
  const trendAssessments = riskHistory.slice(0, 8).reverse()
  const journeyStartedAt = Date.parse(activeJourney?.started_at ?? '')
  const latestJourneyFixAt = Date.parse(activeJourney?.current_location?.observed_at ?? '')
  const journeyDurationMinutes = Number.isFinite(journeyStartedAt) && Number.isFinite(latestJourneyFixAt)
    ? Math.max(0, Math.floor((latestJourneyFixAt - journeyStartedAt) / 60000))
    : null

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
            if (saved.linked_results?.length) {
              setRiskAssessment(saved.linked_results[saved.linked_results.length - 1].assessment)
              await refreshWorkspace()
            }
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
    if (!window.confirm('End this journey and stop browser location monitoring?')) return
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
      if (editingContactId) {
        const result = await api(`/api/contacts/${encodeURIComponent(editingContactId)}`, {
          method: 'PUT',
          body: JSON.stringify(contactForm),
        })
        setContacts((current) => current.map((contact) => contact.id === editingContactId ? result.contact : contact))
        setNotificationMessage('Trusted contact updated. Its address remains masked.')
      } else {
        const result = await api('/api/contacts', { method: 'POST', body: JSON.stringify(contactForm) })
        setContacts((current) => [...current, result.contact])
        setNotificationMessage('Trusted contact saved in memory for this backend session; address is redacted.')
      }
      setContactForm({ name: '', channel: 'email', destination: '' })
      setEditingContactId('')
    })
  }

  function editContact(contact) {
    setEditingContactId(contact.id)
    setContactForm({ name: contact.name, channel: contact.channel, destination: '' })
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

  function runEndToEndDemo() {
    runAction('end-to-end-demo', async () => {
      // Step 1: Record a simulated journey event.
      await api('/api/events/demo', {
        method: 'POST',
        body: JSON.stringify({
          event_type: 'route_deviation',
          route_deviation_meters:
            (journey?.configuration?.route_deviation_threshold_meters ?? 500) + 200,
        }),
      })

      // Step 2: Assess a corroborated, explicitly simulated scenario.
      const assessment = await api('/api/risk/assessments', {
        method: 'POST',
        body: JSON.stringify({
          demo_mode: true,
          gps: {
            source: 'simulated',
            deviation_meters: 1400,
            accuracy_meters: 15,
            reliability: 0.95,
          },
          vehicle: {
            source: 'simulated',
            stopped_minutes: 18,
            sudden_braking: true,
            reliability: 0.9,
          },
          check_in: {
            source: 'simulated',
            responded: false,
            overdue_minutes: 25,
            reliability: 0.9,
          },
        }),
      })

      // Step 3: Show the resulting assessment and reload saved history.
      setRiskAssessment(assessment)
      await refreshWorkspace()

      setNotificationMessage(
        `End-to-end demo completed: ${assessment.risk_level.toUpperCase()} risk (${assessment.risk_score}/100). Simulated event and assessment submitted. No real contacts or emergency services were contacted.`,
      )
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

  function respondToRiskCheckIn() {
    if (!riskAssessment?.assessment_id) return
    runAction('risk-checkin', async () => {
      const result = await api(
        `/api/risk/assessments/${encodeURIComponent(riskAssessment.assessment_id)}/check-in`,
        { method: 'POST' },
      )
      setNotificationMessage('Passenger response recorded. This does not establish that the passenger is safe.')
      await refreshWorkspace()
      setRiskAssessment((current) => current ? { ...current, escalation_state: result.escalation_state } : current)
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
    submitAssistantQuestion(assistantQuestion)
  }

  function submitAssistantQuestion(question) {
    runAction('assistant', async () => {
      const result = await api('/api/assistant/query', {
        method: 'POST',
        body: JSON.stringify({ question }),
      })
      setAssistantAnswer(result)
    })
  }

  function resetEvents() {
    if (!window.confirm('Reset simulated events and demo risk history? Recorded non-demo events will be preserved.')) return
    runAction('reset', async () => {
      await api('/api/events/demo', { method: 'DELETE' })
      setSos(false)
      setAssistantAnswer(null)
      await refreshWorkspace()
      setNotificationMessage('Simulated events and demo assessments were reset. Recorded non-demo events were preserved.')
    })
  }

  return (
    <div className={`app ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand">
          <div className="logo" aria-hidden="true">S</div>
          <div>
            <strong>e-Sentinel AI</strong>
            <small>WOMEN'S JOURNEY SAFETY</small>
          </div>
          <button
            className="sidebar-toggle"
            type="button"
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!sidebarCollapsed}
            onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
          >
            {sidebarCollapsed ? '»' : '«'}
          </button>
        </div>

        <p className="nav-title">WORKSPACE</p>
        <nav className="workspace-nav">
          {[
            ['overview', '▦', 'Overview'],
            ['journey', '⌖', 'Journey monitor'],
            ['cabin', '◫', 'Cabin vision'],
            ['timeline', '◷', 'Event timeline'],
            ['risk-history', '⌁', 'Risk history'],
            ['contacts', '♡', 'Trusted contacts'],
            ['assistant', '✳', 'Local assistant'],
            ['settings', '⚙', 'Settings'],
            ['emergency', '!', 'Emergency SOS'],
          ].map(([section, icon, label]) => (
            <a
              key={section}
              className={`nav ${activeNav === section ? 'active' : ''}`}
              href={`#${section}`}
              aria-current={activeNav === section ? 'page' : undefined}
              onClick={() => setActiveNav(section)}
              title={sidebarCollapsed ? label : undefined}
            >
              <span className="nav-icon" aria-hidden="true">{icon}</span>
              <span className="nav-label">{label}</span>
            </a>
          ))}
        </nav>

        <div className="privacy">
          <span aria-hidden="true">✳</span>
          <strong>Privacy-first demo</strong>
          <p>Cabin videos are processed by your local backend and are not uploaded to a cloud service by this app.</p>
          <small>PROTOTYPE · v0.2.0</small>
        </div>
      </aside>

      <main className="main">
        <header className="header" id="overview">
          <div>
            <p className="eyebrow">E-SENTINEL / COMMAND CENTER</p>
            <h1>Safety overview</h1>
            <p className="muted">Your safety, intelligently monitored.</p>
          </div>
          <div className="header-indicators">
            <span className="mode-badge">DEMO MODE</span>
            <div className="connection" role="status" aria-live="polite">
              <span className={`dot ${online ? 'green' : ''}`} />
              {online ? 'Backend connected' : 'Backend offline'}
            </div>
            {!online && <button type="button" className="retry-button" onClick={() => runAction('refresh', refreshWorkspace)}>Retry connection</button>}
          </div>
        </header>

        <section className="hero">
          <div className="hero-copy">
            <div className="eyebrow">SAFETY WORKSPACE · LOCAL SESSION</div>
            <h2>{greetingForTime()}</h2>
            <p>Your safety, intelligently monitored.</p>
            <div className="hero-status">
              <span className={`status-indicator ${activeJourney?.status === 'active' ? 'is-active' : ''}`} />
              {activeJourney?.status === 'active' ? 'Journey monitoring active' : 'No active journey'}
              <span className="demo-chip">SIMULATED DATA CLEARLY LABELLED</span>
            </div>
          </div>
          <div className="hero-symbol" aria-hidden="true">✳</div>
        </section>

        <section className="stats">
          <article className={`card metric-card ${!workspaceLoaded ? 'loading-skeleton' : ''}`}>
            <div className="metric-heading"><span className="metric-icon violet" aria-hidden="true">◉</span><span>Backend connection</span></div>
            <h3 className={online ? 'green-text' : 'coral-text'}>{workspaceLoaded ? (online ? 'Connected' : 'Offline') : 'Loading'}</h3>
            <small>{online ? 'Local API responding' : 'Retry when the backend is available'}</small>
          </article>
          <article className={`card metric-card ${!workspaceLoaded ? 'loading-skeleton' : ''}`}>
            <div className="metric-heading"><span className="metric-icon turquoise" aria-hidden="true">⌖</span><span>Current journey</span></div>
            <h3>{activeJourney?.status === 'active' ? 'Monitoring' : (workspaceLoaded ? 'Not active' : 'Loading')}</h3>
            <small>{activeJourney?.destination ?? 'Start a journey when you are ready'}</small>
          </article>
          <article className={`card metric-card ${!workspaceLoaded ? 'loading-skeleton' : ''}`}>
            <div className="metric-heading"><span className="metric-icon coral" aria-hidden="true">⌁</span><span>Latest risk score</span></div>
            <h3 className="risk-metric">{riskScore != null ? `${riskScore}/100` : (workspaceLoaded ? 'Not available' : 'Loading')}</h3>
            <small>{riskAssessment ? `${riskAssessment.risk_level.toUpperCase()} · ${riskAssessment.source?.toUpperCase() ?? 'UNKNOWN'} source` : 'Not analyzed yet'}</small>
          </article>
          <article className={`card metric-card ${!workspaceLoaded ? 'loading-skeleton' : ''}`}>
            <div className="metric-heading"><span className="metric-icon amber" aria-hidden="true">▣</span><span>Cabin frames processed</span></div>
            <h3>{result?.frames_processed ?? (workspaceLoaded ? 'Not analyzed yet' : 'Loading')}</h3>
            <small>{result ? `${result.total_person_detections_across_frames} frame-level detections in latest analysis` : 'No cabin video analysis in this view'}</small>
          </article>
        </section>

        <section className="risk-panel panel" aria-labelledby="risk-title">
          <div className="panel-title">
            <div><p className="eyebrow">MULTI-SIGNAL RULE ENGINE</p><h2 id="risk-title">Risk intelligence</h2></div>
            <span className={`risk-pill risk-${riskAssessment?.risk_level ?? 'unknown'}`}>{riskAssessment?.risk_level?.toUpperCase() ?? 'UNKNOWN'}</span>
          </div>
          {riskError && <p className="error" role="alert">Risk data unavailable: {riskError}</p>}
          {!riskAssessment ? <p className="empty-inline">{riskError ? 'Risk assessment could not be loaded.' : 'No assessment available. Run a labelled scenario to see a rule-based assessment.'} Inputs not submitted are unknown, never treated as safe.</p> : <>
            <div className="risk-summary">
              <div
                className={`risk-gauge risk-${riskAssessment.risk_level}`}
                style={{ '--risk-score': `${Math.max(0, Math.min(100, riskAssessment.risk_score)) * 3.6}deg` }}
                role="img"
                aria-label={`Risk score ${riskAssessment.risk_score} out of 100`}
              >
                <div><strong>{riskAssessment.risk_score}</strong><span>/ 100</span></div>
              </div>
              <div className="risk-summary-copy">
                <span className={`risk-pill risk-${riskAssessment.risk_level}`}>{riskAssessment.risk_level.toUpperCase()} RISK</span>
                <h3>Assessment overview</h3>
                <p>{riskAssessment.recommended_action}</p>
                <div className="risk-meta">
                  <span>◷ {formatTimestamp(riskAssessment.timestamp)}</span>
                  <span>Source: {riskAssessment.source?.toUpperCase() ?? 'UNKNOWN'}</span>
                  <span>{riskAssessment.data_quality?.corroborating_signal_count ?? 0} corroborating signals</span>
                </div>
              </div>
            </div>
            <div className="risk-details">
              <div className="risk-factors">
                <strong>Contributing factors</strong>
                {(riskAssessment.reasons ?? []).filter((reason) => reason.signal !== 'scoring').length === 0
                  ? <p>No contributing factors were returned for this assessment.</p>
                  : (riskAssessment.reasons ?? []).filter((reason) => reason.signal !== 'scoring').map((reason, index) => (
                    <div className="factor-row" key={`${reason.signal}-${index}`}>
                      <span><b>{reason.signal.replaceAll('_', ' ')}</b><small>{reason.explanation}</small></span>
                      <strong className={reason.points > 0 ? 'factor-points' : ''}>{reason.points > 0 ? `+${reason.points}` : `${reason.points ?? 0}`} pts</strong>
                    </div>
                  ))}
              </div>
              <div>
                <strong>Data quality</strong>
                <p className="quality-count">{riskAssessment.data_quality?.missing_or_uncertain?.length
                  ? `${riskAssessment.data_quality.missing_or_uncertain.length} signal quality concern(s)`
                  : 'No missing signals reported.'}</p>
                {riskAssessment.data_quality?.missing_or_uncertain?.length
                  ? <ul>{riskAssessment.data_quality.missing_or_uncertain.map((item) => <li key={item}>{item}</li>)}</ul>
                  : <p>Only supplied observations are represented. Unknown is not safe.</p>}
              </div>
            </div>
            <p className="risk-action"><strong>Recommended next action</strong>{riskAssessment.recommended_action}</p>
            <p className="risk-unknown-notice">Unknown signals are not evidence of safety. This score describes only the supplied observations.</p>
            <p className="fine-print">{riskAssessment.disclaimer} The prototype's rule score is not a probability of harm.</p>
          </>}
          <div className="signal-quality">
            <div className="subsection-heading"><div><strong>Signal quality</strong><small>Status is scoped to the latest returned assessment.</small></div><span className="tag">4 SIGNALS</span></div>
            <div className="signal-cards">
              {signalCards.map(({ name, icon, key, signal }) => (
                <article className="signal-card" key={key}>
                  <span className="signal-card-icon" aria-hidden="true">{icon}</span>
                  <div><strong>{name}</strong><b className={`availability availability-${signalAvailability(signal).toLowerCase()}`}>{signalAvailability(signal)}</b></div>
                  <small>{signal?.reliability != null ? `Reliability ${Math.round(signal.reliability * 100)}%` : 'Reliability not provided'}</small>
                </article>
              ))}
            </div>
          </div>
          <div className="risk-demo-controls" aria-label="Simulated risk scenarios">
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('route')}>SIMULATED · Route deviation</button>
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('stop')}>SIMULATED · Prolonged stop</button>
            <button type="button" disabled={!online || !!busyAction} onClick={() => toggleJourneyEvent('missedCheckIn')}>SIMULATED · Missed check-in</button>
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('combined')}>SIMULATED · Corroborated critical</button>
            <button type="button" disabled={!online || !!busyAction} onClick={() => simulateRiskScenario('unknown')}>SIMULATED · Camera unavailable</button>
            <button
              type="button"
              className="primary"
              disabled={!online || !!busyAction}
              onClick={runEndToEndDemo}
            >
              {busyAction === 'end-to-end-demo'
                ? 'Running safety workflow...'
                : '▶ Run end-to-end safety demo'}
            </button>
          </div>
          <div className="discreet-row"><label><input type="checkbox" checked={discreetMode} onChange={(event) => setDiscreetMode(event.target.checked)} /> Discreet check-in controls</label><div className="escalation-actions">{['check_in_required', 'escalation_pending'].includes(riskAssessment?.escalation_state) && <button type="button" disabled={!online || !!busyAction} onClick={respondToRiskCheckIn}>Respond to check-in</button>}{riskAssessment?.escalation_state === 'escalation_pending' && <button type="button" disabled={!online || !!busyAction} onClick={processPendingEscalation}>Run mock escalation</button>}<button type="button" disabled={!online || !!busyAction || !riskAssessment || !['check_in_required', 'escalation_pending'].includes(riskAssessment.escalation_state)} onClick={cancelPendingAssessment}>{discreetMode ? "I'm OK · cancel pending" : 'Cancel pending demo escalation'}</button></div></div>
          {riskAssessment?.notification && <p className="notice" role="status">Mock provider: {riskAssessment.notification.status}. {riskAssessment.notification.provider_response?.message} Real contact notification: no.</p>}
        </section>

        <div className="toast-stack" aria-live="polite">
          {actionError && <p className="error" role="alert">{actionError}</p>}
          {notificationMessage && <p className="notice" role="status" aria-live="polite">{notificationMessage}</p>}
        </div>

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
                <ol className="journey-progress" aria-label="Journey progress">
                  <li className="complete"><span>1</span>Started</li>
                  <li className={locationMonitoring ? 'complete' : 'current'}><span>2</span>Monitoring</li>
                  <li><span>3</span>Completed</li>
                </ol>
                <div className="live-location-card" aria-live="polite">
                  <div className="live-location-heading"><strong>Current device location</strong><span>{locationMonitoring ? 'MONITORING' : 'PAUSED'}</span></div>
                  <p>{activeJourney.current_location.latitude.toFixed(5)}, {activeJourney.current_location.longitude.toFixed(5)}</p>
                  <small>Accuracy ±{Math.round(activeJourney.current_location.accuracy_meters)} m · Updated {formatTimestamp(activeJourney.current_location.observed_at)}</small>
                </div>
                {gpsAccuracyNotice && (
                  <p className="accuracy-warning" role="status">
                    {gpsAccuracyNotice}
                  </p>
                )}
                <div className="journey-details">
                  <div><span>Status</span><strong>{activeJourney.status}</strong></div>
                  <div><span>Started</span><strong>{formatTimestamp(activeJourney.started_at)}</strong></div>
                  <div><span>Recorded duration</span><strong>{journeyDurationMinutes == null ? 'Not available' : `${journeyDurationMinutes} min through latest GPS fix`}</strong></div>
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
                <li key={observation.id}>
                  <span>{observationLabel(observation.observation_type)} · {(observation.severity ?? 'info').toUpperCase()}</span>
                  <p>{observation.summary}</p>
                  {observation.risk_assessment_id && (() => {
                    const linkedRisk = riskHistory.find((item) => item.assessment_id === observation.risk_assessment_id)
                    return linkedRisk && <p className="linked-risk">Linked risk: {linkedRisk.risk_level.toUpperCase()} · {linkedRisk.risk_score}/100</p>
                  })()}
                  <small>{new Date(observation.occurred_at).toLocaleString()} · {observation.source ?? 'browser_geolocation'} · accuracy ±{Math.round(observation.accuracy_meters ?? 0)} m · {observation.risk_assessment_id ? `assessment ${observation.risk_assessment_id.slice(0, 8)}` : 'risk processing pending'}</small>
                </li>
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
                    <p>Last saved fix: {item.current_location.latitude.toFixed(5)}, {item.current_location.longitude.toFixed(5)} · accuracy ±{Math.round(item.current_location.accuracy_meters)} m · {new Date(item.current_location.observed_at).toLocaleString()}</p>
                    {item.observations.map((observation) => <p key={observation.id}>{observationLabel(observation.observation_type)} · {(observation.severity ?? 'info').toUpperCase()} · {observation.summary} · {observation.risk_assessment_id ? `risk assessment ${observation.risk_assessment_id.slice(0, 8)}` : 'risk link pending'}</p>)}
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
            {thresholds && <form className="threshold-form" id="settings" onSubmit={saveThresholds}>
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
            <div className="panel-title"><div><p className="eyebrow">AUDIT TRAIL</p><h2>Event timeline</h2></div><span className="tag">{visibleEvents.length} MATCHING</span></div>
            {eventsError ? <div className="error" role="alert">Saved events are unavailable: {eventsError}</div> : !workspaceLoaded ? <div className="empty-state" role="status">Loading saved events...</div> : <>
              <div className="event-filters" aria-label="Filter timeline events">
                <label>Search<input type="search" value={eventSearch} onChange={(event) => setEventSearch(event.target.value)} placeholder="Search event details" /></label>
                <label>Severity<select value={eventSeverityFilter} onChange={(event) => setEventSeverityFilter(event.target.value)}>
                  <option value="all">All severities</option>
                  {[...new Set(events.map((event) => event.severity))].map((severity) => <option key={severity} value={severity}>{severity}</option>)}
                </select></label>
                <label>Event type<select value={eventTypeFilter} onChange={(event) => setEventTypeFilter(event.target.value)}>
                  <option value="all">All event types</option>
                  {[...new Set(events.map((event) => event.event_type))].map((type) => <option key={type} value={type}>{type.replaceAll('_', ' ')}</option>)}
                </select></label>
                <label>Source<select value={eventSourceFilter} onChange={(event) => setEventSourceFilter(event.target.value)}>
                  <option value="all">All sources</option><option value="simulated">Simulated</option><option value="recorded">Recorded / device</option>
                </select></label>
              </div>
              {events.length === 0 ? <div className="empty-state">No events recorded yet. Run a clearly labelled demo scenario to populate the timeline.</div> : visibleEvents.length === 0 ? <div className="empty-state">No events match these filters. Adjust or clear the filters to see recorded events.</div> : (
              <ol className="timeline-list">
                {visibleEvents.slice(0, 12).map((event) => (
                  <li key={event.id} className="timeline-item">
                    <span className={`severity-mark severity-${event.severity}`} aria-hidden="true" />
                    <div className="timeline-copy">
                      <div className="timeline-heading"><strong>{event.event_type.replaceAll('_', ' ')}</strong><span className={`severity-label severity-text-${event.severity}`}>{event.severity}</span></div>
                      <p>{event.observed_fact}</p>
                      <small>{formatTimestamp(event.occurred_at)} · {eventSourceLabel(event)}</small>
                      <details><summary>Why flagged?</summary><p>{event.rule} {event.inferred_signal} Unknown: {event.unknown}</p></details>
                    </div>
                  </li>
                ))}
              </ol>
              )}
            </>}
          </article>

          <article className="panel" id="assistant">
            <div className="panel-title"><div><p className="eyebrow">AI ASSISTANT</p><h2>Ask about this session</h2></div><span className="tag">LOCAL MODE</span></div>
            <p className="muted description">Answers are generated from this backend session's event records and rules. No external model is called.</p>
            <div className="assistant-suggestions" aria-label="Suggested questions">
              {assistantSuggestions.map((question) => (
                <button key={question} type="button" disabled={!online || !!busyAction} onClick={() => submitAssistantQuestion(question)}>{question}</button>
              ))}
            </div>
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
          <div className="panel-title" id="risk-history"><div><p className="eyebrow">ASSESSMENT HISTORY</p><h2>Risk history &amp; trend</h2></div><span className="tag">{riskHistory.length} RECORDS</span></div>
          {riskError && <p className="error" role="alert">Risk history unavailable: {riskError}</p>}
          {riskHistory.length === 0 ? <p className="empty-inline">No risk assessments recorded yet. Assessments appear here after a scenario is run.</p> : <>
            <div className="risk-trend">
              <div className="subsection-heading"><strong>Recent score trend</strong><small>Historical prototype rule scores · not predictive accuracy</small></div>
              <svg viewBox="0 0 100 42" role="img" aria-label="Risk score trend across recent recorded assessments">
                <line x1="0" y1="8" x2="100" y2="8" />
                <line x1="0" y1="25" x2="100" y2="25" />
                <line x1="0" y1="41" x2="100" y2="41" />
                <polyline points={trendAssessments.map((item, index, items) => {
                  const x = items.length === 1 ? 50 : (index / (items.length - 1)) * 100
                  return `${x},${38 - (Math.max(0, Math.min(100, item.risk_score)) * 0.34)}`
                }).join(' ')} />
                {trendAssessments.map((item, index, items) => {
                  const x = items.length === 1 ? 50 : (index / (items.length - 1)) * 100
                  return <circle key={item.assessment_id} cx={x} cy={38 - (Math.max(0, Math.min(100, item.risk_score)) * 0.34)} r="1.7" />
                })}
              </svg>
              <div className="trend-labels"><span>0</span><span>50</span><span>100</span></div>
            </div>
            <ol className="risk-history-list">{riskHistory.slice(0, 8).map((assessment) => <li key={assessment.assessment_id}><time>{formatTimestamp(assessment.timestamp)}</time><strong className={`risk-pill risk-${assessment.risk_level}`}>{assessment.risk_level.toUpperCase()}</strong><span>{assessment.risk_score}/100</span><small>{assessment.source?.toUpperCase() ?? 'UNKNOWN'}{assessment.is_demo ? ' · SIMULATED' : ''}</small></li>)}</ol>
          </>}
        </section>

        <section className="panel contacts-panel" id="contacts">
          <div className="panel-title"><div><p className="eyebrow">USER-CONTROLLED SETTINGS</p><h2>Trusted contacts</h2></div><span className="tag">IN-MEMORY ONLY</span></div>
          <p className="muted description">Contact entries are held in backend memory for this session only. No message provider is configured.</p>
          {contactsError && <p className="error" role="alert">Trusted contacts unavailable: {contactsError}</p>}
          {contacts.length === 0 ? <p className="empty-inline">No trusted contacts configured.</p> : <ul className="contact-list">{contacts.map((contact) => <li key={contact.id}><span><strong>{contact.name}</strong><small>{contact.channel.toUpperCase()} · {contact.destination_hint}</small></span><div className="contact-actions"><button type="button" disabled={!!busyAction} onClick={() => editContact(contact)}>Edit</button><button type="button" disabled={!!busyAction} onClick={() => testNotification(contact.id)}>Test mock notification</button></div></li>)}</ul>}
          <form className="contact-form" onSubmit={saveContacts}>
            <label>Name<input required maxLength={80} value={contactForm.name} onChange={(event) => setContactForm({ ...contactForm, name: event.target.value })} /></label>
            <label>Channel<select value={contactForm.channel} onChange={(event) => setContactForm({ ...contactForm, channel: event.target.value })}><option value="email">Email</option><option value="sms">SMS</option></select></label>
            <label>{editingContactId ? 'Re-enter address or number' : 'Address or number'}<input required maxLength={160} autoComplete="off" value={contactForm.destination} onChange={(event) => setContactForm({ ...contactForm, destination: event.target.value })} placeholder={editingContactId ? 'Address is masked; enter the updated destination' : ''} /></label>
            <button className="primary" disabled={!online || !!busyAction}>{editingContactId ? 'Update contact' : 'Save contact'}</button>
            {editingContactId && <button className="cancel-edit" type="button" onClick={() => { setEditingContactId(''); setContactForm({ name: '', channel: 'email', destination: '' }) }}>Cancel edit</button>}
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
