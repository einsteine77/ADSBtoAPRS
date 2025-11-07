# ADS-B → APRS Bridge (v2.14) — README

A small Python bridge that listens to **dump1090** SBS/BaseStation (TCP:30003) and injects aircraft as **APRS objects** into **APRSIS32** via its **TCP Listener** (default: 127.0.0.1:14580).
It also polls dump1090’s `data.json` to improve symbols (plane/heli/balloon/glider) and to rename objects from ICAO hex to the **flight number** when available.

---

## 1) What you get

* Creates APRS objects for aircraft within a user-defined radius of **KBUF**.
* Chooses APRS symbols:

  * Plane: `/^`
  * Helicopter: `/X`
  * Balloon/LTA: `/O`
  * Glider/Ultralight: `/g`
* Renames objects to the **flight number** as soon as one is known (old hex object is deleted cleanly).
* Rate limits & de-dupes updates; ignores jitter.
* Cleans up:

  * Removes aircraft silent for **5 min**.
  * Removes “landed” aircraft that stay **≤1000 ft** for **3 min**.
  * Deletes objects that move outside **40 mi** while only adding new ones inside **35 mi** (hysteresis).
* Prints JSON connection status and concise debug lines (`[SEND]`, `[EXPIRE]`, `[LAND]`, `[RENAME]`).

---

## 2) Requirements

* **Windows 10** (script runs anywhere Python runs; these steps assume Windows).
* **dump1090** (or readsb) providing:

  * SBS/BaseStation TCP on **port 30003**
  * Web JSON at `http://<dump1090host>:8080/data.json`
* **APRSIS32** running on the same PC as the bridge (recommended) or reachable over the LAN.

**Python:** 3.8+ (no extra packages; stdlib only).

---

## 3) APRSIS32 setup (one-time)

1. **Enable TCP Listener input**:

   * `Configure → Ports → New Port…`
   * Choose **IS-Server** (or **Local-Server**; both work as a TCP listener).
   * Set **Address** to `127.0.0.1` and **Port** to `14580` (or another free port).
   * Leave filters blank. Finish/OK.
2. Ensure `Enables → APRS-IS Enabled` is **checked**.
3. If Windows Firewall prompts, allow APRSIS32 to listen on the chosen port.

> Tip: In the APRSIS32 log, you should see the listener bind notice like:
> `TCPListener Running on 127.0.0.1:14580`

---

## 4) Bridge script setup

