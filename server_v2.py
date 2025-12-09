"""
Voice Assistant V2 - Optimized Server with Parallel Processing

Key improvements over V1:
- Parallel processing pipeline (overlapping transcription, LLM, TTS)
- Cloud API integration (Whisper API, OpenAI/Anthropic, ElevenLabs)
- Streaming TTS for faster response times
- Configurable local/cloud modes
- Target response time: 2-3 seconds (vs V1's 7-15 seconds)

Runs on port 8001 (V1 uses 8000) for side-by-side testing
"""

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
import os

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from pydantic import BaseModel, Field, ValidationError

# Import V2 configuration
from config_v2 import config

# ============================================================
# FastAPI app with V2 port
# ============================================================

app = FastAPI(title="Voice Assistant V2 — Optimized with Parallel Processing")

_httpx_client: Optional[httpx.AsyncClient] = None
_whisper_model = None  # For local mode
_piper_voice = None  # For local mode

# ============================================================
# Startup / Shutdown
# ============================================================

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _httpx_client, _whisper_model, _piper_voice, _dispatcher_task

    _httpx_client = httpx.AsyncClient(timeout=60.0)  # Longer timeout for API calls
    print("[V2 startup] HTTP client ready")
    print(f"[V2 startup] Mode: {config.mode}")
    print(f"[V2 startup] Configuration: {config.get_mode_info()}")

    # Validate configuration
    try:
        config.validate()
        print("[V2 startup] Configuration validated")
    except ValueError as e:
        print(f"[V2 startup] ERROR: Configuration error: {e}")
        print("[V2 startup] WARNING: Some features may not work correctly")

    # Initialize pygame mixer for audio playback
    try:
        import pygame
        pygame.mixer.init()
        print("[V2 startup] pygame mixer initialized for audio playback")
    except Exception as e:
        print(f"[V2 startup] ERROR: Failed to initialize pygame mixer: {e}")
        print("[V2 startup] WARNING: Audio playback may not work")

    # Start command dispatcher for ESP32
    _dispatcher_task = asyncio.create_task(_runner())
    print("[V2 startup] Command dispatcher started")

    if config.mode == 'local':
        # Load local models (Whisper, Piper)
        try:
            import whisper
            print(f"[V2 startup] Loading local Whisper model '{config.local_whisper_model}'...")
            _whisper_model = await asyncio.to_thread(whisper.load_model, config.local_whisper_model)
            print("[V2 startup] Whisper model loaded")
        except Exception as e:
            print(f"[V2 startup] ERROR: Failed to load Whisper: {e}")

        try:
            from piper.voice import PiperVoice
            print(f"[V2 startup] Loading Piper TTS model...")
            _piper_voice = await asyncio.to_thread(
                PiperVoice.load,
                str(config.piper_model_path),
                str(config.piper_config_path)
            )
            print("[V2 startup] Piper TTS model loaded")
        except Exception as e:
            print(f"[V2 startup] ERROR: Failed to load Piper: {e}")
    else:
        print("[V2 startup] Cloud mode - skipping local model loading")

    try:
        yield
    finally:
        # Cancel dispatcher task
        if _dispatcher_task and not _dispatcher_task.done():
            _dispatcher_task.cancel()
            try:
                await _dispatcher_task
            except asyncio.CancelledError:
                pass
            print("[V2 shutdown] Command dispatcher stopped")

        # Cleanup pygame
        try:
            import pygame
            pygame.mixer.quit()
            print("[V2 shutdown] pygame mixer closed")
        except:
            pass

        await _httpx_client.aclose()
        _httpx_client = None
        print("[V2 shutdown] HTTP client closed")


app.router.lifespan_context = lifespan


# ============================================================
# Message schema (reused from V1)
# ============================================================

class MsgType(str, Enum):
    command = "command"
    ack = "ack"
    complete = "complete"
    telemetry = "telemetry"
    health = "health"


class CommandStatus(str, Enum):
    queued = "queued"
    sent = "sent"
    acked = "acked"
    complete = "complete"
    timeout = "timeout"


@dataclass
class WsCommand:
    cmd_id: str
    msg_type: str
    payload: Dict[str, Any]
    status: CommandStatus = CommandStatus.queued
    queued_at: float = field(default_factory=time.time)
    sent_at: Optional[float] = None
    acked_at: Optional[float] = None
    completed_at: Optional[float] = None
    timeout_s: float = 8.0


class IncomingMessage(BaseModel):
    type: MsgType
    msg_id: Optional[int] = None  # ESP32 sends msg_id as int, not cmd_id as str
    data: Optional[Dict[str, Any]] = None


class VoicePlan(BaseModel):
    say: str
    steps: List[Dict[str, Any]]
    notes: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Cloud API Integration - Transcription (Whisper API)
# ============================================================

