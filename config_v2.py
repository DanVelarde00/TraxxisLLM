"""
Voice Assistant V2 - Configuration System

This module provides configuration management for toggling between:
- Local mode (Piper TTS + Ollama LLM + Local Whisper)
- Cloud mode (ElevenLabs TTS + OpenAI/Anthropic LLM + Whisper API)

Usage:
    from config_v2 import config

    # Access configuration values
    api_key = config.openai_api_key
    mode = config.mode  # 'local' or 'cloud'
"""

import os
from pathlib import Path
from typing import Literal
from dataclasses import dataclass, field

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

@dataclass
class VoiceAssistantConfig:
    """Configuration for Voice Assistant V2"""

    # ===== OPERATION MODE =====
    mode: Literal['local', 'cloud'] = 'cloud'  # Toggle between local and cloud

    # ===== SERVER SETTINGS =====
    server_host: str = "0.0.0.0"
    server_port: int = 8000  # Same as V1 - switch between V1/V2 by running different server

    # ===== DIRECTORY SETTINGS =====
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent)

    @property
    def tts_dir(self) -> Path:
        """Directory for TTS audio files"""
        return self.base_dir / "tts_v2"

    @property
    def asr_dir(self) -> Path:
        """Directory for ASR temporary files"""
        return self.base_dir / "asr_v2"

    # ===== CLOUD API KEYS =====
    # Set these as environment variables or update directly
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    elevenlabs_api_key: str = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))

    # ===== TRANSCRIPTION (ASR) SETTINGS =====
    # Local Whisper settings (when mode='local')
    local_whisper_model: str = "tiny"  # tiny, base, small, medium, large, turbo

    # Cloud Whisper API settings (when mode='cloud')
    whisper_api_model: str = "whisper-1"
    whisper_language: str = "en"

    # ===== LLM SETTINGS =====
    # Local Ollama settings (when mode='local')
    ollama_url: str = "http://localhost:11434/api/chat"
    ollama_model: str = "qwen2.5:14b"
    ollama_temperature: float = 0.15
    ollama_num_predict: int = 150
    ollama_num_ctx: int = 1024

    # Cloud LLM settings (when mode='cloud')
    llm_provider: Literal['openai', 'anthropic'] = 'openai'  # Choose cloud LLM provider

    # OpenAI settings
    openai_model: str = "gpt-4o"  # gpt-4o, gpt-4-turbo, gpt-3.5-turbo
    openai_temperature: float = 0.15
    openai_max_tokens: int = 150

    # Anthropic settings
    anthropic_model: str = "claude-3-5-sonnet-20240620"  # Latest stable Claude 3.5 Sonnet
    anthropic_temperature: float = 0.15
    anthropic_max_tokens: int = 150

    # ===== TTS SETTINGS =====
    # Local Piper settings (when mode='local')
    piper_model_dir: Path = field(default_factory=lambda: Path(__file__).parent / "piper_models" / "bt7274")

    @property
    def piper_model_path(self) -> Path:
        """Path to Piper ONNX model"""
        return self.piper_model_dir / "BT7274.onnx"

    @property
    def piper_config_path(self) -> Path:
        """Path to Piper config JSON"""
        return self.piper_model_dir / "BT7274.onnx.json"

    # Cloud ElevenLabs settings (when mode='cloud')
    elevenlabs_voice_id: str = field(default_factory=lambda: os.getenv("ELEVENLABS_VOICE_ID", ""))  # Set your custom voice ID here
    elevenlabs_model: str = "eleven_turbo_v2_5"  # eleven_turbo_v2_5, eleven_multilingual_v2
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity_boost: float = 0.75
    elevenlabs_style: float = 0.0
    elevenlabs_use_speaker_boost: bool = True

    # ===== PERFORMANCE SETTINGS =====
    enable_streaming_tts: bool = True  # Stream audio as it's generated
    enable_parallel_processing: bool = True  # Process transcription, LLM, and TTS in parallel

    # ===== AUDIO SETTINGS =====
    sample_rate: int = 16000
    audio_format: str = "paInt16"
    channels: int = 1

    # ===== CONVERSATION SETTINGS =====
    max_conversation_history: int = 20  # Maximum conversation turns to keep

    # ===== LOGGING =====
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    enable_debug_logging: bool = False

    def __post_init__(self):
        """Create directories if they don't exist"""
        self.tts_dir.mkdir(parents=True, exist_ok=True)
        self.asr_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> bool:
        """Validate configuration based on current mode"""
        if self.mode == 'cloud':
            if self.llm_provider == 'openai' and not self.openai_api_key:
                raise ValueError("OpenAI API key is required for cloud mode with OpenAI LLM")
            if self.llm_provider == 'anthropic' and not self.anthropic_api_key:
                raise ValueError("Anthropic API key is required for cloud mode with Anthropic LLM")
            if not self.openai_api_key:
                raise ValueError("OpenAI API key is required for cloud mode (Whisper API)")
            if not self.elevenlabs_api_key:
                raise ValueError("ElevenLabs API key is required for cloud mode")
            if not self.elevenlabs_voice_id:
                # DEBUG: Show what we're actually seeing
                print(f"[DEBUG] elevenlabs_voice_id value: '{self.elevenlabs_voice_id}'")
                print(f"[DEBUG] ELEVENLABS_VOICE_ID env var: '{os.getenv('ELEVENLABS_VOICE_ID')}'")
                # Try to reload from environment
                self.elevenlabs_voice_id = os.getenv('ELEVENLABS_VOICE_ID', '')
                if not self.elevenlabs_voice_id:
                    raise ValueError("ElevenLabs voice ID is required for cloud mode")

        elif self.mode == 'local':
            if not self.piper_model_path.exists():
                raise ValueError(f"Piper model not found at {self.piper_model_path}")
            if not self.piper_config_path.exists():
                raise ValueError(f"Piper config not found at {self.piper_config_path}")

        return True

    def get_mode_info(self) -> dict:
        """Get information about current configuration mode"""
        if self.mode == 'cloud':
            return {
                'mode': 'cloud',
                'transcription': 'OpenAI Whisper API',
                'llm': f'{self.llm_provider.upper()} ({self.openai_model if self.llm_provider == "openai" else self.anthropic_model})',
                'tts': f'ElevenLabs ({self.elevenlabs_model})',
                'expected_latency': '2-3 seconds',
                'parallel_processing': self.enable_parallel_processing
            }
        else:
            return {
                'mode': 'local',
                'transcription': f'Local Whisper ({self.local_whisper_model})',
                'llm': f'Ollama ({self.ollama_model})',
                'tts': 'Piper (BT-7274)',
                'expected_latency': '7-15 seconds',
                'parallel_processing': self.enable_parallel_processing
            }

    def switch_mode(self, mode: Literal['local', 'cloud']):
        """Switch between local and cloud modes"""
        if mode not in ['local', 'cloud']:
            raise ValueError(f"Invalid mode: {mode}. Must be 'local' or 'cloud'")
        self.mode = mode
        print(f"Switched to {mode} mode")
        print(f"Configuration: {self.get_mode_info()}")


