import { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import axios from 'axios';

const PALETTE = {
  coordinator: '#667eea',
  dev:         '#4ade80',
  admin:       '#facc15',
};

const agentColor = (name) => {
  if (PALETTE[name]) return PALETTE[name];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h << 5) - h + name.charCodeAt(i);
  return `hsl(${Math.abs(h) % 360}, 60%, 55%)`;
};

const fmtTime = (iso) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

// Highlight @mentions in message text
function highlightMentions(text, agentNames) {
  const parts = text.split(/(@\w+)/g);
  return parts.map((part, i) => {
    const name = part.slice(1).toLowerCase();
    if (part.startsWith('@') && agentNames.includes(name)) {
      return (
        <span key={i} className="hub-mention" style={{ color: agentColor(name) }}>
          {part}
        </span>
      );
    }
    return part;
  });
}

export default function RoomView() {
  const [hub, setHub]           = useState(null);
  const [agents, setAgents]     = useState([]);
  const [messages, setMessages] = useState([]);
  const [input, setInput]       = useState('');
  const [sending, setSending]   = useState(false);
  const [suggest, setSuggest]   = useState([]);  // @mention suggestions
  const sendingRef              = useRef(false);
  const bottomRef               = useRef(null);
  const inputRef                = useRef(null);

  useEffect(() => { sendingRef.current = sending; }, [sending]);

  const agentNames = agents.map(a => a.name);

  const loadMessages = useCallback(async (id) => {
    const res = await axios.get(`/api/threads/${id}/messages`);
    setMessages(res.data);
  }, []);

  // Bootstrap: fetch hub thread + agents
  useEffect(() => {
    (async () => {
      const [hubRes, agRes] = await Promise.all([
        axios.get('/api/hub'),
        axios.get('/api/agents'),
      ]);
      setHub(hubRes.data);
      setAgents(agRes.data);
      await loadMessages(hubRes.data.id);
    })();
  }, []);

  // Poll messages every 2s
  useEffect(() => {
    const t = setInterval(() => {
      if (hub && !sendingRef.current) loadMessages(hub.id);
    }, 2000);
    return () => clearInterval(t);
  }, [hub]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  // @mention autocomplete
  const handleInput = (e) => {
    const val = e.target.value;
    setInput(val);
    const match = val.match(/@(\w*)$/);
    if (match) {
      const prefix = match[1].toLowerCase();
      setSuggest(agentNames.filter(n => n.startsWith(prefix)));
    } else {
      setSuggest([]);
    }
  };

  const completeMention = (name) => {
    setInput(prev => prev.replace(/@\w*$/, `@${name} `));
    setSuggest([]);
    inputRef.current?.focus();
  };

  const handleSend = async () => {
    if (!input.trim() || !hub || sending) return;
    const msg = input.trim();
    setInput('');
    setSuggest([]);
    setSending(true);

    // Optimistic user message
    setMessages(m => [...m, {
      id: `opt-${Date.now()}`, sender: 'user', role: 'user',
      content: msg, created_at: new Date().toISOString(),
    }]);

    try {
      await axios.post(`/api/threads/${hub.id}/room`, {
        content: msg,
        agents: agentNames,   // backend uses @mentions to route; agents list is fallback
        hidden: false,
      }, { timeout: 600000 });
    } catch (err) {
      setMessages(m => [...m, {
        id: `err-${Date.now()}`, sender: 'system', role: 'system',
        content: `Error: ${err.response?.data?.detail ?? err.message}`,
        created_at: new Date().toISOString(),
      }]);
    }

    await loadMessages(hub.id);
    setSending(false);
  };

  // Group consecutive messages from the same sender (skip system ACKs — shown inline)
  const grouped = messages.reduce((acc, m) => {
    if (m.role === 'system') {
      // ACK / status chip — attach to previous group or standalone
      acc.push({ sender: m.sender, role: 'system', items: [m] });
      return acc;
    }
    const last = acc[acc.length - 1];
    if (last && last.role !== 'system' && last.sender === m.sender) {
      last.items.push(m);
    } else {
      acc.push({ sender: m.sender, role: m.role, items: [m] });
    }
    return acc;
  }, []);

  return (
    <div className="hub-layout">
      {/* ── Sidebar: agent presence ── */}
      <div className="hub-sidebar">
        <div className="hub-sidebar-title">Agent Hub</div>
        <div className="hub-sidebar-sub">Agents</div>
        {agents.map(ag => (
          <button
            key={ag.id}
            className="hub-agent-row"
            onClick={() => {
              setInput(prev => {
                const base = prev.endsWith(' ') || prev === '' ? prev : prev + ' ';
                return `${base}@${ag.name} `;
              });
              inputRef.current?.focus();
            }}
            title={`Call @${ag.name}`}
          >
            <span
              className="hub-agent-dot"
              style={{ background: ag.status === 'idle' ? agentColor(ag.name) : ag.status === 'busy' ? '#f59e0b' : '#ef4444' }}
            />
            <span className="hub-agent-name">{ag.name}</span>
            <span className="hub-agent-status">{ag.status}</span>
          </button>
        ))}
        <div className="hub-sidebar-hint">
          Click an agent or type @name to call them
        </div>
      </div>

      {/* ── Main channel ── */}
      <div className="hub-channel">
        <div className="hub-channel-header">
          <span className="hub-channel-name"># agent-hub</span>
          <span className="hub-channel-desc">All agent communication in one place</span>
        </div>

        <div className="hub-messages">
          {messages.length === 0 && !sending && (
            <div className="hub-empty">
              <div className="hub-empty-icon">⬡</div>
              <p>The hub is quiet.</p>
              <p className="muted">Type <code>@coordinator</code>, <code>@dev</code>, or <code>@admin</code> to call an agent.</p>
            </div>
          )}

          {grouped.map((group, gi) => {
            // System ACK chip
            if (group.role === 'system') {
              return (
                <div key={gi} className="hub-ack-row">
                  <span
                    className="hub-ack-chip"
                    style={{ borderColor: agentColor(group.sender) + '55', color: agentColor(group.sender) }}
                  >
                    <span className="hub-ack-dot" style={{ background: agentColor(group.sender) }} />
                    {group.sender}: {group.items[0].content}
                  </span>
                </div>
              );
            }

            const isUser = group.role === 'user' && group.sender === 'user';
            const color  = isUser ? null : agentColor(group.sender);

            return (
              <div key={gi} className={`hub-group ${isUser ? 'hub-group-user' : 'hub-group-agent'}`}>
                {!isUser && (
                  <div className="hub-group-header" style={{ color }}>
                    <span className="hub-avatar" style={{ background: color, color: '#0a0a0f' }}>
                      {group.sender[0].toUpperCase()}
                    </span>
                    <span className="hub-sender">{group.sender}</span>
                    <span className="hub-time">{fmtTime(group.items[0].created_at)}</span>
                  </div>
                )}
                <div className="hub-bubbles" style={!isUser ? { borderLeftColor: color } : {}}>
                  {group.items.map(m => (
                    <div key={m.id} className={`hub-bubble ${isUser ? 'hub-bubble-user' : 'hub-bubble-agent'}`}>
                      {isUser
                        ? <span>{highlightMentions(m.content, agentNames)}</span>
                        : <div className="md"><ReactMarkdown>{m.content}</ReactMarkdown></div>
                      }
                    </div>
                  ))}
                </div>
              </div>
            );
          })}

          {sending && (
            <div className="hub-group hub-group-agent">
              <div className="hub-group-header" style={{ color: '#667eea' }}>
                <span className="hub-avatar" style={{ background: '#667eea', color: '#0a0a0f' }}>…</span>
                <span className="hub-sender">agent thinking…</span>
              </div>
              <div className="hub-bubbles" style={{ borderLeftColor: '#667eea' }}>
                <div className="hub-bubble hub-bubble-agent">
                  <div className="message-bubble typing-indicator"><span /><span /><span /></div>
                </div>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* @mention suggestions */}
        {suggest.length > 0 && (
          <div className="hub-suggest">
            {suggest.map(name => (
              <button key={name} className="hub-suggest-item" onClick={() => completeMention(name)}>
                <span className="hub-suggest-dot" style={{ background: agentColor(name) }} />
                @{name}
              </button>
            ))}
          </div>
        )}

        <div className="chat-input-bar">
          <input
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={handleInput}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
              if (e.key === 'Escape') setSuggest([]);
            }}
            placeholder="@coordinator, @dev, or @admin to call an agent…"
            disabled={sending}
            autoFocus
          />
          <button className="btn-send" onClick={handleSend} disabled={sending || !input.trim()}>↑</button>
        </div>
      </div>
    </div>
  );
}
