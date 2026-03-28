#include <M5StickCPlus.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <HX711_ADC.h>
#include <Preferences.h>

// =====================================================
// SMART MEDICATION SYSTEM - STATION 1
// COMBINED CALIBRATION + LIVE MQTT FIRMWARE
// WITH PERSISTENT CALIBRATION FACTOR
// =====================================================

// -------------------- WiFi --------------------
const char* WIFI_SSID = "Greggyy";
const char* WIFI_PASSWORD = "12345678";

// -------------------- MQTT --------------------
const char* MQTT_BROKER = "192.168.137.63";
const int MQTT_PORT = 1883;
const char* STATION_ID = "station_1";

// -------------------- HX711 --------------------
const int HX711_DT_PIN  = 32;
const int HX711_SCK_PIN = 33;

// -------------------- Calibration --------------------
// Default fallback if nothing is saved yet
float calibrationFactor = 1.0;
float knownWeight = 100.0;

// -------------------- Behaviour --------------------
const unsigned long MQTT_PUBLISH_STABLE_MS = 500;
const unsigned long MQTT_PUBLISH_CHANGING_MS = 2000;
const unsigned long DISPLAY_UPDATE_MS = 200;

const int FILTER_WINDOW_SIZE = 9;
const float FAST_SMOOTH_ALPHA = 0.30f;
const float SLOW_SMOOTH_ALPHA = 0.12f;
const float CHANGE_THRESHOLD_G = 1.0f;
const float STABILITY_RANGE_G = 0.20f;
const float ZERO_CLAMP_G = 0.20;

// -------------------- Preferences --------------------
Preferences preferences;
const char* PREF_NAMESPACE = "med_scale";
const char* PREF_KEY_CAL = "cal_factor";

// -------------------- MQTT Topics --------------------
char topic_weight[64];
char topic_command[64];
char topic_status[64];

// =====================================================
// GLOBAL OBJECTS
// =====================================================
HX711_ADC scale(HX711_DT_PIN, HX711_SCK_PIN);
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

// Live state
float currentWeight = 0.0f;
float filteredWeight = 0.0f;
bool filterInit = false;
bool isStable = false;

float sampleWindow[FILTER_WINDOW_SIZE];
int sampleIndex = 0;
int sampleCount = 0;

unsigned long lastPublish = 0;
unsigned long lastDisplayUpdate = 0;

// -------------------- Dosing State --------------------
bool dosingActive = false;
float bottleBaseline = 0.0f;
int requiredPills = 0;
float pillWeightG = 0.0f;
unsigned long dosingCorrectSince = 0;
bool dosingCompletePublished = false;
const unsigned long DOSING_CONFIRM_MS = 2000;
const float BOTTLE_MIN_WEIGHT_G = 5.0f;

// =====================================================
// DISPLAY / HELPER FUNCTIONS
// =====================================================
void showMsg(const char* line1,
             const char* line2 = "",
             const char* line3 = "",
             uint16_t color = TFT_WHITE) {
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(color);
  M5.Lcd.setCursor(5, 15);  M5.Lcd.println(line1);
  M5.Lcd.setCursor(5, 35);  M5.Lcd.println(line2);
  M5.Lcd.setCursor(5, 55);  M5.Lcd.println(line3);
}

void waitForButtonA() {
  while (true) {
    M5.update();
    mqttClient.loop();
    if (M5.BtnA.wasPressed()) break;
    delay(10);
  }
}

void pumpUpdates(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms) {
    scale.update();
    mqttClient.loop();
    delay(5);
  }
}

float getStableReading(int samples = 30, int spacingMs = 20) {
  float sum = 0.0f;
  int count = 0;

  for (int i = 0; i < samples; i++) {
    if (scale.update()) {
      sum += scale.getData();
      count++;
    }
    mqttClient.loop();
    delay(spacingMs);
  }

  return (count > 0) ? (sum / count) : 0.0f;
}

void resetFilter(float value = 0.0f) {
  for (int i = 0; i < FILTER_WINDOW_SIZE; i++) {
    sampleWindow[i] = value;
  }
  sampleIndex = 0;
  sampleCount = 0;
  filteredWeight = value;
  filterInit = false;
  currentWeight = (fabs(value) < ZERO_CLAMP_G) ? 0.0f : value;
  isStable = false;
}

void pushSample(float value) {
  sampleWindow[sampleIndex] = value;
  sampleIndex = (sampleIndex + 1) % FILTER_WINDOW_SIZE;
  if (sampleCount < FILTER_WINDOW_SIZE) sampleCount++;
}

