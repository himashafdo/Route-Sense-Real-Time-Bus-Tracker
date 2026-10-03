# Bus Tracker: LoRa Link Test (TX / RX)

Range and packet test between a motion-sensing TX node and a laptop-connected RX node.

- **Radio:** SX1278 (433 MHz), LoRa library by Sandeep Mistry
- **Settings (identical on both sides):** 433E6, SF7, BW 125 kHz, sync word 0x12
- **Packet format:** `seq,ax,ay,az,motion`
- **Serial monitor:** 115200 baud

> SX1278 is **3.3 V only**. Attach the antenna before powering.

## TX node: ESP32-C3 Mini + MPU6050 + SX1278

| Signal            | ESP32-C3 GPIO |
| ----------------- | ------------- |
| SX1278 SCK        | 4             |
| SX1278 MISO       | 6             |
| SX1278 MOSI       | 7             |
| SX1278 NSS        | 5             |
| SX1278 RST        | 3             |
| SX1278 DIO0       | 8             |
| SX1278 VCC / GND  | 3V3 / GND     |
| MPU6050 SCL       | 1             |
| MPU6050 SDA       | 2             |
| MPU6050 VCC / GND | 3V3 / GND     |

Powered externally (battery/power bank) during range tests.

Notes:

- Arduino IDE: set **USB CDC On Boot: Enabled** to see Serial output.
- GPIO2 and GPIO8 are strapping pins on the C3. If boot or upload is flaky, move them.

## RX node: ESP32 DevKit + SX1278

| Signal           | ESP32 DevKit GPIO |
| ---------------- | ----------------- |
| SX1278 SCK       | 18                |
| SX1278 MISO      | 19                |
| SX1278 MOSI      | 23                |
| SX1278 NSS       | 5                 |
| SX1278 RST       | 14                |
| SX1278 DIO0      | 26                |
| SX1278 VCC / GND | 3V3 / GND         |

Connected to the laptop via USB. Received data prints in the serial monitor with RSSI, SNR and lost-packet count.

## Bring-up check

Read the SX1278 `RegVersion` register (0x42). Expected value: `0x12`.

- `0x00`: MISO not connected or module unpowered
- `0xFF`: NSS/SCK/MOSI wiring wrong or module unpowered

## Range test

1. Keep RX fixed at the laptop. Carry the powered TX away.
2. Stop every ~50 m for about 1 minute. Note distance, RSSI, SNR and lost count.
3. Range limit: packet loss above ~10-20%, or RSSI near -120 dBm.
4. Keep antennas vertical and high. Test line-of-sight first, then inside a vehicle.
