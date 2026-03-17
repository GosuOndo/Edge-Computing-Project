#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN   26
#define RST_PIN  0

#define SPI_SCK  33
#define SPI_MISO 36   // using the G36/G25 exposed pin as MISO input
#define SPI_MOSI 32

MFRC522 rfid(SS_PIN, RST_PIN);

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("RC522 + M5StickC Plus test starting...");

  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, SS_PIN);
  rfid.PCD_Init();

  Serial.println("Reader initialised.");
  Serial.println("Tap a tag/card on the RC522...");
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

  Serial.print("UID: ");
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) Serial.print("0");
    Serial.print(rfid.uid.uidByte[i], HEX);
    if (i < rfid.uid.size - 1) Serial.print(" ");
  }
  Serial.println();

  Serial.print("Type: ");
  MFRC522::PICC_Type piccType = rfid.PICC_GetType(rfid.uid.sak);
  Serial.println(rfid.PICC_GetTypeName(piccType));

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  delay(1000);
}