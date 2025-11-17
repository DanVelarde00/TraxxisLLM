import pyaudio
import wave
import requests
import numpy as np
import webrtcvad
import collections
from vosk import Model, KaldiRecognizer
import json
import subprocess
import time
import keyboard
import threading
from pathlib import Path
import os
import re
import glob

SERVER_URL = "http://localhost:8000"
AUDIO_DIR = Path(r"C:\Users\danve\Projects\Traxxis LLM\tts")
AUDIO_DIR.mkdir(exist_ok=True)

class LocalVoiceAssistant:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.sample_rate = 16000
        self.awake = False
        self.manual_override = False
        self.listening_paused = False
        self.bt_can_speak = True
        self.conversation_history = []
        self.active_listening_mode = False
        self.last_command_time = time.time()  
        self.bored_threshold = 65
        self.last_tts_end_time = 0
        self.vad = webrtcvad.Vad(3)
        
        # Vosk for wake word detection
        print("[INIT] Loading Vosk model...")
        self.model = Model(model_name="vosk-model-small-en-us-0.15")
        self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
        self.recognizer.SetWords(True)
    
    def cleanup_all_wav_files(self):
        """Delete all .wav files (called on sleep/exit only)"""
        deleted = 0
        for filepath in glob.glob(str(AUDIO_DIR / "cmd_*.wav")):
            try:
                os.remove(filepath)
                deleted += 1
            except:
                pass
        if deleted > 0:
            print(f"[CLEANUP] Deleted {deleted} .wav files")
        
    def is_server_running(self):
        try:
            requests.get(f"{SERVER_URL}/status", timeout=1)
            return True
        except:
            return False
    
    def start_server(self):
        print("[SYS] Starting server...")
        subprocess.Popen(
            ["uvicorn", "server_with_asr:app", "--host", "0.0.0.0", "--port", "8000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(3)
    
    def clean_text_for_speech(self, text):
        """Aggressively remove special characters"""
        text = re.sub(r'[*_~`]', '', text)
        text = re.sub(r'["\'"]', '', text)
        text = re.sub(r'\.\.\.', '', text)
        text = re.sub(r'[!]{2,}', '!', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def add_to_conversation(self, role, text):
        """Add message to conversation history"""
        self.conversation_history.append({
            "role": role,
            "text": text,
            "timestamp": time.time()
        })
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]
    
    def get_conversation_context(self):
        """Format conversation history for LLM"""
        if not self.conversation_history:
            return None
        
        context = {
            "recent_conversation": [
                {"role": msg["role"], "text": msg["text"]}
                for msg in self.conversation_history[-10:]
            ]
        }
        return context
    
    def make_bt_speak(self, text, is_wake=False):
        """Make BT speak - respect active listening mode"""
        if not text or not text.strip():
            print("  [SKIP] Empty response - no speech")
            return
            
        if not self.bt_can_speak:
            print("  [MUTED]")
            return
        
        try:
            clean_text = self.clean_text_for_speech(text)
            
            if not clean_text:
                print("  [SKIP] Nothing to say after cleaning")
                return
            
            # Print what BT will say
            print(f'[BT SAYS] "{clean_text}"')
            
            # Always pause during TTS to prevent feedback
            self.listening_paused = True
            
            requests.post(
                f'{SERVER_URL}/tts',
                json={'text': clean_text},
                timeout=10
            )
            
            # Calculate speech duration
            word_count = len(clean_text.split())
            speech_duration = (word_count / 2.5) + 0.5
            
            # Shorter wait for wake, normal for others
            if is_wake:
                time.sleep(speech_duration + 0.2)
            elif not self.active_listening_mode:
                time.sleep(speech_duration + 0.5)
            else:
                time.sleep(0.3)
            
            self.last_tts_end_time = time.time()
            self.listening_paused = False
            
        except Exception as e:
            self.listening_paused = False
            print(f"  [ERR] TTS: {e}")
    
    def wake_response(self):
        """BT's wake responses - SHORTER"""
        responses = [
            "Ready.",
            "Listening.",
            "Go ahead.",
            "Here.",
            "What's up?",
            "I'm ready.",
        ]
        import random
        message = random.choice(responses)
        self.make_bt_speak(message, is_wake=True)
        self.add_to_conversation("assistant", message)
    
    def sleep_response(self):
        """BT's sleep responses - SHORTER"""
        responses = [
            "Later.",
            "Going dark.",
            "See you.",
            "Signing off.",
            "Peace."
        ]
        import random
        message = random.choice(responses)
        self.make_bt_speak(message)
        self.conversation_history = []
    
    def listen_for_wake_word(self):
        """Listen for 'hey BT' - handle keyboard toggles"""
        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=1600
        )
        
        if not self.awake:
            mode = "ACTIVE" if self.active_listening_mode else "WAIT"
            mute = "MUTED" if not self.bt_can_speak else "UNMUTED"
            print(f"[LISTEN] Wake: 'Hey BT' | {mode} | {mute}")
        
        try:
            while True:
                if self.listening_paused:
                    time.sleep(0.05)
                    continue
                
                if keyboard.is_pressed('v'):
                    stream.close()
                    return "manual"
                
                if keyboard.is_pressed('x'):
                    stream.close()
                    return "toggle_listen_mode"
                
                if keyboard.is_pressed('n'):
                    stream.close()
                    return "toggle_quiet"
                
                data = stream.read(1600, exception_on_overflow=False)
                
                if self.recognizer.AcceptWaveform(data):
                    result = json.loads(self.recognizer.Result())
                    text = result.get('text', '').lower()
                    
                    if ('hey' in text or 'hay' in text or 'a' in text) and ('bt' in text or 'be' in text or 'tea' in text or 'b' in text):
                        print(f"  [HEARD] '{text}'")
                        stream.close()
                        return "wake"
                    
                    if self.awake and 'bye' in text and ('bt' in text or 'be' in text):
                        stream.close()
                        return "sleep"
                    
                    if self.awake:
                        if 'quiet' in text or 'silence' in text or 'shut up' in text or 'stop talking' in text:
                            stream.close()
                            return "quiet"
                        if ('you can speak' in text or 'speak now' in text or 'talk now' in text) and not self.bt_can_speak:
                            stream.close()
                            return "speak"
                        
        except KeyboardInterrupt:
            stream.close()
            return "exit"
    
    def record_manual_override(self):
        """Record while V key is held"""
        chunk_size = 1024
        
        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=chunk_size
        )
        
        print("[REC] Hold V to record")
        frames = []
        
        while keyboard.is_pressed('v'):
            data = stream.read(chunk_size, exception_on_overflow=False)
            frames.append(data)
        
        print("  [OK] Done")
        stream.close()
        
        if len(frames) < 5:
            print("  [ERR] Too short")
            return None
        
        timestamp = int(time.time() * 1000)
        filename = AUDIO_DIR / f"cmd_manual_{timestamp}.wav"
        wf = wave.open(str(filename), 'wb')
        wf.setnchannels(1)
        wf.setsampwidth(self.pa.get_sample_size(pyaudio.paInt16))
        wf.setframerate(self.sample_rate)
        wf.writeframes(b''.join(frames))
        wf.close()
        
        return str(filename)
    
    def record_with_vad(self, max_duration=10):
        """Record command with VAD"""
        # Brief cooldown after TTS
        time_since_tts = time.time() - self.last_tts_end_time
        if time_since_tts < 0.5:
            time.sleep(0.5 - time_since_tts)
        
        frame_duration = 30
        chunk_size = int(self.sample_rate * frame_duration / 1000)
        
        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=chunk_size
        )
        
        print("[REC] Listening...")
        
        voiced_frames = []
        ring_buffer = collections.deque(maxlen=25)
        triggered = False
        
        start_time = time.time()
        
        while time.time() - start_time < max_duration:
            if self.listening_paused:
                time.sleep(0.05)
                continue
            
            if keyboard.is_pressed('v'):
                stream.close()
                return self.record_manual_override()
            
            if keyboard.is_pressed('x'):
                stream.close()
                return "toggle_listen_mode"
            
            if keyboard.is_pressed('n'):
                stream.close()
                return "toggle_quiet"
            
            chunk = stream.read(chunk_size, exception_on_overflow=False)
            is_speech = self.vad.is_speech(chunk, self.sample_rate)
            
            if not triggered:
                ring_buffer.append((chunk, is_speech))
                num_voiced = len([f for f, speech in ring_buffer if speech])
                
                if num_voiced > 0.6 * ring_buffer.maxlen:
                    triggered = True
                    print("  [DETECT]")
                    for f, s in ring_buffer:
                        voiced_frames.append(f)
                    ring_buffer.clear()
            else:
                voiced_frames.append(chunk)
                ring_buffer.append((chunk, is_speech))
                num_unvoiced = len([f for f, speech in ring_buffer if not speech])
                
                if num_unvoiced > 0.85 * ring_buffer.maxlen:
                    print("  [OK]")
                    break
        
        stream.close()
        
        if not voiced_frames:
            return None
        
        timestamp = int(time.time() * 1000)
        filename = AUDIO_DIR / f"cmd_vad_{timestamp}.wav"
        wf = wave.open(str(filename), 'wb')
        wf.setnchannels(1)
        wf.setsampwidth(self.pa.get_sample_size(pyaudio.paInt16))
        wf.setframerate(self.sample_rate)
        wf.writeframes(b''.join(voiced_frames))
        wf.close()
        
        return str(filename)
    
    def send_command(self, audio_file):
        """Send to robot with context"""
        print("[PROC] Sending...")
        
        try:
            context = self.get_conversation_context()
            
            with open(audio_file, 'rb') as f:
                files = {'audio': f}
                data = {'language': 'en'}
                
                if context:
                    data['context'] = json.dumps(context)
                
                response = requests.post(
                    f'{SERVER_URL}/voice_command',
                    files=files,
                    data=data,
                    timeout=30
                )
            
            if response.status_code == 200:
                result = response.json()
                user_text = result["transcript"].strip()
                
                # Print what was heard
                print(f'[HEARD] "{user_text}"')
                
                # Skip if empty
                if not user_text or len(user_text) < 2:
                    print("  [SKIP] Empty transcript - no response\n")
                    return False
                
                self.add_to_conversation("user", user_text)
                
                bt_response = result["plan"]["say"]
                
                # Skip speech if response is empty
                if not bt_response or not bt_response.strip():
                    print("  [SKIP] No verbal response\n")
                else:
                    print(f'[EXEC] {len(result["plan"]["steps"])} step(s)')
                    
                    self.add_to_conversation("assistant", bt_response)
                    
                    # Handle TTS wait
                    if self.bt_can_speak and not self.active_listening_mode:
                        self.listening_paused = True
                        clean_text = self.clean_text_for_speech(bt_response)
                        word_count = len(clean_text.split())
                        speech_duration = (word_count / 2.5) + 1.0
                        time.sleep(speech_duration)
                        self.last_tts_end_time = time.time()
                        self.listening_paused = False
                
                print()
                return True
            else:
                print(f"[ERR] Server: {response.status_code}")
                try:
                    error_detail = response.json()
                    print(f"  Detail: {error_detail}")
                except:
                    pass
                print()
                return False
                
        except Exception as e:
            print(f"[ERR] {e}\n")
            return False
    
    def run(self):
        """Main loop"""
        print("=" * 60)
        print("VOICE ASSISTANT v2.4")
        print("=" * 60)
        
        if not self.is_server_running():
            self.start_server()
            time.sleep(2)
        
        print(f"\n[OK] Ready")
        print("\n[KEYS] V=talk | X=mode | N=mute")
        print("[VOICE] 'Hey BT'=wake | 'Bye BT'=sleep\n")
        
        try:
            while True:
                result = self.listen_for_wake_word()
                
                if result == "exit":
                    break
                
                elif result == "toggle_listen_mode":
                    self.active_listening_mode = not self.active_listening_mode
                    mode = "ACTIVE" if self.active_listening_mode else "WAIT"
                    print(f"\n[X] {mode}\n")
                    continue
                
                elif result == "toggle_quiet":
                    self.bt_can_speak = not self.bt_can_speak
                    status = "ON" if self.bt_can_speak else "OFF"
                    print(f"\n[N] Voice: {status}\n")
                    continue
                
                elif result == "manual":
                    print("\n[V] Push-to-talk")
                    audio_file = self.record_manual_override()
                    if audio_file:
                        self.send_command(audio_file)
                    print()
                    continue
                
                elif result == "quiet":
                    print("\n[VOICE] Muted")
                    self.bt_can_speak = False
                    print()
                    continue
                
                elif result == "speak":
                    print("\n[VOICE] Unmuted")
                    self.bt_can_speak = True
                    print()
                    continue
                
                elif result == "wake":
                    print("\n" + "-" * 60)
                    print("[WAKE]")
                    print("-" * 60 + "\n")
                    self.awake = True
                    self.bt_can_speak = True
                    self.wake_response()
                    
                    while self.awake:
                        audio_file = self.record_with_vad()
                        
                        if audio_file == "toggle_listen_mode":
                            self.active_listening_mode = not self.active_listening_mode
                            mode = "ACTIVE" if self.active_listening_mode else "WAIT"
                            print(f"\n[X] {mode}\n")
                            continue
                        
                        if audio_file == "toggle_quiet":
                            self.bt_can_speak = not self.bt_can_speak
                            status = "ON" if self.bt_can_speak else "OFF"
                            print(f"\n[N] Voice: {status}\n")
                            continue
                        
                        if audio_file:
                            try:
                                with open(audio_file, 'rb') as f:
                                    response = requests.post(
                                        f'{SERVER_URL}/transcribe',
                                        files={'audio': f},
                                        data={'language': 'en'},
                                        timeout=10
                                    )
                                    if response.status_code == 200:
                                        transcript = response.json()['transcript'].strip().lower()
                                        
                                        print(f'[HEARD] "{transcript}"')
                                        
                                        if not transcript or len(transcript) < 2:
                                            continue
                                        
                                        if 'bye' in transcript and ('bt' in transcript or 'be' in transcript):
                                            print("\n" + "-" * 60)
                                            print("[SLEEP]")
                                            print("-" * 60 + "\n")
                                            self.sleep_response()
                                            self.awake = False
                                            # Cleanup all .wav files on sleep
                                            self.cleanup_all_wav_files()
                                            print()
                                            break
                                        
                                        if 'quiet' in transcript or 'silence' in transcript or 'shut up' in transcript:
                                            print("\n[VOICE] Muted\n")
                                            self.bt_can_speak = False
                                            continue
                                        
                                        if ('you can speak' in transcript or 'talk now' in transcript) and not self.bt_can_speak:
                                            print("\n[VOICE] Unmuted\n")
                                            self.bt_can_speak = True
                                            continue
                            except Exception as e:
                                print(f"[ERR] Transcribe: {e}")
                            
                            if self.awake:
                                self.send_command(audio_file)
                        
                        if self.awake:
                            print("[READY]\n")
                            
        except KeyboardInterrupt:
            print("\n\n[EXIT]")
        finally:
            # Cleanup all .wav files on exit
            self.cleanup_all_wav_files()
            self.pa.terminate()

if __name__ == "__main__":
    assistant = LocalVoiceAssistant()
    assistant.run()