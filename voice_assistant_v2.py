"""
Voice Assistant V2 - Client with Enhanced Push-to-Talk

Key improvements over V1:
- Enhanced push-to-talk control (manual voice activation)
- Connects to V2 server (port 8001) for parallel processing
- Simplified interface focused on controlled input
- Better visual feedback during recording
- Conversation history management

Controls:
- Hold 'V' key: Push-to-talk recording
- Press 'Q': Quit application
- Press 'M': Toggle mute/unmute
- Press 'C': Clear conversation history
"""

import pyaudio
import wave
import requests
import webrtcvad
import json
import time
import keyboard
import threading
from pathlib import Path
import os
import io
from typing import Optional

from config_v2 import config

# Server configuration
SERVER_URL = f"http://localhost:{config.server_port}"

# Audio directory
AUDIO_DIR = config.tts_dir
AUDIO_DIR.mkdir(exist_ok=True)


class VoiceAssistantV2:
    """Voice Assistant V2 Client with Push-to-Talk"""

    def __init__(self):
        print("[V2 INIT] Initializing Voice Assistant V2...")

        # PyAudio setup
        self.pa = pyaudio.PyAudio()
        self.sample_rate = config.sample_rate
        self.chunk_size = 320  # 20ms at 16kHz

        # VAD setup
        self.vad = webrtcvad.Vad(3)  # Aggressive mode

        # State
        self.recording = False
        self.muted = False
        self.conversation_history = []
        self.last_short_recording = 0  # Timestamp of last failed short recording
        self.last_handler_call = 0  # Timestamp of last handler invocation (for debouncing)

        # Visual feedback
        self.recording_thread = None
        self.stop_recording_animation = False

        print("[V2 INIT] Voice Assistant V2 ready")
        print(f"[V2 INIT] Server: {SERVER_URL}")
        print(f"[V2 INIT] Audio directory: {AUDIO_DIR}")

    def is_server_running(self) -> bool:
        """Check if V2 server is running"""
        try:
            resp = requests.get(f"{SERVER_URL}/status", timeout=2)
            return resp.status_code == 200
        except:
            return False

    def add_to_conversation(self, role: str, text: str):
        """Add message to conversation history"""
        self.conversation_history.append({
            "role": role,
            "text": text,
            "timestamp": time.time()
        })
        # Keep last 20 messages
        if len(self.conversation_history) > config.max_conversation_history:
            self.conversation_history = self.conversation_history[-config.max_conversation_history:]

    def get_conversation_context(self) -> Optional[dict]:
        """Format conversation history for server"""
        if not self.conversation_history:
            return None

        return {
            "recent_conversation": [
                {"role": msg["role"], "text": msg["text"]}
                for msg in self.conversation_history[-10:]  # Last 10 messages
            ]
        }

    def clear_conversation_history(self):
        """Clear conversation history"""
        self.conversation_history = []
        print("[V2] Conversation history cleared")

    def record_audio(self, duration_seconds: Optional[float] = None) -> bytes:
        """
        Record audio from microphone

        Args:
            duration_seconds: Maximum recording duration (None for VAD-based)

        Returns:
            WAV audio bytes
        """
        print("[V2] Recording... (release 'V' to stop)")

        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size
        )

        frames = []
        start_time = time.time()

        # Start recording animation
        self.stop_recording_animation = False
        self.recording_thread = threading.Thread(target=self._recording_animation)
        self.recording_thread.daemon = True
        self.recording_thread.start()

        try:
            if duration_seconds:
                # Fixed duration recording
                num_chunks = int(self.sample_rate / self.chunk_size * duration_seconds)
                for _ in range(num_chunks):
                    if not keyboard.is_pressed('v'):
                        break
                    chunk = stream.read(self.chunk_size, exception_on_overflow=False)
                    frames.append(chunk)
            else:
                # VAD-based recording (release 'V' to stop)
                while keyboard.is_pressed('v'):
                    chunk = stream.read(self.chunk_size, exception_on_overflow=False)
                    frames.append(chunk)

                    # Stop if too long (30 seconds max)
                    if time.time() - start_time > 30:
                        print("\n[V2] WARNING: Maximum recording duration reached (30s)")
                        break

        finally:
            stream.stop_stream()
            stream.close()
            self.stop_recording_animation = True
            if self.recording_thread:
                self.recording_thread.join(timeout=0.5)

        duration = time.time() - start_time
        print(f"\n[V2] Recorded {duration:.1f}s")

        # Convert frames to WAV bytes
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(self.pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(frames))

        return wav_buffer.getvalue()

    def _recording_animation(self):
        """Show recording animation"""
        animation = [".", ".", "."]
        idx = 0
        while not self.stop_recording_animation:
            print(f"\rRecording{''.join(animation)} ", end='', flush=True)
            animation[idx % 3] = " " if animation[idx % 3] == "." else "."
            idx += 1
            time.sleep(0.3)

    def send_voice_command(self, audio_data: bytes) -> Optional[dict]:
        """
        Send voice command to V2 server

        Args:
            audio_data: WAV audio bytes

        Returns:
            Response dict with transcript, plan, timings
        """
        print("[V2] Sending to server...")

        try:
            # Prepare multipart form data
            files = {
                'file': ('audio.wav', io.BytesIO(audio_data), 'audio/wav')
            }

            # Add conversation context
            data = {}
            context = self.get_conversation_context()
            if context:
                data['context'] = json.dumps(context)

            # Send request
            resp = requests.post(
                f"{SERVER_URL}/voice_command",
                files=files,
                data=data,
                timeout=60
            )

            if resp.status_code != 200:
                print(f"[V2] ERROR: Server error: {resp.status_code}")
                return None

            result = resp.json()

            # Display results
            transcript = result.get('transcript', '')
            plan = result.get('plan', {})
            timings = result.get('timings', {})

            print(f"\n[V2] \"{transcript}\" -> \"{plan.get('say', '')}\" ({timings.get('total', 0):.2f}s total)")
            if plan and plan.get('steps'):
                print(f"[V2] Sending {len(plan.get('steps', []))} command(s) to RC car\n")

            # Update conversation history
            if transcript:
                self.add_to_conversation("user", transcript)
            if plan and plan.get('say'):
                self.add_to_conversation("assistant", plan['say'])

            return result

        except requests.exceptions.Timeout:
            print("[V2] ERROR: Request timeout")
            return None
        except Exception as e:
            print(f"[V2] ERROR: Error: {e}")
            return None

    def handle_push_to_talk(self):
        """Handle push-to-talk recording and command sending"""
        # Debounce check to prevent keyboard repeat spam
        DEBOUNCE_PERIOD = 0.5  # 500ms minimum between handler calls
        time_since_last_call = time.time() - self.last_handler_call

        if time_since_last_call < DEBOUNCE_PERIOD:
            # Silent debounce - ignore rapid keyboard repeat events
            return

        self.last_handler_call = time.time()

        # Cooldown check to prevent spam from accidental key presses
        COOLDOWN_PERIOD = 2.0  # 2 seconds cooldown after failed recording
        time_since_last_short = time.time() - self.last_short_recording

        if 0 < time_since_last_short < COOLDOWN_PERIOD:
            # Silent cooldown - don't even show a message
            return

        if self.muted:
            print("[V2] Muted - unmute with 'M' key")
            return

        if self.recording:
            return  # Already recording, silently ignore

        try:
            self.recording = True
            record_start = time.time()

            # Record audio
            audio_data = self.record_audio()

            # Calculate recording duration
            record_duration = time.time() - record_start

            # Minimum recording length check (prevent accidental taps)
            MIN_RECORDING_DURATION = 0.3  # 300ms minimum
            if record_duration < MIN_RECORDING_DURATION:
                self.last_short_recording = time.time()
                print(f"[V2] Recording too short ({record_duration:.2f}s) - Hold 'V' longer to speak")
                return

            # Check audio data size (WAV header is 44 bytes, need actual audio data)
            MIN_AUDIO_SIZE = 1000  # At least 1KB of data (includes header + audio)
            if len(audio_data) < MIN_AUDIO_SIZE:
                self.last_short_recording = time.time()
                print(f"[V2] Audio too small ({len(audio_data)} bytes) - Hold 'V' and speak clearly")
                return

            # Send to server
            result = self.send_voice_command(audio_data)

            if result:
                # Audio will be played by server
                pass
            else:
                print("[V2] ERROR: No response from server")

        finally:
            self.recording = False

    def show_status(self):
        """Display current status"""
        print("\n" + "="*60)
        print("Voice Assistant V2 - Status")
        print("="*60)
        print(f"Server:       {SERVER_URL}")
        print(f"Connected:    {'✓' if self.is_server_running() else '✗'}")
        print(f"Muted:        {'✓' if self.muted else '✗'}")
        print(f"Recording:    {'✓' if self.recording else '✗'}")
        print(f"History:      {len(self.conversation_history)} messages")
        print("="*60)

        # Show server status
        try:
            resp = requests.get(f"{SERVER_URL}/status", timeout=2)
            if resp.status_code == 200:
                status = resp.json()
                print(f"\nServer Status:")
                print(f"  Version:      {status.get('version', 'unknown')}")
                print(f"  Mode:         {status.get('mode', 'unknown')}")
                print(f"  WS Clients:   {status.get('ws_clients', 0)}")
                print(f"  Commands:     {status.get('inflight_commands', 0)} inflight")
        except:
            print("\nERROR: Could not fetch server status")

        print("="*60 + "\n")

    def show_help(self):
        """Display help information"""
        print("\n" + "="*60)
        print("Voice Assistant V2 - Help")
        print("="*60)
        print("\nControls:")
        print("  Hold 'V':     Push-to-talk (record voice command)")
        print("  Press 'M':    Toggle mute/unmute")
        print("  Press 'C':    Clear conversation history")
        print("  Press 'S':    Show status")
        print("  Press 'H':    Show this help")
        print("  Press 'Q':    Quit application")
        print("\nUsage:")
        print("  1. Hold 'V' key to start recording")
        print("  2. Speak your command")
        print("  3. Release 'V' key to send command")
        print("  4. Wait for BT's response")
        print("\nTips:")
        print("  - Speak clearly and avoid background noise")
        print("  - Commands are processed in 2-3 seconds (cloud mode)")
        print("  - Conversation history helps BT understand context")
        print("="*60 + "\n")

    def run(self):
        """Main event loop"""
        print("\n" + "="*60)
        print("Voice Assistant V2 - Push-to-Talk Mode")
        print("="*60)
        print("\nControls:")
        print("  Hold 'V' = Push-to-talk")
        print("  Press 'M' = Toggle mute")
        print("  Press 'C' = Clear history")
        print("  Press 'S' = Show status")
        print("  Press 'H' = Show help")
        print("  Press 'Q' = Quit")
        print("="*60 + "\n")

        # Check server connection
        if not self.is_server_running():
            print("WARNING: WARNING: V2 server is not running!")
            print(f"Start server with: python server_v2.py")
            print("Or run: uvicorn server_v2:app --host 0.0.0.0 --port 8001\n")

        print("Ready! Hold 'V' to record a voice command.\n")

        # Register keyboard handlers
        keyboard.on_press_key('v', lambda _: self.handle_push_to_talk())
        keyboard.add_hotkey('m', lambda: self._toggle_mute())
        keyboard.add_hotkey('c', lambda: self.clear_conversation_history())
        keyboard.add_hotkey('s', lambda: self.show_status())
        keyboard.add_hotkey('h', lambda: self.show_help())

        try:
            # Main loop
            while True:
                if keyboard.is_pressed('q'):
                    print("\n[V2] Shutting down...")
                    break
                time.sleep(0.1)

        except KeyboardInterrupt:
            print("\n[V2] Interrupted by user")
        finally:
            print("[V2] Cleanup...")
            keyboard.unhook_all()
            self.pa.terminate()
            print("[V2] Goodbye!")

    def _toggle_mute(self):
        """Toggle mute state"""
        self.muted = not self.muted
        status = "Muted" if self.muted else "Unmuted"
        print(f"\n[V2] {status}")


def main():
    """Main entry point"""
    try:
        assistant = VoiceAssistantV2()
        assistant.run()
    except Exception as e:
        print(f"[V2] Fatal error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
