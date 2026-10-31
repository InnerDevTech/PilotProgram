// Jarvis desk controller
//
// Wiring:
//   Buttons 1-4 -> pins 2-5, other leg to GND (uses INPUT_PULLUP, no resistors)
//   Knob (potentiometer) -> outer legs to 5V and GND, middle leg to A0
//   Active buzzer -> + to pin 8, - to GND
//
// Sends to the computer (115200 baud, one message per line):
//   BTN n    button n (1-4) was pressed
//   KNOB m   knob is now at m minutes (1-60), sent only when it changes
//
// Accepts from the computer:
//   BEEP     beep the buzzer 3 times
//   SYNC     resend the current knob value

const int BTN_PINS[4] = {2, 3, 4, 5};
const int KNOB_PIN = A0;
const int BUZZER_PIN = 8;

const unsigned long DEBOUNCE_MS = 30;  // raise this if one press prints twice
const int KNOB_HYSTERESIS = 6;         // raw steps the knob must move before re-checking

const unsigned long BEEP_ON_MS = 200;
const unsigned long BEEP_OFF_MS = 150;

int lastReading[4];
int stableState[4];
unsigned long lastChangeMs[4];

int lastKnobRaw = -100;
int lastMinutes = -1;

int beepsLeft = 0;
unsigned long beepStartMs = 0;

String inputLine = "";

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < 4; i++) {
    pinMode(BTN_PINS[i], INPUT_PULLUP);
    lastReading[i] = HIGH;
    stableState[i] = HIGH;
    lastChangeMs[i] = 0;
  }
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  Serial.println("READY");
}

void loop() {
  readButtons();
  readKnob();
  readSerial();
  updateBuzzer();
}

// A press counts only after the pin has stayed the same for DEBOUNCE_MS.
void readButtons() {
  unsigned long now = millis();
  for (int i = 0; i < 4; i++) {
    int reading = digitalRead(BTN_PINS[i]);
    if (reading != lastReading[i]) {
      lastChangeMs[i] = now;
      lastReading[i] = reading;
    }
    if (now - lastChangeMs[i] >= DEBOUNCE_MS && reading != stableState[i]) {
      stableState[i] = reading;
      if (stableState[i] == LOW) {  // LOW = pressed (pin pulled to GND)
        Serial.print("BTN ");
        Serial.println(i + 1);
      }
    }
  }
}

// Averages a few readings and ignores tiny wiggles so the minutes don't flicker.
void readKnob() {
  long sum = 0;
  for (int i = 0; i < 4; i++) sum += analogRead(KNOB_PIN);
  int raw = sum / 4;

  if (abs(raw - lastKnobRaw) < KNOB_HYSTERESIS) return;
  lastKnobRaw = raw;

  int minutes = constrain(raw * 60L / 1024 + 1, 1, 60);
  if (minutes != lastMinutes) {
    lastMinutes = minutes;
    Serial.print("KNOB ");
    Serial.println(minutes);
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputLine == "BEEP") {
        startBeep(3);
      } else if (inputLine == "SYNC") {
        lastKnobRaw = -100;  // forces readKnob() to send the value again
        lastMinutes = -1;
      }
      inputLine = "";
    } else if (inputLine.length() < 20) {
      inputLine += c;
    }
  }
}

void startBeep(int count) {
  beepsLeft = count;
  beepStartMs = millis();
}

// Beeps without delay(), so the buttons keep working while it beeps.
void updateBuzzer() {
  if (beepsLeft <= 0) {
    digitalWrite(BUZZER_PIN, LOW);
    return;
  }
  unsigned long t = millis() - beepStartMs;
  if (t < BEEP_ON_MS) {
    digitalWrite(BUZZER_PIN, HIGH);
  } else if (t < BEEP_ON_MS + BEEP_OFF_MS) {
    digitalWrite(BUZZER_PIN, LOW);
  } else {
    beepsLeft--;
    beepStartMs = millis();
  }
}