float getWindowMedian() {
  if (sampleCount == 0) return 0.0f;

  float sorted[FILTER_WINDOW_SIZE];
  for (int i = 0; i < sampleCount; i++) {
    sorted[i] = sampleWindow[i];
  }

  for (int i = 1; i < sampleCount; i++) {
    float key = sorted[i];
    int j = i - 1;
    while (j >= 0 && sorted[j] > key) {
      sorted[j + 1] = sorted[j];
      j--;
    }
    sorted[j + 1] = key;
  }

  if ((sampleCount % 2) == 0) {
    return (sorted[sampleCount / 2 - 1] + sorted[sampleCount / 2]) * 0.5f;
  }

  return sorted[sampleCount / 2];
}

bool getStableWindowWeight(float* stableWeight) {
  if (sampleCount < FILTER_WINDOW_SIZE) return false;

  float minValue = sampleWindow[0];
  float maxValue = sampleWindow[0];
  for (int i = 1; i < FILTER_WINDOW_SIZE; i++) {
    if (sampleWindow[i] < minValue) minValue = sampleWindow[i];
    if (sampleWindow[i] > maxValue) maxValue = sampleWindow[i];
  }

  if ((maxValue - minValue) > STABILITY_RANGE_G) return false;

  *stableWeight = getWindowMedian();
  return true;
}

// =====================================================
// PERSISTENCE FUNCTIONS
// =====================================================
void loadCalibrationFactor() {
  preferences.begin(PREF_NAMESPACE, true);  // read-only
  float saved = preferences.getFloat(PREF_KEY_CAL, 1.0f);
  preferences.end();

  calibrationFactor = saved;

  Serial.print("Loaded calibration factor: ");
  Serial.println(calibrationFactor, 6);
}

void saveCalibrationFactor(float value) {
  preferences.begin(PREF_NAMESPACE, false);
  preferences.putFloat(PREF_KEY_CAL, value);
  preferences.end();

  Serial.print("Saved calibration factor: ");
  Serial.println(value, 6);
}

void clearSavedCalibrationFactor() {
  preferences.begin(PREF_NAMESPACE, false);
  preferences.remove(PREF_KEY_CAL);
  preferences.end();

  calibrationFactor = 1.0f;
  Serial.println("Saved calibration factor cleared");
}

// =====================================================
// WIFI / MQTT
// =====================================================
void publishStatus(const char* status) {
  StaticJsonDocument<128> doc;
  doc["station_id"] = STATION_ID;
  doc["status"] = status;
  doc["timestamp"] = millis();

  char buffer[128];
  serializeJson(doc, buffer);
  mqttClient.publish(topic_status, buffer);

  Serial.print("Status published: ");
  Serial.println(status);
}

void connectWiFi() {
  Serial.println("Connecting to WiFi...");
  showMsg("WiFi...", WIFI_SSID, "", TFT_YELLOW);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
    showMsg("WiFi OK", WiFi.localIP().toString().c_str(), "", TFT_GREEN);
  } else {
    Serial.println("\nWiFi failed");
    showMsg("WiFi FAIL", "Check SSID/PW", "", TFT_RED);
  }

  delay(700);
}

void connectMQTT() {
  if (mqttClient.connected()) return;

  Serial.println("Connecting to MQTT...");
  showMsg("MQTT...", "", "", TFT_YELLOW);

  String clientId = String("m5stick_") + STATION_ID;

  if (mqttClient.connect(clientId.c_str())) {
    Serial.println("MQTT connected");
    mqttClient.subscribe(topic_command);
    Serial.print("Subscribed to: ");
    Serial.println(topic_command);
    publishStatus("online");
    showMsg("MQTT OK", "", "", TFT_GREEN);
  } else {
    Serial.print("MQTT failed, rc=");
    Serial.println(mqttClient.state());
    showMsg("MQTT FAIL", String(mqttClient.state()).c_str(), "", TFT_RED);
  }

  delay(700);
}

void publishWeightData() {
  StaticJsonDocument<256> doc;
  doc["station_id"] = STATION_ID;
  doc["weight_g"] = round(currentWeight * 100.0f) / 100.0f;
  doc["stable"] = isStable;
  doc["timestamp"] = millis();
  doc["rssi"] = WiFi.RSSI();

  char buffer[256];
  serializeJson(doc, buffer);
  mqttClient.publish(topic_weight, buffer);
}

