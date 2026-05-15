const express = require('express');
const cors = require('cors');
const dotenv = require('dotenv');
const { AccessToken } = require('livekit-server-sdk');

dotenv.config();

const app = express();
app.use(cors());
app.use(express.json());

const PORT = Number(process.env.PORT || 3000);
const LIVEKIT_API_KEY = process.env.LIVEKIT_API_KEY;
const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET;
const LIVEKIT_URL = process.env.LIVEKIT_URL;

if (!LIVEKIT_API_KEY || !LIVEKIT_API_SECRET || !LIVEKIT_URL) {
  console.warn('Missing LIVEKIT_API_KEY, LIVEKIT_API_SECRET, or LIVEKIT_URL in environment.');
}

async function buildToken({ identity, roomName, name }) {
  if (!LIVEKIT_API_KEY || !LIVEKIT_API_SECRET) {
    throw new Error('LiveKit credentials are not configured');
  }

  const token = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
    identity,
    name,
  });

  token.addGrant({
    roomJoin: true,
    room: roomName,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
  });

  return await token.toJwt();
}

function randomId(prefix) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

app.get('/health', (_req, res) => {
  res.json({ ok: true });
});

app.get('/getToken', async (req, res) => {
  try {
    const room = String(req.query.room || 'voice-agent-room');
    const identity = String(req.query.identity || randomId('user'));
    const token = await buildToken({ identity, roomName: room, name: identity });
    res.json({ token, room, identity, wsUrl: LIVEKIT_URL });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/getAgentToken', async (req, res) => {
  try {
    const room = String(req.query.room || 'voice-agent-room');
    const identity = String(req.query.identity || 'ai-agent');
    const token = await buildToken({ identity, roomName: room, name: 'AI Agent' });
    res.json({ token, room, identity, wsUrl: LIVEKIT_URL });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.listen(PORT, () => {
  console.log(`Token server listening on http://localhost:${PORT}`);
});
