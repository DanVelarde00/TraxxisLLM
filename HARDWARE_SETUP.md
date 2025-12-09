# Hardware Setup Guide

This guide covers the physical hardware setup for the TraxxisLLM voice-controlled RC vehicle system.

## Required Hardware

### Core Components

1. **ESP32 Development Board**
   - Minimum 240MHz dual-core processor
   - WiFi capability (built-in to all ESP32 boards)
   - Minimum 4MB flash memory
   - Recommended: ESP32 DevKit V1 or similar

2. **Traxxas RC Vehicle**
   - Any Traxxas model with standard servo/ESC control
   - Tested with: Traxxas Slash, Rustler, Stampede
   - Must have accessible servo connectors

3. **Quadrature Encoder**
   - 2-channel incremental encoder
   - Recommended: 400-600 PPR (pulses per revolution)
   - Must provide 5V or 3.3V compatible outputs
   - Mounting: Attach to wheel axle or motor shaft

4. **Power Supply**
   - ESP32: USB power or 5V regulated supply
   - RC Vehicle: Standard battery pack (typically 7.2V-11.1V NiMH or LiPo)

### Optional Components

- **External Voltage Regulator**: For powering ESP32 from RC battery
- **Level Shifters**: If encoder outputs are 5V and ESP32 is 3.3V
- **Breadboard/Protoboard**: For initial testing and prototyping

## Wiring Diagram

### ESP32 Pin Connections

```
ESP32 Pin    | Connection              | Description
-------------|-------------------------|---------------------------
GPIO 2       | Encoder Channel A       | Quadrature encoder input
GPIO 3       | Encoder Channel B       | Quadrature encoder input
GPIO 18      | Throttle Servo Signal   | PWM output to ESC
GPIO 19      | Steering Servo Signal   | PWM output to steering servo
GND          | Common Ground           | Connect to encoder, servos
5V/3.3V      | Encoder Power           | Based on encoder voltage
```

### RC Vehicle Connections

1. **Steering Servo**
   - Connect servo signal wire to ESP32 GPIO 19
   - Connect servo power (5V) and ground to appropriate power source
   - Standard 3-wire servo connector: Signal (white/yellow), VCC (red), GND (black/brown)

2. **Electronic Speed Controller (ESC)**
   - Connect ESC signal wire to ESP32 GPIO 18
   - Ensure ESC is powered from RC battery
   - Do NOT connect ESC power (BEC) directly to ESP32 if using separate power supply

3. **Encoder**
   - Mount encoder to measure wheel or motor rotation
   - Channel A to GPIO 2
   - Channel B to GPIO 3
   - Power and ground from ESP32 or external 5V supply

## Physical Installation

### Step 1: Encoder Mounting

1. Identify a rotating component to measure:
   - **Option A**: Rear axle (measures actual wheel movement)
   - **Option B**: Motor shaft (measures motor rotation)

2. Mount encoder using:
   - 3D-printed bracket
   - Aluminum L-bracket
   - Zip ties (temporary testing)

3. Ensure encoder disc/wheel:
   - Rotates freely without rubbing
   - Maintains alignment during movement
   - Is securely fastened to prevent slipping

### Step 2: ESP32 Installation

1. **Temporary Setup** (Testing):
   - Mount ESP32 on breadboard
   - Use jumper wires for all connections
   - Power via USB from laptop

2. **Permanent Installation**:
   - Solder connections to protoboard
   - Mount ESP32 in weatherproof enclosure
   - Use proper strain relief for wires
   - Consider vibration dampening (foam padding)

### Step 3: Servo Integration

1. **Locate existing receiver**:
   - Traxxas vehicles have receiver box near battery compartment
   - Servos are typically connected to channels 1 (steering) and 2 (throttle)

2. **Bypass receiver** (ESP32 takeover):
   - Disconnect steering servo from receiver channel 1
   - Connect steering servo to ESP32 GPIO 19
   - Disconnect ESC from receiver channel 2
   - Connect ESC to ESP32 GPIO 18

3. **Dual control** (Optional - requires switch):
   - Use SPDT relay/switch to toggle between receiver and ESP32
   - Allows manual RC control when voice control is disabled

## Calibration

### Encoder Calibration

1. **Measure distance constant**:
   ```
   - Mark starting position on ground
   - Run command: "go forward 10 feet"
   - Measure actual distance traveled
   - Adjust ticks_per_foot in firmware if needed
   ```

2. **Current setting**: 416 ticks per foot
   - Based on encoder PPR and wheel diameter
   - May need adjustment for different encoders/wheels