# Global configuration instance
config = VoiceAssistantConfig()

# Convenience function to load custom configuration
def load_config(
    mode: Literal['local', 'cloud'] = None,
    openai_api_key: str = None,
    anthropic_api_key: str = None,
    elevenlabs_api_key: str = None,
    elevenlabs_voice_id: str = None,
    llm_provider: Literal['openai', 'anthropic'] = None
) -> VoiceAssistantConfig:
    """
    Load and update configuration with custom values

    Args:
        mode: Operation mode ('local' or 'cloud')
        openai_api_key: OpenAI API key
        anthropic_api_key: Anthropic API key
        elevenlabs_api_key: ElevenLabs API key
        elevenlabs_voice_id: ElevenLabs voice ID
        llm_provider: LLM provider ('openai' or 'anthropic')

    Returns:
        Updated configuration instance
    """
    global config

    if mode:
        config.mode = mode
    if openai_api_key:
        config.openai_api_key = openai_api_key
    if anthropic_api_key:
        config.anthropic_api_key = anthropic_api_key
    if elevenlabs_api_key:
        config.elevenlabs_api_key = elevenlabs_api_key
    if elevenlabs_voice_id:
        config.elevenlabs_voice_id = elevenlabs_voice_id
    if llm_provider:
        config.llm_provider = llm_provider

    return config


if __name__ == "__main__":
    # Display current configuration
    print("=== Voice Assistant V2 Configuration ===")
    print(f"\nCurrent Mode: {config.mode}")
    print(f"\nMode Info:")
    for key, value in config.get_mode_info().items():
        print(f"  {key}: {value}")

    print(f"\nDirectories:")
    print(f"  Base: {config.base_dir}")
    print(f"  TTS: {config.tts_dir}")
    print(f"  ASR: {config.asr_dir}")

    print(f"\nAPI Keys Configured:")
    print(f"  OpenAI: {'✓' if config.openai_api_key else '✗'}")
    print(f"  Anthropic: {'✓' if config.anthropic_api_key else '✗'}")
    print(f"  ElevenLabs: {'✓' if config.elevenlabs_api_key else '✗'}")

    print("\n=== Configuration Validation ===")
    try:
        config.validate()
        print("✓ Configuration is valid")
    except ValueError as e:
        print(f"✗ Configuration error: {e}")
