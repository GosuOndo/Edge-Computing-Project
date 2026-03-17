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

// Read pages 4 to 15 = safe compact payload area
const byte START_PAGE = 4;
const byte END_PAGE = 15;
const int MAX_PAYLOAD_BYTES = 48;

String lastUid = "";
unsigned long lastScanMs = 0;
const unsigned long SCAN_COOLDOWN_MS = 2000;

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

void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.println("Connecting to MQTT...");
    String clientId = String("m5_tag_") + READER_ID;

    if (mqttClient.connect(clientId.c_str())) {
      Serial.println("MQTT connected");
      publishStatus("online");
    } else {
      Serial.print("MQTT failed, rc=");
      Serial.println(mqttClient.state());
      delay(2000);
    }
  }
}

void publishStatus(const char* status) {
  StaticJsonDocument<128> doc;
  doc["reader_id"] = READER_ID;
  doc["status"] = status;
  doc["timestamp"] = millis();

  char buffer[128];
  serializeJson(doc, buffer);
  mqttClient.publish(topic_status, buffer);
}

String uidToString() {
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

String readUltralightPayload() {
  char out[MAX_PAYLOAD_BYTES + 1];
  memset(out, 0, sizeof(out));

  byte buffer[18];
  byte size = sizeof(buffer);

  int outIndex = 0;

  for (byte page = START_PAGE; page <= END_PAGE; page += 4) {
    size = sizeof(buffer);

    MFRC522::StatusCode status = rfid.MIFARE_Read(page, buffer, &size);
    if (status != MFRC522::STATUS_OK) {
      return "";
    }

    for (int i = 0; i < 16 && outIndex < MAX_PAYLOAD_BYTES; i++) {
      out[outIndex++] = (char)buffer[i];
    }
  }

  out[MAX_PAYLOAD_BYTES] = '\0';
  String result = String(out);
  result.trim();
  return result;
}

void publishTag(String uid, String typeName, String payload) {
  StaticJsonDocument<256> doc;
  doc["reader_id"] = READER_ID;
  doc["tag_uid"] = uid;
  doc["tag_type"] = typeName;
  doc["payload_raw"] = payload;
  doc["timestamp"] = millis();
  doc["rssi"] = WiFi.RSSI();

  char buffer[256];
  serializeJson(doc, buffer);

  mqttClient.publish(topic_read, buffer);
  Serial.println("Tag data published to MQTT.");
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  sprintf(topic_read, "medication/tag/read/%s", READER_ID);
  sprintf(topic_status, "medication/tag/status/%s", READER_ID);

  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, SS_PIN);
  rfid.PCD_Init();

  Serial.println("RC522 reader initialised.");

  connectWiFi();

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  connectMQTT();

  Serial.println("Tap a tag to publish its data.");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  if (!mqttClient.connected()) {
    connectMQTT();
  }

  mqttClient.loop();

  if (!rfid.PICC_IsNewCardPresent()) {
    delay(50);
    return;
  }

  if (!rfid.PICC_ReadCardSerial()) {
    delay(50);
    return;
  }

  String uid = uidToString();

  if (uid == lastUid && millis() - lastScanMs < SCAN_COOLDOWN_MS) {
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(100);
    return;
  }

  lastUid = uid;
  lastScanMs = millis();

  MFRC522::PICC_Type piccType = rfid.PICC_GetType(rfid.uid.sak);
  String typeName = String(rfid.PICC_GetTypeName(piccType));

  Serial.print("UID: ");
  Serial.println(uid);

  Serial.print("Type: ");
  Serial.println(typeName);

  String payload = "";

  if (typeName.indexOf("Ultralight") >= 0) {
    payload = readUltralightPayload();
  }

  Serial.print("Payload: ");
  Serial.println(payload);

  publishTag(uid, typeName, payload);

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  delay(500);
}