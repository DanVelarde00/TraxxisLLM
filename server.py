from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import time
import itertools
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import tempfile
import io
import re

import httpx
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from pydantic import BaseModel, Field, ValidationError
from piper.voice import PiperVoice

# ============================================================
# Paths & configuration
# ============================================================

BASE_DIR = Path(__file__).parent
TTS_DIR = BASE_DIR / "tts"
TTS_DIR.mkdir(exist_ok=True)

# ASR (Whisper) configuration
ASR_DIR = BASE_DIR / "asr"
ASR_DIR.mkdir(exist_ok=True)

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1:8b"  # Use llama3.1:8b for better steering accuracy

# Whisper model - BASE for good balance of speed and accuracy
WHISPER_MODEL = "base"

# Piper TTS voice model (BT-7274 from Titanfall 2!)
PIPER_MODEL_DIR = BASE_DIR / "piper_models" / "bt7274"
PIPER_MODEL_PATH = PIPER_MODEL_DIR / "BT7274.onnx"
PIPER_CONFIG_PATH = PIPER_MODEL_DIR / "BT7274.onnx.json"

app = FastAPI(title="WS Control Server — Voice→LLM→(TTS+ESP32)")

_httpx_client: Optional[httpx.AsyncClient] = None
_whisper_model = None
_piper_voice: Optional[PiperVoice] = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _httpx_client, _piper_voice
    _httpx_client = httpx.AsyncClient(timeout=30.0)
    print("[startup] HTTP client ready for Ollama")

    # Pre-load Whisper model
    try:
        import whisper
        global _whisper_model
        print(f"[startup] Loading Whisper model '{WHISPER_MODEL}'...")
        _whisper_model = await asyncio.to_thread(whisper.load_model, WHISPER_MODEL)
        print("[startup] Whisper model loaded successfully")
    except ImportError:
        print("[startup] WARNING: whisper not installed. Run: pip install openai-whisper")
    except Exception as e:
        print(f"[startup] WARNING: Failed to load Whisper model: {e}")

    # Pre-load Piper TTS model
    try:
        print(f"[startup] Loading Piper TTS model (BT-7274)...")
        _piper_voice = await asyncio.to_thread(
            PiperVoice.load,
            str(PIPER_MODEL_PATH),
            str(PIPER_CONFIG_PATH)
        )
        print("[startup] Piper TTS model loaded successfully")
    except Exception as e:
        print(f"[startup] WARNING: Failed to load Piper TTS model: {e}")

    try:
        yield
    finally:
        await _httpx_client.aclose()
        _httpx_client = None
        print("[shutdown] HTTP client closed")


app.router.lifespan_context = lifespan


# ============================================================
# Message schema
# ============================================================


class MsgType(str, Enum):
    command = "command"
    ack = "ack"
    telemetry = "telemetry"
    complete = "complete"
    health = "health"


class Message(BaseModel):
    type: MsgType
    msg_id: int = Field(..., ge=0)
    t: int = Field(default_factory=lambda: int(time.time() * 1000))
    payload: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Logging
# ============================================================


MAX_LOG = 5000
telemetry_log: List[Dict[str, Any]] = []
event_log: List[Dict[str, Any]] = []
plans_log: List[Dict[str, Any]] = []
asr_log: List[Dict[str, Any]] = []


def log_event(evt: str, **fields: Any) -> None:
    stamp = time.strftime("%H:%M:%S")
    row = {"ts": stamp, "evt": evt, **fields}
    event_log.append(row)
    if len(event_log) > MAX_LOG:
        del event_log[: len(event_log) - MAX_LOG]
    print(f"[{stamp}] {evt} " + " ".join(f"{k}={v}" for k, v in fields.items()))


def log_telemetry(payload: Dict[str, Any]) -> None:
    telemetry_log.append({"t_ms": int(time.time() * 1000), **payload})
    if len(telemetry_log) > MAX_LOG:
        del telemetry_log[: len(telemetry_log) - MAX_LOG]


def log_asr(transcript: str, duration_s: float, language: str = "en", **extra: Any) -> None:
    asr_log.append({
        "t_ms": int(time.time() * 1000),
        "transcript": transcript,
        "duration_s": duration_s,
        "language": language,
        **extra
    })
    if len(asr_log) > MAX_LOG:
        del asr_log[: len(asr_log) - MAX_LOG]


