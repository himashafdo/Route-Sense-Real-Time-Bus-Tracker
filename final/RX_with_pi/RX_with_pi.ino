/*
 * RouteSense - gateway-side receiver (ESP32 + SX1278)
 * Sends one line per packet to the Raspberry Pi over USB serial (115200 baud).
 *
 * Output format (one line per packet):
 *   DATA,node,seq,lat,lon,ax,ay,az,motion,rssi,snr
 * Lines starting with '#' are debug info - the Pi ignores them.
 */
#include <SPI.h>
#include <LoRa.h>

// ---------- MODE ----------
// 1 = fake data (no TX / LoRa module needed) - for testing the Pi side
// 0 = real LoRa receive
#define FAKE_MODE 0

// ---------- LoRa pins ----------
#define L_SCK  18
#define L_MISO 19
#define L_MOSI 23
#define L_NSS  5
#define L_RST  14
#define L_DIO0 26

void emit(int node, long seq, double lat, double lon,
          float ax, float ay, float az, int motion, int rssi, float snr) {
  Serial.printf("DATA,%d,%ld,%.6f,%.6f,%.2f,%.2f,%.2f,%d,%d,%.1f\n",
                node, seq, lat, lon, ax, ay, az, motion, rssi, snr);
}

#if FAKE_MODE
// ================= FAKE DATA GENERATOR =================
#define FAKE_INTERVAL_MS 1000   // each bus reports once per interval
#define FAKE_DROP_PCT    5      // % of packets "lost" (tests loss counting on Pi)

struct FakeBus {
  double lat0, lon0, lat1, lon1;  // route end points
  float  pos;       // 0..1 along route
  float  dir;       // +1 / -1
  int    stopLeft;  // ticks left at a stop
  long   seq;
};

// Approximate routes around Moratuwa (test only). Add a line per extra bus.
FakeBus buses[] = {
  {6.7730, 79.8816, 6.7951, 79.9009, 0.0f, 1.0f, 0, 0},  // node 1: Moratuwa -> Katubedda
  {6.7951, 79.9009, 6.8018, 79.9227, 0.5f, 1.0f, 0, 0},  // node 2: Katubedda -> Piliyandala
};
const int NUM_BUSES = sizeof(buses) / sizeof(buses[0]);

float noise(float amp) { return random(-1000, 1001) / 1000.0f * amp; }

void fakeStep(int i) {
  FakeBus &b = buses[i];
  int motion;

  if (b.stopLeft > 0) {                 // waiting at a stop
    b.stopLeft--;
    motion = 0;
  } else {                              // moving
    motion = 1;
    b.pos += b.dir * 0.003f;            // ~35 km/h             
    if (b.pos >= 1.0f || b.pos <= 0.0f) {
      b.pos = constrain(b.pos, 0.0f, 1.0f);
      b.dir = -b.dir;                   // turn around at terminal
      b.stopLeft = 15;
    } else if (random(100) < 3) {
      b.stopLeft = random(5, 11);       // random bus stop
    }
  }

  double lat = b.lat0 + (b.lat1 - b.lat0) * b.pos + noise(0.00003);  // ~3 m GPS jitter
  double lon = b.lon0 + (b.lon1 - b.lon0) * b.pos + noise(0.00003);

  float a = motion ? 0.35f : 0.02f;     // vibration level
  float ax = noise(a), ay = noise(a), az = 1.0f + noise(a);

  int   rssi = -70 - random(0, 31);     // -70 .. -100 dBm
  float snr  = 10.0f - random(0, 151) / 10.0f;  // 10 .. -5 dB

  long seq = b.seq++;
  if (random(100) < FAKE_DROP_PCT) {
    Serial.printf("# node %d seq %ld dropped (simulated)\n", i + 1, seq);
    return;
  }
  emit(i + 1, seq, lat, lon, ax, ay, az, motion, rssi, snr);
}
#endif

void setup() {
  Serial.begin(115200);
  delay(500);

#if FAKE_MODE
  randomSeed(esp_random());
  Serial.println("# RX ready (FAKE MODE)");
#else
  SPI.begin(L_SCK, L_MISO, L_MOSI, L_NSS);
  LoRa.setPins(L_NSS, L_RST, L_DIO0);
  if (!LoRa.begin(433E6)) {
    Serial.println("# LoRa init failed");
    while (1) delay(1000);
  }
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setSyncWord(0x12);
  LoRa.enableCrc();                     // drop corrupted packets (enable on TX too)
  Serial.println("# RX ready (LoRa)");
#endif
}

void loop() {
#if FAKE_MODE
  static uint32_t last = 0;
  if (millis() - last >= FAKE_INTERVAL_MS) {
    last = millis();
    for (int i = 0; i < NUM_BUSES; i++) fakeStep(i);
  }
#else
  int size = LoRa.parsePacket();
  if (!size) return;

  String msg = "";
  while (LoRa.available()) msg += (char)LoRa.read();
  int   rssi = LoRa.packetRssi();
  float snr  = LoRa.packetSnr();

  int node, motion;
  long seq;
  double lat = 0, lon = 0;
  float ax, ay, az;

  // New format: id,seq,lat,lon,ax,ay,az,motion
  if (sscanf(msg.c_str(), "%d,%ld,%lf,%lf,%f,%f,%f,%d",
             &node, &seq, &lat, &lon, &ax, &ay, &az, &motion) == 8) {
    // ok
  }
  // Current node_check.ino format: seq,ax,ay,az,motion (no id, no GPS)
  else if (sscanf(msg.c_str(), "%ld,%f,%f,%f,%d",
                  &seq, &ax, &ay, &az, &motion) == 5) {
    node = 1;
    lat = lon = 0;                      // 0,0 = no GPS fix
  } else {
    Serial.printf("# bad packet: %s\n", msg.c_str());
    return;
  }
  emit(node, seq, lat, lon, ax, ay, az, motion, rssi, snr);
#endif
}
