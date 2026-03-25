#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// -------------------- WiFi --------------------
const char* WIFI_SSID = "Greggyy";
const char* WIFI_PASSWORD = "12345678";

// -------------------- MQTT --------------------
const char* MQTT_BROKER = "192.168.137.75";
const int MQTT_PORT = 1883;
const char* READER_ID = "tag_reader_1";

// -------------------- RC522 Pins --------------------
#define SS_PIN   26
#define RST_PIN  0

#define SPI_SCK  33
#define SPI_MISO 36
#define SPI_MOSI 32

MFRC522 rfid(SS_PIN, RST_PIN);
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

char topic_read[64];
char topic_status[64];
char topic_command[64];   // Pi sends start_scan / stop_scan here

// Read pages 4 to 15 = safe compact payload area
const byte START_PAGE = 4;
const byte END_PAGE = 15;
const int MAX_PAYLOAD_BYTES = 48;

String lastUid = "";
unsigned long lastScanMs = 0;
const unsigned long SCAN_COOLDOWN_MS = 2000;

// ---------------------------------------------------------------------------
// Scanning gate.
// true  = RF polling active  (default on boot so onboarding works immediately)
// false = RF polling paused  (Pi sends stop_scan after onboarding completes)
//
// The Pi sends start_scan when the bottle is lifted during daily use,
// and stop_scan again after identity verification is complete.
// ---------------------------------------------------------------------------
bool scanningEnabled = true;

// ---------------------------------------------------------------------------
// WiFi / MQTT
// ---------------------------------------------------------------------------

void publishStatus(const char* status);   // forward declaration

void connectWiFi() {
  Serial.println("Connecting to WiFi...");
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
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi failed");
  }
}

void publishStatus(const char* status) {
  StaticJsonDocument<128> doc;
  doc["reader_id"] = READER_ID;
  doc["status"]    = status;
  doc["scanning"]  = scanningEnabled;   // Pi can inspect current gate state
  doc["timestamp"] = millis();

  char buffer[128];
  serializeJson(doc, buffer);
  mqttClient.publish(topic_status, buffer);
}

// ---------------------------------------------------------------------------
// MQTT command handler  (runs on the mqttClient.loop() thread)
//
// Accepted commands:
//   {"command": "start_scan"}  — enable RF polling
//   {"command": "stop_scan"}   — disable RF polling
// ---------------------------------------------------------------------------
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<128> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.println("Command JSON parse failed");
    return;
  }

  const char* command = doc["command"];
  if (!command) return;

  Serial.print("Command received: ");
  Serial.println(command);

  if (strcmp(command, "start_scan") == 0) {
    if (!scanningEnabled) {
      scanningEnabled = true;
      // Reset cooldown so the card already on the reader fires immediately
      lastUid    = "";
      lastScanMs = 0;
      Serial.println("Scanning ENABLED by Pi");
      publishStatus("scan_started");
    }
  }
  else if (strcmp(command, "stop_scan") == 0) {
    if (scanningEnabled) {
      scanningEnabled = false;
      // Halt any card currently seated on the reader so it does not
      // re-trigger when scanning is re-enabled later.
      rfid.PICC_HaltA();
      rfid.PCD_StopCrypto1();
      Serial.println("Scanning DISABLED by Pi");
      publishStatus("scan_stopped");
    }
  }
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.println("Connecting to MQTT...");
    String clientId = String("m5_tag_") + READER_ID;

    if (mqttClient.connect(clientId.c_str())) {
      Serial.println("MQTT connected");
      mqttClient.subscribe(topic_command, 1);   // subscribe to command topic
      publishStatus("online");
      Serial.print("Subscribed to command topic: ");
      Serial.println(topic_command);
    } else {
      Serial.print("MQTT failed, rc=");
      Serial.println(mqttClient.state());
      delay(2000);
    }
  }
}

// ---------------------------------------------------------------------------
// UID helper
// ---------------------------------------------------------------------------