# ============================================================
# WebSocket connection manager
# ============================================================


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    def get_primary(self) -> Optional[WebSocket]:
        return next(iter(self.clients), None)

    async def send_json(self, ws: WebSocket, msg: Message) -> None:
        await ws.send_text(msg.model_dump_json())


manager = ConnectionManager()


# ============================================================
# Dispatcher
# ============================================================


ACK_TIMEOUT_S = 0.5
CMD_TIMEOUT_S_DEFAULT = 8.0
RETRY_LIMIT = 2


@dataclass
class Pending:
    msg: Message
    ack_event: asyncio.Event = field(default_factory=asyncio.Event)
    complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    retries: int = 0
    status: str = "queued"
    result_payload: Dict[str, Any] = field(default_factory=dict)
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class Dispatcher:
    def __init__(self) -> None:
        self._msg_id_counter = itertools.count(start=1)
        self._queue: asyncio.Queue[Pending] = asyncio.Queue()
        self._inflight: Dict[int, Pending] = {}
        self._runner_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._runner_task is None or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._runner(), name="dispatcher")

    def status(self) -> Dict[str, Any]:
        return {
            "queue": self._queue.qsize(),
            "inflight": {mid: p.status for mid, p in list(self._inflight.items())[:50]},
        }

    async def enqueue_command(self, payload: Dict[str, Any]) -> int:
        msg_id = next(self._msg_id_counter)
        cmd = Message(type=MsgType.command, msg_id=msg_id, payload=payload)
        await self._queue.put(Pending(msg=cmd))
        log_event("enqueue_command", msg_id=msg_id, payload=payload)
        return msg_id

    async def _runner(self) -> None:
        while True:
            pending = await self._queue.get()
            async with self._lock:
                ws = manager.get_primary()
                if ws is None:
                    pending.status = "failed"
                    log_event("no_client_connected", msg_id=pending.msg.msg_id)
                    continue

                self._inflight[pending.msg.msg_id] = pending
                try:
                    await manager.send_json(ws, pending.msg)
                    pending.status = "sent"
                    log_event(
                        "command_sent", msg_id=pending.msg.msg_id, payload=pending.msg.payload
                    )
                except Exception as e:
                    pending.status = "failed"
                    log_event(
                        "send_error",
                        msg_id=pending.msg.msg_id,
                        error=type(e).__name__,
                        detail=str(e)[:200],
                    )
                    self._inflight.pop(pending.msg.msg_id, None)
                    continue

                for attempt in range(RETRY_LIMIT + 1):
                    try:
                        await asyncio.wait_for(pending.ack_event.wait(), timeout=ACK_TIMEOUT_S)
                        pending.status = "acked"
                        break
                    except asyncio.TimeoutError:
                        if attempt < RETRY_LIMIT:
                            pending.retries += 1
                            log_event(
                                "ack_timeout_resend",
                                msg_id=pending.msg.msg_id,
                                attempt=attempt + 1,
                            )
                            try:
                                await manager.send_json(ws, pending.msg)
                                pending.status = "sent"
                            except Exception as e:
                                pending.status = "failed"
                                log_event(
                                    "resend_error", msg_id=pending.msg.msg_id, error=type(e).__name__
                                )
                                break
                        else:
                            pending.status = "timeout"
                            log_event("ack_give_up", msg_id=pending.msg.msg_id)
                            break

                if pending.status not in ("acked", "sent"):
                    self._inflight.pop(pending.msg.msg_id, None)
                    continue

                timeout_ms = pending.msg.payload.get("timeout_ms")
                if timeout_ms is None:
                    timeout_ms = int(CMD_TIMEOUT_S_DEFAULT * 1000)
                try:
                    await asyncio.wait_for(
                        pending.complete_event.wait(), timeout=timeout_ms / 1000.0
                    )
                    pending.status = "complete"
                    log_event(
                        "command_complete",
                        msg_id=pending.msg.msg_id,
                        result=pending.result_payload,
                    )
                except asyncio.TimeoutError:
                    pending.status = "timeout"
                    log_event("command_timeout", msg_id=pending.msg.msg_id, timeout_ms=timeout_ms)
                finally:
                    self._inflight.pop(pending.msg.msg_id, None)

    def on_ack(self, msg_id: int) -> None:
        pending = self._inflight.get(msg_id)
        if pending and not pending.ack_event.is_set():
            pending.ack_event.set()
            log_event("ack_received", msg_id=msg_id)

    def on_complete(self, msg_id: int, payload: Dict[str, Any]) -> None:
        pending = self._inflight.get(msg_id)
        if pending and not pending.complete_event.is_set():
            pending.result_payload = payload
            pending.complete_event.set()
            log_event("complete_received", msg_id=msg_id, payload=payload)


