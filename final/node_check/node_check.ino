/*
 * RouteSense - bus node (ESP32-C3 SuperMini + SX1278 + MPU6050 + NEO-M8N)
 * Packet: id,seq,lat,lon,ax,ay,az,motion   (lat/lon = 0,0 when no GPS fix)
 *
 */
#include <SPI.h>
#include <Wire.h>
#include <LoRa.h>
#include <TinyGPSPlus.h>

#define NODE_ID 1
#define TX_INTERVAL_MS 1000
#define TX_JITTER_MS   100    

#define L_SCK  4
#define L_MISO 6
#define L_MOSI 7
#define L_NSS  5
#define L_RST  3
#define L_DIO0 8
#define SDA_PIN 2
#define SCL_PIN 1
#define MPU_ADDR 0x68
#define GPS_RX 20  
#define GPS_TX 21   

// Motion detection
#define SAMPLE_MS        20     
#define ACC_RMS_THRESH   0.02    
#define ACC_PEAK_THRESH  0.06    
#define GYRO_THRESH      3.0     
#define MOTION_HOLD_MS   3000    

TinyGPSPlus gps;
uint32_t seq = 0;

// Motion state
float gX = 0, gY = 0, gZ = 1;        // gravity estimate (g)
bool  gInit = false;
float bX = 0, bY = 0, bZ = 0;        // gyro bias estimate (deg/s)
bool  bInit = false;
float accSumSq = 0, accPeak = 0, gyroPeak = 0;
uint16_t accN = 0, okReads = 0;
float lastAx = 0, lastAy = 0, lastAz = 0;
uint32_t lastMotionMs = 0;
bool everMoved = false;

void mpuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission(true);
}

void mpuInit() {
  Wire.begin(SDA_PIN, SCL_PIN);
  mpuWrite(0x6B, 0x00);   
  mpuWrite(0x1A, 0x03);   
  mpuWrite(0x1B, 0x00);   
  mpuWrite(0x1C, 0x00);   
}

bool mpuRead(float &ax, float &ay, float &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);  
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, 6) != 6) return false;
  int16_t x = (Wire.read() << 8) | Wire.read();
  int16_t y = (Wire.read() << 8) | Wire.read();
  int16_t z = (Wire.read() << 8) | Wire.read();
  ax = x / 16384.0;   
  ay = y / 16384.0;
  az = z / 16384.0;
  return true;
}

bool mpuReadGyro(float &wx, float &wy, float &wz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x43);   
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, 6) != 6) return false;
  int16_t x = (Wire.read() << 8) | Wire.read();
  int16_t y = (Wire.read() << 8) | Wire.read();
  int16_t z = (Wire.read() << 8) | Wire.read();
  wx = x / 131.0;     // deg/s (±250 range)
  wy = y / 131.0;
  wz = z / 131.0;
  return true;
}


void sampleMotion() {
  static uint32_t lastS = 0;
  if (millis() - lastS < SAMPLE_MS) return;
  lastS = millis();

  float ax, ay, az;
  if (mpuRead(ax, ay, az)) {
    lastAx = ax; lastAy = ay; lastAz = az;
    okReads++;
    if (!gInit) {
      gX = ax; gY = ay; gZ = az; gInit = true;
    } else {
      const float k = 0.02;            
      gX += k * (ax - gX);
      gY += k * (ay - gY);
      gZ += k * (az - gZ);
      float dx = ax - gX, dy = ay - gY, dz = az - gZ;
      float d2 = dx * dx + dy * dy + dz * dz;
      accSumSq += d2;
      float d = sqrt(d2);
      if (d > accPeak) accPeak = d;
      accN++;
    }
  }

  float wx, wy, wz;
  if (mpuReadGyro(wx, wy, wz)) {
    if (!bInit) {
      bX = wx; bY = wy; bZ = wz; bInit = true;
    } else {
      const float kb = 0.002;        
      bX += kb * (wx - bX);
      bY += kb * (wy - bY);
      bZ += kb * (wz - bZ);
      float rx = wx - bX, ry = wy - bY, rz = wz - bZ;
      float r = sqrt(rx * rx + ry * ry + rz * rz);
      if (r > gyroPeak) gyroPeak = r;
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0); 
  mpuInit();

  Serial1.setRxBufferSize(1024);
  Serial1.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);  

  SPI.begin(L_SCK, L_MISO, L_MOSI, L_NSS);
  LoRa.setPins(L_NSS, L_RST, L_DIO0);
  if (!LoRa.begin(433E6)) { Serial.println("LoRa init failed"); while (1) delay(1000); }
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setTxPower(17);
  LoRa.setSyncWord(0x12);
  LoRa.enableCrc();
  Serial.println("TX ready");
}

void loop() {

  while (Serial1.available()) gps.encode(Serial1.read());

  sampleMotion();

  static uint32_t lastTx = 0;
  static uint32_t txGap = TX_INTERVAL_MS;
  if (millis() - lastTx < txGap) return;
  lastTx = millis();
  txGap = TX_INTERVAL_MS - TX_JITTER_MS + random(0, 2 * TX_JITTER_MS + 1);  

  float ax = 0, ay = 0, az = 0;
  int motion = 0;
  if (okReads > 0) {
    ax = lastAx; ay = lastAy; az = lastAz;
    float accRms = accN ? sqrt(accSumSq / accN) : 0;
    if (accRms > ACC_RMS_THRESH || accPeak > ACC_PEAK_THRESH || gyroPeak > GYRO_THRESH) {
      lastMotionMs = millis();
      everMoved = true;
    }
    motion = (everMoved && millis() - lastMotionMs < MOTION_HOLD_MS) ? 1 : 0;
  } else {
    Serial.println("MPU read failed");
  }
  accSumSq = 0; accPeak = 0; gyroPeak = 0; accN = 0; okReads = 0;

  double lat = 0, lon = 0;
  bool fix = gps.location.isValid() && gps.location.age() < 2000;
  if (fix) {
    lat = gps.location.lat();
    lon = gps.location.lng();
  }

  LoRa.beginPacket();
  LoRa.print(NODE_ID); LoRa.print(",");
  LoRa.print(seq);     LoRa.print(",");
  LoRa.print(lat, 6);  LoRa.print(",");
  LoRa.print(lon, 6);  LoRa.print(",");
  LoRa.print(ax, 2);   LoRa.print(",");
  LoRa.print(ay, 2);   LoRa.print(",");
  LoRa.print(az, 2);   LoRa.print(",");
  LoRa.print(motion);
  LoRa.endPacket();

  Serial.printf("sent id=%d seq=%lu lat=%.6f lon=%.6f motion=%d | fix=%d sats=%d gps_chars=%lu\n",
                NODE_ID, (unsigned long)seq, lat, lon, motion,
                fix, gps.satellites.isValid() ? (int)gps.satellites.value() : 0,
                (unsigned long)gps.charsProcessed());
  seq++;
}