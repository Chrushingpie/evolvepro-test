import { useState, useEffect } from 'react';
import axios from 'axios';
import AgentsView from './views/AgentsView';
import ThreadsView from './views/ThreadsView';
import RoomView from './views/RoomView';
import TasksView from './views/TasksView';
import ClientsView from './views/ClientsView';
import SettingsView from './views/SettingsView';
import ApprovalsView from './views/ApprovalsView';
import './App.css';

const NAV = [
  { id: 'agents',    label: 'Agents',    icon: '⬡'  },
  { id: 'threads',   label: 'Chat',      icon: '◉'  },
  { id: 'room',      label: 'Hub',       icon: '⬡⬡' },
  { id: 'tasks',     label: 'Tasks',     icon: '▦'  },
  { id: 'clients',   label: 'Clients',   icon: '◈'  },
  { id: 'approvals', label: 'Approvals', icon: '⏸'  },
  { id: 'settings',  label: 'Settings',  icon: '⚙'  },
];

export default function App() {
  const [view, setView]               = useState('agents');
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    const check = async () => {
      try {
        const res = await axios.get('/api/approvals?status=pending');
        setPendingCount(res.data.length);
      } catch {}
    };
    check();
    const t = setInterval(check, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <h1>EvolvePro</h1>
          <span className="subtitle">Multi-Agent Dashboard</span>
        </div>
      </header>

      <div className="shell">
        <nav className="sidebar">
          {NAV.map(n => (
            <button
              key={n.id}
              className={`nav-btn${view === n.id ? ' nav-btn-active' : ''}`}
              onClick={() => setView(n.id)}
              style={{ position: 'relative' }}
            >
              <span className="nav-icon">{n.icon}</span>
              <span className="nav-label">{n.label}</span>
              {n.id === 'approvals' && pendingCount > 0 && (
                <span className="nav-badge">{pendingCount}</span>
              )}
            </button>
          ))}
        </nav>

        <main className="content">
          <div className="view-slot" hidden={view !== 'agents'}>    <AgentsView />    </div>
          <div className="view-slot" hidden={view !== 'threads'}>   <ThreadsView />   </div>
          <div className="view-slot" hidden={view !== 'room'}>      <RoomView />      </div>
          <div className="view-slot" hidden={view !== 'tasks'}>     <TasksView />     </div>
          <div className="view-slot" hidden={view !== 'clients'}>   <ClientsView />   </div>
          <div className="view-slot" hidden={view !== 'approvals'}> <ApprovalsView /> </div>
          <div className="view-slot" hidden={view !== 'settings'}>  <SettingsView />  </div>
        </main>
      </div>
    </div>
  );
}