String uidToString() {
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

// ---------------------------------------------------------------------------
// Ultralight payload read — single pass, no retries
// ---------------------------------------------------------------------------

String readUltralightPayload() {
  char out[MAX_PAYLOAD_BYTES + 1];
  memset(out, 0, sizeof(out));

  byte buffer[18];
  byte size    = sizeof(buffer);
  int outIndex = 0;

  for (byte page = START_PAGE; page <= END_PAGE; page += 4) {
    size   = sizeof(buffer);
    MFRC522::StatusCode status = rfid.MIFARE_Read(page, buffer, &size);
    if (status != MFRC522::STATUS_OK) {
      Serial.print("Read failed at page ");
      Serial.print(page);
      Serial.print(": ");
      Serial.println(rfid.GetStatusCodeName(status));
      return "";
    }
    // MIFARE_Read returns 16 data bytes + 2 CRC bytes
    for (int i = 0; i < 16 && outIndex < MAX_PAYLOAD_BYTES; i++) {
      out[outIndex++] = (char)buffer[i];
    }
  }

  out[MAX_PAYLOAD_BYTES] = '\0';
  String result = String(out);
  result.trim();
  return result;
}

// ---------------------------------------------------------------------------
// Retry wrapper — handles tag settling and HALT re-activation
// ---------------------------------------------------------------------------

String readUltralightPayloadWithRetry(int maxAttempts = 4, int delayMs = 120) {
  for (int attempt = 1; attempt <= maxAttempts; attempt++) {
    String payload = readUltralightPayload();

    if (payload.length() > 0) {
      if (attempt > 1) {
        Serial.print("Payload read succeeded on attempt ");
        Serial.println(attempt);
      }
      return payload;
    }

    Serial.print("Payload empty on attempt ");
    Serial.print(attempt);
    Serial.print("/");
    Serial.println(maxAttempts);

    if (attempt < maxAttempts) {
      delay(delayMs);
      // PICC_WakeupA (WUPA) re-activates cards in HALT state so MIFARE_Read
      // can be called again on the next attempt.
      byte atqa[2];
      byte atqaSize = sizeof(atqa);
      rfid.PICC_WakeupA(atqa, &atqaSize);
      delay(20);
      rfid.PICC_ReadCardSerial();   // re-select so MIFARE_Read is authorised
    }
  }

  Serial.println("ERROR: all payload read attempts failed");
  return "";
}

// ---------------------------------------------------------------------------
// Publish tag scan
// ---------------------------------------------------------------------------

void publishTag(String uid, String typeName, String payload) {
  StaticJsonDocument<256> doc;
  doc["reader_id"]   = READER_ID;
  doc["tag_uid"]     = uid;
  doc["tag_type"]    = typeName;
  doc["payload_raw"] = payload;
  doc["timestamp"]   = millis();
  doc["rssi"]        = WiFi.RSSI();

  char buffer[256];
  serializeJson(doc, buffer);
  mqttClient.publish(topic_read, buffer);
  Serial.println("Tag data published to MQTT.");
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(1000);

  sprintf(topic_read,    "medication/tag/read/%s",    READER_ID);
  sprintf(topic_status,  "medication/tag/status/%s",  READER_ID);
  sprintf(topic_command, "medication/tag/command/%s", READER_ID);

  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, SS_PIN);
  rfid.PCD_Init();
  Serial.println("RC522 reader initialised.");

  connectWiFi();

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);   // register command handler
  connectMQTT();

  Serial.println("Ready. Scanning ENABLED by default (onboarding mode).");
}

// ---------------------------------------------------------------------------
// Loop
// ---------------------------------------------------------------------------

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  if (!mqttClient.connected()) {
    connectMQTT();
  }

  // Must run every iteration to receive start_scan / stop_scan commands
  // even when RF polling is paused.
  mqttClient.loop();

  // -------------------------------------------------------------------------
  // Scanning gate: skip all RF activity when disabled.
  // -------------------------------------------------------------------------
  if (!scanningEnabled) {
    delay(50);
    return;
  }

  // Try to detect a card.  Also wake halted cards already seated on the
  // reader so the patient does not need to remove and replace the bottle.
  bool cardPresent = rfid.PICC_IsNewCardPresent();

  if (!cardPresent) {
    byte atqa[2];
    byte atqaSize = sizeof(atqa);
    MFRC522::StatusCode wakeStatus = rfid.PICC_WakeupA(atqa, &atqaSize);
    cardPresent = (wakeStatus == MFRC522::STATUS_OK ||
                   wakeStatus == MFRC522::STATUS_COLLISION);
  }

  if (!cardPresent) {
    delay(50);
    return;
  }

  if (!rfid.PICC_ReadCardSerial()) {
    delay(50);
    return;
  }

  String uid = uidToString();

  // Suppress duplicate scans of the same card within the cooldown window
  if (uid == lastUid && millis() - lastScanMs < SCAN_COOLDOWN_MS) {
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(100);
    return;
  }

  lastUid    = uid;
  lastScanMs = millis();

  MFRC522::PICC_Type piccType = rfid.PICC_GetType(rfid.uid.sak);
  String typeName = String(rfid.PICC_GetTypeName(piccType));

  Serial.print("UID: ");   Serial.println(uid);
  Serial.print("Type: ");  Serial.println(typeName);

  String payload = "";
  if (typeName.indexOf("Ultralight") >= 0) {
    payload = readUltralightPayloadWithRetry();
  }

  Serial.print("Payload: "); Serial.println(payload);

  if (payload.length() > 0) {
    publishTag(uid, typeName, payload);
  } else {
    Serial.println("WARNING: empty payload after all retries — not publishing.");
  }

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  delay(500);
}
