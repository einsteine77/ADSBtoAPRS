ADS-B → APRS Bridge (v2.14) — README

A small Python bridge that listens to dump1090 SBS/BaseStation (TCP:30003) and injects aircraft as APRS objects into APRSIS32 via its TCP Listener (default: 127.0.0.1:14580).
It also polls dump1090’s data.json to improve symbols (plane/heli/balloon/glider) and to rename objects from ICAO hex to the flight number when available.

1) What you get

Creates APRS objects for aircraft within a user-defined radius of KBUF.

Chooses APRS symbols:

Plane: /^

Helicopter: /X

Balloon/LTA: /O

Glider/Ultralight: /g

Renames objects to the flight number as soon as one is known (old hex object is deleted cleanly).

Rate limits & de-dupes updates; ignores jitter.

Cleans up:

Removes aircraft silent for 5 min.

Removes “landed” aircraft that stay ≤1000 ft for 3 min.

Deletes objects that move outside 40 mi while only adding new ones inside 35 mi (hysteresis).

Prints JSON connection status and concise debug lines ([SEND], [EXPIRE], [LAND], [RENAME]).

2) Requirements

Windows 10 (script runs anywhere Python runs; these steps assume Windows).

dump1090 (or readsb) providing:

SBS/BaseStation TCP on port 30003

Web JSON at http://<dump1090host>:8080/data.json

APRSIS32 running on the same PC as the bridge (recommended) or reachable over the LAN.

Python: 3.8+ (no extra packages; stdlib only).

3) APRSIS32 setup (one-time)

Enable TCP Listener input:

Configure → Ports → New Port…

Choose IS-Server (or Local-Server; both work as a TCP listener).

Set Address to 127.0.0.1 and Port to 14580 (or another free port).

Leave filters blank. Finish/OK.

Ensure Enables → APRS-IS Enabled is checked.

If Windows Firewall prompts, allow APRSIS32 to listen on the chosen port.

Tip: In the APRSIS32 log, you should see the listener bind notice like:
TCPListener Running on 127.0.0.1:14580

4) Bridge script setup

Save the script as adsb2aprs.py in a convenient folder (e.g., C:\APRSIS32\).

Open it in a text editor and confirm/update the config block near the top:
