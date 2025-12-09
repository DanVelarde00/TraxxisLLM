/*
  ESP32 WebSocket receiver - FULLY NON-BLOCKING
  - Full Traxxas RC vehicle control with VERBOSE DEBUG OUTPUT
  - Encoder on pins 2 & 3
  - Servo control for steering and throttle
*/

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

// ---- CONFIG ----
const char* WIFI_SSID = "Dan-iPhone";
const char* WIFI_PASS = "test12345";

const char* WS_HOST = "172.20.10.7";   // <-- Your laptop's hotspot IP
const uint16_t WS_PORT = 8000;
const char* WS_PATH = "/ws";

// ---- PIN DEFINITIONS ----
const int ENCODER_PIN_A = 2;  // Interrupt capable
const int ENCODER_PIN_B = 3;  // Interrupt capable
const int THROTTLE_PIN = 18;  // PWM output for ESC
const int STEERING_PIN = 19;  // PWM output for servo

// ---- HARDWARE ----
Servo throttleServo;
Servo steeringServo;

// Encoder state
volatile long encoderTicks = 0;
volatile int lastEncoded = 0;

// ---- STATE ----
WebSocketsClient webSocket;
bool ws_connected = false;
uint32_t last_keepalive_ms = 0;
const uint32_t KEEPALIVE_MS = 2000;

// ---- COMMAND EXECUTION STATE ----
enum class CommandState {
  IDLE,
  EXECUTING_MOVE_TIME,
  EXECUTING_MOVE_DIST,
  EXECUTING_MACRO
};

struct ActiveCommand {
  CommandState state = CommandState::IDLE;
  int msg_id = 0;
  uint32_t start_time = 0;
  uint32_t duration_ms = 0;
  int target_ticks = 0;
  int throttle = 1500;
  int steering = 1500;
  int macro_step = 0;
  uint32_t macro_step_start = 0;
  const char* macro_name = nullptr;
};

ActiveCommand currentCmd;

// Debug counter for periodic status
uint32_t last_status_print = 0;
const uint32_t STATUS_PRINT_MS = 200;  // Print status every 200ms during execution

// ============================================================
// ENCODER INTERRUPT HANDLERS
// ============================================================

void IRAM_ATTR encoderISR() {
  int MSB = digitalRead(ENCODER_PIN_A);
  int LSB = digitalRead(ENCODER_PIN_B);
  
  int encoded = (MSB << 1) | LSB;
  int sum = (lastEncoded << 2) | encoded;
  
  // Quadrature encoding logic
  if (sum == 0b1101 || sum == 0b0100 || sum == 0b0010 || sum == 0b1011) {
    encoderTicks++;
  }
  else if (sum == 0b1110 || sum == 0b0111 || sum == 0b0001 || sum == 0b1000) {
    encoderTicks--;
  }
  
  lastEncoded = encoded;
}

// ============================================================
// MOTOR CONTROL IMPLEMENTATION
// ============================================================

void setMotorControl(int throttle, int steering) {
  // Constrain values to valid servo range
  throttle = constrain(throttle, 1000, 2000);
  steering = constrain(steering, 1000, 2000);
  
  throttleServo.writeMicroseconds(throttle);
  steeringServo.writeMicroseconds(steering);
  
  Serial.printf("  [MOTOR OUTPUT] Throttle=%dµs, Steering=%dµs ", throttle, steering);
  
  // Decode what the values mean
  if (throttle == 1500) {
    Serial.print("(NEUTRAL) ");
  } else if (throttle > 1500) {
    Serial.printf("(FORWARD %d%%) ", map(throttle, 1500, 2000, 0, 100));
  } else {
    Serial.printf("(REVERSE %d%%) ", map(throttle, 1000, 1500, 100, 0));
  }
  
  if (steering == 1500) {
    Serial.print("(STRAIGHT)");
  } else if (steering > 1500) {
    Serial.printf("(RIGHT %d%%)", map(steering, 1500, 2000, 0, 100));
  } else {
    Serial.printf("(LEFT %d%%)", map(steering, 1000, 1500, 100, 0));
  }
  
  Serial.println();
}

void stopMotors() {
  Serial.println("  [MOTOR] STOP COMMAND - Setting neutral (1500µs, 1500µs)");
  setMotorControl(1500, 1500);
}

int getEncoderTicks() {
  // Read encoder atomically
  noInterrupts();
  int ticks = encoderTicks;
  interrupts();
  return ticks;
}

void resetEncoder() {
  noInterrupts();
  encoderTicks = 0;
  interrupts();
  Serial.println("  [ENCODER] Reset to 0");
}

// ============================================================
// MESSAGE SENDING
// ============================================================

void sendAck(int msg_id) {
  StaticJsonDocument<256> doc;
  doc["type"] = "ack";
  doc["msg_id"] = msg_id;
  doc["t"] = millis();
  
  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
  Serial.printf("  → ACK sent (msg_id=%d)\n", msg_id);
}

void sendComplete(int msg_id, const char* status = "ok") {
  StaticJsonDocument<256> doc;
  doc["type"] = "complete";
  doc["msg_id"] = msg_id;
  doc["t"] = millis();
  doc["payload"]["status"] = status;
  
  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
  Serial.printf("  → COMPLETE sent (msg_id=%d, status=%s)\n", msg_id, status);
}

void sendHealthPing() {
  StaticJsonDocument<256> doc;
  doc["type"] = "health";
  doc["msg_id"] = 0;
  doc["t"] = millis();
  doc["payload"]["client"] = "esp32";
  doc["payload"]["uptime_ms"] = millis();
  doc["payload"]["encoder_ticks"] = getEncoderTicks();
  
  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
  // Removed the log line - health pings are silent now
}

// ============================================================
// NON-BLOCKING COMMAND EXECUTION
// ============================================================

void startCommand(int msg_id, CommandState state) {
  currentCmd.state = state;
  currentCmd.msg_id = msg_id;
  currentCmd.start_time = millis();
  currentCmd.macro_step = 0;
  last_status_print = 0;  // Reset status print timer
  
  const char* state_name = "UNKNOWN";
  if (state == CommandState::EXECUTING_MOVE_TIME) state_name = "MOVE_TIME";
  else if (state == CommandState::EXECUTING_MOVE_DIST) state_name = "MOVE_DIST";
  else if (state == CommandState::EXECUTING_MACRO) state_name = "MACRO";
  
  Serial.printf("  [EXECUTION START] State=%s, msg_id=%d\n", state_name, msg_id);
}

void finishCommand(const char* status = "ok") {
  Serial.printf("  [EXECUTION END] Status=%s, Duration=%lums\n", 
                status, millis() - currentCmd.start_time);
  stopMotors();
  sendComplete(currentCmd.msg_id, status);
  currentCmd.state = CommandState::IDLE;
  Serial.println("  [DONE]\n");
}

void updateMoveTime() {
  uint32_t elapsed = millis() - currentCmd.start_time;
  
  // Periodic status update
  if (millis() - last_status_print >= STATUS_PRINT_MS) {
    Serial.printf("  [STATUS] Time: %lu/%lums, Throttle=%d, Steering=%d, Encoder=%d\n",
                  elapsed, currentCmd.duration_ms, 
                  currentCmd.throttle, currentCmd.steering,
                  getEncoderTicks());
    last_status_print = millis();
  }
  
  if (elapsed >= currentCmd.duration_ms) {
    Serial.printf("  [MOVE_TIME] Duration reached: %lums\n", elapsed);
    finishCommand("ok");
  }
}

void updateMoveDist() {
  uint32_t elapsed = millis() - currentCmd.start_time;
  int current_ticks = abs(getEncoderTicks());

  // Periodic status update
  if (millis() - last_status_print >= STATUS_PRINT_MS) {
    float progress = (float)current_ticks / currentCmd.target_ticks * 100.0;
    Serial.printf("  [STATUS] Distance: %d/%d ticks (%.1f%%), Time: %lums, Throttle=%d, Steering=%d\n",
                  current_ticks, currentCmd.target_ticks, progress,
                  elapsed, currentCmd.throttle, currentCmd.steering);
    last_status_print = millis();
  }

  // Check if distance reached
  if (current_ticks >= currentCmd.target_ticks) {
    Serial.printf("  [MOVE_DIST] Distance reached! (%d ticks in %lums)\n",
                  current_ticks, elapsed);
    finishCommand("ok");
    return;
  }

  // Check timeout (use duration_ms from command, default 60 seconds)
  uint32_t timeout = (currentCmd.duration_ms > 0) ? currentCmd.duration_ms : 60000;
  if (elapsed >= timeout) {
    Serial.printf("  [MOVE_DIST] Timeout after %lums! Only reached %d/%d ticks\n",
                  elapsed, current_ticks, currentCmd.target_ticks);
    finishCommand("timeout");
  }
}