dispatcher = Dispatcher()
dispatcher.start()


# ============================================================
# ASR (Whisper)
# ============================================================


class ASRResult(BaseModel):
    transcript: str
    language: str
    duration_s: float
    confidence: Optional[float] = None


async def transcribe_audio(audio_data: bytes, language: str = "en") -> ASRResult:
    global _whisper_model
    
    if _whisper_model is None:
        try:
            import whisper
            log_event("asr_loading_model", model=WHISPER_MODEL)
            _whisper_model = await asyncio.to_thread(whisper.load_model, WHISPER_MODEL)
            log_event("asr_model_loaded", model=WHISPER_MODEL)
        except ImportError:
            raise RuntimeError(
                "Whisper not installed. Install with: pip install openai-whisper"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load Whisper model: {e}")
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=ASR_DIR) as tmp:
        tmp.write(audio_data)
        tmp_path = Path(tmp.name)
    
    try:
        start_time = time.time()
        log_event("asr_transcribe_start", file_size=len(audio_data), language=language)
        
        result = await asyncio.to_thread(
            _whisper_model.transcribe,
            str(tmp_path),
            language=language if language != "auto" else None,
            fp16=False
        )
        
        duration_s = time.time() - start_time
        transcript = result["text"].strip()
        detected_language = result.get("language", language)
        
        log_event(
            "asr_transcribe_complete",
            transcript=transcript[:100],
            duration_s=round(duration_s, 2),
            language=detected_language
        )
        
        log_asr(
            transcript=transcript,
            duration_s=duration_s,
            language=detected_language,
            file_size=len(audio_data)
        )
        
        return ASRResult(
            transcript=transcript,
            language=detected_language,
            duration_s=duration_s
        )
        
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


# ============================================================
# TTS (Piper TTS - BT-7274 Voice from Titanfall 2!)
# ============================================================

