#!/usr/bin/env python3
"""
RouteSense dashboard v2 - live fleet map + bus cards + events.
Reads routesense.db written by gateway.py.

Run:   python3 dashboard.py
Open:  http://localhost:5000        (on the Pi)
       http://<pi-ip>:5000          (laptop / phone on same Wi-Fi)

Edit NODES and STOPS below for your demo.
"""
import math
import os
import sqlite3
import time

from flask import Flask, Response, jsonify

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routesense.db")
OFFLINE_AFTER_S = 10     # no packet for this long -> offline
STATS_WINDOW_S = 300     # loss / RSSI stats over last 5 min
TRAIL_POINTS = 200       # trail length per bus
SPARK_POINTS = 60        # RSSI history points per bus

# ---- Fleet config: expected buses (shown even before they send anything) ----
NODES = {
    1: {"name": "Bus 01", "route": "Moratuwa ⇄ Katubedda",    "color": "#22d3ee"},
    2: {"name": "Bus 02", "route": "Katubedda ⇄ Piliyandala", "color": "#f472b6"},
}

# ---- Bus stops drawn on the map (approximate - edit to real coordinates) ----
STOPS = [
    {"name": "Moratuwa",        "lat": 6.7730, "lon": 79.8816},
    {"name": "Katubedda (UoM)", "lat": 6.7951, "lon": 79.9009},
    {"name": "Piliyandala",     "lat": 6.8018, "lon": 79.9227},
]

PALETTE = ["#22d3ee", "#f472b6", "#a3e635", "#fb923c", "#a78bfa", "#facc15"]

app = Flask(__name__)


