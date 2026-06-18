import { useState, useEffect } from 'react';
import axios from 'axios';

const EMPTY_FORM = { name: '', email: '', phone: '', company: '', notes: '' };

export default function ClientsView() {
  const [clients, setClients]   = useState([]);
  const [search, setSearch]     = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing]   = useState(null);
  const [form, setForm]         = useState(EMPTY_FORM);

  const load = async (q = '') => {
    const res = await axios.get('/api/clients', { params: q ? { search: q } : {} });
    setClients(res.data);
  };

  useEffect(() => { load(); }, []);

  // Debounce search — wait 300ms after last keystroke before hitting API
  useEffect(() => {
    const t = setTimeout(() => load(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Escape to close modal
  useEffect(() => {
    if (!showForm) return;
    const handler = (e) => { if (e.key === 'Escape') setShowForm(false); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [showForm]);

  const openNew = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  };

  const openEdit = (c) => {
    setEditing(c);
    setForm({ name: c.name, email: c.email ?? '', phone: c.phone ?? '', company: c.company ?? '', notes: c.notes ?? '' });
    setShowForm(true);
  };

  const save = async () => {
    if (!form.name.trim()) return;
    if (editing) {
      await axios.patch(`/api/clients/${editing.id}`, form);
    } else {
      await axios.post('/api/clients', form);
    }
    setShowForm(false);
    await load(search);
  };

  const remove = async (e, id) => {
    e.stopPropagation();
    await axios.delete(`/api/clients/${id}`);
    await load(search);
  };

  return (
    <div className="view">
      <div className="view-header">
        <div>
          <h2>Clients</h2>
          <p className="view-sub">{clients.length} records</p>
        </div>
        <button className="btn-primary" onClick={openNew}>+ Add Client</button>
      </div>

      <input
        className="input search-input"
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="Search by name or company…"
      />

      {clients.length === 0 ? (
        <p className="muted" style={{ marginTop: 24 }}>
          {search ? 'No clients match your search.' : 'No clients yet — add your first one.'}
        </p>
      ) : (
        <div className="client-table">
          <div className="client-row client-header">
            <span>Name</span>
            <span>Company</span>
            <span>Email</span>
            <span>Phone</span>
            <span />
          </div>
          {clients.map(c => (
            <div key={c.id} className="client-row" onClick={() => openEdit(c)}>
              <span className="client-name">{c.name}</span>
              <span>{c.company || '—'}</span>
              <span>{c.email   || '—'}</span>
              <span>{c.phone   || '—'}</span>
              <span className="client-del" onClick={e => remove(e, c.id)}>✕</span>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <div className="modal-backdrop" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>{editing ? 'Edit Client' : 'New Client'}</h3>
            <label className="field-label">Name *</label>
            <input
              className="input"
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              onKeyDown={e => { if (e.key === 'Enter') save(); }}
              autoFocus
            />
            <label className="field-label">Company</label>
            <input className="input" value={form.company} onChange={e => setForm({ ...form, company: e.target.value })} />
            <label className="field-label">Email</label>
            <input className="input" type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} />
            <label className="field-label">Phone</label>
            <input className="input" value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} />
            <label className="field-label">Notes</label>
            <textarea className="textarea" rows={3} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} />
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn-primary" onClick={save}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