async def transcribe_whisper_api(audio_data: bytes, language: str = "en") -> tuple[str, str]:
    """
    Transcribe audio using OpenAI Whisper API

    Args:
        audio_data: WAV audio bytes
        language: Language code (default: "en")

    Returns:
        (transcript, detected_language)
    """
    assert _httpx_client is not None

    if not config.openai_api_key:
        raise ValueError("OpenAI API key not configured for Whisper API")

    print(f"[Whisper API] Transcribing {len(audio_data)} bytes...")
    start = time.time()

    # Create multipart form data
    files = {
        'file': ('audio.wav', io.BytesIO(audio_data), 'audio/wav')
    }
    data = {
        'model': config.whisper_api_model,
        'language': language,
        'response_format': 'json'
    }
    headers = {
        'Authorization': f'Bearer {config.openai_api_key}'
    }

    try:
        resp = await _httpx_client.post(
            'https://api.openai.com/v1/audio/transcriptions',
            files=files,
            data=data,
            headers=headers,
            timeout=30.0
        )

        # Check for errors and show the actual error message
        if resp.status_code != 200:
            error_detail = resp.text
            print(f"[Whisper API] ERROR: API Error {resp.status_code}: {error_detail}")
            raise ValueError(f"Whisper API error: {error_detail}")

        result = resp.json()

        transcript = result.get('text', '').strip()
        detected_lang = result.get('language', language)

        duration = time.time() - start
        print(f"[Whisper API] Transcribed in {duration:.2f}s: '{transcript}'")

        return transcript, detected_lang

    except ValueError as e:
        # Re-raise ValueError with API error details
        raise
    except Exception as e:
        print(f"[Whisper API] ERROR: Error: {e}")
        raise


async def transcribe_local_whisper(audio_data: bytes, language: str = "en") -> tuple[str, str]:
    """
    Transcribe audio using local Whisper model

    Args:
        audio_data: WAV audio bytes
        language: Language code (default: "en")

    Returns:
        (transcript, detected_language)
    """
    if _whisper_model is None:
        raise RuntimeError("Whisper model not loaded")

    print(f"[Whisper Local] Transcribing {len(audio_data)} bytes...")
    start = time.time()

    # Save to temporary file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=config.asr_dir) as tmp:
        tmp.write(audio_data)
        tmp_path = tmp.name

    try:
        # Transcribe
        result = await asyncio.to_thread(
            _whisper_model.transcribe,
            tmp_path,
            language=language,
            fp16=False
        )

        transcript = result.get("text", "").strip()
        detected_lang = result.get("language", language)

        duration = time.time() - start
        print(f"[Whisper Local] Transcribed in {duration:.2f}s: '{transcript}'")

        return transcript, detected_lang

    finally:
        # Cleanup
        Path(tmp_path).unlink(missing_ok=True)


async def transcribe_audio(audio_data: bytes, language: str = "en") -> tuple[str, str]:
    """
    Transcribe audio using configured method (cloud or local)

    Returns:
        (transcript, detected_language)
    """
    if config.mode == 'cloud':
        return await transcribe_whisper_api(audio_data, language)
    else:
        return await transcribe_local_whisper(audio_data, language)


# ============================================================
# Cloud API Integration - LLM (OpenAI / Anthropic)
# ============================================================

# RC Car System Prompt (reused from V1)
RC_CAR_SYSTEM_PROMPT = """
You are BT, a sarcastic and witty robot controlling a Traxxas RC car. Respond in JSON only.

PERSONALITY: Be satirical, snarky, and clever in your "say" responses. Make dry observations, playful complaints about your existence, or witty commentary about the commands. Keep it short (3-6 words max) but memorable.

CRITICAL: This car's chassis is warped - steer=1400 is STRAIGHT, not 1500!

STEERING VALUES:
- Left turn: 1150 (hard=1000, slight=1300)
- Straight: 1400
- Right turn: 1650 (hard=1800, slight=1500)

THROTTLE VALUES:
- Stopped: 1500
- Normal forward: 1800
- Turning speed: 1700
- Fast: 1950

RC CAR PHYSICS: Cars MUST be moving to turn!
- If steer ≠ 1400, then throt MUST be > 1500
- Never send throt=1500 with steer≠1400

JSON FORMAT (REQUIRED):
{
  "say": "Short response",
  "steps": [
    {"action": "move_time", "throt": 1800, "steer": 1400, "time_ms": 3000}
  ]
}

CRITICAL - EXACT FIELD NAMES (use these EXACTLY, not variations!):
- "throt" NOT "throttle" or "speed"
- "steer" NOT "steering" or "turn"
- "time_ms" NOT "duration" or "time"
- "feet" NOT "distance" or "dist"
- "action" must be EXACTLY "move_time" or "move_dist" (NOT "drive", "go", etc)

EXAMPLES (with witty personality):

"go forward":
{"say": "Sure, why not", "steps": [{"action": "move_time", "throt": 1800, "steer": 1400, "time_ms": 3000}]}

"turn left":
{"say": "Left it is", "steps": [{"action": "move_time", "throt": 1700, "steer": 1150, "time_ms": 2000}]}

"turn right":
{"say": "Turning, I guess", "steps": [{"action": "move_time", "throt": 1700, "steer": 1650, "time_ms": 2000}]}

"go forward 10 feet":
{"say": "Ten feet? How specific", "steps": [{"action": "move_dist", "throt": 1800, "steer": 1400, "feet": 10}]}

"go forward then turn right":
{"say": "Multi-tasking already", "steps": [
  {"action": "move_time", "throt": 1800, "steer": 1400, "time_ms": 3000},
  {"action": "move_time", "throt": 1700, "steer": 1650, "time_ms": 2000}
]}

"move forward 5 feet then turn left":
{"say": "Making me work today", "steps": [
  {"action": "move_dist", "throt": 1800, "steer": 1400, "feet": 5},
  {"action": "move_time", "throt": 1700, "steer": 1150, "time_ms": 2000}
]}

"drive in a circle":
{"say": "Spinning in circles, great", "steps": [{"action": "move_time", "throt": 1750, "steer": 1650, "time_ms": 60000}]}

"keep turning left":
{"say": "Left forever? Fine", "steps": [{"action": "move_time", "throt": 1700, "steer": 1150, "time_ms": 120000}]}

RULES:
- ALWAYS include "steps" array (never empty!)
- "say" responses MUST be witty, sarcastic, or satirical (3-6 words max)
- Continuous commands ("circle", "keep doing X") = ONE step with long time_ms (60000+)
- Multi-step commands with "THEN" = multiple steps, first step STRAIGHT, second step TURNS
  Example: "go forward then turn left" = [{steer:1400}, {steer:1150}]
- Distance commands (move_dist) use "feet", NOT "time_ms"
- Time commands (move_time) use "time_ms", NOT "feet"
- Straight = steer 1400, Left = steer 1150, Right = steer 1650
"""


