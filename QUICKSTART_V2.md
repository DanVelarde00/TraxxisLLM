# Voice Assistant V2 - Quick Start Guide

Get up and running with V2 in 5 minutes!

## Prerequisites

- Python 3.8+
- Microphone and speakers
- Internet connection (for cloud mode)

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements_v2.txt
```

### 2. Set Up API Keys

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and add your API keys
nano .env  # or use your preferred editor
```

**Required API Keys**:
- `OPENAI_API_KEY` - Get at https://platform.openai.com/api-keys
- `ANTHROPIC_API_KEY` - Get at https://console.anthropic.com/
- `ELEVENLABS_API_KEY` - Get at https://elevenlabs.io/app/settings/api-keys
- `ELEVENLABS_VOICE_ID` - Find at https://elevenlabs.io/app/voice-lab

### 3. Configure Mode

Edit `config_v2.py` and set:

```python
# Cloud mode (recommended for speed)
mode: Literal['local', 'cloud'] = 'cloud'
llm_provider: Literal['openai', 'anthropic'] = 'anthropic'
```

## Running V2

### Option 1: Using Scripts (Recommended)

**Terminal 1 - Start Server:**
```bash
./start_v2_server.sh
```

**Terminal 2 - Start Client:**
```bash
./start_v2_client.sh
```

### Option 2: Manual Start

**Terminal 1 - Start Server:**
```bash
python server_v2.py
```

**Terminal 2 - Start Client:**
```bash
python voice_assistant_v2.py
```

## Usage

1. **Hold 'V' key** to start recording
2. **Speak your command** (e.g., "Go forward 10 feet")
3. **Release 'V' key** to send command
4. **Wait for response** (should be 2-3 seconds!)

### Controls

- `V` (hold) = Record voice command
- `M` = Toggle mute/unmute
- `C` = Clear conversation history
- `S` = Show status
- `H` = Show help
- `Q` = Quit

## Example Commands

Try these commands to test the system:

- "Go forward"
- "Turn left"
- "Go forward 10 feet"
- "Turn right then go forward"
- "Drive in a circle"
- "Move backward 5 feet"

## Troubleshooting

### Server won't start
- Check API keys in `.env` file
- Verify port 8001 is not in use

### No response
- Check internet connection
- Verify API keys are correct
- Check server logs for errors

### Slow responses
- Make sure `mode = 'cloud'` in config_v2.py
- Check internet speed
- Try different LLM provider

## Next Steps

- Read full documentation: `README_V2.md`
- Try A/B testing with V1 (see README_V2.md)
- Customize voice settings in `config_v2.py`
- Set up local mode for offline usage

## Getting Help

If you encounter issues:
1. Check `README_V2.md` Troubleshooting section
2. Review server logs
3. Test with simple commands first
4. Verify all API keys are valid

---

**Ready to go! Hold 'V' and start talking to BT!**