void runCalibrationWorkflow();

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  Serial.print("MQTT topic: ");
  Serial.println(topic);

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, payload, length);

  if (err) {
    Serial.println("MQTT JSON parse failed");
    return;
  }

  const char* command = doc["command"];
  if (!command) return;
  
  Serial.print("Command received: ");
  Serial.println(command);

  if (strcmp(command, "tare") == 0) {
    showMsg("Re-taring...", "Remove all weight", "Keep still", TFT_YELLOW);
    pumpUpdates(800);
    scale.tare();
    pumpUpdates(1200);

    currentWeight = 0.0f;
    resetFilter(0.0f);

    publishStatus("tare_complete");
    showMsg("Tare complete", "", "", TFT_GREEN);
    delay(800);
  }
  else if (strcmp(command, "calibrate") == 0) {
    float mqttKnownWeight = doc["params"]["known_weight_g"] | 0.0f;
    if (mqttKnownWeight > 0) {
      knownWeight = mqttKnownWeight;
    }
    runCalibrationWorkflow();
  }
  else if (strcmp(command, "clear_calibration") == 0) {
    clearSavedCalibrationFactor();
    scale.setCalFactor(calibrationFactor);
    publishStatus("calibration_cleared");
    showMsg("Calibration", "cleared", "", TFT_YELLOW);
    delay(1000);
  }
  else if (strcmp(command, "start_dosing") == 0) {
    requiredPills = doc["params"]["dosage_pills"] | 1;
    float pillWeightMg = doc["params"]["pill_weight_mg"] | 290.0f;
    pillWeightG = pillWeightMg / 1000.0f;
    bottleBaseline = currentWeight;
    dosingActive = true;
    dosingCompletePublished = false;
    dosingCorrectSince = 0;
    Serial.print("Dosing started. Baseline: ");
    Serial.print(bottleBaseline);
    Serial.print("g, Need: ");
    Serial.print(requiredPills);
    Serial.print(" pills @ ");
    Serial.print(pillWeightG, 3);
    Serial.println("g each");
    publishStatus("dosing_started");
  }
  else if (strcmp(command, "stop_dosing") == 0) {
    dosingActive = false;
    dosingCompletePublished = false;
    publishStatus("dosing_stopped");
  }
}

// =====================================================
// CALIBRATION WORKFLOW
// =====================================================
void runCalibrationWorkflow() {
  Serial.println("Starting calibration workflow...");
  publishStatus("calibration_started");

  // STEP 1: TARE
  showMsg("STEP 1: TARE",
          "Remove ALL weight",
          "Press BtnA",
          TFT_YELLOW);
  waitForButtonA();

  showMsg("Taring...",
          "Do NOT touch",
          "Keep still",
          TFT_YELLOW);

  pumpUpdates(1000);
  scale.tare();
  pumpUpdates(1500);

  float tareCheck = getStableReading(25, 25);

  Serial.print("Tare check: ");
  Serial.println(tareCheck, 3);

  // STEP 2: PLACE KNOWN WEIGHT
  char line2[40];
  snprintf(line2, sizeof(line2), "Place %.0fg on centre", knownWeight);

  showMsg("STEP 2: CALIBRATE",
          line2,
          "Press BtnA",
          TFT_YELLOW);
  waitForButtonA();

  showMsg("Reading weight...",
          "Hands off scale",
          "Settling...",
          TFT_YELLOW);

  pumpUpdates(2500);

  // STEP 3: COMPUTE CAL FACTOR
  showMsg("Calculating...",
          "Please wait",
          "",
          TFT_YELLOW);

  scale.refreshDataSet();
  pumpUpdates(1500);

  float newCal = scale.getNewCalibration(knownWeight);
  scale.setCalFactor(newCal);
  calibrationFactor = newCal;
  saveCalibrationFactor(newCal);

  // STEP 4: VERIFY
  showMsg("Verifying...",
          "Keep weight on",
          "",
          TFT_YELLOW);
          
  pumpUpdates(1200);
  float verifyReading = getStableReading(35, 20);
  float err = verifyReading - knownWeight;

  Serial.print("Calibration Factor: ");
  Serial.println(newCal, 6);
  Serial.print("Verify reading: ");
  Serial.println(verifyReading, 3);
  Serial.print("Error (g): ");
  Serial.println(err, 3);

  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setTextSize(1);
  M5.Lcd.setCursor(5, 5);
  M5.Lcd.setTextColor(TFT_GREEN);
  M5.Lcd.println("CALIBRATED!");

  M5.Lcd.setTextColor(TFT_WHITE);
  M5.Lcd.setCursor(5, 25);
  M5.Lcd.println("Cal Factor:");

  M5.Lcd.setTextSize(2);
  M5.Lcd.setTextColor(TFT_CYAN);
  M5.Lcd.setCursor(5, 40);
  M5.Lcd.println(newCal, 6);

  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(TFT_WHITE);
  M5.Lcd.setCursor(5, 85);
  M5.Lcd.print("Verify: ");
  M5.Lcd.print(verifyReading, 1);
  M5.Lcd.print(" g");

  M5.Lcd.setCursor(5, 100);
  M5.Lcd.print("Error: ");
  M5.Lcd.print(err, 1);
  M5.Lcd.println(" g");

  publishStatus("calibration_complete");

  delay(2500);

  currentWeight = 0.0f;
  resetFilter(0.0f);

  M5.Lcd.fillScreen(BLACK);
}