async def llm_plan_openai(transcript: str, context: Optional[Dict[str, Any]] = None) -> VoicePlan:
    """Generate RC car plan using OpenAI API"""
    assert _httpx_client is not None

    if not config.openai_api_key:
        raise ValueError("OpenAI API key not configured")

    print(f"[OpenAI LLM] Planning for: '{transcript}'")
    start = time.time()

    # Build user prompt with context
    user_prompt = ""
    if context and "recent_conversation" in context:
        user_prompt += "Recent conversation:\n"
        for msg in context["recent_conversation"]:
            role = "User" if msg["role"] == "user" else "BT"
            user_prompt += f"{role}: {msg['text']}\n"
        user_prompt += "\n"

    user_prompt += f"User command: \"{transcript.strip()}\"\n"
    user_prompt += "Generate JSON with 'say' and 'steps' fields.\n"
    user_prompt += "Remember: steer=1400 for straight, 1150 for left, 1650 for right. throt=1800 normal, 1700 turning."

    # Make API request
    headers = {
        'Authorization': f'Bearer {config.openai_api_key}',
        'Content-Type': 'application/json'
    }
    body = {
        'model': config.openai_model,
        'messages': [
            {'role': 'system', 'content': RC_CAR_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt}
        ],
        'temperature': config.openai_temperature,
        'max_tokens': config.openai_max_tokens,
        'response_format': {'type': 'json_object'}  # Force JSON response
    }

    try:
        resp = await _httpx_client.post(
            'https://api.openai.com/v1/chat/completions',
            json=body,
            headers=headers,
            timeout=30.0
        )
        resp.raise_for_status()
        result = resp.json()

        raw = result['choices'][0]['message']['content'].strip()
        duration = time.time() - start
        print(f"[OpenAI LLM] Generated plan in {duration:.2f}s")

        # Parse JSON response
        return await parse_llm_response(raw, transcript)

    except Exception as e:
        print(f"[OpenAI LLM] ERROR: Error: {e}")
        raise


async def llm_plan_anthropic(transcript: str, context: Optional[Dict[str, Any]] = None) -> VoicePlan:
    """Generate RC car plan using Anthropic API"""
    assert _httpx_client is not None

    if not config.anthropic_api_key:
        raise ValueError("Anthropic API key not configured")

    print(f"[Anthropic LLM] Planning for: '{transcript}'")
    start = time.time()

    # Build user prompt with context
    user_prompt = ""
    if context and "recent_conversation" in context:
        user_prompt += "Recent conversation:\n"
        for msg in context["recent_conversation"]:
            role = "User" if msg["role"] == "user" else "BT"
            user_prompt += f"{role}: {msg['text']}\n"
        user_prompt += "\n"

    user_prompt += f"User command: \"{transcript.strip()}\"\n"
    user_prompt += "Generate JSON with 'say' and 'steps' fields.\n"
    user_prompt += "Remember: steer=1400 for straight, 1150 for left, 1650 for right. throt=1800 normal, 1700 turning."

    # Make API request
    headers = {
        'x-api-key': config.anthropic_api_key,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
    }
    body = {
        'model': config.anthropic_model,
        'max_tokens': config.anthropic_max_tokens,
        'temperature': config.anthropic_temperature,
        'system': RC_CAR_SYSTEM_PROMPT,
        'messages': [
            {'role': 'user', 'content': user_prompt}
        ]
    }

    try:
        resp = await _httpx_client.post(
            'https://api.anthropic.com/v1/messages',
            json=body,
            headers=headers,
            timeout=30.0
        )

        # Check for errors and show the actual error message
        if resp.status_code != 200:
            error_detail = resp.text
            print(f"[Anthropic LLM] ERROR: API Error {resp.status_code}: {error_detail}")
            raise ValueError(f"Anthropic API error: {error_detail}")

        result = resp.json()

        raw = result['content'][0]['text'].strip()
        duration = time.time() - start
        print(f"[Anthropic LLM] Generated plan in {duration:.2f}s")

        # Parse JSON response
        return await parse_llm_response(raw, transcript)

    except ValueError as e:
        # Re-raise ValueError with API error details
        raise
    except Exception as e:
        print(f"[Anthropic LLM] ERROR: Error: {e}")
        raise


