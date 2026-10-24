
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

export default function App() {
  const [online, setOnline] = useState(false)
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [sos, setSos] = useState(false)
  const [journey, setJourney] = useState(null)
  const [thresholds, setThresholds] = useState(null)
  const [events, setEvents] = useState([])
  const [contacts, setContacts] = useState([])
  const [contactForm, setContactForm] = useState({ name: '', channel: 'email', destination: '' })
  const [assistantQuestion, setAssistantQuestion] = useState('Summarize recent events')
  const [assistantAnswer, setAssistantAnswer] = useState(null)
  const [notificationMessage, setNotificationMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [busyAction, setBusyAction] = useState('')
  const fileRef = useRef(null)

  async function refreshWorkspace() {
    try {
      const [state, eventData, contactData] = await Promise.all([
        api('/api/monitoring/state'),
        api('/api/events?limit=30'),
        api('/api/contacts'),
      ])
      setJourney(state)
      setThresholds((current) => current ?? state.configuration)
      setEvents(eventData.events)
      setContacts(contactData.contacts)
      setOnline(true)
    } catch {
      setOnline(false)
    }
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
    return () => clearInterval(timer)
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
          <p>Local video analysis · simulated journey signals · user-controlled alerts</p>
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
            <p>Safety assessment</p>
            <h3 className="amber-text">Unknown</h3>
            <small>No validated risk classifier</small>
          </article>
        </section>

        {actionError && <p className="error" role="alert">{actionError}</p>}
        {notificationMessage && <p className="notice" role="status" aria-live="polite">{notificationMessage}</p>}

        <section className="columns">
          <article className="panel map-panel" id="journey">
            <div className="panel-title">
              <div><p className="eyebrow">JOURNEY MONITOR</p><h2>Route overview</h2></div>
              <span className="tag">SIMULATED ROUTE</span>
            </div>
            <p className="muted description">{journey?.location_label ?? 'Loading simulated route state...'}</p>
            <div className="map-board" role="img" aria-label="Illustrative simulated route with no real location data">
              <div className="map-grid" />
              <svg className="route-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                <polyline points={(journey?.map_route ?? []).map((point) => `${point.x},${point.y}`).join(' ')} />
              </svg>
              {(journey?.map_route ?? []).map((point, index) => (
                <span key={`${point.x}-${point.y}`} className={`route-point ${index === journey.current_waypoint ? 'current' : ''}`} style={{ left: `${point.x}%`, top: `${point.y}%` }} aria-hidden="true" />
              ))}
              <span className="map-caption">ILLUSTRATIVE PATH · NOT GPS</span>
            </div>
            <div className="map-state-row"><span>Provider</span><strong>Simulated coordinates</strong><span>Safety status</span><strong>Unknown</strong></div>
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
            {events.length === 0 ? <div className="empty-state">No events yet. Cabin observations and simulated journey events will appear here.</div> : (
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

        <section className="panel contacts-panel" id="contacts">
          <div className="panel-title"><div><p className="eyebrow">USER-CONTROLLED SETTINGS</p><h2>Trusted contacts</h2></div><span className="tag">IN-MEMORY ONLY</span></div>
          <p className="muted description">Contact entries are held in backend memory for this session only. No message provider is configured.</p>
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