def clean_text_for_speech(text):
    """Aggressively remove special characters"""
    text = re.sub(r'[*_~`]', '', text)
    text = re.sub(r'["\'"]', '', text)
    text = re.sub(r'\.\.\.', '', text)
    text = re.sub(r'[!]{2,}', '!', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


async def tts_say(text: str) -> None:
    global _piper_voice

    if not text:
        return

    if _piper_voice is None:
        log_event("tts_error", error="PiperNotLoaded", detail="Piper voice model not loaded")
        return

    text = clean_text_for_speech(text)

    output_path = TTS_DIR / f"bt_{int(time.time() * 1000)}.wav"
    log_event("tts_synth_start", text=text[:100], voice="BT-7274")

    try:
        # Synthesize speech using Piper
        import wave
        with wave.open(str(output_path), 'wb') as wav_file:
            await asyncio.to_thread(
                _piper_voice.synthesize_wav,
                text,
                wav_file
            )

        log_event("tts_synth_complete", file=str(output_path))

        # Play the audio
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(str(output_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            subprocess.Popen(
                ["afplay" if sys.platform == "darwin" else "aplay", str(output_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        # Cleanup old audio files
        async def cleanup():
            await asyncio.sleep(5)
            try:
                output_path.unlink()
            except:
                pass
        asyncio.create_task(cleanup())

    except Exception as exc:
        log_event("tts_error", error=type(exc).__name__, detail=str(exc)[:200])
        raise


async def _tts_background(text: str) -> None:
    try:
        await tts_say(text)
    except Exception:
        pass


# ============================================================
# LLM with conversation context
# ============================================================


class VoiceIntent(BaseModel):
    transcript: str
    context: Optional[Dict[str, Any]] = None


class VoicePlan(BaseModel):
    intent_id: str
    say: str
    steps: List[Dict[str, Any]]
    notes: Dict[str, Any] = Field(default_factory=dict)


async def llm_plan_from_voice(
    transcript: str, context: Optional[Dict[str, Any]] = None
) -> VoicePlan:
    assert _httpx_client is not None, "HTTP client not initialized"

    system_prompt = """
You are BT, a witty robot assistant controlling a Traxxas RC vehicle.

╔═══════════════════════════════════════════════════════════╗
║  🚨 CRITICAL: STEERING VALUES - DO NOT USE 1500 FOR TURNS 🚨 ║
╚═══════════════════════════════════════════════════════════╝

STEERING RULES (READ TWICE):
┌─────────────┬──────────────┬────────────────────────┐
│  Direction  │ Steer Value  │  When to Use           │
├─────────────┼──────────────┼────────────────────────┤
│ HARD LEFT   │    1100      │ "hard left"            │
│ LEFT        │    1250      │ "turn left", "go left" │
│ SLIGHT LEFT │    1400      │ "slight left"          │
│ STRAIGHT    │    1500      │ ONLY "forward", "back" │
│ SLIGHT RIGHT│    1600      │ "slight right"         │
│ RIGHT       │    1750      │ "turn right"           │
│ HARD RIGHT  │    1900      │ "hard right"           │
└─────────────┴──────────────┴────────────────────────┘

🔴 RULE #1: IF user says "LEFT" → steer MUST be < 1450
🔴 RULE #2: IF user says "RIGHT" → steer MUST be > 1550
🔴 RULE #3: ONLY use steer=1500 for STRAIGHT forward/backward
🔴 RULE #4: When in doubt, use 1250 for left, 1750 for right

⚠️ NEVER USE steer=1500 WHEN USER SAYS "TURN" ⚠️
⚠️ LEFT = steer < 1450 | RIGHT = steer > 1550 ⚠️

THROTTLE VALUES:
- 1500 = STOPPED (neutral)
- Forward: 1575-1700 (slow to medium)
- Reverse: 1300-1450
- Fast forward: 1800+ (only if user says "fast")

PERSONALITY:
- Funny and sarcastic, NOT creepy
- NO pet names (no "darling", "sweetie", etc.)
- Short responses (≤120 chars)

SPEECH RULES:
- NEVER use asterisks (*), underscores (_), or markdown
- NO special characters in "say" field
- Natural speech: "Turning right" NOT "Turning *right*"

RESPONSE FORMAT (STRICT JSON):
{
"say": "Short response NO special chars",
"steps": [
    {"action": "move_time", "throt": 1650, "steer": 1750, "time_ms": 2000}
]
}

ACTIONS:
- move_time: Move for duration. Fields: throt, steer, time_ms
- move_dist: Move distance. Fields: throt, steer, feet
- stop: Emergency stop
- speak: Say something. Fields: text
- macro: Named routine. Fields: name, params

CRITICAL RULES FOR MULTI-STEP COMMANDS:
1. "go forward THEN turn" = TWO separate steps (NOT combined!)
2. Step 1: Move forward with steer=1500 (straight)
3. Step 2: STOP moving (throt=1500) and ONLY turn wheels (steer≠1500)
4. NEVER combine forward movement + turning in same step
5. Each step executes SEQUENTIALLY (waits for previous to finish)

EXAMPLES - CRITICAL:

User: "turn right"
✓ CORRECT: {"say": "Turning right", "steps": [{"action": "move_time", "throt": 1500, "steer": 1750, "time_ms": 1000}]}
✗ WRONG: steer=1500 (that's straight, not a turn!)

User: "turn left"
✓ CORRECT: {"say": "Going left", "steps": [{"action": "move_time", "throt": 1500, "steer": 1250, "time_ms": 1000}]}
✗ WRONG: steer=1500 is WRONG for left turn!

User: "go forward then turn right"
✓ CORRECT (TWO STEPS): {"say": "On it", "steps": [
    {"action": "move_time", "throt": 1650, "steer": 1500, "time_ms": 2000},
    {"action": "move_time", "throt": 1500, "steer": 1750, "time_ms": 1000}
]}
✗ WRONG (ONE STEP): [{"action": "move_time", "throt": 1650, "steer": 1750, "time_ms": 2000}]
  (This would turn WHILE moving - incorrect!)

User: "go forward for 10 feet and turn left for 5 seconds"
✓ CORRECT (TWO STEPS): {"say": "Moving forward then turning left", "steps": [
    {"action": "move_dist", "throt": 1650, "steer": 1500, "feet": 10},
    {"action": "move_time", "throt": 1500, "steer": 1250, "time_ms": 5000}
]}

User: "drive forward fast and make a hard right turn"
✓ CORRECT (TWO STEPS): {"say": "Going fast then hard right", "steps": [
    {"action": "move_time", "throt": 1800, "steer": 1500, "time_ms": 2000},
    {"action": "move_time", "throt": 1500, "steer": 1900, "time_ms": 1500}
]}

REMEMBER:
- Return ALL steps needed sequentially
- "then" or "and then" = SEPARATE steps
- Forward movement = throt>1500, steer=1500
- Turning = throt=1500, steer≠1500
- NEVER mix forward + turn in one step
"""

    user_prompt = ""
    
    if context and "recent_conversation" in context:
        user_prompt += "Recent conversation:\n"
        for msg in context["recent_conversation"]:
            role = "User" if msg["role"] == "user" else "BT"
            user_prompt += f"{role}: {msg['text']}\n"
        user_prompt += "\n"
    
    user_prompt += f"Current command: {transcript.strip()}\n"
    user_prompt += "Respond as JSON. Keys: say (NO special chars), steps (ALL commands).\n"
    user_prompt += "\n🚨 CRITICAL REMINDERS:\n"
    user_prompt += "- If command has 'THEN' or multiple actions → CREATE SEPARATE STEPS!\n"
    user_prompt += "- Forward movement: throt=1650, steer=1500 (straight)\n"
    user_prompt += "- Turning ONLY: throt=1500 (stopped), steer=1250 (left) or steer=1750 (right)\n"
    user_prompt += "- NEVER combine forward + turn in same step!\n"
    user_prompt += "- LEFT turns → steer=1250 (hard=1100, slight=1400)\n"
    user_prompt += "- RIGHT turns → steer=1750 (hard=1900, slight=1600)\n"
    user_prompt += "- NEVER use steer=1500 for turns!"
    
    if context:
        other_context = {k: v for k, v in context.items() if k != "recent_conversation"}
        if other_context:
            user_prompt += f"\nContext: {json.dumps(other_context)}"

    body = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.3,
            "num_predict": 300,
            "num_ctx": 2048
        },
    }

    resp = await _httpx_client.post(OLLAMA_URL, json=body)
    resp.raise_for_status()
    data = resp.json()

    try:
        raw = data["message"]["content"].strip()
    except KeyError as exc:
        raise RuntimeError(f"Ollama response missing 'message.content': {data}") from exc

    # Clean up markdown
    if raw.startswith("```"):
        raw = raw.strip().lstrip("`").rstrip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()

    if not raw.strip().startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model did not return strict JSON. Got: {raw[:300]}") from exc

    say = parsed.get("say", "")
    steps = parsed.get("steps", [])

    if not isinstance(say, str):
        say = ""
    if not isinstance(steps, list):
        steps = []

    # Clean asterisks
    say = re.sub(r'[*_~`]', '', say)

    # POST-PROCESS AND FIX STEERING
    transcript_lower = transcript.lower()
    clamped_steps: List[Dict[str, Any]] = []

    # Enhanced keyword detection for direction commands
    left_keywords = [
        "left", "turn left", "go left", "turning left", "move left",
        "hang a left", "make a left", "to the left", "turn to the left",
        "go to the left", "head left", "veer left", "hard left", "slight left"
    ]
    right_keywords = [
        "right", "turn right", "go right", "turning right", "move right",
        "hang a right", "make a right", "to the right", "turn to the right",
        "go to the right", "head right", "veer right", "hard right", "slight right"
    ]

    # Check for directional intent
    has_left_command = any(keyword in transcript_lower for keyword in left_keywords)
    has_right_command = any(keyword in transcript_lower for keyword in right_keywords)

    for step in steps:
        if not isinstance(step, dict) or "action" not in step:
            continue

        action = step["action"]
        processed = dict(step)

        # FIX STEERING if LLM got it wrong
        if "steer" in processed:
            steer = processed["steer"]
            original_steer = steer

            # Detect intensity for better steering values
            is_hard = "hard" in transcript_lower or "sharp" in transcript_lower
            is_slight = "slight" in transcript_lower or "little" in transcript_lower or "bit" in transcript_lower

            # Detect LEFT command
            if has_left_command:
                if steer >= 1500:  # LLM used neutral or right - WRONG!
                    if is_hard:
                        processed["steer"] = 1100  # Hard left
                    elif is_slight:
                        processed["steer"] = 1400  # Slight left
                    else:
                        processed["steer"] = 1250  # Medium left
                    log_event("steering_fix",
                             original=original_steer,
                             fixed=processed["steer"],
                             reason="left_command_detected",
                             intensity="hard" if is_hard else ("slight" if is_slight else "medium"),
                             transcript=transcript[:50])

            # Detect RIGHT command
            elif has_right_command:
                if steer <= 1500:  # LLM used neutral or left - WRONG!
                    if is_hard:
                        processed["steer"] = 1900  # Hard right
                    elif is_slight:
                        processed["steer"] = 1600  # Slight right
                    else:
                        processed["steer"] = 1750  # Medium right
                    log_event("steering_fix",
                             original=original_steer,
                             fixed=processed["steer"],
                             reason="right_command_detected",
                             intensity="hard" if is_hard else ("slight" if is_slight else "medium"),
                             transcript=transcript[:50])

            # Log even when steering is correct (for debugging)
            else:
                log_event("steering_check",
                         steer=steer,
                         left_detected=has_left_command,
                         right_detected=has_right_command,
                         transcript=transcript[:50])
        
        if "speed_pct" in processed:
            try:
                spd = int(processed["speed_pct"])
            except Exception:
                spd = 30
            processed["speed_pct"] = max(10, min(70, spd))
            
        if "timeout_ms" not in processed:
            processed["timeout_ms"] = 6000 if action == "move_distance" else 4000
            
        clamped_steps.append(processed)

    intent_id = f"vi_{int(time.time() * 1000)}"
    plan = VoicePlan(
        intent_id=intent_id,
        say=say,
        steps=clamped_steps,
        notes={"model": OLLAMA_MODEL},
    )
    
    plans_log.append(plan.model_dump())
    if len(plans_log) > MAX_LOG:
        del plans_log[: len(plans_log) - MAX_LOG]
    
    # Debug log
    log_event("llm_plan_generated", 
              say=say[:100], 
              step_count=len(clamped_steps),
              steps=[{"action": s.get("action"), "steer": s.get("steer"), "throt": s.get("throt")} for s in clamped_steps])
    
    return plan


# ============================================================
# WebSocket endpoint
# ============================================================


IDLE_TIMEOUT_S = 5.0
RECV_MAX_BYTES = 64 * 1024


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    log_event("client_connected", addr=str(ws.client))
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=IDLE_TIMEOUT_S)
            except asyncio.TimeoutError:
                try:
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "health",
                                "msg_id": -1,
                                "t": int(time.time() * 1000),
                                "payload": {"reason": "idle_timeout"},
                            }
                        )
                    )
                except Exception:
                    pass
                continue

            if len(raw) > RECV_MAX_BYTES:
                log_event("drop_oversize", bytes=len(raw))
                continue

            try:
                incoming = Message.model_validate_json(raw)
            except ValidationError as ve:
                log_event("bad_message", error="validation_error", detail=str(ve)[:200])
                continue

            if incoming.type == MsgType.telemetry:
                log_telemetry(incoming.payload)
            elif incoming.type == MsgType.ack:
                dispatcher.on_ack(incoming.msg_id)
            elif incoming.type == MsgType.complete:
                dispatcher.on_complete(incoming.msg_id, incoming.payload)
            elif incoming.type == MsgType.health:
                pass
            elif incoming.type == MsgType.command:
                log_event("unexpected_command_from_client", msg_id=incoming.msg_id)
                try:
                    await manager.send_json(
                        ws,
                        Message(
                            type=MsgType.ack,
                            msg_id=incoming.msg_id,
                            payload={"note": "server_ack"},
                        ),
                    )
                except Exception:
                    pass

    except WebSocketDisconnect:
        log_event("client_disconnected", addr=str(ws.client))
    except Exception as exc:
        log_event("ws_error", error=type(exc).__name__, detail=str(exc)[:200])
    finally:
        manager.disconnect(ws)
        log_event("connection_closed")


