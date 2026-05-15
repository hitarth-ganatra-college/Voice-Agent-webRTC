import asyncio
import hashlib
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
RECORDINGS_DIR = Path(__file__).with_name('recordings')
TOKEN_RESPONSE_PREVIEW_LENGTH = 120


class Recorder:
    def __init__(self, participant_identity: str, recordings_dir: Path):
        self.participant_identity = participant_identity
        self.recordings_dir = recordings_dir
        self.file_path: Path | None = None
        self._wav = None
        self.recording = False
        self._sample_rate: int | None = None
        self._channels: int | None = None

    def start(self):
        self.stop()
        self.file_path = None
        self.recording = True
        print(
            f'[{datetime.now(timezone.utc).isoformat()}] '
            f'recording armed for participant={self.participant_identity}'
        )

    def _open_wav_if_needed(self, sample_rate: int, channels: int):
        if self._wav is not None:
            return

        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        safe_identity = ''.join(c if c.isalnum() or c in ('-', '_') else '_' for c in self.participant_identity)
        identity_hash = hashlib.sha1(self.participant_identity.encode('utf-8')).hexdigest()[:8]
        self.file_path = self.recordings_dir / f'{safe_identity}-{identity_hash}-{timestamp}.wav'
        self._wav = wave.open(str(self.file_path), 'wb')
        self._wav.setnchannels(channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)
        self._sample_rate = sample_rate
        self._channels = channels
        print(f'[{datetime.now(timezone.utc).isoformat()}] recording started -> {self.file_path}')

    def push(self, frame: rtc.AudioFrame):
        if self.recording and self._wav is not None:
            if frame.sample_rate != self._sample_rate or frame.num_channels != self._channels:
                print(
                    f'Audio format changed for participant={self.participant_identity}; '
                    f'expected {self._sample_rate}Hz/{self._channels}ch, '
                    f'got {frame.sample_rate}Hz/{frame.num_channels}ch. Skipping frame.'
                )
                return
            self._wav.writeframes(bytes(frame.data))
            return
        if self.recording:
            self._open_wav_if_needed(sample_rate=frame.sample_rate, channels=frame.num_channels)
            if self._wav is not None:
                self._wav.writeframes(bytes(frame.data))

    def stop(self) -> Path | None:
        completed_file = self.file_path
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        self._sample_rate = None
        self._channels = None
        if self.recording:
            print(
                f'[{datetime.now(timezone.utc).isoformat()}] '
                f'recording stopped for participant={self.participant_identity}'
            )
        self.recording = False
        return completed_file


def get_participant_identity_from_data_packet(data_packet: rtc.DataPacket) -> str | None:
    participant = getattr(data_packet, 'participant', None)
    if participant is not None:
        identity = getattr(participant, 'identity', None)
        if identity:
            return str(identity)

    for attr in ('participant_identity', 'identity'):
        value = getattr(data_packet, attr, None)
        if value:
            return str(value)

    return None


async def fetch_agent_token() -> tuple[str, str]:
    qs = urllib.parse.urlencode({'room': ROOM_NAME, 'identity': 'ai-agent'})
    url = f'{TOKEN_SERVER}/getAgentToken?{qs}'
    setup_hint = (
        "Start the token server from the repository's server directory using npm start, "
        "or set LIVEKIT_URL and AGENT_TOKEN environment variables to skip token fetching."
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
        payload_preview = payload[:TOKEN_RESPONSE_PREVIEW_LENGTH].replace('\n', ' ')
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


async def transcribe(model, wav_path: Path, participant_identity: str):
    if not wav_path.exists() or wav_path.stat().st_size == 0:
        print(f'No audio file to transcribe for participant={participant_identity}')
        return

    print(f'Running Whisper transcription for participant={participant_identity}...')
    result = model.transcribe(str(wav_path))
    print(f'Transcript ({participant_identity}):', result.get('text', '').strip())


async def run_agent():
    token = AGENT_TOKEN
    ws_url = LIVEKIT_URL
    if not token or not ws_url:
        token, ws_url = await fetch_agent_token()

    room = rtc.Room()
    recorders_by_identity: dict[str, Recorder] = {}
    whisper_model = whisper.load_model(os.getenv('WHISPER_MODEL', 'base'))

    def get_recorder(participant_identity: str) -> Recorder:
        recorder = recorders_by_identity.get(participant_identity)
        if recorder is None:
            recorder = Recorder(participant_identity=participant_identity, recordings_dir=RECORDINGS_DIR)
            recorders_by_identity[participant_identity] = recorder
        return recorder

    @room.on('data_received')
    def on_data_received(data_packet: rtc.DataPacket, participant: rtc.RemoteParticipant | None = None):
        try:
            payload = json.loads(bytes(data_packet.data).decode('utf-8'))
            msg_type = payload.get('type')
            participant_identity = participant.identity if participant is not None else None
            if not participant_identity:
                participant_identity = get_participant_identity_from_data_packet(data_packet)
            if not participant_identity:
                print('Ignoring control message with unknown participant identity')
                return

            recorder = get_recorder(participant_identity)
            if msg_type == 'START':
                recorder.start()
            elif msg_type == 'STOP':
                audio_path = recorder.stop()
                if audio_path:
                    asyncio.create_task(transcribe(whisper_model, audio_path, participant_identity))
        except Exception as exc:
            print('Failed parsing control message:', exc)

    @room.on('track_subscribed')
    def on_track_subscribed(track: rtc.Track, _publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return

        participant_identity = participant.identity
        print(f'Audio track subscribed for participant={participant_identity}')

        async def consume_audio(audio_track: rtc.AudioTrack):
            stream = rtc.AudioStream(audio_track)
            recorder = get_recorder(participant_identity)
            async for event in stream:
                frame = event.frame
                recorder.push(frame)

        asyncio.create_task(consume_audio(track))

    await room.connect(ws_url, token)
    print(f'AI agent connected to {ROOM_NAME}')

    try:
        await asyncio.Event().wait()
    finally:
        for recorder in recorders_by_identity.values():
            recorder.stop()
        await room.disconnect()


if __name__ == '__main__':
    asyncio.run(run_agent())
