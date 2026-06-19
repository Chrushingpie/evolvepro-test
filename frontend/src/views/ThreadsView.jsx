import { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import axios from 'axios';

const smartTime = (iso) => {
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString()
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
};

export default function ThreadsView() {
  const [threads, setThreads]     = useState([]);
  const [agents, setAgents]       = useState([]);
  const [selected, setSelected]   = useState(null);
  const [messages, setMessages]   = useState([]);
  const [agentName, setAgentName] = useState('coordinator');
  const [input, setInput]         = useState('');
  const [sending, setSending]     = useState(false);
  const bottomRef                 = useRef(null);
  const selectedRef               = useRef(null);
  const sendingRef                = useRef(false);

  useEffect(() => { sendingRef.current = sending; }, [sending]);
  useEffect(() => { selectedRef.current = selected; }, [selected]);

  const loadThreads = useCallback(async () => {
    const res = await axios.get('/api/threads');
    setThreads(res.data);
  }, []);

  const loadAgents = useCallback(async () => {
    const res = await axios.get('/api/agents');
    setAgents(res.data);
  }, []);

  const loadMessages = useCallback(async (id) => {
    const res = await axios.get(`/api/threads/${id}/messages`);
    setMessages(res.data);
  }, []);

  useEffect(() => { loadThreads(); loadAgents(); }, []);
  useEffect(() => { if (selected) loadMessages(selected.id); }, [selected]);

  useEffect(() => {
    const t = setInterval(() => {
      loadThreads();
      if (selectedRef.current && !sendingRef.current) {
        loadMessages(selectedRef.current.id);
      }
    }, 4000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  const newThread = async () => {
    const res = await axios.post('/api/threads', {
      title: `Thread — ${new Date().toLocaleString('en-NZ', { dateStyle: 'short', timeStyle: 'short' })}`,
    });
    await loadThreads();
    setSelected(res.data);
    setMessages([]);
  };

  const deleteThread = async (e, id) => {
    e.stopPropagation();
    await axios.delete(`/api/threads/${id}`);
    if (selected?.id === id) { setSelected(null); setMessages([]); }
    await loadThreads();
  };

  const send = async () => {
    if (!input.trim() || !selected || sending) return;
    const msg = input.trim();
    setInput('');
    setSending(true);

    const streamId = `stream-${Date.now()}`;
    setMessages(m => [
      ...m,
      { id: `opt-${Date.now()}`, sender: 'user', role: 'user', content: msg, created_at: new Date().toISOString() },
      { id: streamId, sender: agentName, role: 'assistant', content: '', created_at: new Date().toISOString(), streaming: true },
    ]);

    try {
      const response = await fetch(`/api/threads/${selected.id}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_name: agentName, content: msg }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'token') {
              setMessages(m => m.map(msg =>
                msg.id === streamId ? { ...msg, content: msg.content + data.content } : msg
              ));
            } else if (data.type === 'tool_start') {
              setMessages(m => m.map(msg =>
                msg.id === streamId ? { ...msg, activeTool: data.content } : msg
              ));
            } else if (data.type === 'tool_end') {
              setMessages(m => m.map(msg =>
                msg.id === streamId ? { ...msg, activeTool: null } : msg
              ));
            } else if (data.type === 'done') {
              setMessages(m => m.map(msg =>
                msg.id === streamId
                  ? { ...msg, content: data.content || msg.content, streaming: false, activeTool: null }
                  : msg
              ));
            } else if (data.type === 'error') {
              setMessages(m => m.map(msg =>
                msg.id === streamId
                  ? { ...msg, content: `Error: ${data.content}`, streaming: false, role: 'system' }
                  : msg
              ));
            }
          } catch {}
        }
      }

      await loadMessages(selected.id);
      await loadThreads();
    } catch (err) {
      setMessages(m => m.map(msg =>
        msg.id === streamId
          ? { ...msg, content: `Error: ${err.message}`, streaming: false, role: 'system' }
          : msg
      ));
    }
    setSending(false);
  };

  const renderBubble = (m) => {
    if (m.role !== 'assistant') return m.content;
    if (m.streaming && !m.content && !m.activeTool) {
      return <div className="typing-indicator"><span /><span /><span /></div>;
    }
    if (m.activeTool && !m.content) {
      return (
        <span style={{ color: '#667eea', fontSize: '.82rem', fontStyle: 'italic' }}>
          calling {m.activeTool}…
        </span>
      );
    }
    return (
      <>
        <div className="md"><ReactMarkdown>{m.content}</ReactMarkdown></div>
        {m.activeTool && (
          <div style={{ marginTop: 6, color: '#667eea', fontSize: '.78rem', fontStyle: 'italic' }}>
            calling {m.activeTool}…
          </div>
        )}
        {m.streaming && <span className="stream-cursor">▋</span>}
      </>
    );
  };

  return (
    <div className="threads-layout">
      <div className="thread-list">
        <div className="thread-list-header">
          <span>Conversations</span>
          <button className="btn-ghost" onClick={newThread}>+ New</button>
        </div>
        {threads.length === 0 && <p className="muted pad">No threads yet</p>}
        {threads.map(t => (
          <div
            key={t.id}
            className={`thread-item${selected?.id === t.id ? ' thread-item-active' : ''}`}
            onClick={() => setSelected(t)}
          >
            <div className="thread-title">{t.title ?? `Thread #${t.id}`}</div>
            <div className="thread-meta">{t.message_count} msg · {smartTime(t.updated_at)}</div>
            <button className="thread-delete" onClick={e => deleteThread(e, t.id)}>✕</button>
          </div>
        ))}
      </div>

      <div className="chat-panel">
        {!selected ? (
          <div className="chat-empty">
            <div className="chat-empty-inner">
              <div className="chat-empty-icon">◉</div>
              <p>Select a conversation or start a new one</p>
              <button className="btn-primary" onClick={newThread}>+ New Thread</button>
            </div>
          </div>
        ) : (
          <>
            <div className="chat-header">
              <span className="chat-title">{selected.title ?? `Thread #${selected.id}`}</span>
            </div>

            <div className="chat-messages">
              {messages.map(m => (
                <div key={m.id} className={`message message-${m.role}`}>
                  <div className="message-sender">{m.sender}</div>
                  <div className="message-bubble">{renderBubble(m)}</div>
                  <div className="message-time">
                    {new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                </div>
              ))}
              <div ref={bottomRef} />
            </div>

            <div className="chat-input-bar">
              <select
                className="agent-select"
                value={agentName}
                onChange={e => setAgentName(e.target.value)}
                disabled={sending}
              >
                {agents.map(a => (
                  <option key={a.id} value={a.name}>{a.name}</option>
                ))}
              </select>
              <input
                className="chat-input"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
                placeholder={sending ? `${agentName} is thinking…` : 'Message… (Enter to send)'}
                disabled={sending}
                autoFocus
              />
              <button className="btn-send" onClick={send} disabled={sending || !input.trim()}>↑</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