void updateMacroTest() {
  uint32_t step_elapsed = millis() - currentCmd.macro_step_start;
  
  switch (currentCmd.macro_step) {
    case 0:  // Forward slow for 1s
      if (step_elapsed == 0) {
        Serial.println("    [MACRO TEST] Step 1/3: Forward slow (1600µs) for 1000ms");
        setMotorControl(1600, 1500);
        currentCmd.macro_step_start = millis();
      }
      if (step_elapsed >= 1000) {
        Serial.println("    [MACRO TEST] Step 1 complete");
        currentCmd.macro_step++;
        currentCmd.macro_step_start = millis();
      }
      break;
      
    case 1:  // Turn right for 500ms
      if (step_elapsed == 0) {
        Serial.println("    [MACRO TEST] Step 2/3: Turn right (1700µs) for 500ms");
        setMotorControl(1500, 1700);
        currentCmd.macro_step_start = millis();
      }
      if (step_elapsed >= 500) {
        Serial.println("    [MACRO TEST] Step 2 complete");
        currentCmd.macro_step++;
        currentCmd.macro_step_start = millis();
      }
      break;
      
    case 2:  // Reverse slow for 1s
      if (step_elapsed == 0) {
        Serial.println("    [MACRO TEST] Step 3/3: Reverse slow (1400µs) for 1000ms");
        setMotorControl(1400, 1500);
        currentCmd.macro_step_start = millis();
      }
      if (step_elapsed >= 1000) {
        Serial.println("    [MACRO TEST] All steps complete!");
        finishCommand("ok");
      }
      break;
  }
}

void updateMacroGreet() {
  uint32_t step_elapsed = millis() - currentCmd.macro_step_start;
  
  switch (currentCmd.macro_step) {
    case 0:  // Right
      if (step_elapsed == 0) {
        Serial.println("    [MACRO GREET] Step 1/3: Steering RIGHT (1800µs)");
        setMotorControl(1500, 1800);
        currentCmd.macro_step_start = millis();
      }
      if (step_elapsed >= 200) {
        currentCmd.macro_step++;
        currentCmd.macro_step_start = millis();
      }
      break;
      
    case 1:  // Left
      if (step_elapsed == 0) {
        Serial.println("    [MACRO GREET] Step 2/3: Steering LEFT (1200µs)");
        setMotorControl(1500, 1200);
        currentCmd.macro_step_start = millis();
      }
      if (step_elapsed >= 200) {
        currentCmd.macro_step++;
        currentCmd.macro_step_start = millis();
      }
      break;
      
    case 2:  // Center
      if (step_elapsed == 0) {
        Serial.println("    [MACRO GREET] Step 3/3: Steering CENTER (1500µs)");
        setMotorControl(1500, 1500);
        currentCmd.macro_step_start = millis();
      }
      if (step_elapsed >= 100) {
        Serial.println("    [MACRO GREET] Complete!");
        finishCommand("ok");
      }
      break;
  }
}

void updateMacroTwerk() {
  uint32_t step_elapsed = millis() - currentCmd.macro_step_start;
  
  if (currentCmd.macro_step < 10) {  // 5 oscillations = 10 steps
    bool forward = (currentCmd.macro_step % 2 == 0);
    
    if (step_elapsed == 0) {
      int throttle_val = forward ? 1700 : 1300;
      Serial.printf("    [MACRO TWERK] Step %d/10: %s (%dµs) for 150ms\n",
                    currentCmd.macro_step + 1,
                    forward ? "FORWARD" : "REVERSE",
                    throttle_val);
      setMotorControl(throttle_val, 1500);
      currentCmd.macro_step_start = millis();
    }
    
    if (step_elapsed >= 150) {
      currentCmd.macro_step++;
      currentCmd.macro_step_start = millis();
    }
  } else {
    Serial.println("    [MACRO TWERK] All 10 steps complete!");
    finishCommand("ok");
  }
}

void updateMacro() {
  if (strcmp(currentCmd.macro_name, "test") == 0) {
    updateMacroTest();
  }
  else if (strcmp(currentCmd.macro_name, "greet") == 0) {
    updateMacroGreet();
  }
  else if (strcmp(currentCmd.macro_name, "twerk") == 0) {
    updateMacroTwerk();
  }
  else {
    Serial.printf("    [MACRO ERROR] Unknown macro: '%s'\n", currentCmd.macro_name);
    finishCommand("error_unknown_macro");
  }
}

