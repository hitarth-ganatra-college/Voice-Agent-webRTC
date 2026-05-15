import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

import whisper
from livekit import rtc

TOKEN_SERVER = os.getenv('TOKEN_SERVER', 'http://localhost:3000')
ROOM_NAME = os.getenv('ROOM_NAME', 'voice-agent-room')
LIVEKIT_URL = os.getenv('LIVEKIT_URL')
AGENT_TOKEN = os.getenv('AGENT_TOKEN')
AUDIO_PATH = Path(__file__).with_name('received.wav')


class Recorder:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._wav = None
        self.recording = False

    def start(self, sample_rate: int, channels: int):
        self.stop()
        self._wav = wave.open(str(self.file_path), 'wb')
        self._wav.setnchannels(channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)
        self.recording = True
        print(f'[{datetime.now(timezone.utc).isoformat()}] recording started -> {self.file_path}')

    def push(self, pcm_bytes: bytes):
        if self.recording and self._wav is not None:
            self._wav.writeframes(pcm_bytes)

    def stop(self):
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        if self.recording:
            print(f'[{datetime.now(timezone.utc).isoformat()}] recording stopped')
        self.recording = False


async def fetch_agent_token() -> tuple[str, str]:
    qs = urllib.parse.urlencode({'room': ROOM_NAME, 'identity': 'ai-agent'})
    url = f'{TOKEN_SERVER}/getAgentToken?{qs}'
    setup_hint = (
        "Start the token server first (cd ../server && npm start), or set LIVEKIT_URL and "
        "AGENT_TOKEN environment variables to skip token fetching."
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(
            f'Token server request failed ({exc.code}) at {url}. '
            f'{body or exc.reason}. {setup_hint}'
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Failed to reach token server at {url}. {setup_hint}') from exc
    except Exception as exc:
        raise RuntimeError(f'Failed to fetch agent token from {url}. {setup_hint}') from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        payload_preview = payload[:120].replace('\n', ' ')
        raise RuntimeError(
            f'Token server returned invalid JSON from {url}. '
            f'Received: {payload_preview!r}. {setup_hint}'
        ) from exc

    token = data.get('token')
    ws_url = data.get('wsUrl')
    if not token or not ws_url:
        server_error = data.get('error')
        details = f'Server error: {server_error}. ' if server_error else ''
        raise RuntimeError(
            f'{details}Token response missing "token" or "wsUrl" from {url}. {setup_hint}'
        )

    return token, ws_url


async def transcribe(model, wav_path: Path):
    if not wav_path.exists() or wav_path.stat().st_size == 0:
        print('No audio file to transcribe')
        return

    print('Running Whisper transcription...')
    result = model.transcribe(str(wav_path))
    print('Transcript:', result.get('text', '').strip())


async def run_agent():
    token = AGENT_TOKEN
    ws_url = LIVEKIT_URL
    if not token or not ws_url:
        token, ws_url = await fetch_agent_token()

    room = rtc.Room()
    recorder = Recorder(AUDIO_PATH)
    whisper_model = whisper.load_model(os.getenv('WHISPER_MODEL', 'base'))

    @room.on('data_received')
    def on_data_received(data_packet: rtc.DataPacket):
        try:
            payload = json.loads(bytes(data_packet.data).decode('utf-8'))
            msg_type = payload.get('type')
            if msg_type == 'START':
                recorder.start(sample_rate=48000, channels=1)
            elif msg_type == 'STOP':
                recorder.stop()
                asyncio.create_task(transcribe(whisper_model, AUDIO_PATH))
        except Exception as exc:
            print('Failed parsing control message:', exc)

    @room.on('track_subscribed')
    def on_track_subscribed(track: rtc.Track, *_args):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return

        print('Audio track subscribed')

        async def consume_audio(audio_track: rtc.AudioTrack):
            stream = rtc.AudioStream(audio_track)
            async for event in stream:
                frame = event.frame
                if recorder.recording:
                    recorder.push(bytes(frame.data))

        asyncio.create_task(consume_audio(track))

    await room.connect(ws_url, token)
    print(f'AI agent connected to {ROOM_NAME}')

    try:
        await asyncio.Event().wait()
    finally:
        recorder.stop()
        await room.disconnect()


if __name__ == '__main__':
    asyncio.run(run_agent())
