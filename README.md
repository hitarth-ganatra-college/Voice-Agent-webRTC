# Voice-Agent-webRTC

Minimal greenfield MVP for a realtime **Human ↔ AI Voice Agent** using **LiveKit + WebRTC + Whisper**.

## Architecture

- **frontend/**: Browser client that joins a LiveKit room, publishes one persistent microphone track, and sends `START`/`STOP` control messages.
- **server/**: Node.js token server that issues LiveKit JWTs via `/getToken` and `/getAgentToken`.
- **agent/**: Python AI media participant that joins the same room, captures incoming PCM to WAV, and transcribes with Whisper on `STOP`.

## Prerequisites

- LiveKit server URL and credentials
- Node.js 18+
- Python 3.10+

## Environment

Token server (`server/.env`):

```env
PORT=3000
LIVEKIT_URL=wss://<your-livekit-host>
LIVEKIT_API_KEY=<key>
LIVEKIT_API_SECRET=<secret>
```

Optional agent env vars:

```env
TOKEN_SERVER=http://localhost:3000
ROOM_NAME=voice-agent-room
WHISPER_MODEL=base
# Optional if you want to skip token fetch:
# LIVEKIT_URL=wss://<your-livekit-host>
# AGENT_TOKEN=<jwt>
```

## Run

### 1) Start token server

```bash
cd server
npm install
npm start
```

### 2) Start AI agent

```bash
cd agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python agent.py
```

### 3) Open frontend

Open `frontend/index.html` in a browser.

1. Click **Join Room**
2. Click **START** to unmute mic and signal recording start
3. Click **STOP** to mute mic and trigger transcription

Transcript logs appear in the Python agent console.

## Troubleshooting

- If `python agent.py` fails at the `asyncio.run(run_agent())` line, the most common cause is token bootstrap:
  - Ensure token server is running from `server/` with `npm start`
  - Ensure `server/.env` has valid `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`
  - Or set `LIVEKIT_URL` and `AGENT_TOKEN` for the agent directly to skip token fetch

## Notes

- This MVP is single-room, single-user oriented.
- Recording control is separated from media transport using LiveKit data messages.
- No streaming STT, no TTS, and no AI response publishing yet.