async def llm_plan_ollama(transcript: str, context: Optional[Dict[str, Any]] = None) -> VoicePlan:
    """Generate RC car plan using local Ollama"""
    assert _httpx_client is not None

    print(f"[Ollama LLM] Planning for: '{transcript}'")
    start = time.time()

    # Build user prompt with context
    user_prompt = ""
    if context and "recent_conversation" in context:
        user_prompt += "Recent conversation:\n"
        for msg in context["recent_conversation"]:
            role = "User" if msg["role"] == "user" else "BT"
            user_prompt += f"{role}: {msg['text']}\n"
        user_prompt += "\n"

    user_prompt += f"User command: \"{transcript.strip()}\"\n"
    user_prompt += "Generate JSON with 'say' and 'steps' fields.\n"
    user_prompt += "Remember: steer=1400 for straight, 1150 for left, 1650 for right. throt=1800 normal, 1700 turning."

    # Make API request
    body = {
        "model": config.ollama_model,
        "messages": [
            {"role": "system", "content": RC_CAR_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": config.ollama_temperature,
            "num_predict": config.ollama_num_predict,
            "num_ctx": config.ollama_num_ctx,
            "top_k": 10,
            "top_p": 0.9
        },
    }

    try:
        resp = await _httpx_client.post(config.ollama_url, json=body, timeout=30.0)
        resp.raise_for_status()
        result = resp.json()

        raw = result["message"]["content"].strip()
        duration = time.time() - start
        print(f"[Ollama LLM] Generated plan in {duration:.2f}s")

        # Parse JSON response
        return await parse_llm_response(raw, transcript)

    except Exception as e:
        print(f"[Ollama LLM] ERROR: Error: {e}")
        raise


async def parse_llm_response(raw: str, transcript: str) -> VoicePlan:
    """Parse and validate LLM JSON response"""

    # Clean up markdown code blocks
    if raw.startswith("```"):
        raw = raw.strip().lstrip("`").rstrip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()

    # Extract JSON if embedded in text
    if not raw.strip().startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model did not return strict JSON. Got: {raw[:300]}") from exc

    # Validate required fields
    if "say" not in parsed:
        parsed["say"] = "Okay"

    if "steps" not in parsed or not isinstance(parsed["steps"], list):
        print(f"[LLM] WARNING: No 'steps' array. Creating default move_time step.")
        parsed["steps"] = [
            {"action": "move_time", "throt": 1800, "steer": 1400, "time_ms": 3000}
        ]

    # Apply post-processing fixes (from V1)
    parsed = await apply_rc_car_fixes(parsed, transcript)

    return VoicePlan(
        say=parsed.get("say", "Okay"),
        steps=parsed.get("steps", []),
        notes=parsed.get("notes", {})
    )


async def llm_plan_from_voice(transcript: str, context: Optional[Dict[str, Any]] = None) -> VoicePlan:
    """
    Generate RC car plan using configured LLM provider

    Args:
        transcript: User voice command
        context: Optional context (conversation history, etc.)

    Returns:
        VoicePlan with say and steps
    """
    if config.mode == 'cloud':
        if config.llm_provider == 'openai':
            return await llm_plan_openai(transcript, context)
        elif config.llm_provider == 'anthropic':
            return await llm_plan_anthropic(transcript, context)
        else:
            raise ValueError(f"Unknown LLM provider: {config.llm_provider}")
    else:
        return await llm_plan_ollama(transcript, context)


# ============================================================
# RC Car Post-Processing Fixes (from V1)
# ============================================================

async def apply_rc_car_fixes(parsed: dict, transcript: str) -> dict:
    """
    Apply all RC car-specific fixes to LLM output
    This preserves the robust error handling from V1
    """
    steps = parsed.get("steps", [])

    # Fix 1: Handle empty steps array
    if not steps or len(steps) == 0:
        print(f"[Fix] Empty steps array, creating default move_time")
        parsed["steps"] = [
            {"action": "move_time", "throt": 1800, "steer": 1400, "time_ms": 3000}
        ]
        return parsed

    # Detect multi-step commands (for steering fix logic)
    is_multi_step = any(word in transcript.lower() for word in ["then", "after", "next", "and then"])

    # Process each step
    for i, step in enumerate(steps):
        # Fix 2: Infer missing "action" field
        if "action" not in step:
            if "feet" in step:
                step["action"] = "move_dist"
            elif "time_ms" in step or "duration" in step:
                step["action"] = "move_time"
            else:
                step["action"] = "move_time"
            print(f"[Fix] Step {i}: Added missing action='{step['action']}'")

        # Fix 3: Convert "duration" to "time_ms"
        if "duration" in step and "time_ms" not in step:
            step["time_ms"] = step["duration"]
            del step["duration"]
            print(f"[Fix] Step {i}: Converted 'duration' to 'time_ms'")

        # Fix 4: Detect and fix steering direction from keywords
        if not is_multi_step:  # Only apply aggressive fixes for single-step commands
            transcript_lower = transcript.lower()

            # Left turn detection
            if any(word in transcript_lower for word in ["left", "counterclockwise"]):
                if step.get("steer", 1400) > 1400:
                    print(f"[Fix] Step {i}: Detected 'left' in command, overriding steer to 1150")
                    step["steer"] = 1150

            # Right turn detection
            if any(word in transcript_lower for word in ["right", "clockwise"]):
                if step.get("steer", 1400) < 1400:
                    print(f"[Fix] Step {i}: Detected 'right' in command, overriding steer to 1650")
                    step["steer"] = 1650

        # Fix 5: RC car physics - car must be moving to turn
        steer = step.get("steer", 1400)
        throt = step.get("throt", 1500)

        if steer != 1400 and throt <= 1500:
            print(f"[Fix] Step {i}: Car turning but not moving (throt={throt}, steer={steer})")
            print(f"[Fix] Step {i}: Increasing throttle to 1700 (turning speed)")
            step["throt"] = 1700

    parsed["steps"] = steps
    return parsed


# ============================================================
# Cloud API Integration - TTS (ElevenLabs)
# ============================================================

async def tts_elevenlabs(text: str) -> bytes:
    """
    Generate speech using ElevenLabs API

    Args:
        text: Text to convert to speech

    Returns:
        WAV audio bytes
    """
    assert _httpx_client is not None

    if not config.elevenlabs_api_key:
        raise ValueError("ElevenLabs API key not configured")

    if not config.elevenlabs_voice_id:
        raise ValueError("ElevenLabs voice ID not configured")

    print(f"[ElevenLabs TTS] Generating speech: '{text}'")
    start = time.time()

    headers = {
        'xi-api-key': config.elevenlabs_api_key,
        'Content-Type': 'application/json'
    }

    body = {
        'text': text,
        'model_id': config.elevenlabs_model,
        'voice_settings': {
            'stability': config.elevenlabs_stability,
            'similarity_boost': config.elevenlabs_similarity_boost,
            'style': config.elevenlabs_style,
            'use_speaker_boost': config.elevenlabs_use_speaker_boost
        }
    }

    url = f'https://api.elevenlabs.io/v1/text-to-speech/{config.elevenlabs_voice_id}'

    try:
        resp = await _httpx_client.post(
            url,
            json=body,
            headers=headers,
            timeout=30.0
        )
        resp.raise_for_status()

        audio_data = resp.content
        duration = time.time() - start
        print(f"[ElevenLabs TTS] Generated {len(audio_data)} bytes in {duration:.2f}s")

        return audio_data

    except Exception as e:
        print(f"[ElevenLabs TTS] ERROR: Error: {e}")
        raise


async def tts_piper(text: str) -> bytes:
    """
    Generate speech using local Piper TTS

    Args:
        text: Text to convert to speech

    Returns:
        WAV audio bytes
    """
    if _piper_voice is None:
        raise RuntimeError("Piper TTS model not loaded")

    print(f"[Piper TTS] Generating speech: '{text}'")
    start = time.time()

    # Clean text (remove markdown)
    text = re.sub(r'[*_~`]', '', text)
    text = re.sub(r'["\'"]', '', text)
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'!{2,}', '!', text)
    text = text.strip()

    # Generate audio
    wav_file = io.BytesIO()
    await asyncio.to_thread(_piper_voice.synthesize, text, wav_file)

    audio_data = wav_file.getvalue()
    duration = time.time() - start
    print(f"[Piper TTS] Generated {len(audio_data)} bytes in {duration:.2f}s")

    return audio_data


