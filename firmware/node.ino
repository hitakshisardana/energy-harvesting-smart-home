/*
 * EHSH - energy-harvesting sensor node (Wokwi ESP32-C3)
 * -----------------------------------------------------
 * This firmware is the "embedded node" side of the project. Its job:
 *
 *   1. know how much energy is in local storage      (potentiometer = state of charge)
 *   2. pick a sensing MODE from that energy level     (HIGH / MEDIUM / LOW / CRITICAL)
 *   3. sense at the cadence for that mode             (DHT22 + PIR + button)
 *   4. publish node state over MQTT                   -> ehsh/<PREFIX>/<NODE_ID>/state
 *   5. accept a schedule command from the hub         <- ehsh/<PREFIX>/<NODE_ID>/schedule
 *      and follow it until the next command
 *
 * It does NOT model piezo / PMIC / supercapacitor physics. The energy number is
 * either the pot (standalone) or a value pushed by the Python hub (closed loop).
 *
 * Message format: config/interface.schema.json  (keep in sync with Member 3).
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ------------------------------------------------------------------ config ---
const char *WIFI_SSID = "Wokwi-GUEST";      // Wokwi's built-in network
const char *WIFI_PASS = "";

const char *MQTT_HOST = "broker.hivemq.com"; // public broker, no login
const int   MQTT_PORT = 1883;

// CHANGE THIS to something unique to your team so you don't clash with other
// people on the public broker (e.g. "ehsh-reet-4821").
const char *PREFIX  = "ehsh-CHANGEME";

// One of: floor | door | furniture | gym | ambient
const char *NODE_ID = "floor";

// ------------------------------------------------------------------- pins ----
const int PIN_SOC = 2;    // potentiometer wiper  -> state of charge
const int PIN_PIR = 3;    // PIR motion OUT
const int PIN_BTN = 4;    // pushbutton (to GND, INPUT_PULLUP) -> simulated event
const int PIN_DHT = 10;   // DHT22 data
const int PIN_LED = 5;    // "sensing active" LED
const int I2C_SDA = 8;
const int I2C_SCL = 9;

// ---------------------------------------------------------------- objects ----
DHT dht(PIN_DHT, DHT22);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool haveOled = false;

WiFiClient   net;
PubSubClient mqtt(net);

// ------------------------------------------------------------- node state ----
enum Mode { M_CRITICAL = 0, M_LOW = 1, M_MEDIUM = 2, M_HIGH = 3 };
const char *MODE_NAME[] = {"CRITICAL", "LOW", "MEDIUM", "HIGH"};

int intervalForMode(Mode m) {
  switch (m) {
    case M_HIGH:   return 5;
    case M_MEDIUM: return 20;
    case M_LOW:    return 60;
    default:       return 0;   // CRITICAL: no routine sensing, alarm only
  }
}

float  soc               = 0.0f;   // 0..100 %
Mode   mode              = M_LOW;
int    samplingInterval  = 60;     // seconds
String lastEvent         = "none";
bool   scheduleOverride  = false;  // true once the hub has sent a command
bool   energyFromHub     = false;  // true if hub also supplies stored_energy_pct

uint32_t lastSenseMs = 0;
uint32_t lastOledMs  = 0;
int      lastPir     = LOW;
int      lastBtn     = HIGH;

char topicState[64];
char topicSched[64];

// --------------------------------------------------------------- helpers ----
Mode modeFromSoc(float s) {
  if (s >= 70) return M_HIGH;
  if (s >= 40) return M_MEDIUM;
  if (s >= 15) return M_LOW;
  return M_CRITICAL;
}

float readSoc() {
  // ESP32 ADC is 12-bit (0..4095). Pot spans the full range.
  int raw = analogRead(PIN_SOC);
  return (raw / 4095.0f) * 100.0f;
}

void drawOled() {
  if (!haveOled) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("node: ");
  oled.println(NODE_ID);
  oled.setTextSize(2);
  oled.setCursor(0, 14);
  oled.printf("%3.0f%%\n", soc);
  oled.setTextSize(1);
  oled.setCursor(0, 36);
  oled.print("mode: ");
  oled.println(MODE_NAME[mode]);
  oled.setCursor(0, 46);
  oled.print("every ");
  oled.print(samplingInterval);
  oled.println("s");
  oled.setCursor(0, 56);
  oled.print(scheduleOverride ? "src: hub " : "src: local ");
  oled.print(lastEvent);
  oled.display();
}

void publishState(const char *evt) {
  StaticJsonDocument<256> doc;
  doc["node_id"]             = NODE_ID;
  doc["ts"]                  = millis();
  doc["stored_energy_pct"]   = roundf(soc * 10) / 10.0;
  doc["mode"]                = MODE_NAME[mode];
  doc["sampling_interval_s"] = samplingInterval;
  doc["last_event"]          = evt;

  if (mode != M_CRITICAL) {
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (!isnan(t) && !isnan(h)) {
      JsonObject s = doc.createNestedObject("sensor");
      s["temp_c"] = roundf(t * 10) / 10.0;
      s["rh"]     = roundf(h * 10) / 10.0;
    }
  }
  doc["fw"] = "0.1";

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicState, buf, n);
  Serial.print("-> ");
  Serial.println(buf);
}

void applyLocalPolicy() {
  if (scheduleOverride) return;         // hub is in charge
  mode = modeFromSoc(soc);
  samplingInterval = intervalForMode(mode);
}

void onMessage(char *topic, byte *payload, unsigned int len) {
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, payload, len)) return;
  if (strcmp(doc["node_id"] | "", NODE_ID) != 0) return;

  const char *m = doc["mode"] | "";
  for (int i = 0; i < 4; i++) {
    if (strcmp(m, MODE_NAME[i]) == 0) mode = (Mode)i;
  }
  if (doc.containsKey("sampling_interval_s"))
    samplingInterval = doc["sampling_interval_s"];
  if (doc.containsKey("stored_energy_pct")) {
    soc = doc["stored_energy_pct"];
    energyFromHub = true;
  }
  scheduleOverride = true;
  Serial.print("<- schedule: mode=");
  Serial.print(MODE_NAME[mode]);
  Serial.print(" every=");
  Serial.println(samplingInterval);
  publishState("none");                 // ack the new schedule
}

void connectWifi() {
  Serial.print("wifi");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.print(" ok  ");
  Serial.println(WiFi.localIP());
}

void connectMqtt() {
  while (!mqtt.connected()) {
    String cid = String("ehsh-") + NODE_ID + "-" + String((uint32_t)esp_random(), HEX);
    Serial.print("mqtt...");
    if (mqtt.connect(cid.c_str())) {
      Serial.println(" ok");
      mqtt.subscribe(topicSched);
      Serial.print("subscribed ");
      Serial.println(topicSched);
    } else {
      Serial.print(" rc=");
      Serial.print(mqtt.state());
      delay(1500);
    }
  }
}

// ------------------------------------------------------------------ setup ----
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_BTN, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  analogReadResolution(12);

  dht.begin();
  Wire.begin(I2C_SDA, I2C_SCL);
  haveOled = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  snprintf(topicState, sizeof(topicState), "%s/%s/state", PREFIX, NODE_ID);
  snprintf(topicSched, sizeof(topicSched), "%s/%s/schedule", PREFIX, NODE_ID);

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  connectMqtt();

  soc = readSoc();
  applyLocalPolicy();
  publishState("none");
}

// ------------------------------------------------------------------- loop ----
void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  if (!energyFromHub) soc = readSoc();
  applyLocalPolicy();

  // --- events: button (footstep/door) and PIR (motion) ---
  int btn = digitalRead(PIN_BTN);
  if (btn == LOW && lastBtn == HIGH) {
    lastEvent = (strcmp(NODE_ID, "door") == 0) ? "door" : "button";
    publishState(lastEvent.c_str());
  }
  lastBtn = btn;

  int pir = digitalRead(PIN_PIR);
  if (pir == HIGH && lastPir == LOW) {
    lastEvent = "motion";
    publishState("motion");
  }
  lastPir = pir;

  // --- routine sensing at the mode's cadence ---
  uint32_t now = millis();
  if (samplingInterval > 0 && now - lastSenseMs >= (uint32_t)samplingInterval * 1000) {
    lastSenseMs = now;
    digitalWrite(PIN_LED, HIGH);
    delay(60);
    digitalWrite(PIN_LED, LOW);
    publishState("none");
  }

  if (now - lastOledMs >= 500) {
    lastOledMs = now;
    drawOled();
  }
}
