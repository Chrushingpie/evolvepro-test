import { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';

const STATUS_COLOR = { idle: '#4ade80', busy: '#facc15', error: '#f87171' };
const CORE_AGENTS  = ['coordinator', 'dev', 'admin'];

const LOG_STYLE = {
  thinking:    { icon: '···', color: '#4a4a68', italic: true  },
  tool_call:   { icon: '▶',   color: '#667eea', italic: false },
  tool_result: { icon: '✓',   color: '#4ade80', italic: false },
  response:    { icon: '◀',   color: '#c0c0d8', italic: false },
  start:       { icon: '○',   color: '#3a3a52', italic: true  },
};

const fmtTime = (iso) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export default function AgentsView() {
  const [agents, setAgents]       = useState([]);
  const [memories, setMemories]   = useState({});
  const [expanded, setExpanded]   = useState({});
  const [thoughts, setThoughts]   = useState({});
  const [thoughtsOpen, setThoughtsOpen] = useState({});
  const thoughtIntervals          = useRef({});
  const [showForm, setShowForm]   = useState(false);
  const [saving, setSaving]       = useState(false);
  const [form, setForm]           = useState({ name: '', role: '', model: 'gpt-oss:20b', system_prompt: '' });

  const load = useCallback(async () => {
    const res = await axios.get('/api/agents');
    setAgents(res.data);
  }, []);

  const loadMemory = useCallback(async (name) => {
    const res = await axios.get(`/api/agents/${name}/memory`);
    setMemories(m => ({ ...m, [name]: res.data }));
  }, []);

  const loadThoughts = useCallback(async (name) => {
    const res = await axios.get(`/api/agents/${name}/logs`);
    setThoughts(t => ({ ...t, [name]: res.data }));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  // Escape to close modal
  useEffect(() => {
    if (!showForm) return;
    const handler = (e) => { if (e.key === 'Escape') setShowForm(false); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [showForm]);

  // Clean up thought intervals on unmount
  useEffect(() => {
    return () => Object.values(thoughtIntervals.current).forEach(clearInterval);
  }, []);

  const toggleMemory = async (name) => {
    const next = !expanded[name];
    setExpanded(e => ({ ...e, [name]: next }));
    if (next) await loadMemory(name);
  };

  const toggleThoughts = async (name) => {
    const next = !thoughtsOpen[name];
    setThoughtsOpen(t => ({ ...t, [name]: next }));
    if (next) {
      await loadThoughts(name);
      thoughtIntervals.current[name] = setInterval(() => loadThoughts(name), 2000);
    } else {
      clearInterval(thoughtIntervals.current[name]);
      delete thoughtIntervals.current[name];
    }
  };

  const clearThoughts = async (name) => {
    await axios.delete(`/api/agents/${name}/logs`);
    setThoughts(t => ({ ...t, [name]: [] }));
  };

  const forgetEntry = async (agentName, key) => {
    await axios.delete(`/api/agents/${agentName}/memory/${encodeURIComponent(key)}`);
    await loadMemory(agentName);
  };

  const create = async () => {
    if (!form.name.trim() || !form.role.trim()) return;
    setSaving(true);
    try {
      await axios.post('/api/agents', form);
      setForm({ name: '', role: '', model: 'gpt-oss:20b', system_prompt: '' });
      setShowForm(false);
      await load();
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id) => {
    await axios.delete(`/api/agents/${id}`);
    await load();
  };

  const runTasks = async (name) => {
    setAgents(a => a.map(ag => ag.name === name ? { ...ag, status: 'busy' } : ag));
    try {
      await axios.post(`/api/agents/${name}/work`, {}, { timeout: 300000 });
    } finally {
      await load();
    }
  };

  return (
    <div className="view">
      <div className="view-header">
        <div>
          <h2>Agents</h2>
          <p className="view-sub">Live status · memory persists across sessions</p>
        </div>
        <button className="btn-primary" onClick={() => setShowForm(true)}>+ New Agent</button>
      </div>

      <div className="agent-grid">
        {agents.map(ag => (
          <div key={ag.id} className="agent-card">
            <div className="agent-card-top">
              <div className="agent-avatar">{ag.name[0].toUpperCase()}</div>
              <div className="agent-info">
                <div className="agent-name">{ag.name}</div>
                <div className="agent-role">{ag.role}</div>
              </div>
              <div className="agent-status" style={{ background: STATUS_COLOR[ag.status] ?? '#5a5a78' }}>
                {ag.status}
              </div>
            </div>

            <div className="agent-model-badge">{ag.model}</div>

            {ag.system_prompt && (
              <p className="agent-prompt">
                {ag.system_prompt.slice(0, 120)}{ag.system_prompt.length > 120 ? '…' : ''}
              </p>
            )}

            {/* Thoughts panel */}
            <div className="memory-section">
              <button className="memory-toggle" onClick={() => toggleThoughts(ag.name)}>
                <span>
                  ◎ Thoughts
                  {ag.status === 'busy' && thoughtsOpen[ag.name] && (
                    <span className="thoughts-live-dot" />
                  )}
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {thoughtsOpen[ag.name] && (
                    <span
                      className="thoughts-clear"
                      onClick={e => { e.stopPropagation(); clearThoughts(ag.name); }}
                      title="Clear log"
                    >
                      clear
                    </span>
                  )}
                  <span className="memory-chevron">{thoughtsOpen[ag.name] ? '▲' : '▼'}</span>
                </span>
              </button>
              {thoughtsOpen[ag.name] && (
                <div className="thoughts-list">
                  {!thoughts[ag.name] || thoughts[ag.name].length === 0 ? (
                    <p className="memory-empty">No activity yet — chat with this agent or run its tasks</p>
                  ) : (
                    [...thoughts[ag.name]].reverse().map(entry => {
                      const style = LOG_STYLE[entry.type] ?? LOG_STYLE.thinking;
                      return (
                        <div key={entry.id} className="thought-entry">
                          <span className="thought-time">{fmtTime(entry.created_at)}</span>
                          <span className="thought-icon" style={{ color: style.color }}>{style.icon}</span>
                          <span
                            className="thought-msg"
                            style={{ color: style.color, fontStyle: style.italic ? 'italic' : 'normal' }}
                          >
                            {entry.message}
                          </span>
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>

            {/* Memory panel */}
            <div className="memory-section">
              <button className="memory-toggle" onClick={() => toggleMemory(ag.name)}>
                <span>◈ Memory</span>
                <span className="memory-chevron">{expanded[ag.name] ? '▲' : '▼'}</span>
              </button>
              {expanded[ag.name] && (
                <div className="memory-list">
                  {!memories[ag.name] || memories[ag.name].length === 0 ? (
                    <p className="memory-empty">No memories yet — agent will build these over time</p>
                  ) : (
                    memories[ag.name].map(m => (
                      <div key={m.id} className="memory-entry">
                        <div className="memory-key">{m.key}</div>
                        <div className="memory-value">{m.value}</div>
                        <div className="memory-meta">
                          {new Date(m.updated_at).toLocaleString()}
                          <button className="memory-forget" onClick={() => forgetEntry(ag.name, m.key)} title="Forget">✕</button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>

            <div className="agent-card-footer">
              <span className="muted">Since {new Date(ag.created_at).toLocaleDateString()}</span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  className="btn-run-tasks"
                  onClick={() => runTasks(ag.name)}
                  disabled={ag.status === 'busy'}
                  title="Process pending tasks now"
                >
                  {ag.status === 'busy' ? '⏳' : '▶ Run Tasks'}
                </button>
                {!CORE_AGENTS.includes(ag.name) && (
                  <button className="btn-danger-sm" onClick={() => remove(ag.id)}>Remove</button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {showForm && (
        <div className="modal-backdrop" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>New Agent</h3>
            <label className="field-label">Name</label>
            <input className="input" value={form.name} placeholder="e.g. researcher"
              onChange={e => setForm({ ...form, name: e.target.value })} />
            <label className="field-label">Role</label>
            <input className="input" value={form.role} placeholder="e.g. Research Specialist"
              onChange={e => setForm({ ...form, role: e.target.value })} />
            <label className="field-label">Model</label>
            <select className="input" value={form.model}
              onChange={e => setForm({ ...form, model: e.target.value })}>
              <option value="gpt-oss:20b">gpt-oss:20b — fast</option>
              <option value="gpt-oss:120b">gpt-oss:120b — smart</option>
            </select>
            <label className="field-label">System Prompt</label>
            <textarea className="textarea" rows={5} value={form.system_prompt}
              placeholder="Describe this agent's role and behaviour…"
              onChange={e => setForm({ ...form, system_prompt: e.target.value })} />
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn-primary" onClick={create} disabled={saving}>
                {saving ? 'Creating…' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