def query(sql, args=()):
    if not os.path.exists(DB):
        return []
    db = sqlite3.connect(DB, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []  # table not created yet
    finally:
        db.close()


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def node_meta(nid):
    if nid in NODES:
        return NODES[nid]
    return {"name": f"Bus {nid:02d}", "route": "Unregistered node",
            "color": PALETTE[(nid - 1) % len(PALETTE)]}


@app.route("/api/config")
def config():
    return jsonify({"stops": STOPS})


@app.route("/api/status")
def status():
    now = time.time()
    latest = {r["node"]: r for r in query(
        "SELECT p.* FROM packets p "
        "JOIN (SELECT node, MAX(id) AS mid FROM packets GROUP BY node) m ON p.id = m.mid")}
    stats = {r["node"]: r for r in query(
        "SELECT node, COUNT(*) AS rx, COALESCE(SUM(lost),0) AS lost FROM packets "
        "WHERE ts > ? GROUP BY node", (now - STATS_WINDOW_S,))}

    nodes = []
    for nid in sorted(set(NODES) | set(latest)):
        meta = node_meta(nid)
        p = latest.get(nid)
        if p is None:
            nodes.append({"node": nid, **meta, "seen": False, "state": "waiting"})
            continue

        s = stats.get(nid, {"rx": 0, "lost": 0})
        total = s["rx"] + s["lost"]
        age = now - p["ts"]
        has_fix = not (p["lat"] == 0 and p["lon"] == 0)
        state = "offline" if age > OFFLINE_AFTER_S else ("moving" if p["motion"] else "stopped")

        trail = query("SELECT ts, lat, lon FROM packets WHERE node = ? AND NOT (lat = 0 AND lon = 0) "
                      "ORDER BY id DESC LIMIT ?", (nid, TRAIL_POINTS))
        trail.reverse()

        speed = None
        if state == "stopped":
            speed = 0.0
        elif state == "moving":
            recent = [t for t in trail[-6:] if now - t["ts"] < 30]
            if len(recent) >= 2 and recent[-1]["ts"] - recent[0]["ts"] >= 2:
                dist = sum(haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                           for a, b in zip(recent, recent[1:]))
                speed = dist / (recent[-1]["ts"] - recent[0]["ts"]) * 3.6

        rssi_hist = [r["rssi"] for r in query(
            "SELECT rssi FROM packets WHERE node = ? ORDER BY id DESC LIMIT ?", (nid, SPARK_POINTS))][::-1]
        lost_total = query("SELECT COALESCE(SUM(lost),0) AS l FROM packets WHERE node = ?", (nid,))[0]["l"]

        nodes.append({
            "node": nid, **meta, "seen": True, "state": state,
            "seq": p["seq"], "lat": p["lat"], "lon": p["lon"], "has_fix": has_fix,
            "rssi": p["rssi"], "snr": p["snr"], "age": round(age, 1),
            "speed_kmh": None if speed is None else round(speed, 1),
            "rx": s["rx"], "lost": s["lost"], "lost_total": lost_total,
            "loss_pct": round(100 * s["lost"] / total, 1) if total else 0.0,
            "rssi_hist": rssi_hist,
            "trail": [[t["lat"], t["lon"]] for t in trail],
        })

    last = query("SELECT MAX(ts) AS t FROM packets")
    last_ts = last[0]["t"] if last else None
    ppm = query("SELECT COUNT(*) AS c FROM packets WHERE ts > ?", (now - 60,))
    win = query("SELECT COUNT(*) AS rx, COALESCE(SUM(lost),0) AS lost, AVG(rssi) AS rssi "
                "FROM packets WHERE ts > ?", (now - STATS_WINDOW_S,))
    w = win[0] if win else {"rx": 0, "lost": 0, "rssi": None}
    wt = w["rx"] + w["lost"]

    return jsonify({
        "time": now,
        "gateway": {"last_packet_age": None if last_ts is None else round(now - last_ts, 1)},
        "summary": {
            "online": sum(1 for n in nodes if n["state"] in ("moving", "stopped")),
            "total": len(nodes),
            "ppm": ppm[0]["c"] if ppm else 0,
            "loss_pct": round(100 * w["lost"] / wt, 1) if wt else None,
            "avg_rssi": round(w["rssi"]) if w["rssi"] is not None else None,
        },
        "nodes": nodes,
    })


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RouteSense - Live Fleet</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{
  --bg:#0a0f1c; --panel:#111827; --line:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --dim:#64748b;
  --moving:#22c55e; --stopped:#f59e0b; --offline:#64748b; --bad:#ef4444;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
  font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:14px}
.app{display:grid;grid-template-rows:auto auto 1fr;height:100vh;gap:12px;padding:14px}
header{display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:40px;height:40px;border-radius:11px;background:linear-gradient(135deg,#22d3ee,#6366f1);
  display:grid;place-items:center;font-weight:800;color:#0a0f1c;font-size:15px}
.brand h1{margin:0;font-size:19px;letter-spacing:.2px}
.brand p{margin:2px 0 0;color:var(--muted);font-size:12px}
.hstat{display:flex;align-items:center;gap:14px;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.live{display:flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;background:var(--panel);border:1px solid var(--line);color:var(--text)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--offline);display:inline-block}
.live.on .dot{background:var(--moving);animation:ping 1.6s infinite}
.live.off .dot{background:var(--bad)}
@keyframes ping{0%{box-shadow:0 0 0 0 rgba(34,197,94,.55)}100%{box-shadow:0 0 0 10px rgba(34,197,94,0)}}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 16px}
.kpi .label{color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.07em}
.kpi .value{font-size:26px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi .value small,.m .v small{font-size:12px;color:var(--muted);font-weight:500;margin-left:3px}

.main{display:grid;grid-template-columns:380px 1fr;gap:12px;min-height:0}
.side{display:flex;flex-direction:column;gap:12px;min-height:0;overflow:auto}
#buses{display:flex;flex-direction:column;gap:12px}
.bus{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--c);border-radius:14px;
  padding:14px;cursor:pointer;transition:border-color .2s,box-shadow .2s}
.bus:hover{border-color:#334155;border-left-color:var(--c)}
.bus.sel{box-shadow:0 0 0 1px var(--c)}
.bus.waiting{border-style:dashed;border-left-style:dashed;border-left-color:#475569;cursor:default;opacity:.8}
.bus-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.bus-name{font-weight:700;font-size:15px;display:flex;align-items:center;gap:8px}
.swatch{width:10px;height:10px;border-radius:50%;background:var(--c)}
.route{color:var(--muted);font-size:12px;margin-top:3px}
.pill{font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:4px 9px;border-radius:999px;white-space:nowrap}
.pill.moving{background:rgba(34,197,94,.15);color:#4ade80}
.pill.stopped{background:rgba(245,158,11,.15);color:#fbbf24}
.pill.offline{background:rgba(100,116,139,.2);color:#94a3b8}
.pill.waiting{background:rgba(71,85,105,.25);color:#94a3b8}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}
.m .k{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em}
.m .v{font-size:17px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.sig{margin-top:12px}
.sigrow{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.bar{height:6px;background:#1e293b;border-radius:99px;margin-top:6px;overflow:hidden}
.bar>i{display:block;height:100%;border-radius:99px;transition:width .4s}
.spark{width:100%;height:34px;margin-top:8px;display:block}
.coords{margin-top:6px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--muted);
  display:flex;justify-content:space-between}
.nofix{color:#c4b5fd}
.hint{color:var(--muted);font-size:12px;margin-top:10px;line-height:1.45}

.events{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px;
  flex:1;min-height:170px;display:flex;flex-direction:column}
.events h3{margin:0 0 6px;font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:600}
#log{overflow:auto;flex:1;font-size:12.5px}
.ev{display:flex;gap:10px;padding:5px 0;border-bottom:1px solid #162033}
.ev time{color:var(--dim);font-variant-numeric:tabular-nums;flex:none}
.ev.warn span{color:#fbbf24}.ev.bad span{color:#f87171}.ev.good span{color:#4ade80}

.mapwrap{position:relative;border-radius:14px;overflow:hidden;border:1px solid var(--line);min-height:0}
#map{position:absolute;inset:0;background:#0b1220}
.mapbar{position:absolute;top:12px;left:12px;z-index:500;display:flex;gap:8px}
.btn{background:rgba(15,23,42,.92);color:var(--text);border:1px solid #334155;border-radius:10px;
  padding:7px 12px;font-size:12px;cursor:pointer;font-family:inherit}
.btn:hover{border-color:#64748b}
.legend{position:absolute;bottom:12px;left:12px;z-index:500;background:rgba(15,23,42,.92);border:1px solid var(--line);
  border-radius:10px;padding:8px 12px;font-size:12px;color:var(--muted);display:flex;gap:14px}
.legend span{display:flex;align-items:center;gap:6px}
.lstop{width:10px;height:10px;border-radius:3px;background:#f8fafc;display:inline-block}

.bm{width:34px;height:34px;border-radius:50%;background:var(--c);display:grid;place-items:center;color:#0a0f1c;
  font-weight:800;font-size:13px;border:3px solid #0a0f1c;box-shadow:0 0 0 2px var(--ring),0 4px 14px rgba(0,0,0,.6);position:relative}
.bm.moving{--ring:#22c55e}.bm.stopped{--ring:#f59e0b}
.bm.offline{--ring:#64748b;filter:grayscale(.85);opacity:.7}
.bm.moving::after{content:"";position:absolute;inset:-7px;border-radius:50%;border:2px solid var(--c);animation:pulse 1.8s infinite}
@keyframes pulse{0%{transform:scale(.8);opacity:.9}100%{transform:scale(1.7);opacity:0}}
.stop{width:12px;height:12px;border-radius:3px;background:#f8fafc;border:2px solid #0a0f1c;box-shadow:0 0 0 1px #64748b}
.leaflet-tooltip.dark{background:#0f172a;color:#e2e8f0;border:1px solid #334155;box-shadow:none;font-weight:600}
.leaflet-tooltip-top.dark:before{border-top-color:#334155}
.leaflet-tooltip-right.dark:before{border-right-color:#334155}
.leaflet-container{font-family:inherit}
.leaflet-control-attribution{background:rgba(15,23,42,.8)!important;color:#64748b!important}
.leaflet-control-attribution a{color:#94a3b8!important}
.leaflet-bar a{background:#0f172a;color:#e2e8f0;border-bottom-color:#1e293b}
.leaflet-bar a:hover{background:#1e293b}
.leaflet-control-layers{background:rgba(15,23,42,.92)!important;color:#e2e8f0;border:1px solid #334155!important;border-radius:10px!important}
.leaflet-control-layers label{font-size:12px}
.maperr{position:absolute;inset:0;display:none;place-items:center;color:var(--muted);z-index:600;text-align:center;padding:20px}

@media (max-width:1000px){
  .app{height:auto}.main{grid-template-columns:1fr}.mapwrap{height:60vh;order:-1}.kpis{grid-template-columns:repeat(2,1fr)}
}
@media (max-width:560px){ #clock,.brand p{display:none} }
.live{white-space:nowrap}
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="brand">
      <div class="logo">RS</div>
      <div><h1>RouteSense</h1><p>LoRa bus fleet tracking &middot; Raspberry Pi 5 gateway</p></div>
    </div>
    <div class="hstat">
      <span id="clock">--:--:--</span>
      <div class="live" id="live"><span class="dot"></span><span id="liveTxt">Connecting&hellip;</span></div>
    </div>
  </header>

  <section class="kpis">
    <div class="kpi"><div class="label">Buses online</div><div class="value" id="kOnline">&ndash;</div></div>
    <div class="kpi"><div class="label">Packets / min</div><div class="value" id="kPpm">&ndash;</div></div>
    <div class="kpi"><div class="label">Packet loss &middot; 5 min</div><div class="value" id="kLoss">&ndash;</div></div>
    <div class="kpi"><div class="label">Avg RSSI &middot; 5 min</div><div class="value" id="kRssi">&ndash;</div></div>
  </section>

  <section class="main">
    <aside class="side">
      <div id="buses"></div>
      <div class="events"><h3>Live events</h3><div id="log"></div></div>
    </aside>
    <div class="mapwrap">
      <div id="map"></div>
      <div class="maperr" id="maperr">Map library failed to load &ndash; check the Pi's internet connection.</div>
      <div class="mapbar">
        <button class="btn" id="fitBtn">Fit all buses</button>
        <button class="btn" id="followBtn" style="display:none"></button>
      </div>
      <div class="legend">
        <span><i class="dot" style="background:var(--moving)"></i>Moving</span>
        <span><i class="dot" style="background:var(--stopped)"></i>Stopped</span>
        <span><i class="dot" style="background:var(--offline)"></i>Offline</span>
        <span><i class="lstop"></i>Bus stop</span>
      </div>
    </div>
  </section>
</div>

<script>
const $ = id => document.getElementById(id);
const hasMap = typeof L !== 'undefined';
let map = null;
const markers = {}, trails = {}, prev = {};
let events = [], fitted = false, follow = null, first = true, lastNodes = [], lastPts = [];

if (hasMap) {
  map = L.map('map', {zoomControl: false}).setView([6.787, 79.90], 13);
  L.control.zoom({position: 'bottomright'}).addTo(map);
  const layers = {
    'Street': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}),
    'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19, attribution: 'Imagery &copy; Esri'}),
    'Light': L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      subdomains: 'abcd', maxZoom: 20, attribution: '&copy; OpenStreetMap contributors &copy; CARTO'}),
    'Dark': L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      subdomains: 'abcd', maxZoom: 20, attribution: '&copy; OpenStreetMap contributors &copy; CARTO'}),
  };
  layers['Street'].addTo(map);
  L.control.layers(layers, null, {position: 'topright', collapsed: false}).addTo(map);
  fetch('/api/config').then(r => r.json()).then(cfg => {
    cfg.stops.forEach(s => {
      L.marker([s.lat, s.lon], {icon: L.divIcon({className: '', html: '<div class="stop"></div>', iconSize: [12, 12], iconAnchor: [6, 6]})})
        .addTo(map).bindTooltip(s.name, {direction: 'right', offset: [8, 0], className: 'dark'});
    });
  });
} else {
  $('maperr').style.display = 'grid';
}

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])); }
function ago(s) {
  if (s == null) return '&mdash;';
  if (s < 60) return Math.round(s) + '<small>s</small>';
  if (s < 3600) return Math.round(s / 60) + '<small>min</small>';
  return Math.round(s / 3600) + '<small>h</small>';
}
function agoTxt(s) { return s < 60 ? Math.round(s) + ' s' : s < 3600 ? Math.round(s / 60) + ' min' : Math.round(s / 3600) + ' h'; }
function rssiInfo(r) {
  const pct = Math.max(0, Math.min(100, (r + 120) / 80 * 100));
  if (r > -70) return {pct, label: 'Excellent', color: '#22c55e'};
  if (r > -85) return {pct, label: 'Good', color: '#84cc16'};
  if (r > -100) return {pct, label: 'Fair', color: '#f59e0b'};
  return {pct, label: 'Weak', color: '#ef4444'};
}
function spark(vals, color) {
  if (!vals || vals.length < 2) return '';
  const w = 300, h = 34, lo = -120, hi = -40;
  const pts = vals.map((v, i) => {
    const x = i / (vals.length - 1) * w;
    const y = h - 2 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo) * (h - 4);
    return x.toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="0,${h} ${pts} ${w},${h}" fill="${color}" fill-opacity=".08" stroke="none"/>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function log(text, kind) {
  events.unshift({t: new Date(), text, kind: kind || ''});
  events = events.slice(0, 60);
  $('log').innerHTML = events.map(e =>
    `<div class="ev ${e.kind}"><time>${e.t.toLocaleTimeString([], {hour12: false})}</time><span>${esc(e.text)}</span></div>`).join('');
}

function card(n) {
  if (!n.seen) {
    return `<div class="bus waiting" style="--c:${n.color}">
      <div class="bus-top"><div><div class="bus-name"><span class="swatch"></span>${esc(n.name)}</div>
      <div class="route">${esc(n.route)}</div></div><span class="pill waiting">Awaiting node</span></div>
      <div class="hint">No packets from node ${n.node} yet. Power it on and it will appear here automatically.</div></div>`;
  }
  const ri = rssiInfo(n.rssi);
  const speed = n.speed_kmh == null ? '&mdash;' : Math.round(n.speed_kmh) + '<small>km/h</small>';
  const pos = n.has_fix ? `<span>${n.lat.toFixed(5)}, ${n.lon.toFixed(5)}</span>` : `<span class="nofix">&#9679; Waiting for GPS fix</span>`;
  return `<div class="bus ${follow === n.node ? 'sel' : ''}" data-node="${n.node}" style="--c:${n.color}">
    <div class="bus-top"><div><div class="bus-name"><span class="swatch"></span>${esc(n.name)}</div>
    <div class="route">${esc(n.route)}</div></div><span class="pill ${n.state}">${n.state}</span></div>
    <div class="grid3">
      <div class="m"><div class="k">Speed</div><div class="v">${speed}</div></div>
      <div class="m"><div class="k">Last seen</div><div class="v">${ago(n.age)}</div></div>
      <div class="m"><div class="k">Loss 5 min</div><div class="v">${n.loss_pct}<small>%</small></div></div>
    </div>
    <div class="sig">
      <div class="sigrow"><span>RSSI ${n.rssi} dBm &middot; SNR ${n.snr.toFixed(1)} dB</span><span style="color:${ri.color}">${ri.label}</span></div>
      <div class="bar"><i style="width:${ri.pct}%;background:${ri.color}"></i></div>
      ${spark(n.rssi_hist, n.color)}
    </div>
    <div class="coords">${pos}<span>seq ${n.seq}</span></div>
  </div>`;
}
function renderCards(nodes) { $('buses').innerHTML = nodes.map(card).join(''); }

function track(n) {
  const p = prev[n.node];
  if (!first && p) {
    if (!p.seen && n.seen) log(`${n.name} connected`, 'good');
    else if (n.seen && p.state !== n.state) {
      if (n.state === 'offline') log(`${n.name} went offline`, 'bad');
      else if (p.state === 'offline') log(`${n.name} back online`, 'good');
      else if (n.state === 'stopped') log(`${n.name} stopped`, 'warn');
      else log(`${n.name} started moving`);
    }
    if (n.seen && p.seen && p.has_fix !== n.has_fix)
      log(n.has_fix ? `${n.name}: GPS fix acquired` : `${n.name}: GPS fix lost`, n.has_fix ? 'good' : 'warn');
    if (n.seen && p.seen && n.lost_total > p.lost_total) {
      const k = n.lost_total - p.lost_total;
      log(`${n.name}: ${k} packet${k > 1 ? 's' : ''} lost`, 'warn');
    }
  } else if (!first && !p && n.seen) {
    log(`${n.name} connected`, 'good');
  }
  prev[n.node] = {seen: n.seen, state: n.state, has_fix: n.has_fix, lost_total: n.lost_total || 0};
}

function busIcon(n) {
  return L.divIcon({className: '', html: `<div class="bm ${n.state}" style="--c:${n.color}">${n.node}</div>`,
                    iconSize: [34, 34], iconAnchor: [17, 17]});
}
function fitAll() {
  if (!map || !lastPts.length) return;
  let all = lastPts.slice();
  Object.values(trails).forEach(t => { all = all.concat(t.getLatLngs()); });
  map.fitBounds(L.latLngBounds(all).pad(0.1), {padding: [60, 60], maxZoom: 16});
}
function updateMap(nodes) {
  if (!map) return;
  const pts = [];
  nodes.forEach(n => {
    if (!n.seen || !n.has_fix) return;
    const pos = [n.lat, n.lon];
    pts.push(pos);
    let m = markers[n.node];
    if (!m) {
      trails[n.node] = L.polyline([], {color: n.color, weight: 4, opacity: .55, lineCap: 'round'}).addTo(map);
      m = markers[n.node] = L.marker(pos, {icon: busIcon(n), zIndexOffset: 1000}).addTo(map)
        .bindTooltip(n.name, {direction: 'top', offset: [0, -20], className: 'dark'});
      m.on('click', () => setFollow(n.node));
      m._st = n.state;
    } else {
      m.setLatLng(pos);
      if (m._st !== n.state) { m.setIcon(busIcon(n)); m._st = n.state; }
    }
    trails[n.node].setLatLngs(n.trail);
  });
  lastPts = pts;
  if (!fitted && pts.length) { fitAll(); fitted = true; }
  if (follow != null) {
    const n = nodes.find(x => x.node === follow);
    if (n && n.has_fix) map.panTo([n.lat, n.lon], {animate: true});
  }
}

function setFollow(id) {
  follow = (follow === id) ? null : id;
  const b = $('followBtn');
  if (follow == null) b.style.display = 'none';
  else {
    const n = lastNodes.find(x => x.node === id);
    b.style.display = '';
    b.textContent = `Following ${n ? n.name : 'Bus ' + id}  ✕`;
    if (map && n && n.has_fix) map.setView([n.lat, n.lon], Math.max(map.getZoom(), 16));
  }
  renderCards(lastNodes);
}
$('followBtn').onclick = () => setFollow(follow);
$('fitBtn').onclick = () => { follow = null; $('followBtn').style.display = 'none'; fitAll(); renderCards(lastNodes); };
$('buses').addEventListener('click', e => {
  const c = e.target.closest('.bus[data-node]');
  if (c) setFollow(+c.dataset.node);
});

function setLive(on, txt) { $('live').className = 'live ' + (on ? 'on' : 'off'); $('liveTxt').textContent = txt; }

async function refresh() {
  let d;
  try { d = await (await fetch('/api/status', {cache: 'no-store'})).json(); }
  catch (e) { setLive(false, 'Dashboard server offline'); return; }

  const age = d.gateway.last_packet_age;
  if (age == null) setLive(false, 'No data yet');
  else if (age > 10) setLive(false, 'No packets for ' + agoTxt(age));
  else setLive(true, 'Live');

  const s = d.summary;
  $('kOnline').innerHTML = `${s.online}<small>/ ${s.total}</small>`;
  $('kPpm').textContent = s.ppm;
  $('kLoss').innerHTML = s.loss_pct == null ? '&ndash;' : `${s.loss_pct}<small>%</small>`;
  $('kRssi').innerHTML = s.avg_rssi == null ? '&ndash;' : `${s.avg_rssi}<small>dBm</small>`;

  d.nodes.forEach(track);
  if (first) { log('Dashboard started'); first = false; }
  lastNodes = d.nodes;
  renderCards(d.nodes);
  updateMap(d.nodes);
}

setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString([], {hour12: false}); }, 1000);
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
