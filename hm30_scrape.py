#!/usr/bin/env python3
import argparse
import json
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ZERO_SESSION = "00000000000000000000000000000000"
SIYI_VIDEO_LINK_STATUS = bytes.fromhex("55 66 01 00 00 00 00 44 05 dc")


def rpc(base_url, method, params=None, timeout=5):
    body = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1000000, "method": method}
    if params is not None:
        body["params"] = params

    req = urllib.request.Request(
        base_url.rstrip("/") + "/cgi-bin/luci/admin/ubus",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as res:
        payload = json.loads(res.read().decode())

    if "error" in payload:
        raise RuntimeError(payload["error"])

    return payload.get("result")


def login(base_url, username, password):
    result = rpc(
        base_url,
        "call",
        [ZERO_SESSION, "session", "login", {"username": username, "password": password}],
    )
    if not isinstance(result, list) or len(result) < 2 or "ubus_rpc_session" not in result[1]:
        raise RuntimeError(f"unexpected login result: {result!r}")
    return result[1]["ubus_rpc_session"]


def call(base_url, session, obj, method, params=None):
    return rpc(base_url, "call", [session, obj, method, params or {}])


def rf_status(base_url, session):
    objects = rpc(base_url, "list")
    print(json.dumps({"ubus_objects": objects}, indent=2))

    candidates = ["wlan0", "wlan1", "wlan_usb", "phy0-ap0", "phy0-sta0", "radio0", "radio1"]
    try:
        devices = call(base_url, session, "iwinfo", "devices", {})
        print(json.dumps({"iwinfo": "devices", "result": devices}, indent=2))
        if isinstance(devices, list):
            candidates = list(dict.fromkeys(devices + candidates))
    except Exception as exc:
        print(json.dumps({"iwinfo.devices": {"error": str(exc)}}))

    for dev in candidates:
        for method in ("info", "assoclist", "survey"):
            try:
                result = call(base_url, session, "iwinfo", method, {"device": dev})
            except Exception as exc:
                print(json.dumps({"iwinfo": method, "device": dev, "error": str(exc)}))
                continue
            print(json.dumps({"iwinfo": method, "device": dev, "result": result}, indent=2))

    for obj, method, params in (
        ("network.wireless", "status", {}),
        ("luci", "getWirelessDevices", {}),
        ("system", "board", {}),
        ("system", "info", {}),
    ):
        try:
            print(json.dumps({f"{obj}.{method}": call(base_url, session, obj, method, params)}, indent=2))
        except Exception as exc:
            print(json.dumps({f"{obj}.{method}": {"error": str(exc)}}))

    readonly_commands = {
        "proc_net_wireless": "cat /proc/net/wireless 2>/dev/null || true",
        "wireless_config": "uci show wireless 2>/dev/null || true",
        "ifaces": "ip -br addr 2>/dev/null || ifconfig -a 2>/dev/null || true",
        "routes": "ip route 2>/dev/null || route -n 2>/dev/null || true",
        "processes": "ps w 2>/dev/null || ps 2>/dev/null || true",
        "listeners": "netstat -tunlp 2>/dev/null || ss -lunpt 2>/dev/null || true",
        "tmp_rf_files": "find /tmp /var/run -maxdepth 3 -type f 2>/dev/null | grep -Ei 'rf|rssi|dbm|link|siyi|hm30|wireless|radio' || true",
    }
    for name, command in readonly_commands.items():
        try:
            result = call(
                base_url,
                session,
                "file",
                "exec",
                {"command": "/bin/sh", "params": ["-c", command], "ubus_rpc_session": session},
            )
            print(json.dumps({"exec": name, "command": command, "result": result}, indent=2))
        except Exception as exc:
            print(json.dumps({"exec": name, "error": str(exc)}))


def checksum_x25(data):
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def parse_mavlink_packet(buf, offset=0):
    if offset >= len(buf) or buf[offset] not in (0xFE, 0xFD):
        return None

    magic = buf[offset]
    if magic == 0xFE:
        if offset + 8 > len(buf):
            return None
        length = buf[offset + 1]
        frame_len = 8 + length
        if offset + frame_len > len(buf):
            return None
        seq, sysid, compid, msgid = buf[offset + 2 : offset + 6]
        payload_start = offset + 6
    else:
        if offset + 12 > len(buf):
            return None
        length = buf[offset + 1]
        incompat = buf[offset + 2]
        signature_len = 13 if (incompat & 0x01) else 0
        frame_len = 12 + length + signature_len
        if offset + frame_len > len(buf):
            return None
        seq, sysid, compid = buf[offset + 4 : offset + 7]
        msgid = buf[offset + 7] | (buf[offset + 8] << 8) | (buf[offset + 9] << 16)
        payload_start = offset + 10

    payload = buf[payload_start : payload_start + length]
    return {
        "version": 1 if magic == 0xFE else 2,
        "frame_len": frame_len,
        "seq": seq,
        "sysid": sysid,
        "compid": compid,
        "msgid": msgid,
        "payload": payload,
    }


def decode_known(msgid, payload):
    try:
        if msgid == 0:
            payload = payload.ljust(9, b"\x00")
            return {"name": "HEARTBEAT", "type": payload[6], "autopilot": payload[7], "base_mode": payload[8]}
        if msgid == 1:
            payload = payload.ljust(31, b"\x00")
            vals = struct.unpack_from("<IIIHHHHHHHHHHHH", payload)
            return {"name": "SYS_STATUS", "voltage_battery_mv": vals[10], "current_battery_ca": vals[11], "battery_remaining_pct": struct.unpack_from("<b", payload, 30)[0]}
        if msgid == 24:
            payload = payload.ljust(30, b"\x00")
            time_usec, lat, lon, alt, eph, epv, vel, cog, fix_type, sats = struct.unpack_from("<QiiiHHHHBB", payload)
            return {"name": "GPS_RAW_INT", "fix_type": fix_type, "satellites": sats, "lat": lat / 1e7, "lon": lon / 1e7, "alt_m": alt / 1000.0}
        if msgid == 30:
            payload = payload.ljust(28, b"\x00")
            _, roll, pitch, yaw, rollspeed, pitchspeed, yawspeed = struct.unpack_from("<Iffffff", payload)
            return {"name": "ATTITUDE", "roll": roll, "pitch": pitch, "yaw": yaw, "rollspeed": rollspeed, "pitchspeed": pitchspeed, "yawspeed": yawspeed}
        if msgid == 74:
            payload = payload.ljust(20, b"\x00")
            airspeed, groundspeed, alt, climb, heading, throttle = struct.unpack_from("<ffffhH", payload)
            return {"name": "VFR_HUD", "airspeed": airspeed, "groundspeed": groundspeed, "heading": heading, "throttle": throttle, "alt": alt, "climb": climb}
        if msgid == 109:
            payload = payload.ljust(9, b"\x00")
            rssi, remrssi, txbuf, noise, remnoise, rxerrors, fixed = struct.unpack_from("<BBBBBHH", payload)
            return {"name": "RADIO_STATUS", "rssi": rssi, "remrssi": remrssi, "noise": noise, "remnoise": remnoise, "rxerrors": rxerrors, "fixed": fixed}
    except struct.error:
        return None
    return None


def parse_siyi_sdk_frames(buf):
    pos = 0
    while pos < len(buf) - 1:
        if buf[pos : pos + 2] != b"\x55\x66":
            pos += 1
            continue
        if pos + 10 > len(buf):
            break

        data_len = buf[pos + 3] | (buf[pos + 4] << 8)
        frame_len = 8 + data_len + 2
        if pos + frame_len > len(buf):
            pos += 1
            continue

        frame = buf[pos : pos + frame_len]
        yield {
            "ctrl": frame[2],
            "data_len": data_len,
            "seq": frame[5] | (frame[6] << 8),
            "cmd": frame[7],
            "payload": frame[8 : 8 + data_len],
            "crc": frame[8 + data_len : 8 + data_len + 2],
            "raw": frame,
        }
        pos += frame_len


def decode_siyi_video_link_status(payload):
    if len(payload) == 36:
        vals = struct.unpack("<9i", payload)
        result = dict(
            zip(
                (
                    "signal_pct",
                    "inactive_time",
                    "upstream_Bps",
                    "downstream_Bps",
                    "tx_bandwidth_raw",
                    "rx_bandwidth_raw",
                    "rssi_dbm",
                    "freq_mhz",
                    "channel",
                ),
                vals,
            )
        )
        result["tx_bandwidth_mbps"] = result["tx_bandwidth_raw"] / 1000
        result["rx_bandwidth_mbps"] = result["rx_bandwidth_raw"] / 1000
        return result

    if len(payload) >= 8:
        video_up, video_down, channel, signal_strength, signal_quality = struct.unpack("<HHBhB", payload[:8])
        return {
            "video_up_raw": video_up,
            "video_down_raw": video_down,
            "channel": channel,
            "signal_strength": signal_strength,
            "signal_quality": signal_quality,
        }

    return {"raw_payload": payload.hex(" ")}


def host_from_base_url(base_url):
    parsed = urllib.parse.urlparse(base_url)
    return parsed.hostname or base_url.split(":", 1)[0].strip("/")


def request_siyi_rf(sock, peer, timeout):
    sock.sendto(SIYI_VIDEO_LINK_STATUS, peer)
    deadline = time.time() + timeout

    while time.time() < deadline:
        sock.settimeout(max(0.01, deadline - time.time()))
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            return None

        for frame in parse_siyi_sdk_frames(data):
            if frame["cmd"] == 0x44:
                return {
                    "ts": time.time(),
                    "from": f"{addr[0]}:{addr[1]}",
                    "cmd": "0x44",
                    "seq": frame["seq"],
                    "decoded": decode_siyi_video_link_status(frame["payload"]),
                    "raw": frame["raw"].hex(" "),
                }

    return None


def poll_siyi_rf(host, port, seconds, interval):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer = (host, port)
    end = time.time() + seconds
    next_poll = time.time()
    timeout = max(0.2, min(interval * 0.8, 1.0))

    print(f"polling SIYI SDK RF {host}:{port} every {interval}s for {seconds}s", file=sys.stderr)
    while time.time() < end:
        now = time.time()
        if now < next_poll:
            time.sleep(min(next_poll - now, max(0.01, interval / 10)))
            continue

        result = request_siyi_rf(sock, peer, timeout)
        if result:
            print(json.dumps(result, separators=(",", ":")), flush=True)

        next_poll += interval
        if next_poll < time.time() - interval:
            next_poll = time.time() + interval


def heartbeat_frame(seq=0, sysid=255, compid=0):
    payload = struct.pack("<IBBBBB", 0, 6, 8, 0, 0, 3)
    header = bytes([len(payload), seq & 0xFF, sysid & 0xFF, compid & 0xFF, 0])
    crc = checksum_x25(header + payload + bytes([50]))
    return bytes([0xFE]) + header + payload + struct.pack("<H", crc)


def listen_mavlink(host, port, seconds, peer=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(0.5)

    end = time.time() + seconds
    print(f"listening UDP {host}:{port} for {seconds}s", file=sys.stderr)
    last_heartbeat = 0
    seq = 0
    while time.time() < end:
        if peer and time.time() - last_heartbeat >= 1:
            sock.sendto(heartbeat_frame(seq), peer)
            seq = (seq + 1) % 256
            last_heartbeat = time.time()

        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue

        pos = 0
        while pos < len(data):
            if data[pos] not in (0xFE, 0xFD):
                pos += 1
                continue
            pkt = parse_mavlink_packet(data, pos)
            if not pkt:
                pos += 1
                continue
            decoded = decode_known(pkt["msgid"], pkt["payload"])
            print(json.dumps({
                "ts": time.time(),
                "from": f"{addr[0]}:{addr[1]}",
                "version": pkt["version"],
                "seq": pkt["seq"],
                "sysid": pkt["sysid"],
                "compid": pkt["compid"],
                "msgid": pkt["msgid"],
                "decoded": decoded,
            }, separators=(",", ":")))
            pos += pkt["frame_len"]


def main():
    parser = argparse.ArgumentParser(description="Scrape SIYI HM30 LuCI RF status, SIYI SDK RF status, and MAVLink UDP data.")
    parser.add_argument("--base-url", default="http://192.168.144.12")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=None)
    parser.add_argument("--rf", action="store_true", help="query LuCI/ubus RF status; requires password")
    parser.add_argument("--siyi-rf", action="store_true", help="poll SIYI UDP SDK image-link RF status; no LuCI password needed")
    parser.add_argument("--mavlink", action="store_true", help="listen for MAVLink UDP packets")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19856)
    parser.add_argument("--peer", default=None, help="optional HOST:PORT to send MAVLink heartbeat probes")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--interval", type=float, default=1.0, help="poll interval for --siyi-rf")
    args = parser.parse_args()

    if args.rf:
        if args.password is None:
            raise SystemExit("--rf needs --password")
        session = login(args.base_url, args.username, args.password)
        print(json.dumps({"session": session}))
        rf_status(args.base_url, session)

    if args.siyi_rf:
        poll_siyi_rf(host_from_base_url(args.base_url), args.port, args.seconds, args.interval)

    if args.mavlink:
        peer = None
        if args.peer:
            host, port = args.peer.rsplit(":", 1)
            peer = (host, int(port))
        listen_mavlink(args.bind, args.port, args.seconds, peer)

    if not args.rf and not args.siyi_rf and not args.mavlink:
        parser.print_help()


if __name__ == "__main__":
    main()
