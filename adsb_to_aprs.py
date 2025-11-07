#!/usr/bin/env python3
"""ADSB to APRS bridge.

This script connects to a dump1090 SBS feed and APRS-IS server,
bridging position updates from aircraft into APRS object packets.
"""

import json
import math
import re
import socket
import time
import urllib.request
from datetime import datetime, timezone

# =========================
# ADSB → APRS Bridge
VERSION = "2.14"
# =========================

# -------- CONFIG ---------
DUMP1090_HOST = "192.168.35.33"
DUMP1090_PORT = 30003
DUMP1090_JSON_URL = f"http://{DUMP1090_HOST}:8080/data.json"

APRSIS_HOST = "127.0.0.1"
APRSIS_PORT = 14580
CALLSIGN = "N2UGS-10"
PASSCODE = -1
# -------------------------

MAX_PKTS_PER_SEC = 5
MIN_UPDATE_SEC = 5
MIN_MOVE_MI = 0.50

OBJECT_TTL_SEC = 300
LANDED_ALT_FT = 1000
LANDED_WAIT_SEC = 180
LAND_CLEAR_ALT = 1500

EPS_LATLON_DEG = 0.00015
EPS_ALT_FT = 25
EPS_TRK_DEG = 3
EPS_GS_KT = 2

KBUF_LAT = 42.9405
KBUF_LON = -78.7322
ADD_DISTANCE_MI = 35
CLEAR_DISTANCE_MI = 40

JSON_REFRESH_SEC = 5

APPEND_SYM_TAG = True
DEBUG = True
RENAME_LOG_BRIEF_ONLY = True


# ----------------- Helpers -----------------
def utc_hhmmss() -> str:
    """Return current UTC time in HHMMSS format."""

    return datetime.now(timezone.utc).strftime("%H%M%S")


def dm_lat(lat):
    hemi = "N" if lat >= 0 else "S"
    a = abs(lat)
    d = int(a)
    m = (a - d) * 60
    return f"{d:02d}{m:05.2f}{hemi}"


