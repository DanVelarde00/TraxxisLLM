# TraxxisLLM Setup Guide

## 🚀 Quick Start

Get your voice-controlled RC vehicle up and running in minutes!

## Prerequisites

### Hardware
- **Computer** (Linux/Mac/Windows) to run the server
- **ESP32 microcontroller** connected to your RC vehicle
- **Traxxas RC vehicle** (or compatible RC car)
- **Microphone** for voice input

### Software Requirements
- **Python 3.8+**
- **Ollama** (for LLM inference)
- **Audio playback** capability (`aplay` on Linux, `afplay` on Mac, or `winsound` on Windows)

---

## Step 1: Install Ollama

Download and install Ollama from: https://ollama.ai

Then pull the required model:
```bash
ollama pull llama3.2:8b
```

**Tip**: For better steering accuracy, you can upgrade to:
```bash
ollama pull llama3.1:8b
```
Then update `OLLAMA_MODEL` in `server.py` line 36.

---

## Step 2: Install Python Dependencies

```bash
# Install required packages
pip install fastapi uvicorn httpx pydantic
pip install openai-whisper
pip install piper-tts
pip install pyaudio webrtcvad vosk
pip install keyboard
pip install wave
```

**Linux users** may need additional audio packages:
```bash
sudo apt-get install portaudio19-dev python3-pyaudio
sudo apt-get install alsa-utils  # for aplay
```

---

## Step 3: Download Piper Voice Model

The BT-7274 voice model is required for TTS:

```bash
cd piper_models/bt7274

# Download the voice model files (61 MB total)
curl -L -O "https://raw.githubusercontent.com/DJMalachite/PiperVoiceModels/main/Titanfall2/BT7274/BT7274.onnx"
curl -L -O "https://raw.githubusercontent.com/DJMalachite/PiperVoiceModels/main/Titanfall2/BT7274/BT7274.onnx.json"

cd ../..
```

**Already done?** Check if files exist:
```bash
ls -lh piper_models/bt7274/BT7274.onnx*
```

---

## Step 4: Install Vosk Model for Wake Word Detection

Download the Vosk model for wake word detection:

```bash
# This will auto-download on first run, or manually:
# Download from: https://alphacephei.com/vosk/models
# Get: vosk-model-small-en-us-0.15
```

The voice assistant will download it automatically when you first run it.

---

## Step 5: Flash ESP32 Firmware

1. Open `llmreciever.ino` in Arduino IDE
2. Install ESP32 board support in Arduino IDE
3. Install required libraries:
   - WebSocketsClient
   - ArduinoJson
4. Update WiFi credentials in the `.ino` file
5. Upload to ESP32

---

## Step 6: Start the System

### Terminal 1: Start the FastAPI Server

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

**Expected output:**
```
[startup] HTTP client ready for Ollama
[startup] Loading Whisper model 'basic'...
[startup] Whisper model loaded successfully
[startup] Loading Piper TTS model (BT-7274)...
[startup] Piper TTS model loaded successfully
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Terminal 2: Start the Voice Assistant

```bash
python voice_assistant.py
```

**Expected output:**
```
============================================================
VOICE ASSISTANT v2.4
============================================================
[INIT] Loading Vosk model...
[OK] Ready

[KEYS] V=talk | X=mode | N=mute
[VOICE] 'Hey BT'=wake | 'Bye BT'=sleep