### Servo Calibration

1. **Steering Neutral**:
   - Set steering to 1500µs
   - Verify wheels are straight
   - If chassis is warped, adjust neutral point (e.g., 1400µs)

2. **Steering Range**:
   - Test full left (1000µs) and right (2000µs)
   - Ensure no binding or excessive force
   - Adjust min/max values if needed

3. **Throttle Neutral**:
   - Set to 1500µs
   - Motor should not move
   - If crawling, trim ESC or adjust firmware value

4. **Throttle Range**:
   - Forward: Test 1501-1950µs
   - Reverse: Test 1200-1499µs
   - Stay within safe speed limits

## Firmware Configuration

Edit `llmreciever.ino` before uploading:

```cpp
// WiFi Settings
const char* WIFI_SSID = "Your_Network_Name";
const char* WIFI_PASS = "Your_Password";

// Server Settings
const char* WS_HOST = "192.168.1.XXX";  // Your computer's IP
const uint16_t WS_PORT = 8000;

// Pin Definitions (if different from defaults)
const int ENCODER_PIN_A = 2;
const int ENCODER_PIN_B = 3;
const int THROTTLE_PIN = 18;
const int STEERING_PIN = 19;
```

## Uploading Firmware

### Arduino IDE Setup

1. **Install ESP32 Board Support**:
   - File → Preferences → Additional Board Manager URLs
   - Add: `https://dl.espressif.com/dl/package_esp32_index.json`
   - Tools → Board → Boards Manager
   - Search "ESP32" and install

2. **Install Required Libraries**:
   - Sketch → Include Library → Manage Libraries
   - Install: `ArduinoJson`, `WebSocketsClient`, `ESP32Servo`

3. **Configure Board**:
   - Tools → Board → ESP32 Dev Module
   - Tools → Port → Select your ESP32's COM port
   - Tools → Upload Speed → 115200

4. **Upload**:
   - Open `llmreciever.ino`
   - Click Upload button
   - Wait for "Done uploading" message

## Testing

### Initial Power-On Test

1. Open Serial Monitor (115200 baud)
2. You should see:
   ```
   [INIT] Attaching servo outputs...
   [OK] Throttle ESC on pin 18
   [OK] Steering servo on pin 19
   [INIT] Connecting to WiFi...
   [OK] Connected
   [WS] Connected to server
   [READY] System ready - Waiting for voice commands
   ```

### Movement Test

1. **Steering Test**:
   - Send command: "turn left"
   - Verify wheels turn left
   - Send command: "turn right"
   - Verify wheels turn right

2. **Throttle Test**:
   - **IMPORTANT**: Prop up vehicle so wheels are off ground
   - Send command: "go forward"
   - Verify wheels rotate forward
   - Send command: "go backward"
   - Verify wheels rotate backward

3. **Distance Test**:
   - Place vehicle on ground
   - Send command: "go forward 5 feet"
   - Measure actual distance
   - Should be within ±0.5 feet

## Troubleshooting

### ESP32 Won't Connect to WiFi

- Verify SSID and password in firmware
- Check WiFi is 2.4GHz (ESP32 doesn't support 5GHz)
- Ensure WiFi allows client-to-client communication
- Try moving closer to router

### Servos Don't Move

- Check power supply to servos
- Verify servo signal wires connected to correct GPIO pins
- Confirm servos work when connected to receiver
- Check for loose connections

### Encoder Not Reading

- Verify encoder power (should be 3.3V or 5V)
- Check for proper alignment with encoder disc
- Test with multimeter: channels should pulse when rotated
- Confirm interrupt pins are GPIO 2 and 3

### Erratic Movement

- Check for loose encoder connections (causes distance errors)
- Verify battery is charged (low voltage affects ESC behavior)
- Ensure wheels spin freely without obstruction
- Check for interference near WiFi antenna

## Safety Guidelines

1. **Always test on blocks first** - Never test throttle with wheels on ground initially
2. **Keep emergency stop ready** - Know how to cut power quickly
3. **Start with low speeds** - Use conservative throttle values (1600-1650) for testing
4. **Clear testing area** - Ensure no obstacles or people in path
5. **Monitor battery voltage** - Low voltage causes unpredictable behavior

## Next Steps

After hardware is set up and tested:

1. Return to [Quick Start Guide](QUICKSTART_V2.md) to configure software
2. Test with simple commands before complex maneuvers
3. Calibrate distance measurements for accuracy
4. Experiment with different speed and turning values

---

**Need help?** Check the main [README.md](README.md) or open an issue on GitHub.