void updateCommandExecution() {
  switch (currentCmd.state) {
    case CommandState::EXECUTING_MOVE_TIME:
      updateMoveTime();
      break;
      
    case CommandState::EXECUTING_MOVE_DIST:
      updateMoveDist();
      break;
      
    case CommandState::EXECUTING_MACRO:
      updateMacro();
      break;
      
    case CommandState::IDLE:
      // Nothing to do
      break;
  }
}

// ============================================================
// COMMAND PARSING
// ============================================================

void executeCommand(JsonDocument& doc) {
  // Don't accept new commands while executing
  if (currentCmd.state != CommandState::IDLE) {
    Serial.println("[WARN] Command already executing, ignoring new command");
    return;
  }
  
  int msg_id = doc["msg_id"];
  const char* action = doc["payload"]["action"];
  
  Serial.println("\n╔════════════════════════════════════════════════════════");
  Serial.printf("║ [NEW COMMAND] Action='%s', msg_id=%d\n", action, msg_id);
  Serial.println("╚════════════════════════════════════════════════════════");
  
  // Send immediate ACK
  sendAck(msg_id);
  
  // Execute based on action type
  if (strcmp(action, "move_time") == 0) {
    int throt = doc["payload"]["throt"];
    int steer = doc["payload"]["steer"];
    int time_ms = doc["payload"]["time_ms"];
    
    Serial.println("  [COMMAND TYPE] move_time");
    Serial.printf("  [PARAMETERS] throttle=%dµs, steering=%dµs, duration=%dms\n", 
                  throt, steer, time_ms);
    Serial.printf("  [EXPECTED] Run for %.2f seconds\n", time_ms / 1000.0);
    
    currentCmd.throttle = throt;
    currentCmd.steering = steer;
    currentCmd.duration_ms = time_ms;
    
    setMotorControl(throt, steer);
    startCommand(msg_id, CommandState::EXECUTING_MOVE_TIME);
  }
  else if (strcmp(action, "move_dist") == 0) {
    int throt = doc["payload"]["throt"];
    int steer = doc["payload"]["steer"];
    float feet = doc["payload"]["feet"];
    int timeout_ms = doc["payload"]["timeout_ms"] | 60000;  // Default 60s if not specified

    Serial.println("  [COMMAND TYPE] move_dist");
    Serial.printf("  [PARAMETERS] throttle=%dµs, steering=%dµs, distance=%.2fft, timeout=%dms\n",
                  throt, steer, feet, timeout_ms);

    int target_ticks = (int)(feet * 416.0);
    Serial.printf("  [CONVERSION] %.2f feet = %d encoder ticks (@ 416 ticks/ft)\n",
                  feet, target_ticks);

    currentCmd.throttle = throt;
    currentCmd.steering = steer;
    currentCmd.target_ticks = target_ticks;
    currentCmd.duration_ms = timeout_ms;  // Store timeout for updateMoveDist()

    resetEncoder();
    setMotorControl(throt, steer);
    startCommand(msg_id, CommandState::EXECUTING_MOVE_DIST);
  }
  else if (strcmp(action, "stop") == 0) {
    Serial.println("  [COMMAND TYPE] STOP - Emergency motor kill");
    stopMotors();
    sendComplete(msg_id, "ok");
    Serial.println("  [DONE]\n");
  }
  else if (strcmp(action, "speak") == 0) {
    const char* text = doc["payload"]["text"];
    Serial.println("  [COMMAND TYPE] speak");
    Serial.printf("  [TEXT] \"%s\"\n", text);
    Serial.println("  [NOTE] Speech is handled by server, not ESP32");
    sendComplete(msg_id, "ok");
    Serial.println("  [DONE]\n");
  }
  else if (strcmp(action, "macro") == 0) {
    const char* name = doc["payload"]["name"];
    Serial.println("  [COMMAND TYPE] macro");
    Serial.printf("  [MACRO NAME] '%s'\n", name);
    
    currentCmd.macro_name = name;
    currentCmd.macro_step_start = millis();
    startCommand(msg_id, CommandState::EXECUTING_MACRO);
  }
  else {
    Serial.printf("  [ERROR] Unknown action '%s'\n", action);
    sendComplete(msg_id, "error_unknown_action");
    Serial.println("  [DONE]\n");
  }
}