1. Save the script as `adsb2aprs.py` in a convenient folder (e.g., `C:\APRSIS32\`).
2. Open it in a text editor and confirm/update the **config block** near the top:

```python
# -------- CONFIG ---------
DUMP1090_HOST     = "192.168.35.33"
DUMP1090_PORT     = 30003
DUMP1090_JSON_URL = f"http://{DUMP1090_HOST}:8080/data.json"

APRSIS_HOST       = "127.0.0.1"
APRSIS_PORT       = 14580
CALLSIGN          = "N2UGS-10"
PASSCODE          = -1           # keep -1 when injecting locally into APRSIS32
```

> Keep `PASSCODE = -1` when **not** sending to the public APRS-IS backbone.
> The script connects to your local APRSIS32 listener and APRSIS32 will display (and optionally gate) the objects.

3. Optional tuning (defaults work well):

   * Range: `ADD_DISTANCE_MI = 35`, `CLEAR_DISTANCE_MI = 40`
   * Update pacing: `MIN_UPDATE_SEC = 5`, `MIN_MOVE_MI = 0.50`, `MAX_PKTS_PER_SEC = 5`
   * Landed cleanup: `LANDED_ALT_FT = 1000`, `LANDED_WAIT_SEC = 180`
   * Center: `KBUF_LAT = 42.9405`, `KBUF_LON = -78.7322`
   * Debug prints: `DEBUG = True`

---

## 5) Run it

Open **PowerShell** (or CMD) in the folder where the script lives:

```powershell
python adsb2aprs.py
```

You should see lines like:

```
[APRS] Connected as N2UGS-10 (v2.14)
[SBS] Connected to 192.168.35.33:30003
[JSON] OK  source=http://192.168.35.33:8080/data.json count=xx last_ok=0s
[SEND] AAL123 42.94,-78.73 alt=34000 gs=450 trk=090 sym=/^ tag=PLANE
```

Objects appear in APRSIS32’s station list and on the map with the correct symbols.
When a flight number is discovered, you’ll see `[RENAME] HEX → FLT` and the hex object is withdrawn.

---

## 6) Running automatically (Windows)

### Option A — Task Scheduler (simple)

1. Open **Task Scheduler → Create Basic Task…**
2. Trigger: **At log on** (or choose a schedule).
3. Action: **Start a program**

   * Program/script: `python`
   * Add arguments: `C:\APRSIS32\adsb2aprs.py`
   * Start in: `C:\APRSIS32\`
4. Check **Run whether user is logged on or not** (optional) and **Start only if network available**.

### Option B — Service via NSSM (advanced)

If you use **nssm**:

```
nssm install ADSB2APRS "C:\Python311\python.exe" "C:\APRSIS32\adsb2aprs.py"
nssm start ADSB2APRS
```

---

## 7) Troubleshooting

* **`ConnectionRefusedError` to APRSIS32**
  APRSIS32’s TCP listener isn’t running or port mismatched.
  Fix: Recheck Port config; confirm you see `Bound 127.0.0.1:14580` in APRSIS32 logs.

* **No JSON status / `[JSON] FAIL (no data)`**
  Dump1090’s web server isn’t running or port is different. Visit
  `http://192.168.35.33:8080/data.json` in a browser; update `DUMP1090_JSON_URL` if needed.

* **Objects look like cars or weather stations**
  This build forces aircraft symbols and uses category/type from JSON to select **plane/heli/balloon/glider**.
  If your dump1090 provides unusual fields, they’re normalized—open an issue with a sample of `data.json`.

* **Too many updates / “Moved TOO SOON” messages in APRSIS32**
  Increase `MIN_UPDATE_SEC` (e.g., 8–10) or `MIN_MOVE_MI` (e.g., 0.8).
  `MAX_PKTS_PER_SEC` caps burstiness.

* **Landed aircraft not clearing**
  Ensure your receiver still reports position while taxiing/after landing.
  The script deletes after **3 min continuously ≤1000 ft**; you can raise `LANDED_ALT_FT` or `LANDED_WAIT_SEC`.

* **Range control**
  Only new objects inside **35 mi** are created; anything that drifts beyond **40 mi** is deleted.
  Adjust `ADD_DISTANCE_MI` / `CLEAR_DISTANCE_MI` to taste.

---

## 8) How it works (quick tech notes)

* **Input:** SBS/BaseStation text (`MSG,3/4…`) from dump1090 on TCP/30003.
* **Metadata:** Polls `data.json` every 5 s to fetch `category`, `type`, and `flight`.
* **Naming:** Uses `flight` when present; otherwise 6-digit ICAO hex. On first flight discovery, issues a **delete** for the hex-named object and recreates under the flight number.
* **Symbols:** Derived from ICAO **emitter category** where possible; falls back to `type` heuristics; default is plane.
* **Output:** APRS **object report** lines to the APRSIS32 TCP listener (`CALLSIGN>APRS,TCPIP*:` payload).
* **Cleanup:** 5-minute TTL on silence; “landed” dwell logic; geographic hysteresis; per-second throttle.

---

## 9) Safety & etiquette

* Keep injections local to **your APRSIS32**. Do **not** send raw ADS-B to the global APRS-IS mesh unless you have explicit permission and follow that network’s policies.
* Consider marking your APRSIS32 RX-only if you do not intend to gate to RF.

---

## 10) Support / quick edits

Most knobs are at the top of `adsb2aprs.py`. If you want different center coordinates, symbols, or pacing, edit those constants and re-run. Keep an eye on the console for `[JSON]`, `[SEND]`, `[RENAME]`, `[LAND]`, and `[EXPIRE]` lines—they tell you exactly what the bridge is doing.

Happy flying, Jer. ✈️

