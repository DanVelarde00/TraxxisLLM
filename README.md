# TraxxisLLM
# Voice-Controlled RC Vehicle Project

## Project Overview

This project is a sophisticated voice-controlled RC vehicle system that enables natural language interaction with a Traxxas RC car. The system allows users to wake the robot with "Hey BT" and give conversational commands like "turn left" or "go forward faster," which are intelligently processed and executed on the physical vehicle. The robot provides audio feedback, creating a conversational experience that feels more like talking to a smart assistant than controlling a remote-control car.

## What It Does

The system transforms a standard Traxxas RC vehicle into a conversational robot with the following capabilities:

- **Wake Word Activation**: Responds to "Hey BT" to begin listening
- **Natural Language Commands**: Understands conversational instructions rather than requiring specific command syntax
- **Multi-Step Sequences**: Maintains conversation context, allowing follow-up commands without repeating information
- **Audio Feedback**: Responds with synthesized speech to confirm actions
- **Multiple Interaction Modes**: Supports wake word activation, push-to-talk controls, and active listening

## System Architecture

The system uses a multi-stage pipeline that coordinates several specialized technologies:

```
Voice Input → Speech Recognition → LLM Planning → Motor Control → Physical Execution → Audio Feedback
```

### Pipeline Stages

1. **Voice Input**: Captures audio from a microphone
2. **Speech Recognition**: Whisper AI converts speech to text
3. **Command Planning**: LLM (Ollama) interprets the command and generates precise motor control instructions
4. **Signal Generation**: Converts commands into microsecond pulse values for steering and throttle
5. **Physical Execution**: ESP32 sends signals via WebSocket to control the RC vehicle motors
6. **Audio Feedback**: Edge-TTS synthesizes speech responses that are played back to the user

## Key Technologies

### Hardware
- **ESP32 Microcontroller**: Controls the vehicle's motors and receives commands via WebSocket
- **Traxxas RC Vehicle**: The physical platform being controlled

### Software Stack
- **FastAPI Server**: Handles command processing and coordinates the pipeline
- **Whisper**: Open-source speech recognition for converting voice to text
- **Ollama**: Local LLM inference (currently using llama3.2:3b, with recommendations to upgrade to llama3.1:8b)
- **Edge-TTS**: Fast text-to-speech synthesis for audio feedback
- **WebSocket Communication**: Real-time command transmission to the ESP32

## How It Works: Detailed Flow

### 1. Voice Activation
The system continuously listens for the wake word "Hey BT." Once detected, it enters an active listening state.

### 2. Speech Processing
When the user speaks a command, Whisper transcribes the audio into text. The system filters out empty or invalid transcripts to prevent processing errors.

### 3. Intelligent Command Planning
The transcribed text is sent to the LLM with conversation context. The LLM generates specific motor control instructions:
- **Steering Control**: Precise microsecond values where 1500 = straight, with specific ranges for left/right turns
- **Throttle Control**: Speed values for forward/backward movement
- **Duration**: How long to execute the command

The LLM considers previous commands in the conversation, allowing for natural follow-ups like:
- User: "Go forward"
- Robot: *executes*
- User: "Now turn left"
- Robot: *understands context and executes turn*

### 4. Command Execution
The FastAPI server uses a dispatcher system that:
- Queues commands for sequential processing
- Converts LLM output into precise motor control signals
- Sends commands to the ESP32 via WebSocket
- Tracks execution state to prevent command overlap

### 5. Audio Feedback
Edge-TTS generates speech responses confirming the action. The system includes sophisticated feedback prevention to ensure the robot doesn't respond to its own speech output.

## Technical Challenges & Solutions

### Challenge 1: LLM Model Sizing
**Problem**: Smaller models (3b parameters) consistently output incorrect steering values, particularly failing to maintain the neutral position (1500 microseconds) when given directional commands.

**Solution**: Upgrading to larger models (llama3.1:8b) provides better numerical instruction following and steering precision.

### Challenge 2: Pipeline Latency
**Problem**: Multiple processing stages created noticeable delays between command and execution.

**Solutions**:
- Replaced slower TTS systems with Edge-TTS for faster audio synthesis
- Optimized command queuing and execution flow
- Implemented efficient WebSocket communication

### Challenge 3: Audio Feedback Loops
**Problem**: The robot would sometimes respond to its own speech output, creating infinite loops.

**Solution**: Implemented timing coordination and audio filtering to prevent the system from processing its own TTS output.

### Challenge 4: Conversation Context
**Problem**: Each command was processed in isolation, requiring users to be overly explicit.

**Solution**: Implemented conversation memory that maintains state across multiple interactions, allowing for natural follow-up commands.

## Current State

The system is fully functional with:
- ✅ Working wake word detection
- ✅ Reliable speech recognition
- ✅ Context-aware command processing
- ✅ Precise motor control via ESP32
- ✅ Fast audio feedback
- ✅ Multiple interaction modes
- ✅ Sequential command queuing
- ✅ Audio feedback prevention

## Development Approach

The project demonstrates a systematic debugging methodology:
- **Verbose Logging**: Detailed output tracks commands through each pipeline stage
- **Iterative Optimization**: Systematically identifying and upgrading bottlenecks
- **Component Testing**: Isolating problems across voice processing, LLM planning, and motor control stages

## Key Learnings

1. **Model Size Matters**: For precise numerical control, larger LLM models are essential
2. **Pipeline Visibility**: Detailed logging is crucial for debugging multi-stage systems
3. **Timing is Critical**: Preventing audio feedback requires careful coordination of input/output streams
4. **Context Enhances UX**: Conversation memory transforms the interaction from command-driven to conversational

## Future Optimization Opportunities

- Upgrade to llama3.1:8b for better steering accuracy
- Further latency reduction in the processing pipeline
- Enhanced conversation context understanding
- Additional voice feedback customization
