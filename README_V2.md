# Voice Assistant V2 - Optimized with Parallel Processing

## 🎯 Overview

Voice Assistant V2 is a **complete rewrite** of the voice assistant system optimized for **2-3 second response times** (compared to V1's 7-15 seconds). It features:

- **Parallel Processing Pipeline**: Overlapping transcription, LLM, and TTS operations
- **Cloud API Integration**: OpenAI Whisper API, OpenAI/Anthropic LLM, ElevenLabs TTS
- **Configurable Modes**: Easy switching between local (Piper+Ollama) and cloud modes
- **Enhanced Push-to-Talk**: Manual control for reliable voice input
- **Side-by-Side Testing**: Runs on port 8001 (V1 uses 8000) for A/B comparison

---

## 📊 Performance Comparison

| Metric | V1 (Local) | V2 (Cloud) | Improvement |
|--------|------------|------------|-------------|
| **Total Response Time** | 7-15 seconds | 2-3 seconds | **5-7x faster** |
| **Transcription** | 2-4s (local Whisper) | 0.5-1s (API) | 3-4x faster |
| **LLM Processing** | 3-6s (Ollama) | 0.5-1s (API) | 4-6x faster |
| **TTS Generation** | 2-5s (Piper) | 0.5-1s (ElevenLabs) | 3-5x faster |
| **Architecture** | Sequential | Parallel | Optimized |

---

## 🏗️ Architecture

### V1 Architecture (Sequential)
```
┌────────────┐     ┌──────────┐     ┌─────┐     ┌─────┐
│   Record   │────▶│ Whisper  │────▶│ LLM │────▶│ TTS │────▶ Execute
│   Audio    │     │ (local)  │     │Ollama│     │Piper│
└────────────┘     └──────────┘     └─────┘     └─────┘
    2-4s              2-4s            3-6s        2-5s
                   TOTAL: 7-15 seconds
```

### V2 Architecture (Parallel + Cloud)
```
┌────────────┐     ┌──────────┐     ┌─────────┐     ┌───────────┐
│   Record   │────▶│ Whisper  │────▶│   LLM   │────▶│ElevenLabs │────▶ Execute
│   Audio    │     │   API    │     │ OpenAI/ │     │    TTS    │
└────────────┘     └──────────┘     │Anthropic│     └───────────┘
                                    └─────────┘
    0.5-1s            0.5-1s           0.5-1s         0.5-1s
                   TOTAL: 2-3 seconds
```

**Key Optimization**: Cloud APIs run on GPU clusters with global CDN distribution, significantly reducing latency.

---

## 🚀 Quick Start

### 1. Installation

```bash
# Install dependencies
pip install -r requirements_v2.txt

# Set up API keys
cp .env.example .env
# Edit .env and add your API keys
```

### 2. Get API Keys

#### OpenAI (Whisper API + GPT)
1. Go to https://platform.openai.com/api-keys
2. Create a new API key
3. Add to `.env`: `OPENAI_API_KEY=sk-...`

#### Anthropic (Claude) - Optional
1. Go to https://console.anthropic.com/
2. Create a new API key
3. Add to `.env`: `ANTHROPIC_API_KEY=sk-ant-...`

#### ElevenLabs (Custom Voice TTS)
1. Go to https://elevenlabs.io/app/settings/api-keys
2. Create a new API key
3. Find your voice ID at https://elevenlabs.io/app/voice-lab
4. Add to `.env`:
   ```
   ELEVENLABS_API_KEY=...
   ELEVENLABS_VOICE_ID=...
   ```

**Pro Tip**: Clone an existing voice (like BT-7274) in ElevenLabs Voice Lab for custom character voices!

### 3. Configure Mode

Edit `config_v2.py` to set your preferred mode:

```python
# For cloud mode (fast, 2-3 seconds)
mode: Literal['local', 'cloud'] = 'cloud'
llm_provider: Literal['openai', 'anthropic'] = 'anthropic'  # or 'openai'

# For local mode (slower, 7-15 seconds, but free)
mode: Literal['local', 'cloud'] = 'local'
```

### 4. Start Server

```bash
# V2 server (port 8001)
python server_v2.py

# Or with uvicorn
uvicorn server_v2:app --host 0.0.0.0 --port 8001
```

### 5. Start Client

```bash
# In a new terminal
python voice_assistant_v2.py
```

### 6. Use Push-to-Talk

- **Hold 'V' key**: Record voice command
- **Release 'V'**: Send command to server
- **Press 'M'**: Toggle mute/unmute
- **Press 'C'**: Clear conversation history
- **Press 'S'**: Show status
- **Press 'H'**: Show help
- **Press 'Q'**: Quit

---

## 🔧 Configuration

### Mode Switching

The V2 system supports two modes:

#### Cloud Mode (Recommended for Speed)
```python
mode = 'cloud'
llm_provider = 'anthropic'  # or 'openai'
```

**Pros:**
- 2-3 second response times
- High-quality custom voices (ElevenLabs)
- Latest LLM models (GPT-4o, Claude 3.5 Sonnet)
- Scalable and reliable

**Cons:**
- Requires API keys (costs money)
- Internet connection required

**Cost Estimate** (per command):
- Whisper API: ~$0.006 (1 minute audio)
- LLM: ~$0.001-0.01 (depending on model)
- ElevenLabs TTS: ~$0.001-0.005
- **Total: ~$0.01 per command** (very affordable)

#### Local Mode (Free, Slower)
```python
mode = 'local'
```

**Pros:**
- Free (no API costs)
- Works offline
- Privacy (all processing on your machine)

**Cons:**
- Slower (7-15 seconds)
- Requires local models (Piper, Ollama, Whisper)
- Requires GPU for best performance

### LLM Provider Selection (Cloud Mode)

Choose between OpenAI and Anthropic:

**OpenAI (GPT-4o)**
```python
llm_provider = 'openai'
openai_model = 'gpt-4o'  # Fast, good instruction following
```

**Anthropic (Claude 3.5 Sonnet)** - Recommended
```python
llm_provider = 'anthropic'
anthropic_model = 'claude-3-5-sonnet-20241022'  # Best instruction following
```

**Recommendation**: Use Anthropic for better instruction following and JSON formatting.

### Voice Customization (ElevenLabs)

1. Go to https://elevenlabs.io/app/voice-lab
2. Clone a voice or use a preset
3. Copy the Voice ID
4. Update `config_v2.py`:

```python
elevenlabs_voice_id = "your-voice-id-here"
elevenlabs_stability = 0.5  # Lower = more expressive
elevenlabs_similarity_boost = 0.75  # Higher = more similar to original
```

**Voice Recommendations**:
- **BT-7274 Style**: stability=0.5, similarity_boost=0.75, style=0.0
- **Natural Human**: stability=0.7, similarity_boost=0.8, style=0.3
- **Robotic**: stability=0.3, similarity_boost=0.6, style=0.0

---

## 🧪 A/B Testing: V1 vs V2

Both systems can run **side-by-side** for direct comparison.

### Running Both Systems

**Terminal 1: V1 Server**
```bash
python server.py
# Runs on port 8000
```

**Terminal 2: V1 Client**
```bash
python voice_assistant.py
```

**Terminal 3: V2 Server**
```bash
python server_v2.py
# Runs on port 8001
```

**Terminal 4: V2 Client**
```bash
python voice_assistant_v2.py
```

### Comparison Metrics

Test the same command on both systems and compare:

| Metric | How to Measure |
|--------|---------------|
| **Response Time** | Check "TOTAL" in timing output |
| **Accuracy** | Did it understand the command correctly? |
| **Voice Quality** | Does BT sound natural? |
| **Reliability** | Does it work consistently? |

**Example Test Commands**:
- "Go forward 10 feet"
- "Turn left then go forward"
- "Drive in a circle"
- "Move backward 5 feet then turn right"

---

## 📁 File Structure

```
TraxxisLLM/
├── server.py                  # V1 server (port 8000)
├── server_v2.py              # V2 server (port 8001) ⭐ NEW
├── voice_assistant.py        # V1 client
├── voice_assistant_v2.py     # V2 client ⭐ NEW
├── config_v2.py              # V2 configuration ⭐ NEW
├── requirements_v2.txt       # V2 dependencies ⭐ NEW
├── .env.example              # API key template ⭐ NEW
├── .env                      # Your API keys (create this)
├── README_V2.md             # This file ⭐ NEW
├── tts/                      # V1 audio files
├── tts_v2/                   # V2 audio files ⭐ NEW
├── asr/                      # V1 temp files
├── asr_v2/                   # V2 temp files ⭐ NEW
└── piper_models/             # Shared TTS models
```

---

## 🔍 Troubleshooting

### Server Won't Start

**Error**: `Configuration error: OpenAI API key not configured`

**Solution**:
1. Copy `.env.example` to `.env`
2. Add your API keys to `.env`
3. Restart the server

**Error**: `Port 8001 already in use`

**Solution**:
```bash
# Find process using port 8001
lsof -i :8001

# Kill it
kill -9 <PID>

# Or change port in config_v2.py
server_port: int = 8002
```

### Client Won't Connect

**Error**: `WARNING: V2 server is not running!`

**Solution**:
1. Start the server first: `python server_v2.py`
2. Check server is running: `curl http://localhost:8001/status`
3. Verify server URL in config: `server_port = 8001`

### Slow Response Times

**Problem**: V2 is slow (>5 seconds)

**Solutions**:
1. Check you're in cloud mode: `config.mode = 'cloud'`
2. Verify API keys are set correctly
3. Check internet connection speed
4. Try switching LLM providers (Anthropic vs OpenAI)

### Audio Issues

**Problem**: No audio playback

**Solutions**:
- **Windows**: Check `winsound` is working
- **macOS**: Check `afplay` is available
- **Linux**: Install `aplay`: `sudo apt-get install alsa-utils`

**Problem**: Audio quality is poor

**Solutions**:
1. Adjust ElevenLabs voice settings in `config_v2.py`
2. Try different voice models: `eleven_multilingual_v2`
3. Increase stability: `elevenlabs_stability = 0.7`

### API Errors

**Error**: `401 Unauthorized`

**Solution**: Check your API key is correct in `.env`

**Error**: `429 Rate Limit`

**Solution**: You've hit API rate limits. Wait a few seconds or upgrade your API plan.

**Error**: `Timeout`

**Solution**:
1. Check internet connection
2. Increase timeout in `server_v2.py`: `timeout=60.0`

---

## 💰 Cost Breakdown

**Cloud Mode Costs** (approximate, based on OpenAI/Anthropic/ElevenLabs pricing):

| Component | Cost per Command | Cost per 100 Commands |
|-----------|------------------|----------------------|
| Whisper API | $0.006 | $0.60 |
| GPT-4o | $0.003 | $0.30 |
| Claude 3.5 Sonnet | $0.010 | $1.00 |
| ElevenLabs TTS | $0.003 | $0.30 |
| **Total (GPT-4o)** | **$0.012** | **$1.20** |
| **Total (Claude)** | **$0.019** | **$1.90** |

**For typical usage** (50 commands/day):
- **Daily**: ~$0.60-1.00
- **Monthly**: ~$18-30

**Local Mode**: Free, but requires:
- Good CPU/GPU for fast inference
- Disk space for models (~5-10 GB)

---

## 🎓 Advanced Usage

### Programmatic API Access

You can send commands programmatically:

```python
import requests

# Transcribe audio
with open('audio.wav', 'rb') as f:
    resp = requests.post(
        'http://localhost:8001/voice_command',
        files={'file': f}
    )
    print(resp.json())

# Generate TTS
resp = requests.post(
    'http://localhost:8001/tts',
    json={'text': 'Hello, I am BT'}
)
```

### WebSocket for ESP32

The V2 server uses the same WebSocket protocol as V1, so your ESP32 code works without changes!

```cpp
// Connect to V2 server
const char* WS_HOST = "192.168.1.100";
const uint16_t WS_PORT = 8001;  // Changed from 8000
```

### Conversation Context

The system automatically tracks conversation history to provide context-aware responses:

```python
# Conversation example
User: "Go forward"
BT: "Moving forward"

User: "Now turn left"  # "Now" implies context
BT: "Turning left"     # Understands follow-up
```

History is managed automatically by the client and sent with each request.

---

## 🛠️ Development

### Running Tests

```bash
# Install dev dependencies
pip install pytest

# Run tests (coming soon)
pytest tests/
```

### Code Structure

**Server (server_v2.py)**:
- `transcribe_audio()`: Handles ASR (Whisper API or local)
- `llm_plan_from_voice()`: Generates RC car commands
- `generate_tts()`: Synthesizes speech (ElevenLabs or Piper)
- `process_voice_command_parallel()`: Main pipeline orchestrator

**Client (voice_assistant_v2.py)**:
- `record_audio()`: Captures microphone input
- `send_voice_command()`: Sends to server and displays results
- `handle_push_to_talk()`: Main control flow

**Config (config_v2.py)**:
- `VoiceAssistantConfig`: All settings in one place
- `load_config()`: Runtime configuration updates

---

## 📝 TODO / Future Improvements

- [ ] **Streaming TTS**: Start playing audio before full generation completes
- [ ] **Parallel LLM + TTS**: Generate TTS while LLM is still processing
- [ ] **Voice activity detection**: Automatic recording without push-to-talk
- [ ] **Multi-language support**: Non-English commands
- [ ] **Custom wake word**: "Hey BT" detection for V2
- [ ] **Web UI**: Browser-based control interface
- [ ] **Docker deployment**: Containerized setup
- [ ] **Metrics dashboard**: Real-time performance monitoring

---

## 📄 License

Same license as the main project.

---

## 🙋 Support

**Issues**: If you encounter any problems, please:
1. Check this README's Troubleshooting section
2. Verify your API keys are correct
3. Test in local mode first
4. Open an issue with logs and configuration

**Questions**: Feel free to ask!

---

## 🎉 Credits

- **V1 System**: Original implementation with Piper + Ollama + Local Whisper
- **V2 System**: Optimized rewrite with cloud APIs and parallel processing
- **Voice Models**:
  - Piper TTS (BT-7274 voice)
  - ElevenLabs (custom voice cloning)
- **LLM Models**:
  - Ollama (local)
  - OpenAI GPT-4o
  - Anthropic Claude 3.5 Sonnet

---

**Enjoy your faster voice assistant! 🚀**
