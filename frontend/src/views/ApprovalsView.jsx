import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const STATUS_COLOR = {
  pending:  '#facc15',
  approved: '#4ade80',
  rejected: '#f87171',
  timeout:  '#6b7280',
};

const fmtTime = (iso) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export default function ApprovalsView() {
  const [approvals, setApprovals] = useState([]);
  const [reasons, setReasons]     = useState({});
  const [acting, setActing]       = useState({});

  const load = useCallback(async () => {
    const res = await axios.get('/api/approvals');
    setApprovals(res.data);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  const approve = async (id) => {
    setActing(a => ({ ...a, [id]: true }));
    await axios.post(`/api/approvals/${id}/approve`);
    await load();
    setActing(a => ({ ...a, [id]: false }));
  };

  const reject = async (id) => {
    setActing(a => ({ ...a, [id]: true }));
    await axios.post(`/api/approvals/${id}/reject`, { reason: reasons[id] || '' });
    await load();
    setActing(a => ({ ...a, [id]: false }));
  };

  const pending = approvals.filter(a => a.status === 'pending');
  const history = approvals.filter(a => a.status !== 'pending');

  return (
    <div className="view">
      <div className="view-header">
        <div>
          <h2>Approvals</h2>
          <p className="view-sub">Agents pause here before sensitive or irreversible actions</p>
        </div>
        {pending.length > 0 && (
          <div className="approval-pending-badge">{pending.length} pending</div>
        )}
      </div>

      {pending.length === 0 && history.length === 0 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: '#3a3a52', flexDirection: 'column', gap: 12 }}>
          <span style={{ fontSize: '2rem', opacity: .3 }}>✓</span>
          <span style={{ fontSize: '.9rem' }}>No approvals yet — agents will pause here before sensitive actions</span>
        </div>
      )}

      {pending.map(a => (
        <div key={a.id} className="approval-card approval-card-pending">
          <div className="approval-header">
            <span className="approval-agent-pill">{a.agent_name}</span>
            <span className="approval-action-text">{a.action}</span>
            <span className="approval-time">{fmtTime(a.created_at)}</span>
          </div>

          {a.details && (
            <pre className="approval-details">{a.details}</pre>
          )}

          <div className="approval-actions">
            <input
              className="input"
              placeholder="Rejection reason (optional)"
              value={reasons[a.id] || ''}
              onChange={e => setReasons(r => ({ ...r, [a.id]: e.target.value }))}
              onKeyDown={e => e.key === 'Enter' && reject(a.id)}
              style={{ flex: 1, fontSize: '.82rem' }}
              disabled={acting[a.id]}
            />
            <button
              className="btn-danger-sm"
              onClick={() => reject(a.id)}
              disabled={acting[a.id]}
            >
              ✕ Reject
            </button>
            <button
              className="btn-primary"
              onClick={() => approve(a.id)}
              disabled={acting[a.id]}
            >
              ✓ Approve
            </button>
          </div>
        </div>
      ))}

      {history.length > 0 && (
        <>
          <div className="settings-label" style={{ marginTop: 28, marginBottom: 10 }}>History</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {history.slice(0, 30).map(a => (
              <div key={a.id} className="approval-card approval-card-history">
                <div className="approval-header">
                  <span className="approval-agent-pill" style={{ opacity: .6 }}>{a.agent_name}</span>
                  <span className="approval-action-text" style={{ color: '#7878a0' }}>{a.action}</span>
                  <span
                    className="approval-status-badge"
                    style={{ background: STATUS_COLOR[a.status] }}
                  >
                    {a.status}
                  </span>
                  <span className="approval-time">{fmtTime(a.created_at)}</span>
                </div>
                {a.reason && (
                  <div className="approval-reason">Reason: {a.reason}</div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