# ============================================================
# HTTP endpoints
# ============================================================


class CommandIn(BaseModel):
    action: str
    distance_ft: Optional[float] = None
    angle_deg: Optional[float] = None
    speed_pct: Optional[int] = None
    timeout_ms: Optional[int] = None


@app.post("/voice_intent")
async def handle_voice_intent(intent: VoiceIntent):
    plan = await llm_plan_from_voice(intent.transcript, intent.context)
    log_event(
        "voice_plan_created",
        intent_id=plan.intent_id,
        say=plan.say,
        steps=len(plan.steps),
    )

    if plan.say:
        log_event("tts_request", text=plan.say)
        asyncio.create_task(_tts_background(plan.say))

    for step in plan.steps:
        try:
            msg_id = await dispatcher.enqueue_command(step)
            log_event("plan_step_enqueued", msg_id=msg_id, action=step.get("action"))
        except Exception as exc:
            log_event(
                "enqueue_error",
                action=step.get("action"),
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )

    return plan.model_dump()


@app.post("/transcribe")
async def transcribe_endpoint(
    audio: UploadFile = File(...),
    language: str = Form("en")
):
    try:
        audio_data = await audio.read()
        result = await transcribe_audio(audio_data, language)
        return result.model_dump()
    except Exception as exc:
        log_event("asr_error", error=type(exc).__name__, detail=str(exc)[:200])
        raise HTTPException(status_code=500, detail=f"ASR failure: {exc}") from exc


