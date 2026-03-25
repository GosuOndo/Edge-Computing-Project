#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN   26
#define RST_PIN  0

#define SPI_SCK  33
#define SPI_MISO 36
#define SPI_MOSI 32

MFRC522 rfid(SS_PIN, RST_PIN);

// Compact payload including W=<pill_weight_mg> so the Pi can use per-medicine
// weights without relying on hard-coded config values.
// W field: per-pill weight in milligrams (e.g. W=290 for a 290 mg tablet).
// Total must be <= MAX_PAYLOAD_BYTES (56 bytes).
const char* TEST_PAYLOAD = "ID=M001;P=P001;N=ASPIRIN100;D=2;T=08,20;M=AF;S=1;W=290";

// Pages 4-17 = 14 pages = 56 bytes (safe for NTAG213 and larger variants).
// Standard MIFARE Ultralight only has pages 4-15; use NTAG213 or larger tags.
const byte START_PAGE = 4;
const byte END_PAGE = 17;
const int MAX_PAYLOAD_BYTES = 56;

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
      Serial.print("Read failed at page ");
      Serial.print(page);
      Serial.print(": ");
      Serial.println(rfid.GetStatusCodeName(status));
      return "";
    }

    // MIFARE_Read returns 16 data bytes + 2 CRC bytes in buffer
    for (int i = 0; i < 16 && outIndex < MAX_PAYLOAD_BYTES; i++) {
      out[outIndex++] = (char)buffer[i];
    }
  }

  out[MAX_PAYLOAD_BYTES] = '\0';

  // Trim at first null
  String result = String(out);
  result.trim();
  return result;
}

bool writeUltralightPayload(const char* payload) {
  int len = strlen(payload);

  if (len > MAX_PAYLOAD_BYTES) {
    Serial.println("ERROR: Payload too long for safe sticker storage.");
    return false;
  }

  byte data[MAX_PAYLOAD_BYTES];
  memset(data, 0, sizeof(data));
  memcpy(data, payload, len);

  int offset = 0;

  for (byte page = START_PAGE; page <= END_PAGE; page++) {
    MFRC522::StatusCode status = rfid.MIFARE_Ultralight_Write(page, &data[offset], 4);

    if (status != MFRC522::STATUS_OK) {
      Serial.print("Write failed at page ");
      Serial.print(page);
      Serial.print(": ");
      Serial.println(rfid.GetStatusCodeName(status));
      return false;
    }

    offset += 4;
  }

  return true;
}

bool isUltralightTag() {
  MFRC522::PICC_Type piccType = rfid.PICC_GetType(rfid.uid.sak);
  return (
    piccType == MFRC522::PICC_TYPE_MIFARE_UL ||
    piccType == MFRC522::PICC_TYPE_MIFARE_PLUS ||
    String(rfid.PICC_GetTypeName(piccType)).indexOf("Ultralight") >= 0
  );
}

void printUid() {
  Serial.print("UID: ");
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) Serial.print("0");
    Serial.print(rfid.uid.uidByte[i], HEX);
    if (i < rfid.uid.size - 1) Serial.print(" ");
  }
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("Medication Tag Write/Read Test Starting...");

  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, SS_PIN);
  rfid.PCD_Init();

  Serial.println("Reader initialised.");
  Serial.println("Tap ONE spare sticker tag to WRITE and READ test payload.");
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent()) {
    delay(50);
    return;
  }

  if (!rfid.PICC_ReadCardSerial()) {
    delay(50);
    return;
  }

  printUid();

  MFRC522::PICC_Type piccType = rfid.PICC_GetType(rfid.uid.sak);
  Serial.print("Type: ");
  Serial.println(rfid.PICC_GetTypeName(piccType));

  if (!isUltralightTag()) {
    Serial.println("This test is intended for the sticker tag first (Ultralight-type).");
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(1500);
    return;
  }

  Serial.println("Writing payload...");
  Serial.println(TEST_PAYLOAD);

  bool writeOk = writeUltralightPayload(TEST_PAYLOAD);

  if (!writeOk) {
    Serial.println("Write failed.");
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(1500);
    return;
  }

  Serial.println("Write successful.");

  delay(200);

  String readBack = readUltralightPayload();

  Serial.println("Read-back payload:");
  Serial.println(readBack);

  if (readBack == String(TEST_PAYLOAD)) {
    Serial.println("SUCCESS: payload matches.");
  } else {
    Serial.println("WARNING: payload mismatch.");
  }

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  Serial.println("Remove tag before next test.\n");
  delay(3000);
}