// ============================================================
// WEBSOCKET EVENT HANDLER
// ============================================================

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      ws_connected = false;
      Serial.println("\n[WS] Disconnected from server");
      break;

    case WStype_CONNECTED:
      ws_connected = true;
      Serial.println("\n[WS] Connected to server");
      sendHealthPing();  // Send one on connect
      break;
      
    case WStype_TEXT: {
      // Only print if it's NOT a health message
      StaticJsonDocument<1024> doc;
      DeserializationError error = deserializeJson(doc, payload, length);
      
      if (!error) {
        const char* type = doc["type"];
        
        // Skip logging health messages
        if (strcmp(type, "health") != 0) {
          Serial.print("[WS] Received: ");
          Serial.write(payload, length);
          Serial.println();
          Serial.printf("[WS] Message type: '%s'\n", type);
        }

        if (strcmp(type, "command") == 0) {
          executeCommand(doc);
        }
        // No need to log health - they're just keepalives
      } else {
        Serial.printf("[WS] JSON parse error: %s\n", error.c_str());
      }
      
      break;
    }
    
    default:
      break;
  }
}

// ============================================================
// SETUP & LOOP
// ============================================================

void setup() {
  Serial.begin(115200);
  delay(1000);  // Give serial time to initialize
  
  Serial.println("\n\n");
  Serial.println("════════════════════════════════════════════════════════════");
  Serial.println("  ESP32 TRAXXAS VOICE CONTROLLER v1.0");
  Serial.println("  Non-blocking execution with verbose debug output");
  Serial.println("════════════════════════════════════════════════════════════");
  Serial.println();
  
  // Initialize servo outputs
  Serial.println("[INIT] Attaching servo outputs...");
  throttleServo.attach(THROTTLE_PIN, 1000, 2000);  // Min 1000µs, Max 2000µs
  steeringServo.attach(STEERING_PIN, 1000, 2000);
  Serial.printf("  [OK] Throttle ESC on pin %d\n", THROTTLE_PIN);
  Serial.printf("  [OK] Steering servo on pin %d\n", STEERING_PIN);

  // Set neutral position
  stopMotors();
  Serial.println("  [OK] Servos initialized at neutral (1500µs)");
  
  // Initialize encoder pins
  Serial.println("\n[INIT] Setting up encoder...");
  pinMode(ENCODER_PIN_A, INPUT_PULLUP);
  pinMode(ENCODER_PIN_B, INPUT_PULLUP);
  
  // Attach interrupts for encoder
  attachInterrupt(digitalPinToInterrupt(ENCODER_PIN_A), encoderISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENCODER_PIN_B), encoderISR, CHANGE);

  Serial.printf("  [OK] Encoder A on pin %d (interrupt)\n", ENCODER_PIN_A);
  Serial.printf("  [OK] Encoder B on pin %d (interrupt)\n", ENCODER_PIN_B);
  Serial.println("  [OK] Quadrature decoding active");
  
  // Connect to WiFi
  Serial.println("\n[INIT] Connecting to WiFi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  
  Serial.printf("  SSID: %s\n", WIFI_SSID);
  Serial.print("  Status: ");
}

void loop() {
  // Non-blocking WiFi connection check
  static bool wifi_connected = false;
  static uint32_t last_wifi_check = 0;
  
  if (!wifi_connected && millis() - last_wifi_check > 500) {
    last_wifi_check = millis();
    if (WiFi.status() == WL_CONNECTED) {
      wifi_connected = true;
      Serial.println(" [OK] Connected");
      Serial.printf("  IP Address: %s\n", WiFi.localIP().toString().c_str());

      // Start WebSocket
      Serial.println("\n[INIT] Starting WebSocket client...");
      Serial.printf("  Server: ws://%s:%d%s\n", WS_HOST, WS_PORT, WS_PATH);
      webSocket.begin(WS_HOST, WS_PORT, WS_PATH);
      webSocket.onEvent(webSocketEvent);
      webSocket.setReconnectInterval(5000);

      Serial.println("\n════════════════════════════════════════════════════════════");
      Serial.println("  [READY] System ready - Waiting for voice commands");
      Serial.println("════════════════════════════════════════════════════════════\n");
    } else {
      Serial.print(".");
    }
  }
  
  // Handle WebSocket (non-blocking)
  webSocket.loop();
  
  // Update command execution (non-blocking)
  updateCommandExecution();
  
  // Send periodic keepalive (non-blocking)
  if (ws_connected) {
    uint32_t now = millis();
    if (now - last_keepalive_ms >= KEEPALIVE_MS) {
      sendHealthPing();
      last_keepalive_ms = now;
    }
  }
}