[LISTEN] Wake: 'Hey BT' | WAIT | UNMUTED
```

---

## Step 7: Connect ESP32

Make sure your ESP32 is:
1. Powered on
2. Connected to the same WiFi network
3. Successfully connected to the WebSocket server

Check server logs for:
```
[event] client_connected addr=('192.168.x.x', port)
```

---

## 🎮 Using the System

### Wake Word Mode (Hands-Free)
1. Say **"Hey BT"** to wake the robot
2. BT responds: *"Ready."* or similar
3. Give your command: *"Turn left"* or *"Go forward"*
4. BT executes and confirms
5. Say **"Bye BT"** to put it to sleep

### Push-to-Talk Mode
- Press and hold **V** key
- Speak your command
- Release **V** when done

### Keyboard Controls
- **V** = Push-to-talk (hold to record)
- **X** = Toggle active listening mode
- **N** = Mute/unmute BT's voice

### Voice Commands
- **"Bye BT"** = Put robot to sleep
- **"Quiet"** / **"Shut up"** = Mute BT
- **"You can speak"** = Unmute BT

---

## 🎯 Example Commands

### Movement
- "Go forward"
- "Turn left"
- "Turn right"
- "Go backward"
- "Stop"

### Advanced
- "Go forward then turn right"
- "Make a hard left"
- "Turn slightly to the right"
- "Go forward fast"

---

## 🔧 Configuration

### Update Voice Model Location
Edit `server.py` lines 41-44:
```python
PIPER_MODEL_DIR = BASE_DIR / "piper_models" / "bt7274"
PIPER_MODEL_PATH = PIPER_MODEL_DIR / "BT7274.onnx"
PIPER_CONFIG_PATH = PIPER_MODEL_DIR / "BT7274.onnx.json"
```

### Change LLM Model
Edit `server.py` line 36:
```python
OLLAMA_MODEL = "llama3.1:8b"  # or any Ollama model
```

### Adjust Whisper Model
Edit `server.py` line 39:
```python
WHISPER_MODEL = "base"  # Options: tiny, base, small, medium, large, turbo
```

Available models:
- `tiny` - Fastest, least accurate
- `base` - Good balance (recommended)
- `small` - Better accuracy, slower
- `medium` - High accuracy, much slower
- `large` - Best accuracy, very slow
- `turbo` - Fast and accurate (newer model)

### ESP32 Server URL
Edit `llmreciever.ino`:
```cpp
const char* serverUrl = "ws://YOUR_IP:8000/ws";
```

---

## 🐛 Troubleshooting

### Server won't start
**Problem**: `ModuleNotFoundError: No module named 'piper'`
```bash
pip install piper-tts
```

**Problem**: `Failed to load Piper TTS model`
- Check if model files exist in `piper_models/bt7274/`
- Re-download using Step 3 instructions

### Whisper not working
```bash
pip install openai-whisper
```

### ESP32 not connecting
1. Check WiFi credentials in `.ino` file
2. Verify server IP address is correct
3. Make sure port 8000 is not blocked
4. Check ESP32 serial monitor for errors

### No audio output
**Linux**:
```bash
sudo apt-get install alsa-utils
aplay -l  # List audio devices
```

**Mac**: Should work with built-in `afplay`

**Windows**: Should work with `winsound`

### Wake word not detected
- Speak clearly and slightly louder
- Say "Hey BT" with a pause between words
- Check microphone input levels
- Try alternate pronunciations the model accepts

### Robot turns neutral (1500) instead of turning
This is fixed in the latest code! The post-processing now catches and corrects this. Check logs at:
```bash
curl http://localhost:8000/logs/events | grep steering_fix
```

---

## 📊 Monitoring

### Check System Status
```bash
curl http://localhost:8000/status
```

### View Event Logs
```bash
curl http://localhost:8000/logs/events
```

### View ASR (Speech Recognition) Logs
```bash
curl http://localhost:8000/logs/asr
```

### View LLM Plans
```bash
curl http://localhost:8000/plans
```

---

## 🎨 Customization

### Change BT's Personality
Edit the system prompt in `server.py` starting at line 461:
```python
system_prompt = """
You are BT, a witty robot assistant...
"""
```

### Adjust Steering Values
Edit steering values in `server.py` lines 472-478 if your RC car needs different ranges.

### Modify Wake Word
Edit `voice_assistant.py` line 219 to detect different wake words.

---

## 🚦 System Architecture

```
Voice Input → Whisper (ASR) → Ollama (LLM) → Command Parser → ESP32 → RC Vehicle
                                                     ↓
                                              Piper TTS (BT-7274 Voice)
```

---

## 📝 Notes

- **First run** may be slower while models download/load
- **Whisper basic model** is fast but less accurate than larger models
- **BT-7274 voice** is from Titanfall 2 - perfect for a robot assistant!
- **Conversation context** is maintained for natural follow-up commands
- **Post-processing** fixes steering even if LLM gets it wrong

---

## 🆘 Need Help?

1. Check the main [README.md](README.md) for architecture details
2. Review logs at `http://localhost:8000/logs/events`
3. Enable verbose logging for more details
4. Check ESP32 serial monitor for hardware issues

---

## ✅ Quick Test

After setup, test the complete pipeline:

```bash
# Terminal 1
uvicorn server:app --host 0.0.0.0 --port 8000

# Terminal 2
python voice_assistant.py

# Say "Hey BT"
# BT responds
# Say "Turn left"
# Check if steering_fix appears in logs (if LLM got it wrong)
```

**Success indicators:**
- ✅ Server starts and loads all models
- ✅ Voice assistant detects "Hey BT"
- ✅ Commands are transcribed correctly
- ✅ BT responds with voice
- ✅ ESP32 receives commands
- ✅ Vehicle responds to commands

Enjoy your voice-controlled RC robot! 🎉