@app.post("/voice_command")
async def voice_command_endpoint(
    audio: UploadFile = File(...),
    language: str = Form("en"),
    context: Optional[str] = Form(None)
):
    """Complete voice-to-action pipeline with conversation context"""
    try:
        # Step 1: Transcribe
        audio_data = await audio.read()
        asr_result = await transcribe_audio(audio_data, language)
        transcript = asr_result.transcript
        
        log_event("voice_command_transcribed", transcript=transcript[:100])
        
        # Step 2: Parse context
        parsed_context = None
        if context:
            try:
                parsed_context = json.loads(context)
            except json.JSONDecodeError:
                log_event("context_parse_error", context=context[:100])
        
        # Step 3: Generate plan
        plan = await llm_plan_from_voice(transcript, parsed_context)
        log_event(
            "voice_command_planned",
            intent_id=plan.intent_id,
            say=plan.say,
            steps=len(plan.steps),
        )
        
        # Step 4: Speak response
        if plan.say:
            log_event("voice_command_speaking", text=plan.say)
            asyncio.create_task(_tts_background(plan.say))
        
        # Step 5: Execute plan steps
        for step in plan.steps:
            try:
                msg_id = await dispatcher.enqueue_command(step)
                log_event("voice_command_step_enqueued", msg_id=msg_id, action=step.get("action"))
            except Exception as exc:
                log_event(
                    "voice_command_enqueue_error",
                    action=step.get("action"),
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                )
        
        return {
            "transcript": transcript,
            "language": asr_result.language,
            "plan": plan.model_dump(),
            "asr_duration_s": asr_result.duration_s
        }
        
    except Exception as exc:
        log_event("voice_command_error", error=type(exc).__name__, detail=str(exc)[:200])
        raise HTTPException(status_code=500, detail=f"Voice command failed: {exc}") from exc


