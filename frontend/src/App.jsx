import { useEffect, useState } from 'react'

const CATEGORIES = ['CONSULTATION', 'DIAGNOSTIC', 'PHARMACY', 'DENTAL', 'VISION', 'ALTERNATIVE_MEDICINE']

const STATUS_COLORS = {
  PASSED: '#059669',
  FAILED: '#dc2626',
  SKIPPED: '#d97706',
  DEGRADED: '#ea580c',
  WARNING: '#ca8a04',
}

function TraceViewer({ trace }) {
  if (!trace?.length) return null
  return (
    <div className="trace-viewer">
      <h3>Decision Trace</h3>
      <div className="trace-steps">
        {trace.map((step, i) => (
          <div key={i} className="trace-step">
            <div className="trace-header">
              <span className="trace-component">{step.component}</span>
              <span className="trace-status" style={{ color: STATUS_COLORS[step.status] || '#666' }}>
                {step.status}
              </span>
            </div>
            <div className="trace-action">{step.action}</div>
            <div className="trace-message">{step.message}</div>
            {Object.keys(step.details || {}).length > 0 && (
              <pre className="trace-details">{JSON.stringify(step.details, null, 2)}</pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function DecisionCard({ result }) {
  if (!result) return null
  const decision = result.blocked ? 'BLOCKED' : (result.decision || 'PENDING')
  const decisionClass = result.blocked ? 'blocked' : (result.decision || '').toLowerCase()

  return (
    <div className={`decision-card ${decisionClass}`}>
      <div className="decision-header">
        <span className="decision-label">Decision</span>
        <span className={`decision-value ${decisionClass}`}>{decision}</span>
      </div>

      {result.blocked && (
        <div className="alert alert-warning">
          <strong>{result.block_reason}</strong>
          <p>{result.member_message}</p>
        </div>
      )}

      {!result.blocked && (
        <>
          <div className="metrics">
            <div className="metric">
              <span className="metric-label">Approved Amount</span>
              <span className="metric-value">₹{result.approved_amount?.toLocaleString('en-IN')}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Confidence</span>
              <span className="metric-value">{(result.confidence_score * 100).toFixed(0)}%</span>
            </div>
          </div>

          {result.reason && <p className="reason">{result.reason}</p>}

          {result.rejection_reasons?.length > 0 && (
            <div className="rejection-reasons">
              <strong>Rejection Reasons:</strong> {result.rejection_reasons.join(', ')}
            </div>
          )}

          {result.financial_breakdown && Object.keys(result.financial_breakdown).length > 0 && (
            <div className="financial-breakdown">
              <h4>Financial Breakdown</h4>
              <ul>
                {result.financial_breakdown.network_hospital && (
                  <li>Network hospital: {result.financial_breakdown.hospital_name}</li>
                )}
                {result.financial_breakdown.network_discount_amount > 0 && (
                  <li>Network discount ({result.financial_breakdown.network_discount_percent}%): -₹{result.financial_breakdown.network_discount_amount}</li>
                )}
                {result.financial_breakdown.copay_amount > 0 && (
                  <li>Co-pay ({result.financial_breakdown.copay_percent}%): -₹{result.financial_breakdown.copay_amount}</li>
                )}
                <li><strong>Final approved: ₹{result.financial_breakdown.approved_amount}</strong></li>
              </ul>
            </div>
          )}

          {result.line_item_decisions?.length > 0 && (
            <div className="line-items">
              <h4>Line Item Decisions</h4>
              <table>
                <thead>
                  <tr><th>Item</th><th>Amount</th><th>Status</th><th>Reason</th></tr>
                </thead>
                <tbody>
                  {result.line_item_decisions.map((li, i) => (
                    <tr key={i} className={li.approved ? 'approved' : 'rejected'}>
                      <td>{li.description}</td>
                      <td>₹{li.amount?.toLocaleString('en-IN')}</td>
                      <td>{li.approved ? 'Approved' : 'Rejected'}</td>
                      <td>{li.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.component_failures?.length > 0 && (
            <div className="alert alert-degraded">
              <strong>Component Failures</strong>
              <ul>{result.component_failures.map((f, i) => <li key={i}>{f}</li>)}</ul>
            </div>
          )}

          {result.manual_review_recommended && (
            <div className="alert alert-info">Manual review recommended due to incomplete processing.</div>
          )}
        </>
      )}

      <TraceViewer trace={result.trace} />
    </div>
  )
}

export default function App() {
  const [members, setMembers] = useState([])
  const [tab, setTab] = useState('submit')
  const [claims, setClaims] = useState([])
  const [selectedClaim, setSelectedClaim] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [docRequirements, setDocRequirements] = useState(null)

  const [form, setForm] = useState({
    member_id: 'EMP001',
    claim_category: 'CONSULTATION',
    treatment_date: '2024-11-01',
    claimed_amount: 1500,
    hospital_name: '',
    files: [],
  })

  useEffect(() => {
    fetch('/api/members').then(r => r.json()).then(setMembers).catch(console.error)
  }, [])

  useEffect(() => {
    fetch(`/api/document-requirements/${form.claim_category}`)
      .then(r => r.json())
      .then(setDocRequirements)
      .catch(console.error)
  }, [form.claim_category])

  const loadClaims = () => {
    fetch('/api/claims').then(r => r.json()).then(setClaims).catch(console.error)
  }

  useEffect(() => {
    if (tab === 'review') loadClaims()
  }, [tab])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setResult(null)

    const fd = new FormData()
    fd.append('member_id', form.member_id)
    fd.append('claim_category', form.claim_category)
    fd.append('treatment_date', form.treatment_date)
    fd.append('claimed_amount', form.claimed_amount)
    fd.append('hospital_name', form.hospital_name)
    form.files.forEach(f => fd.append('files', f))

    try {
      const res = await fetch('/api/claims/submit-with-files', { method: 'POST', body: fd })
      const data = await res.json()
      setResult(data)
    } catch (err) {
      setResult({ error: err.message })
    } finally {
      setLoading(false)
    }
  }

  const viewClaim = async (claimId) => {
    const res = await fetch(`/api/claims/${claimId}`)
    const data = await res.json()
    setSelectedClaim(data)
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <span className="logo-icon">🍑</span>
            <div>
              <h1>Plum Claims</h1>
              <p>AI-Powered Health Insurance Claims Processing</p>
            </div>
          </div>
          <nav>
            <button className={tab === 'submit' ? 'active' : ''} onClick={() => setTab('submit')}>Submit Claim</button>
            <button className={tab === 'review' ? 'active' : ''} onClick={() => setTab('review')}>Ops Review</button>
          </nav>
        </div>
      </header>

      <main className="main">
        {tab === 'submit' && (
          <div className="grid">
            <section className="card form-card">
              <h2>Submit a Claim</h2>
              <form onSubmit={handleSubmit}>
                <label>
                  Member
                  <select value={form.member_id} onChange={e => setForm({ ...form, member_id: e.target.value })}>
                    {members.map(m => (
                      <option key={m.member_id} value={m.member_id}>{m.name} ({m.member_id})</option>
                    ))}
                  </select>
                </label>

                <label>
                  Claim Category
                  <select value={form.claim_category} onChange={e => setForm({ ...form, claim_category: e.target.value })}>
                    {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>

                {docRequirements && (
                  <div className="doc-req">
                    <strong>Required documents:</strong> {docRequirements.required?.join(', ')}
                    {docRequirements.optional?.length > 0 && (
                      <span> · Optional: {docRequirements.optional.join(', ')}</span>
                    )}
                  </div>
                )}

                <div className="row">
                  <label>
                    Treatment Date
                    <input type="date" value={form.treatment_date} onChange={e => setForm({ ...form, treatment_date: e.target.value })} required />
                  </label>
                  <label>
                    Claimed Amount (₹)
                    <input type="number" value={form.claimed_amount} onChange={e => setForm({ ...form, claimed_amount: +e.target.value })} required min={500} />
                  </label>
                </div>

                <label>
                  Hospital Name (optional)
                  <input type="text" value={form.hospital_name} onChange={e => setForm({ ...form, hospital_name: e.target.value })} placeholder="e.g. Apollo Hospitals" />
                </label>

                <label>
                  Upload Documents
                  <input type="file" multiple accept="image/*,.pdf" onChange={e => setForm({ ...form, files: Array.from(e.target.files) })} required />
                </label>

                <button type="submit" className="btn-primary" disabled={loading}>
                  {loading ? 'Processing...' : 'Submit Claim'}
                </button>
              </form>
            </section>

            <section className="card result-card">
              <h2>Result</h2>
              {loading && <div className="loading">Processing claim through multi-agent pipeline...</div>}
              {result?.error && <div className="alert alert-warning">{result.error}</div>}
              {result?.result && <DecisionCard result={result.result} />}
              {!loading && !result && (
                <p className="placeholder">Submit a claim to see the decision and full trace.</p>
              )}
            </section>
          </div>
        )}

        {tab === 'review' && (
          <div className="grid">
            <section className="card">
              <h2>Claims History</h2>
              <button className="btn-secondary" onClick={loadClaims}>Refresh</button>
              {claims.length === 0 ? (
                <p className="placeholder">No claims processed yet.</p>
              ) : (
                <ul className="claims-list">
                  {claims.map(c => (
                    <li key={c.claim_id} onClick={() => viewClaim(c.claim_id)} className={selectedClaim?.claim_id === c.claim_id ? 'selected' : ''}>
                      <span className="claim-id">{c.claim_id}</span>
                      <span className={`badge ${c.blocked ? 'blocked' : (c.decision || '').toLowerCase()}`}>
                        {c.blocked ? 'BLOCKED' : c.decision}
                      </span>
                      {!c.blocked && <span>₹{c.approved_amount?.toLocaleString('en-IN')}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card result-card">
              <h2>Claim Detail</h2>
              {selectedClaim ? <DecisionCard result={selectedClaim} /> : (
                <p className="placeholder">Select a claim to view the full decision trace.</p>
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  )
}