// =====================================================
// LIVE DISPLAY
// =====================================================
void updateLiveDisplay() {
  M5.Lcd.fillScreen(BLACK);

  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(TFT_YELLOW);
  M5.Lcd.setCursor(5, 5);
  M5.Lcd.println(STATION_ID);

  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(5, 35);
  M5.Lcd.setTextColor(TFT_WHITE);
  M5.Lcd.print("Live: ");

  uint16_t col = isStable ? TFT_GREEN : TFT_ORANGE;
  if (fabs(currentWeight) < 1.0f) col = TFT_DARKGREY;

  M5.Lcd.setTextColor(col);
  M5.Lcd.print(currentWeight, 2);
  M5.Lcd.println(" g");
  
  M5.Lcd.setTextSize(1);
  M5.Lcd.setCursor(5, 80);

  M5.Lcd.setTextColor(WiFi.status() == WL_CONNECTED ? TFT_GREEN : TFT_RED);
  M5.Lcd.print("WiFi ");

  M5.Lcd.setTextColor(mqttClient.connected() ? TFT_GREEN : TFT_RED);
  M5.Lcd.print("MQTT ");

  M5.Lcd.setTextColor(isStable ? TFT_GREEN : TFT_ORANGE);
  M5.Lcd.println(isStable ? "STABLE" : "CHANGING");

  M5.Lcd.setTextColor(TFT_YELLOW);
  M5.Lcd.setCursor(5, 100);
  M5.Lcd.println("BtnA:tare BtnB:cal");
}

// =====================================================
// DOSING DISPLAY
// =====================================================
void updateDosingDisplay() {
  M5.Lcd.fillScreen(BLACK);

  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(TFT_CYAN);
  M5.Lcd.setCursor(5, 5);
  M5.Lcd.println(STATION_ID);

  // Bottle removed — weight dropped far below any reasonable pill-removal delta
  if (currentWeight < BOTTLE_MIN_WEIGHT_G) {
    M5.Lcd.setTextSize(2);
    M5.Lcd.setTextColor(TFT_RED);
    M5.Lcd.setCursor(5, 35);
    M5.Lcd.println("Put bottle");
    M5.Lcd.setCursor(5, 60);
    M5.Lcd.println("back!");
    return;
  }

  float weightDelta = bottleBaseline - currentWeight;
  int pillsRemoved = (int)round(weightDelta / pillWeightG);
  if (pillsRemoved < 0) pillsRemoved = 0;
  int pillsDiff = pillsRemoved - requiredPills;

  char infoLine[32];
  snprintf(infoLine, sizeof(infoLine), "Removed:%d  Need:%d", pillsRemoved, requiredPills);
  M5.Lcd.setTextColor(TFT_WHITE);
  M5.Lcd.setCursor(5, 18);
  M5.Lcd.println(infoLine);

  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(5, 38);

  if (pillsDiff < 0) {
    char msg[32];
    snprintf(msg, sizeof(msg), "Take %d more", -pillsDiff);
    M5.Lcd.setTextColor(TFT_YELLOW);
    M5.Lcd.println(msg);
  } else if (pillsDiff > 0) {
    char msg[32];
    snprintf(msg, sizeof(msg), "Put back %d", pillsDiff);
    M5.Lcd.setTextColor(TFT_RED);
    M5.Lcd.println(msg);
  } else {
    M5.Lcd.setTextColor(TFT_GREEN);
    M5.Lcd.println("Correct!");
    M5.Lcd.setCursor(5, 63);
    M5.Lcd.setTextSize(1);
    M5.Lcd.setTextColor(TFT_WHITE);
    M5.Lcd.println("Hold still...");
  }
}

