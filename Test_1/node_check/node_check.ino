#include <SPI.h>
#include <Wire.h>
#include <LoRa.h>

#define L_SCK  4
#define L_MISO 6
#define L_MOSI 7
#define L_NSS  5
#define L_RST  3
#define L_DIO0 8
#define SDA_PIN 2
#define SCL_PIN 1
#define MPU_ADDR 0x68

uint32_t seq = 0;

void mpuInit() {
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);   // PWR_MGMT_1
  Wire.write(0);      // wake up
  Wire.endTransmission(true);
}

bool mpuRead(float &ax, float &ay, float &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);   // ACCEL_XOUT_H
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, 6) != 6) return false;
  int16_t x = (Wire.read() << 8) | Wire.read();
  int16_t y = (Wire.read() << 8) | Wire.read();
  int16_t z = (Wire.read() << 8) | Wire.read();
  ax = x / 16384.0;   // g (±2g range)
  ay = y / 16384.0;
  az = z / 16384.0;
  return true;
}

void setup() {
  Serial.begin(115200);
  mpuInit();
  SPI.begin(L_SCK, L_MISO, L_MOSI, L_NSS);
  LoRa.setPins(L_NSS, L_RST, L_DIO0);
  if (!LoRa.begin(433E6)) { Serial.println("LoRa init failed"); while (1); }
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setTxPower(17);
  LoRa.setSyncWord(0x12);
  Serial.println("TX ready");
}

void loop() {
  float ax, ay, az;
  if (!mpuRead(ax, ay, az)) {
    Serial.println("MPU read failed");
    delay(1000);
    return;
  }
  float mag = sqrt(ax * ax + ay * ay + az * az);
  int motion = (fabs(mag - 1.0) > 0.15) ? 1 : 0;   // deviation from 1g

  LoRa.beginPacket();
  LoRa.print(seq);   LoRa.print(",");
  LoRa.print(ax, 2); LoRa.print(",");
  LoRa.print(ay, 2); LoRa.print(",");
  LoRa.print(az, 2); LoRa.print(",");
  LoRa.print(motion);
  LoRa.endPacket();

  Serial.printf("sent seq=%lu ax=%.2f ay=%.2f az=%.2f motion=%d\n",
                (unsigned long)seq, ax, ay, az, motion);
  seq++;
  delay(1000);
}