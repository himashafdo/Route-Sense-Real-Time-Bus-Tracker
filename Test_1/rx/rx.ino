#include <SPI.h>
#include <LoRa.h>

#define L_SCK  18
#define L_MISO 19
#define L_MOSI 23
#define L_NSS  5
#define L_RST  14
#define L_DIO0 26

long lastSeq = -1;
uint32_t received = 0;

void setup() {
  Serial.begin(115200);
  SPI.begin(L_SCK, L_MISO, L_MOSI, L_NSS);
  LoRa.setPins(L_NSS, L_RST, L_DIO0);
  if (!LoRa.begin(433E6)) { Serial.println("LoRa init failed"); while (1); }
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setSyncWord(0x12);
  Serial.println("RX ready");
}

void loop() {
  int size = LoRa.parsePacket();
  if (!size) return;

  String msg = "";
  while (LoRa.available()) msg += (char)LoRa.read();

  // Packet format: seq,ax,ay,az,motion
  long seq;
  float ax, ay, az;
  int motion;
  if (sscanf(msg.c_str(), "%ld,%f,%f,%f,%d", &seq, &ax, &ay, &az, &motion) != 5) {
    Serial.printf("bad packet: %s\n", msg.c_str());
    return;
  }

  received++;
  long lost = (lastSeq >= 0) ? seq - lastSeq - 1 : 0;
  lastSeq = seq;

  Serial.printf("seq=%ld ax=%.2f ay=%.2f az=%.2f motion=%d | RSSI=%d dBm SNR=%.1f dB | lost=%ld total_rx=%lu\n",
                seq, ax, ay, az, motion, LoRa.packetRssi(), LoRa.packetSnr(), lost, received);
}