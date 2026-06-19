import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const INTERVALS = [
  { label: '5 minutes',  value: '5'  },
  { label: '15 minutes', value: '15' },
  { label: '30 minutes', value: '30' },
  { label: '1 hour',     value: '60' },
  { label: '2 hours',    value: '120'},
];

const smartTime = (iso) => {
  if (!iso) return 'Never';
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : d.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
};

export default function SettingsView() {
  const [settings, setSettings]       = useState({});
  const [events, setEvents]           = useState([]);
  const [running, setRunning]         = useState(false);
  const [copied, setCopied]           = useState(false);
  const [saving, setSaving]           = useState(null);
  const [registerUrl, setRegisterUrl] = useState('');
  const [registerSecret, setRegisterSecret] = useState('');
  const [registering, setRegistering] = useState(false);
  const [regResults, setRegResults]   = useState(null);

  const webhookUrl = `${window.location.origin}/api/webhook/github`;

  // Pre-fill register URL with current origin on mount
  useEffect(() => { setRegisterUrl(`${window.location.origin}/api/webhook/github`); }, []);

  const load = useCallback(async () => {
    const [sRes, hRes] = await Promise.all([
      axios.get('/api/settings'),
      axios.get('/api/hub').then(r => axios.get(`/api/threads/${r.data.id}/messages`)),
    ]);
    setSettings(sRes.data);
    setEvents(hRes.data.filter(m => m.sender === 'github' || m.role === 'system').slice(-20).reverse());
  }, []);

  useEffect(() => { load(); }, []);

  // Poll to refresh last-run time and events
  useEffect(() => {
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const patch = async (key, value) => {
    setSaving(key);
    await axios.patch(`/api/settings/${key}`, { value });
    setSettings(prev => ({ ...prev, [key]: { ...prev[key], value } }));
    setSaving(null);
  };

  const runNow = async () => {
    setRunning(true);
    try { await axios.post('/api/autonomous/run'); } catch {}
    setTimeout(() => setRunning(false), 3000);
  };

  const copyUrl = () => {
    navigator.clipboard.writeText(webhookUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const registerAll = async () => {
    if (!registerUrl.trim()) return;
    setRegistering(true);
    setRegResults(null);
    try {
      const res = await axios.post('/api/webhook/github/register', {
        url: registerUrl.trim(),
        secret: registerSecret.trim(),
      });
      setRegResults(res.data.results);
    } catch (err) {
      setRegResults([{ repo: 'Error', status: 'error', reason: err.response?.data?.detail ?? err.message }]);
    }
    setRegistering(false);
  };

  const [newRepo, setNewRepo] = useState('');

  const blockedRepos = (settings.blocked_repos?.value ?? '')
    .split(',').map(r => r.trim()).filter(Boolean);

  const addBlockedRepo = async () => {
    const repo = newRepo.trim();
    if (!repo) return;
    const updated = [...new Set([...blockedRepos, repo])].join(',');
    await patch('blocked_repos', updated);
    setNewRepo('');
  };

  const removeBlockedRepo = async (repo) => {
    const updated = blockedRepos.filter(r => r !== repo).join(',');
    await patch('blocked_repos', updated);
  };

  const enabled   = settings.autonomous_enabled?.value === 'true';
  const interval  = settings.autonomous_interval_minutes?.value ?? '30';
  const lastRun   = settings.autonomous_last_run?.value ?? '';

  return (
    <div className="view">
      <div className="view-header">
        <div>
          <h2>Settings</h2>
          <div className="view-sub">Autonomous scheduling &amp; webhook intake</div>
        </div>
      </div>

      <div className="settings-grid">

        {/* ── Autonomous ── */}
        <div className="settings-card">
          <div className="settings-card-title">
            <span className="settings-card-icon">⏰</span>
            Autonomous Agent
          </div>
          <p className="settings-desc">
            Coordinator wakes up on a schedule, checks for pending tasks, and dispatches agents automatically — no prompting needed.
          </p>

          <div className="settings-row">
            <span className="settings-label">Enabled</span>
            <button
              className={`toggle-btn ${enabled ? 'toggle-on' : ''}`}
              onClick={() => patch('autonomous_enabled', enabled ? 'false' : 'true')}
              disabled={saving === 'autonomous_enabled'}
            >
              {enabled ? 'ON' : 'OFF'}
            </button>
          </div>

          <div className="settings-row">
            <span className="settings-label">Run every</span>
            <select
              className="input settings-select"
              value={interval}
              onChange={e => patch('autonomous_interval_minutes', e.target.value)}
              disabled={!enabled}
            >
              {INTERVALS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          <div className="settings-row">
            <span className="settings-label">Last run</span>
            <span className="settings-value">{smartTime(lastRun)}</span>
          </div>

          <button
            className="btn-primary settings-run-btn"
            onClick={runNow}
            disabled={running}
          >
            {running ? '⏳ Running…' : '▶ Run Now'}
          </button>
        </div>

        {/* ── Webhooks ── */}
        <div className="settings-card">
          <div className="settings-card-title">
            <span className="settings-card-icon">⚡</span>
            GitHub Webhooks
          </div>
          <p className="settings-desc">
            Optional — gives instant reaction (seconds). Without it, the autonomous loop scans GitHub on every scheduled run instead. Register once and dev reacts to issues and PRs immediately.
          </p>

          <div className="settings-label" style={{ marginBottom: 6 }}>Webhook URL</div>
          <div className="webhook-url-row" style={{ marginBottom: 8 }}>
            <input
              className="chat-input"
              value={registerUrl}
              onChange={e => setRegisterUrl(e.target.value)}
              placeholder="https://your-ngrok-url.ngrok.io/api/webhook/github"
              style={{ fontSize: '.78rem', fontFamily: 'monospace' }}
            />
            <button className="btn-ghost" onClick={copyUrl} style={{ flexShrink: 0 }}>
              {copied ? '✓' : 'Copy'}
            </button>
          </div>

          <div className="settings-label" style={{ marginBottom: 6 }}>Secret (optional)</div>
          <input
            className="input"
            value={registerSecret}
            onChange={e => setRegisterSecret(e.target.value)}
            placeholder="Leave blank to skip signature verification"
            type="password"
            style={{ marginBottom: 4 }}
          />

          <div className="settings-hint">
            For a public URL locally: run <code>ngrok http 3000</code> and paste the <code>https://xxxx.ngrok.io</code> URL above.
            If you add a secret, also add <code>WEBHOOK_SECRET=same-value</code> to your <code>py_service/.env</code>.
          </div>

          <button
            className="btn-primary settings-run-btn"
            onClick={registerAll}
            disabled={registering || !registerUrl.trim()}
          >
            {registering ? '⏳ Registering…' : '⚡ Register on all repos'}
          </button>

          {regResults && (
            <div className="reg-results">
              {regResults.map((r, i) => (
                <div key={i} className={`reg-row reg-${r.status}`}>
                  <span className="reg-icon">
                    {r.status === 'registered' ? '✓' : r.status === 'already_registered' ? '=' : r.status === 'skipped' ? '—' : '✕'}
                  </span>
                  <span className="reg-repo">{r.repo}</span>
                  <span className="reg-status">{r.status === 'already_registered' ? 'already set' : r.reason ?? r.status}</span>
                </div>
              ))}
            </div>
          )}

          <div className="settings-label" style={{ marginTop: 8 }}>Recent events</div>
          <div className="event-list">
            {events.length === 0 && <div className="settings-empty">No events yet</div>}
            {events.map((e, i) => (
              <div key={i} className={`event-row ${e.sender === 'github' ? 'event-github' : 'event-system'}`}>
                <span className="event-icon">{e.sender === 'github' ? '⚡' : '⏰'}</span>
                <span className="event-text">{e.content.replace('[GitHub] ', '')}</span>
                <span className="event-time">{smartTime(e.created_at)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* ── Blocked Repos ── */}
        <div className="settings-card">
          <div className="settings-card-title">
            <span className="settings-card-icon">🚫</span>
            Blocked Repos
          </div>
          <p className="settings-desc">
            Agents will refuse to read, write, or interact with any repo on this list.
            Use the format <code>owner/repo</code>.
          </p>

          <div className="webhook-url-row" style={{ marginBottom: 8 }}>
            <input
              className="chat-input"
              value={newRepo}
              onChange={e => setNewRepo(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addBlockedRepo()}
              placeholder="owner/repo-name"
              style={{ fontFamily: 'monospace', fontSize: '.82rem' }}
            />
            <button className="btn-primary" onClick={addBlockedRepo} style={{ flexShrink: 0 }}>
              Block
            </button>
          </div>

          <div className="event-list">
            {blockedRepos.length === 0 && (
              <div className="settings-empty">No repos blocked</div>
            )}
            {blockedRepos.map(repo => (
              <div key={repo} className="event-row" style={{ justifyContent: 'space-between' }}>
                <span style={{ fontFamily: 'monospace', fontSize: '.82rem' }}>{repo}</span>
                <button
                  className="memory-forget"
                  onClick={() => removeBlockedRepo(repo)}
                  title="Unblock"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
