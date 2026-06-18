import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const COLUMNS = [
  { id: 'pending',     label: 'Pending',     color: '#7878a0' },
  { id: 'in_progress', label: 'In Progress', color: '#667eea' },
  { id: 'done',        label: 'Done',        color: '#4ade80' },
  { id: 'failed',      label: 'Failed',      color: '#f87171' },
];

export default function TasksView() {
  const [tasks, setTasks]       = useState([]);
  const [agents, setAgents]     = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm]         = useState({ title: '', description: '', assigned_to: 'coordinator' });

  const load = useCallback(async () => {
    const [t, a] = await Promise.all([axios.get('/api/tasks'), axios.get('/api/agents')]);
    setTasks(t.data);
    setAgents(a.data);
  }, []);

  useEffect(() => { load(); }, []);

  // Auto-refresh every 5s so agent-created tasks appear live
  useEffect(() => {
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

  const create = async () => {
    if (!form.title.trim()) return;
    await axios.post('/api/tasks', { ...form, created_by: 'user' });
    setForm({ title: '', description: '', assigned_to: form.assigned_to });
    setShowForm(false);
    await load();
  };

  const moveTo = async (id, status) => {
    await axios.patch(`/api/tasks/${id}`, { status });
    await load();
  };

  const remove = async (id) => {
    await axios.delete(`/api/tasks/${id}`);
    await load();
  };

  const byStatus = (s) => tasks.filter(t => t.status === s);

  return (
    <div className="view">
      <div className="view-header">
        <div>
          <h2>Tasks</h2>
          <p className="view-sub">{tasks.length} total · {byStatus('in_progress').length} in progress</p>
        </div>
        <button className="btn-primary" onClick={() => setShowForm(true)}>+ New Task</button>
      </div>

      <div className="kanban">
        {COLUMNS.map(col => (
          <div key={col.id} className="kanban-col">
            <div className="kanban-col-header" style={{ borderTopColor: col.color }}>
              <span style={{ color: col.color }}>{col.label}</span>
              <span className="kanban-count">{byStatus(col.id).length}</span>
            </div>
            <div className="kanban-cards">
              {byStatus(col.id).map(task => (
                <div key={task.id} className="task-card">
                  <div className="task-title">{task.title}</div>
                  {task.description && <p className="task-desc">{task.description}</p>}
                  <div className="task-tags">
                    {task.assigned_to && <span className="tag tag-purple">{task.assigned_to}</span>}
                    {task.created_by  && <span className="tag tag-ghost">by {task.created_by}</span>}
                  </div>
                  <div className="task-actions">
                    {col.id !== 'pending'     && <button className="btn-xs" onClick={() => moveTo(task.id, 'pending')}>← Pending</button>}
                    {col.id === 'pending'     && <button className="btn-xs btn-xs-blue"  onClick={() => moveTo(task.id, 'in_progress')}>Start</button>}
                    {col.id === 'in_progress' && <button className="btn-xs btn-xs-green" onClick={() => moveTo(task.id, 'done')}>Done</button>}
                    {col.id === 'in_progress' && <button className="btn-xs btn-xs-red"   onClick={() => moveTo(task.id, 'failed')}>Failed</button>}
                    <button className="btn-xs btn-xs-ghost ml-auto" onClick={() => remove(task.id)}>✕</button>
                  </div>
                </div>
              ))}
              {byStatus(col.id).length === 0 && (
                <p className="kanban-empty">No tasks</p>
              )}
            </div>
          </div>
        ))}
      </div>

      {showForm && (
        <div className="modal-backdrop" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>New Task</h3>
            <label className="field-label">Title</label>
            <input
              className="input"
              value={form.title}
              placeholder="What needs to be done?"
              onChange={e => setForm({ ...form, title: e.target.value })}
              onKeyDown={e => { if (e.key === 'Enter') create(); }}
              autoFocus
            />
            <label className="field-label">Description</label>
            <textarea className="textarea" rows={3} value={form.description} placeholder="Optional details…"
              onChange={e => setForm({ ...form, description: e.target.value })} />
            <label className="field-label">Assign To</label>
            <select className="input" value={form.assigned_to}
              onChange={e => setForm({ ...form, assigned_to: e.target.value })}>
              {agents.map(a => <option key={a.id} value={a.name}>{a.name} — {a.role}</option>)}
            </select>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn-primary" onClick={create}>Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
