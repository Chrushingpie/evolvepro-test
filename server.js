const express = require('express');
require('dotenv').config();
const fetch = require('node-fetch');

const app = express();
app.use(express.json());

const PYTHON_URL = process.env.PYTHON_SERVICE_URL || 'http://127.0.0.1:8000';

app.get('/', (req, res) => res.json({
  service: 'EvolvePro Agent Orchestrator',
  status: 'running',
  endpoints: [
    'GET  /api/langgraph/status',
    'GET  /api/agents',
    'POST /api/agents',
    'GET  /api/threads',
    'POST /api/threads',
    'GET  /api/threads/:id/messages',
    'POST /api/threads/:id/chat',
    'GET  /api/tasks',
    'POST /api/tasks',
    'GET  /api/clients',
    'POST /api/clients',
  ],
}));

app.get('/api/langgraph/status', async (req, res) => {
  try {
    const resp = await fetch(`${PYTHON_URL}/health`);
    if (!resp.ok) {
      return res.status(503).json({ error: 'Python service returned non-OK status', status: resp.status });
    }
    const data = await resp.json();
    res.json({ python_service: data, node_service: 'running' });
  } catch (err) {
    res.status(503).json({ error: 'Python service unavailable', details: err.message });
  }
});

app.post('/api/langgraph', async (req, res) => {
  if (!req.body || typeof req.body !== 'object') {
    return res.status(400).json({ error: 'Request body must be a JSON object' });
  }
  try {
    const resp = await fetch(`${PYTHON_URL}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    const text = await resp.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      return res.status(502).json({ error: 'Python service returned non-JSON response', raw: text.slice(0, 500) });
    }
    res.status(resp.status).json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Generic proxy — catches all /api/* routes not handled above
app.use('/api', async (req, res) => {
  try {
    const url = `${PYTHON_URL}${req.path}${req.search || ''}`;
    const options = {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (['POST', 'PUT', 'PATCH'].includes(req.method) && req.body) {
      options.body = JSON.stringify(req.body);
    }
    const resp = await fetch(url, options);
    const text = await resp.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text.slice(0, 500) }; }
    res.status(resp.status).json(data);
  } catch (err) {
    res.status(502).json({ error: 'Python service unavailable', details: err.message });
  }
});

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Server running on port ${port}`));