// =====================================================
// SETUP
// =====================================================
void setup() {
  M5.begin();
  M5.Lcd.setRotation(1);
  Serial.begin(115200);

  sprintf(topic_weight, "medication/weight/%s", STATION_ID);
  sprintf(topic_command, "medication/command/%s", STATION_ID);
  sprintf(topic_status, "medication/status/%s", STATION_ID);

  showMsg("Starting HX711...", "Please wait", "", TFT_YELLOW);

  loadCalibrationFactor();

  scale.begin();
  scale.start(5000, true);

  if (scale.getTareTimeoutFlag() || scale.getSignalTimeoutFlag()) {
    showMsg("ERROR: HX711",
            "Not detected/ready",
            "Check VCC/GND/DT/SCK",
            TFT_RED);
    while (1) delay(1000);
  }

  scale.setCalFactor(calibrationFactor);

  resetFilter(0.0f);

  connectWiFi();

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setKeepAlive(60);
  mqttClient.setSocketTimeout(30);

  connectMQTT();

  // Auto-tare at startup
  showMsg("Startup tare", "Remove all weight", "Keep still", TFT_YELLOW);
  pumpUpdates(800);
  scale.tare();
  pumpUpdates(1200);

  showMsg("Ready", "BtnA=tare", "BtnB=calibrate", TFT_GREEN);
  delay(1000);
}

// =====================================================
// LOOP
// =====================================================
void loop() {
  M5.update();

  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  if (!mqttClient.connected()) {
    connectMQTT();
  }

  mqttClient.loop();

  if (scale.update()) {
    float raw = scale.getData();
    pushSample(raw);

    float median = getWindowMedian();
    if (!filterInit) {
      filteredWeight = median;
      filterInit = true;
    }

    float delta = fabs(median - filteredWeight);
    float alpha = (delta > CHANGE_THRESHOLD_G) ? FAST_SMOOTH_ALPHA : SLOW_SMOOTH_ALPHA;
    filteredWeight += (median - filteredWeight) * alpha;

    float stableWeight = 0.0f;
    if (getStableWindowWeight(&stableWeight)) {
      currentWeight = stableWeight;
      isStable = true;
    } else {
      currentWeight = filteredWeight;
      isStable = false;
    }

    if (fabs(currentWeight) < ZERO_CLAMP_G) {
      currentWeight = 0.0f;
    }
  }

  // Dosing completion check
  if (dosingActive && isStable && !dosingCompletePublished && mqttClient.connected()) {
    // If bottle is off the scale, reset the confirmation timer and skip
    if (currentWeight < BOTTLE_MIN_WEIGHT_G) {
      dosingCorrectSince = 0;
    } else {
      float weightDelta = bottleBaseline - currentWeight;
      int pillsRemoved = (int)round(weightDelta / pillWeightG);
      if (pillsRemoved < 0) pillsRemoved = 0;

      if (pillsRemoved == requiredPills) {
        if (dosingCorrectSince == 0) {
          dosingCorrectSince = millis();
        } else if (millis() - dosingCorrectSince >= DOSING_CONFIRM_MS) {
          // Confirmed — publish detailed dosing_complete status
          StaticJsonDocument<256> statusDoc;
          statusDoc["station_id"] = STATION_ID;
          statusDoc["status"] = "dosing_complete";
          statusDoc["pills_removed"] = pillsRemoved;
          statusDoc["weight_delta_g"] = round(weightDelta * 100.0f) / 100.0f;
          statusDoc["baseline_g"] = round(bottleBaseline * 100.0f) / 100.0f;
          statusDoc["timestamp"] = millis();
          char statusBuf[256];
          serializeJson(statusDoc, statusBuf);
          mqttClient.publish(topic_status, statusBuf);
          Serial.println("Dosing complete published");
          dosingCompletePublished = true;
          dosingActive = false;
        }
      } else {
        dosingCorrectSince = 0;
      }
    }
  }

  if (M5.BtnA.wasPressed()) {
    showMsg("Re-taring...", "Remove all weight", "Keep still", TFT_YELLOW);
    pumpUpdates(800);
    scale.tare();
    pumpUpdates(1200);

    currentWeight = 0.0f;
    resetFilter(0.0f);

    publishStatus("tare_complete");
  }

  if (M5.BtnB.wasPressed()) {
    runCalibrationWorkflow();
  }

  unsigned long now = millis();

  unsigned long publishInterval = isStable ? MQTT_PUBLISH_STABLE_MS : MQTT_PUBLISH_CHANGING_MS;
  if (now - lastPublish >= publishInterval) {
    lastPublish = now;
    if (mqttClient.connected()) {
      publishWeightData();
    }
  }

  if (now - lastDisplayUpdate >= DISPLAY_UPDATE_MS) {
    lastDisplayUpdate = now;
    if (dosingActive) {
      updateDosingDisplay();
    } else {
      updateLiveDisplay();
    }
  }

  delay(20);
}
