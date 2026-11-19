#!/usr/bin/env python3
"""
RouteSense gateway - reads DATA lines from the ESP32 receiver over USB serial
and stores them in SQLite (routesense.db).

Line format from ESP32:
  DATA,node,seq,lat,lon,ax,ay,az,motion,rssi,snr
Lines starting with '#' are debug output and are printed but not stored.

Run:  python3 gateway.py            (auto-detects /dev/ttyUSB* or /dev/ttyACM*)
      python3 gateway.py /dev/ttyUSB0
"""
import glob
import os
import sqlite3
import sys
import time

import serial

BAUD = 115200
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routesense.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS packets (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL,      -- unix time the Pi received it
    node   INTEGER,
    seq    INTEGER,
    lat    REAL,
    lon    REAL,
    ax     REAL,
    ay     REAL,
    az     REAL,
    motion INTEGER,
    rssi   INTEGER,
    snr    REAL,
    lost   INTEGER    -- packets missed since previous one from this node
);
CREATE INDEX IF NOT EXISTS idx_node_ts ON packets(node, ts);
"""


def find_port():
    ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    return ports[0] if ports else None


def open_db():
    db = sqlite3.connect(DB)
    db.execute("PRAGMA journal_mode=WAL")  # lets the dashboard read while we write
    db.executescript(SCHEMA)
    return db


def parse(line):
    """Return dict for a DATA line, or None."""
    parts = line.split(",")
    if len(parts) != 11 or parts[0] != "DATA":
        return None
    try:
        return {
            "node": int(parts[1]),
            "seq": int(parts[2]),
            "lat": float(parts[3]),
            "lon": float(parts[4]),
            "ax": float(parts[5]),
            "ay": float(parts[6]),
            "az": float(parts[7]),
            "motion": int(parts[8]),
            "rssi": int(parts[9]),
            "snr": float(parts[10]),
        }
    except ValueError:
        return None


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else None
    db = open_db()
    last_seq = {}  # node -> last seq seen
    print(f"DB: {DB}")

    while True:
        p = port or find_port()
        if not p:
            print("No serial port found, retrying in 3 s ...")
            time.sleep(3)
            continue
        try:
            with serial.Serial(p, BAUD, timeout=2) as ser:
                print(f"Listening on {p} @ {BAUD}")
                while True:
                    raw = ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("#"):
                        print(line)
                        continue

                    d = parse(line)
                    if d is None:
                        print(f"? ignored: {line}")
                        continue

                    prev = last_seq.get(d["node"])
                    if prev is None or d["seq"] <= prev:
                        lost = 0  # first packet, or node rebooted
                    else:
                        lost = d["seq"] - prev - 1
                    last_seq[d["node"]] = d["seq"]

                    db.execute(
                        "INSERT INTO packets (ts,node,seq,lat,lon,ax,ay,az,motion,rssi,snr,lost) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), d["node"], d["seq"], d["lat"], d["lon"],
                         d["ax"], d["ay"], d["az"], d["motion"], d["rssi"], d["snr"], lost),
                    )
                    db.commit()

                    print(f"node={d['node']} seq={d['seq']} "
                          f"pos=({d['lat']:.5f},{d['lon']:.5f}) "
                          f"motion={d['motion']} rssi={d['rssi']} snr={d['snr']:.1f} "
                          f"lost={lost}")
        except serial.SerialException as e:
            print(f"Serial error: {e} - reconnecting in 3 s ...")
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nStopped.")
            break

    db.close()


if __name__ == "__main__":
    main()