def dm_lon(lon):
    hemi = "E" if lon >= 0 else "W"
    a = abs(lon)
    d = int(a)
    m = (a - d) * 60
    return f"{d:03d}{m:05.2f}{hemi}"


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles."""

    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def symbol_for_category(emitter_cat, ac_type=None):
    plane = ("/", "^", "PLANE")
    heli = ("/", "X", "HELI")
    balloon = ("/", "O", "BALLOON")
    glider = ("/", "g", "GLIDER")

    if emitter_cat:
        cat = str(emitter_cat).upper().strip()
        if cat == "A7":  # rotorcraft
            return heli
        if cat == "B2":  # lighter-than-air
            return balloon
        if cat in ("B1", "B4"):  # glider/ultralight
            return glider
        return plane

    t = (ac_type or "").upper()
    if t:
        if t.startswith("H") or "HELI" in t or t.startswith(("EC", "UH", "AH", "CH", "MH", "R22", "R44", "BELL", "BK")):
            return heli
        if "GLID" in t or t.startswith(("DG", "ASW", "ASK", "LS", "G1", "G2", "G3")):
            return glider
        if "BAL" in t or "BLN" in t or "BALLOON" in t or "HAB" in t:
            return balloon
    return plane


_name_cleaner = re.compile(r"[^A-Z0-9]")


def normalize_callsign(cs):
    if not cs:
        return None
    n = _name_cleaner.sub("", cs.upper())
    return n or None


def name_from_callsign_or_hex(callsign, icao_hex):
    n = normalize_callsign(callsign)
    if n:
        return n[:9].ljust(9)
    return (icao_hex or "AIRCRAFT")[:9].ljust(9)


def make_aprs_object(
    name,
    lat,
    lon,
    table="/",
    code="^",
    trk=None,
    gs=None,
    alt=None,
    icao=None,
    callsign=None,
    sym_tag=None,
    delete=False,
):
    ts = utc_hhmmss() + "z"
    lat_s, lon_s = dm_lat(lat), dm_lon(lon)

    parts = []
    if trk is not None:
        parts.append(f"TRK {int(trk) % 360:03d}")
    if gs is not None:
        parts.append(f"GS {int(gs)}kt")
    if alt is not None:
        parts.append(f"ALT {int(alt)}ft")
    if callsign:
        cs = normalize_callsign(callsign)
        if cs:
            parts.append(f"FLT {cs}")
    if icao:
        parts.append(f"ICAO {icao}")
    if APPEND_SYM_TAG and sym_tag:
        parts.append(f"SYM {sym_tag}")
    if delete:
        parts.append("DEL")

    comment = " ".join(parts) if parts else "ADS-B"
    return f";{name}*{ts}{lat_s}{table}{lon_s}{code}{comment}"


# --- connections, parsers, JSON helpers ---
def connect_aprs():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((APRSIS_HOST, APRSIS_PORT))
            s.send(
                f"user {CALLSIGN} pass {PASSCODE} vers ADSB2APRS {VERSION} filter m/500\n".encode()
            )
            print(f"[APRS] Connected as {CALLSIGN} (v{VERSION})")
            return s
        except Exception as exc:
            print(f"[APRS] Connect fail ({exc}); retry 3s")
            time.sleep(3)


def connect_sbs():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((DUMP1090_HOST, DUMP1090_PORT))
            print(f"[SBS] Connected to {DUMP1090_HOST}:{DUMP1090_PORT}")
            return s
        except Exception as exc:
            print(f"[SBS] Connect fail ({exc}); retry 3s")
            time.sleep(3)


def parse_sbs(line):
    f = line.strip().split(",")
    if len(f) < 22 or f[0] != "MSG":
        return None
    try:
        subtype = int(f[1])
    except ValueError:
        return None
    if subtype not in (3, 4):
        return None

    icao = f[4].strip().upper() if f[4] else None
    callsign = f[10].strip() if len(f) > 10 and f[10].strip() else None

    try:
        lat = float(f[14]) if f[14] else None
    except ValueError:
        lat = None
    try:
        lon = float(f[15]) if f[15] else None
    except ValueError:
        lon = None
    try:
        alt = float(f[11]) if f[11] else None
    except ValueError:
        alt = None
    try:
        gs = float(f[12]) if f[12] else None
    except ValueError:
        gs = None
    try:
        trk = float(f[13]) if f[13] else None
    except ValueError:
        trk = None

    if lat is None or lon is None:
        return None
    return {
        "icao": icao,
        "callsign": callsign,
        "lat": lat,
        "lon": lon,
        "trk": trk,
        "gs": gs,
        "alt": alt,
    }


def fetch_aircraft_json():
    try:
        with urllib.request.urlopen(DUMP1090_JSON_URL, timeout=1.5) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


def _maybe_print_json_status(js):
    now = time.time()
    last = js.get("_last_print", 0)
    changed = js.get("_last_ok_state") != js.get("ok")
    if changed or (now - last) > 60:
        if js.get("ok"):
            cnt = js.get("count", 0)
            print(
                f"[JSON] OK  source={DUMP1090_JSON_URL} count={cnt} last_ok={int(now-js.get('last_ok', now))}s"
            )
        else:
            print(f"[JSON] FAIL ({js.get('last_err', 'no data')})  url={DUMP1090_JSON_URL}")
        js["_last_print"] = now
        js["_last_ok_state"] = js.get("ok")


def refresh_meta_cache(meta_cache, json_status):
    aircraft_json = fetch_aircraft_json()
    now = time.time()
    if isinstance(aircraft_json, dict) and "aircraft" in aircraft_json:
        ac_list = aircraft_json.get("aircraft", [])
    elif isinstance(aircraft_json, list):
        ac_list = aircraft_json
    else:
        ac_list = None

    if isinstance(ac_list, list):
        count = 0
        for aircraft in ac_list:
            icao = (aircraft.get("hex") or "").upper()
            if not icao:
                continue
            entry = meta_cache.setdefault(icao, {})
            cat = aircraft.get("category")
            typ = aircraft.get("type") or aircraft.get("t")
            flt = aircraft.get("flight") or aircraft.get("call") or aircraft.get("flightnumber")
            if cat:
                entry["cat"] = str(cat).strip()
            if typ:
                entry["type"] = str(typ).strip()
            if flt:
                entry["flight"] = str(flt).strip()
            count += 1
        json_status["ok"] = True
        json_status["last_ok"] = now
        json_status["last_err"] = None
        json_status["count"] = count
        _maybe_print_json_status(json_status)
    else:
        json_status["ok"] = False
        json_status["last_err"] = "bad format" if aircraft_json is not None else "no data"
        _maybe_print_json_status(json_status)


# -------------- Main loop --------------
def main():
    aprs = connect_aprs()
    sbs = connect_sbs()
    buff = b""

    last_seen, last_sent = {}, {}
    low_alt_since, landed_block = {}, set()
    hex_to_name, name_to_hex = {}, {}

    meta_cache, json_status = {}, {"ok": False, "_last_ok_state": None, "_last_print": 0}
    last_json_poll = 0

    last_sec = 0
    sent_this_sec = 0

    print(
        f"ADSB→APRS bridge v{VERSION} | Add={ADD_DISTANCE_MI}mi / Clear>{CLEAR_DISTANCE_MI}mi | "
        f"pacing {MIN_UPDATE_SEC}s / {MIN_MOVE_MI}mi | Landed dwell {LANDED_WAIT_SEC}s ={LANDED_ALT_FT}ft | JSON {DUMP1090_JSON_URL}"
    )

    while True:
        try:
            now_time = time.time()
            if now_time - last_json_poll >= JSON_REFRESH_SEC:
                refresh_meta_cache(meta_cache, json_status)
                last_json_poll = now_time

            data = sbs.recv(4096)
            if not data:
                print("[SBS] Lost connection; reconnecting...")
                try:
                    sbs.close()
                except Exception:
                    pass
                sbs = connect_sbs()
                continue

            buff += data
            while b"\n" in buff:
                raw, buff = buff.split(b"\n", 1)
                line = raw.decode(errors="ignore").strip()
                msg = parse_sbs(line)
                if not msg:
                    continue

                icao_hex = (msg["icao"] or "").upper()
                meta = meta_cache.get(icao_hex, {})
                json_callsign = meta.get("flight")
                callsign = msg["callsign"] or json_callsign
                desired_name = name_from_callsign_or_hex(callsign, icao_hex)

                table, code, sym_tag = symbol_for_category(meta.get("cat"), meta.get("type"))
                dist_kbuf = haversine_miles(KBUF_LAT, KBUF_LON, msg["lat"], msg["lon"])

                current_name_for_hex = hex_to_name.get(icao_hex)
                tracked_name = current_name_for_hex if current_name_for_hex else desired_name

                # Range hysteresis
                if tracked_name in last_seen and dist_kbuf > CLEAR_DISTANCE_MI:
                    last_info = last_sent.get(tracked_name)
                    lat = last_info["lat"] if last_info else msg["lat"]
                    lon = last_info["lon"] if last_info else msg["lon"]
                    try:
                        delpkt = make_aprs_object(
                            tracked_name, lat, lon, table, code, sym_tag=sym_tag, delete=True
                        )
                        aprs.send(f"{CALLSIGN}>APRS,TCPIP*:{delpkt}\n".encode())
                    except Exception:
                        pass
                    last_seen.pop(tracked_name, None)
                    last_sent.pop(tracked_name, None)
                    low_alt_since.pop(tracked_name, None)
                    landed_block.discard(tracked_name)
                    if current_name_for_hex:
                        hex_to_name.pop(icao_hex, None)
                    name_to_hex.pop(tracked_name, None)
                    if DEBUG:
                        print(f"[EXPIRE] Out of range >{CLEAR_DISTANCE_MI}mi: Deleted {tracked_name.strip()}")
                    continue

                if tracked_name not in last_seen and dist_kbuf > ADD_DISTANCE_MI:
                    continue

                alt = msg["alt"]
                now = int(time.time())

                # Landed suppression re-enable
                if tracked_name in landed_block and (alt is None or alt > LAND_CLEAR_ALT):
                    landed_block.discard(tracked_name)
                    low_alt_since.pop(tracked_name, None)
                    if DEBUG:
                        print(f"[LAND] {tracked_name.strip()} climbed >{LAND_CLEAR_ALT}ft; re-enable")

                if tracked_name in landed_block and alt is not None and alt <= LANDED_ALT_FT:
                    continue

                # Landed dwell
                if alt is not None and alt <= LANDED_ALT_FT:
                    if tracked_name not in low_alt_since:
                        low_alt_since[tracked_name] = now
                    if now - low_alt_since[tracked_name] >= LANDED_WAIT_SEC:
                        last_info = last_sent.get(tracked_name)
                        lat = last_info["lat"] if last_info else msg["lat"]
                        lon = last_info["lon"] if last_info else msg["lon"]
                        try:
                            delpkt = make_aprs_object(
                                tracked_name, lat, lon, table, code, sym_tag=sym_tag, delete=True
                            )
                            aprs.send(f"{CALLSIGN}>APRS,TCPIP*:{delpkt}\n".encode())
                        except Exception:
                            pass
                        last_seen.pop(tracked_name, None)
                        last_sent.pop(tracked_name, None)
                        landed_block.add(tracked_name)
                        if current_name_for_hex:
                            hex_to_name.pop(icao_hex, None)
                        name_to_hex.pop(tracked_name, None)
                        if DEBUG:
                            print(
                                f"[LAND] Dwell delete {tracked_name.strip()} (≤{LANDED_ALT_FT}ft for {LANDED_WAIT_SEC}s)"
                            )
                        continue
                else:
                    low_alt_since.pop(tracked_name, None)

                # global throttle
                if now != last_sec:
                    last_sec = now
                    sent_this_sec = 0
                if sent_this_sec >= MAX_PKTS_PER_SEC:
                    continue

                # Rename hex → callsign when flight appears
                if current_name_for_hex and desired_name != current_name_for_hex:
                    last_info = last_sent.get(current_name_for_hex)
                    lat = last_info["lat"] if last_info else msg["lat"]
                    lon = last_info["lon"] if last_info else msg["lon"]
                    try:
                        delpkt = make_aprs_object(
                            current_name_for_hex, lat, lon, table, code, sym_tag=sym_tag, delete=True
                        )
                        aprs.send(f"{CALLSIGN}>APRS,TCPIP*:{delpkt}\n".encode())
                    except Exception:
                        pass
                    last_seen.pop(current_name_for_hex, None)
                    prev_info = last_sent.pop(current_name_for_hex, None)
                    if prev_info:
                        last_sent[desired_name] = prev_info
                    name_to_hex.pop(current_name_for_hex, None)
                    hex_to_name[icao_hex] = desired_name
                    name_to_hex[desired_name] = icao_hex
                    tracked_name = desired_name
                    if DEBUG:
                        print(f"[RENAME] {current_name_for_hex.strip()} → {desired_name.strip()}")

                if icao_hex and icao_hex not in hex_to_name:
                    hex_to_name[icao_hex] = tracked_name
                    name_to_hex[tracked_name] = icao_hex

                last_seen[tracked_name] = now

                prev_info = last_sent.get(tracked_name)
                prev_state = prev_info["state"] if prev_info else None
                prev_time = prev_info["time"] if prev_info else 0
                prev_lat = prev_info["lat"] if prev_info else None
                prev_lon = prev_info["lon"] if prev_info else None

                moved_far_enough = False
                if prev_lat is not None and prev_lon is not None:
                    moved_far_enough = (
                        haversine_miles(prev_lat, prev_lon, msg["lat"], msg["lon"]) >= MIN_MOVE_MI
                    )

                def state_changed(prev, cur):
                    if prev is None:
                        return True
                    if abs(cur["lat"] - prev["lat"]) >= EPS_LATLON_DEG:
                        return True
                    if abs(cur["lon"] - prev["lon"]) >= EPS_LATLON_DEG:
                        return True
                    if (cur["alt"] is None) != (prev["alt"] is None):
                        return True
                    if cur["alt"] is not None and prev["alt"] is not None:
                        if abs(cur["alt"] - prev["alt"]) >= EPS_ALT_FT:
                            return True
                    if (cur["trk"] is None) != (prev["trk"] is None):
                        return True
                    if cur["trk"] is not None and prev["trk"] is not None:
                        a = int(cur["trk"]) % 360
                        b = int(prev["trk"]) % 360
                        d = abs(a - b)
                        d = min(d, 360 - d)
                        if d >= EPS_TRK_DEG:
                            return True
                    if (cur["gs"] is None) != (prev["gs"] is None):
                        return True
                    if cur["gs"] is not None and prev["gs"] is not None:
                        if abs(cur["gs"] - prev["gs"]) >= EPS_GS_KT:
                            return True
                    return False

                need_send = False
                if prev_state is None:
                    need_send = True
                elif moved_far_enough:
                    need_send = True
                elif state_changed(prev_state, msg) and (now - prev_time) >= MIN_UPDATE_SEC:
                    need_send = True

                if not need_send:
                    continue

                pkt = make_aprs_object(
                    tracked_name,
                    msg["lat"],
                    msg["lon"],
                    table,
                    code,
                    msg["trk"],
                    msg["gs"],
                    msg["alt"],
                    icao_hex,
                    callsign,
                    sym_tag,
                )
                out = f"{CALLSIGN}>APRS,TCPIP*:{pkt}\n"

                try:
                    aprs.send(out.encode("ascii", errors="ignore"))
                    last_sent[tracked_name] = {
                        "time": now,
                        "state": msg,
                        "lat": msg["lat"],
                        "lon": msg["lon"],
                        "icao": icao_hex,
                    }
                    sent_this_sec += 1
                    if DEBUG:
                        print(
                            f"[SEND] {tracked_name.strip()} {msg['lat']:.5f},{msg['lon']:.5f} "
                            f"alt={int(msg['alt']) if msg['alt'] is not None else '-'} "
                            f"gs={int(msg['gs']) if msg['gs'] is not None else '-'} "
                            f"trk={int(msg['trk']) if msg['trk'] is not None else '-'} "
                            f"sym={table}{code} tag={sym_tag}"
                        )
                except Exception as exc:
                    print(f"[APRS] Send fail ({exc}); reconnecting...")
                    try:
                        aprs.close()
                    except Exception:
                        pass
                    aprs = connect_aprs()

            # Cleanup phase
            now = int(time.time())
            to_delete = [name for name, t0 in list(last_seen.items()) if now - t0 >= OBJECT_TTL_SEC]

            for name in to_delete:
                last_info = last_sent.get(name)
                lat = last_info["lat"] if last_info else 0
                lon = last_info["lon"] if last_info else 0
                last_seen.pop(name, None)
                last_sent.pop(name, None)
                low_alt_since.pop(name, None)
                landed_block.discard(name)
                hex_code = name_to_hex.pop(name, None)
                if hex_code:
                    hex_to_name.pop(hex_code, None)
                try:
                    meta = {} if not hex_code else meta_cache.get(hex_code, {})
                    table_, code_, tag_ = symbol_for_category(meta.get("cat"), meta.get("type"))
                    delpkt = make_aprs_object(name, lat, lon, table_, code_, sym_tag=tag_, delete=True)
                    aprs.send(f"{CALLSIGN}>APRS,TCPIP*:{delpkt}\n".encode())
                except Exception:
                    pass
                if DEBUG:
                    print(f"[EXPIRE] Deleted {name.strip()} (silent = {OBJECT_TTL_SEC}s)")

        except Exception as exc:
            print(f"[SBS] Error {exc}; retry 2s")
            time.sleep(2)
            try:
                sbs.close()
            except Exception:
                pass
            sbs = connect_sbs()


if __name__ == "__main__":
    print(
        f"ADSB→APRS bridge v{VERSION} | Add={ADD_DISTANCE_MI}mi / Clear>{CLEAR_DISTANCE_MI}mi | "
        f"pacing {MIN_UPDATE_SEC}s / {MIN_MOVE_MI}mi | Landed dwell {LANDED_WAIT_SEC}s ={LANDED_ALT_FT}ft | JSON {DUMP1090_JSON_URL}"
    )
    main()
