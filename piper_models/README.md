# Piper TTS Voice Models

This directory contains voice models for Piper TTS.

## BT-7274 Voice Model

The BT-7274 voice model is from the Titanfall 2 game character.

### Download

Download the voice model files from:
https://github.com/DJMalachite/PiperVoiceModels

Files needed:
- `BT7274.onnx` (61 MB)
- `BT7274.onnx.json` (7 KB)
- `voices.json` (optional)

### Installation

Place the files in: `piper_models/bt7274/`

Direct download links:
```bash
mkdir -p piper_models/bt7274
cd piper_models/bt7274
curl -L -O "https://raw.githubusercontent.com/DJMalachite/PiperVoiceModels/main/Titanfall2/BT7274/BT7274.onnx"
curl -L -O "https://raw.githubusercontent.com/DJMalachite/PiperVoiceModels/main/Titanfall2/BT7274/BT7274.onnx.json"
```

### Usage

The voice models are automatically loaded by the server on startup.

Model info:
- Voice: BT-7274 (Titanfall 2)
- Sample rate: 22050 Hz
- Format: ONNX