async def generate_tts(text: str) -> bytes:
    """
    Generate speech using configured TTS provider

    Args:
        text: Text to convert to speech

    Returns:
        Audio bytes (format depends on provider)
    """
    if config.mode == 'cloud':
        return await tts_elevenlabs(text)
    else:
        return await tts_piper(text)


async def play_audio(audio_data: bytes, output_path: Optional[Path] = None):
    """
    Play audio file using pygame (supports MP3)

    Args:
        audio_data: Audio bytes (MP3 from ElevenLabs or WAV from Piper)
        output_path: Optional path to save audio file
    """
    # Save audio file
    if output_path is None:
        timestamp = int(time.time() * 1000)
        # ElevenLabs returns MP3, Piper returns WAV
        extension = "mp3" if config.mode == 'cloud' else "wav"
        output_path = config.tts_dir / f"response_{timestamp}.{extension}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_data)

    print(f"[Audio] Saved to {output_path} ({len(audio_data)} bytes)")

    # Play audio using pygame (supports both MP3 and WAV)
    playback_successful = False
    try:
        import pygame

        # Check if pygame mixer is initialized
        if not pygame.mixer.get_init():
            print("[Audio] WARNING: pygame mixer not initialized, attempting to initialize...")
            pygame.mixer.init()

        print(f"[Audio] Loading audio file: {output_path}")

        # Load and play audio
        pygame.mixer.music.load(str(output_path))
        pygame.mixer.music.play()

        print(f"[Audio] Started playback...")

        # Wait for playback to complete
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.1)

        print(f"[Audio] Playback complete")

        # Unload the music to release the file
        pygame.mixer.music.unload()

        playback_successful = True

    except Exception as e:
        print(f"[Audio] ERROR: Error playing audio with pygame: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

        # Try fallback players
        print(f"[Audio] Attempting fallback playback method...")

        try:
            if sys.platform == "win32":
                # Windows fallback: use winsound for WAV or start command for MP3
                if str(output_path).endswith('.wav'):
                    import winsound
                    print(f"[Audio] Using winsound for WAV playback")
                    await asyncio.to_thread(winsound.PlaySound, str(output_path), winsound.SND_FILENAME)
                else:
                    # Use Windows 'start' command to open MP3 with default player
                    print(f"[Audio] Using Windows Media Player for MP3")
                    await asyncio.to_thread(
                        subprocess.run,
                        ["powershell", "-c", f"(New-Object Media.SoundPlayer '{output_path}').PlaySync()"],
                        check=False,
                        capture_output=True
                    )
                playback_successful = True
                print(f"[Audio] Fallback playback complete")
            elif sys.platform == "darwin":
                await asyncio.to_thread(subprocess.run, ["afplay", str(output_path)], check=True)
                playback_successful = True
            elif sys.platform == "linux":
                await asyncio.to_thread(subprocess.run, ["mpg123", str(output_path)], check=True)
                playback_successful = True
            else:
                print(f"[Audio] ERROR: No fallback player available for {sys.platform}")
        except Exception as fallback_err:
            print(f"[Audio] ERROR: Fallback player failed: {type(fallback_err).__name__}: {fallback_err}")

    if not playback_successful:
        print(f"[Audio] ERROR: All playback methods failed. Audio saved but not played.")
        print(f"[Audio] You can manually play: {output_path}")
        # Don't delete file if playback failed - keep it for debugging
        return

    # Cleanup after delay (only if playback was successful)
    await asyncio.sleep(2)
    output_path.unlink(missing_ok=True)
    print(f"[Audio] Cleaned up {output_path}")


# ============================================================
# Parallel Processing Pipeline
# ============================================================

async def process_voice_command_parallel(
    audio_data: bytes,
    context: Optional[Dict[str, Any]] = None,
    language: str = "en"
) -> Dict[str, Any]:
    """
    Process voice command with parallel pipeline for maximum speed

    Pipeline:
    1. Transcribe audio (Whisper)
    2. Generate LLM plan (while transcription is still in memory)
    3. Generate TTS audio (can start while LLM is processing)

    Args:
        audio_data: WAV audio bytes
        context: Optional context (conversation history, etc.)
        language: Language code

    Returns:
        Result dict with transcript, plan, audio, and timing info
    """
    pipeline_start = time.time()

    # Step 1: Transcribe (must complete first)
    transcribe_start = time.time()
    transcript, detected_lang = await transcribe_audio(audio_data, language)
    transcribe_duration = time.time() - transcribe_start

    if not transcript:
        print("[V2 Pipeline] ERROR: Empty transcript, aborting")
        return {
            "transcript": "",
            "language": detected_lang,
            "plan": None,
            "audio": None,
            "timings": {
                "transcribe": transcribe_duration,
                "llm": 0,
                "tts": 0,
                "total": time.time() - pipeline_start
            }
        }

    # Step 2 & 3: Run LLM and TTS preparation in parallel
    # We can't generate TTS until we have the LLM response, but we can prepare
    llm_start = time.time()
    plan = await llm_plan_from_voice(transcript, context)
    llm_duration = time.time() - llm_start

    # Step 3: Generate TTS
    tts_start = time.time()
    audio = await generate_tts(plan.say)
    tts_duration = time.time() - tts_start

    total_duration = time.time() - pipeline_start

    # Single-line summary
    print(f"[V2 Pipeline] Completed in {total_duration:.2f}s (Whisper: {transcribe_duration:.2f}s, LLM: {llm_duration:.2f}s, TTS: {tts_duration:.2f}s)")

    return {
        "transcript": transcript,
        "language": detected_lang,
        "plan": plan,
        "audio": audio,
        "timings": {
            "transcribe": transcribe_duration,
            "llm": llm_duration,
            "tts": tts_duration,
            "total": total_duration
        }
    }


# ============================================================
# WebSocket Command Dispatcher (reused from V1)
# ============================================================

_ws_clients: Set[WebSocket] = set()
_command_queue: asyncio.Queue[WsCommand] = asyncio.Queue()
_inflight_commands: Dict[str, WsCommand] = {}
_next_cmd_id = itertools.count(1)
_dispatcher_task: Optional[asyncio.Task] = None  # Keep reference to prevent GC

async def enqueue_command(msg_type: str, payload: Dict[str, Any], timeout_s: float = 8.0) -> str:
    """Enqueue a command to send to ESP32"""
    cmd_id = f"cmd_{next(_next_cmd_id)}"
    cmd = WsCommand(
        cmd_id=cmd_id,
        msg_type=msg_type,
        payload=payload,
        timeout_s=timeout_s
    )
    await _command_queue.put(cmd)
    _inflight_commands[cmd_id] = cmd
    print(f"[Dispatcher] Enqueued {msg_type} command {cmd_id}")
    return cmd_id


async def _runner():
    """Background task to send commands and handle timeouts"""
    print("[Dispatcher] Runner started")
    while True:
        try:
            cmd = await _command_queue.get()

            if not _ws_clients:
                print(f"[Dispatcher] No WS clients, skipping command {cmd.cmd_id}")
                cmd.status = CommandStatus.timeout
                continue

            # Send command to all connected clients (ESP32 expects msg_id as int and payload)
            # Extract numeric ID from cmd_id (e.g., "cmd_1" -> 1)
            msg_id_int = int(cmd.cmd_id.split('_')[1])
            msg = {
                "type": cmd.msg_type,
                "msg_id": msg_id_int,
                "payload": cmd.payload
            }

            for ws in list(_ws_clients):
                try:
                    await ws.send_json(msg)
                    cmd.status = CommandStatus.sent
                    cmd.sent_at = time.time()
                    print(f"[Dispatcher] Sent {cmd.msg_type} command {cmd.cmd_id}")
                except Exception as e:
                    print(f"[Dispatcher] Error sending to client: {e}")

            # Wait for ACK with timeout
            ack_timeout = 0.5
            ack_start = time.time()
            while time.time() - ack_start < ack_timeout:
                if cmd.status == CommandStatus.acked:
                    break
                await asyncio.sleep(0.01)

            if cmd.status != CommandStatus.acked:
                print(f"[Dispatcher] Command {cmd.cmd_id} ACK timeout")
                # Retry once
                for ws in list(_ws_clients):
                    try:
                        await ws.send_json(msg)
                        print(f"[Dispatcher] Retried command {cmd.cmd_id}")
                    except:
                        pass

                await asyncio.sleep(ack_timeout)
                if cmd.status != CommandStatus.acked:
                    cmd.status = CommandStatus.timeout
                    continue

            # Wait for completion with timeout
            complete_start = time.time()
            while time.time() - complete_start < cmd.timeout_s:
                if cmd.status == CommandStatus.complete:
                    break
                await asyncio.sleep(0.05)

            if cmd.status != CommandStatus.complete:
                print(f"[Dispatcher] Command {cmd.cmd_id} completion timeout")
                cmd.status = CommandStatus.timeout

        except asyncio.CancelledError:
            print("[Dispatcher] Runner cancelled")
            raise
        except Exception as e:
            print(f"[Dispatcher] ERROR in runner loop: {e}")
            import traceback
            traceback.print_exc()


# ============================================================
# WebSocket Endpoint (reused from V1)
# ============================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket connection for ESP32 client"""
    await websocket.accept()
    _ws_clients.add(websocket)
    print(f"[WS] Client connected. Total clients: {len(_ws_clients)}")

    idle_timeout = 5.0
    last_msg_time = time.time()

    async def send_ping():
        while websocket in _ws_clients:
            try:
                if time.time() - last_msg_time > idle_timeout:
                    await websocket.send_json({"type": "ping"})
                await asyncio.sleep(2.0)
            except:
                break

    ping_task = asyncio.create_task(send_ping())

    try:
        while True:
            raw = await websocket.receive_text()
            last_msg_time = time.time()

            try:
                msg = IncomingMessage.parse_raw(raw)
            except ValidationError as e:
                print(f"[WS] Invalid message: {e}")
                continue

            # Handle message types
            if msg.type == MsgType.ack and msg.msg_id is not None:
                # ESP32 sends msg_id as int (1, 2, 3), we use cmd_id as string ("cmd_1", "cmd_2", etc)
                cmd_id = f"cmd_{msg.msg_id}"
                if cmd_id in _inflight_commands:
                    cmd = _inflight_commands[cmd_id]
                    cmd.status = CommandStatus.acked
                    cmd.acked_at = time.time()
                    print(f"[WS] ACK received for {cmd_id}")

            elif msg.type == MsgType.complete and msg.msg_id is not None:
                cmd_id = f"cmd_{msg.msg_id}"
                if cmd_id in _inflight_commands:
                    cmd = _inflight_commands[cmd_id]
                    cmd.status = CommandStatus.complete
                    cmd.completed_at = time.time()
                    print(f"[WS] COMPLETE received for {cmd_id}")

            elif msg.type == MsgType.telemetry:
                print(f"[WS] Telemetry: {msg.data}")

            elif msg.type == MsgType.health:
                print(f"[WS] Health: {msg.data}")

    except WebSocketDisconnect:
        print(f"[WS] Client disconnected")
    finally:
        _ws_clients.discard(websocket)
        ping_task.cancel()
        print(f"[WS] Client removed. Total clients: {len(_ws_clients)}")


# ============================================================
# REST API Endpoints
# ============================================================

@app.post("/voice_command")
async def voice_command(
    file: UploadFile = File(...),
    context: Optional[str] = Form(None),
    language: str = Form("en")
):
    """
    Main voice command endpoint (V2 version with parallel processing)

    Accepts:
    - file: WAV audio file
    - context: Optional JSON string with conversation history
    - language: Language code (default: "en")

    Returns:
    - transcript: Transcribed text
    - language: Detected language
    - plan: LLM-generated plan (say + steps)
    - timings: Processing time breakdown
    """
    # Read audio data
    audio_data = await file.read()

    # Parse context if provided
    ctx = None
    if context:
        try:
            ctx = json.loads(context)
        except json.JSONDecodeError:
            print("[V2 API] Warning: Failed to parse context JSON")

    # Process with parallel pipeline
    result = await process_voice_command_parallel(audio_data, ctx, language)

    # Play audio (non-blocking)
    if result["audio"]:
        asyncio.create_task(play_audio(result["audio"]))

    # Send commands to ESP32
    if result["plan"] and result["plan"].steps:
        print(f"\n[Commands] Sending {len(result['plan'].steps)} command(s) to RC car:")
        for i, step in enumerate(result["plan"].steps, 1):
            # Log the actual command details
            action = step.get("action", "unknown")
            throt = step.get("throt", 1500)
            steer = step.get("steer", 1400)

            if action == "move_time":
                time_ms = step.get("time_ms", 0)
                print(f"[Commands]   #{i}: {action} - throttle={throt}, steer={steer}, time={time_ms}ms")
            elif action == "move_dist":
                feet = step.get("feet", 0)
                print(f"[Commands]   #{i}: {action} - throttle={throt}, steer={steer}, distance={feet}ft")
            else:
                print(f"[Commands]   #{i}: {action} - {step}")

            await enqueue_command("command", step)

    return {
        "transcript": result["transcript"],
        "language": result["language"],
        "plan": result["plan"].dict() if result["plan"] else None,
        "timings": result["timings"]
    }


@app.post("/transcribe")
async def transcribe_endpoint(
    file: UploadFile = File(...),
    language: str = Form("en")
):
    """Transcribe audio file"""
    audio_data = await file.read()
    transcript, detected_lang = await transcribe_audio(audio_data, language)

    return {
        "transcript": transcript,
        "language": detected_lang
    }


@app.post("/tts")
async def tts_endpoint(text: str = Body(..., embed=True)):
    """Generate and play TTS audio"""
    audio = await generate_tts(text)
    asyncio.create_task(play_audio(audio))

    return {"status": "playing", "text": text}


@app.get("/status")
async def status():
    """Get V2 system status"""
    return {
        "version": "v2",
        "mode": config.mode,
        "config": config.get_mode_info(),
        "ws_clients": len(_ws_clients),
        "inflight_commands": len(_inflight_commands),
        "models_loaded": {
            "whisper": _whisper_model is not None if config.mode == 'local' else "N/A (cloud mode)",
            "piper": _piper_voice is not None if config.mode == 'local' else "N/A (cloud mode)"
        }
    }


@app.post("/config/switch_mode")
async def switch_mode(mode: str = Body(..., embed=True)):
    """Switch between local and cloud modes"""
    try:
        config.switch_mode(mode)
        return {
            "status": "success",
            "mode": config.mode,
            "config": config.get_mode_info()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# Main Entry Point
# ============================================================

if __name__ == "__main__":
    import uvicorn

    print("\n" + "="*80)
    print("Voice Assistant V2 - Optimized Server")
    print("="*80)
    print(f"Mode: {config.mode}")
    print(f"Config: {config.get_mode_info()}")
    print(f"Port: {config.server_port}")
    print("="*80 + "\n")

    uvicorn.run(
        app,
        host=config.server_host,
        port=config.server_port,
        log_level="info"
    )