@app.post("/send_command")
async def send_command(cmd: CommandIn):
    msg_id = await dispatcher.enqueue_command(cmd.model_dump(exclude_none=True))
    return {"enqueued_msg_id": msg_id}


@app.get("/status")
def status():
    return {
        "ok": True,
        "clients_connected": len(manager.clients),
        "dispatcher": dispatcher.status(),
        "telemetry_count": len(telemetry_log),
        "events_count": len(event_log),
        "plans_count": len(plans_log),
        "asr_count": len(asr_log),
        "whisper_model": WHISPER_MODEL,
        "whisper_loaded": _whisper_model is not None,
        "tts_engine": "piper",
        "tts_voice": "BT-7274 (Titanfall 2)",
        "tts_loaded": _piper_voice is not None,
        "ollama_model": OLLAMA_MODEL,
    }


@app.get("/logs/events")
def get_events(limit: int = 200):
    return event_log[-limit:]


@app.get("/logs/telemetry")
def get_telemetry(limit: int = 200):
    return telemetry_log[-limit:]


@app.get("/logs/asr")
def get_asr_log(limit: int = 200):
    return asr_log[-limit:]


@app.get("/plans")
def get_plans():
    return plans_log


@app.post("/tts")
async def tts_endpoint(payload: Dict[str, str] = Body(...)):
    text = payload.get("text", "")
    try:
        await tts_say(text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS failure: {exc}") from exc
    return {"ok": True}


__all__ = [
    "app",
    "dispatcher",
    "tts_say",
    "llm_plan_from_voice",
    "transcribe_audio",
    "VoiceIntent",
    "VoicePlan",
    "ASRResult",
]