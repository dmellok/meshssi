"""Turn RX_LOG_DATA events (every packet the radio hears) into monitor lines."""

import time

from rich.text import Text

CONTACT_TYPES = {0: "?", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}
PAYLOAD_STYLE = {
    "ADVERT": "bold green", "GRP_TXT": "cyan", "TXT_MSG": "magenta", "ACK": "grey62", "PATH": "yellow",
    "REQ": "blue", "RESPONSE": "blue", "ANON_REQ": "blue", "TRACE": "bold yellow", "GRP_DATA": "cyan",
    "CONTROL": "orange1", "MULTIPART": "grey62", "RAW_CUSTOM": "grey62",
}


def split_path(path: str, hash_size: int) -> list[str]:
    w = max(hash_size, 1) * 2
    return [path[i : i + w] for i in range(0, len(path), w)]


def summarize(p: dict, resolve_hash) -> dict:
    """Reduce an RX log payload to what the monitor and heard-by tracking need."""
    hs = p.get("path_hash_size", 1)
    hops = split_path(p.get("path", ""), hs)
    s = {
        "t": p.get("recv_time", time.time()),
        "snr": p.get("snr"),
        "rssi": p.get("rssi"),
        "route": p.get("route_typename", "?"),
        "ptype": p.get("payload_typename", "?"),
        "hops": hops,
        "hop_names": [resolve_hash(h) for h in hops],
        "len": p.get("payload_length", 0),
        "hash": p.get("pkt_hash"),
    }
    for k in ("adv_name", "adv_type", "adv_key", "adv_lat", "adv_lon", "chan_name", "chan_hash", "message",
              "sender_timestamp"):
        if k in p and p[k] is not None:
            s[k] = p[k]
    return s


def render(s: dict, styles: dict, name_color, distance: str = "") -> Text:
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(time.strftime("%H:%M:%S ", time.localtime(s["t"])), styles["timestamp"])
    rssi = s.get("rssi")
    snr = s.get("snr")
    line.append(f"{rssi:>4}dBm " if rssi is not None else "   ?dBm ", styles["meta"])
    snr_style = "green" if (snr or 0) >= 5 else ("yellow" if (snr or 0) >= -5 else "red")
    line.append(f"{snr:>+6.2f}dB " if snr is not None else "     ?dB ", snr_style)
    route = s["route"].replace("TC_", "")
    line.append(f"{route[:6]:<6} ", styles["dim"])
    ptype = s["ptype"]
    line.append(f"{ptype:<8} ", PAYLOAD_STYLE.get(ptype, ""))
    n = len(s["hops"])
    line.append(f"{n} hop{' ' if n == 1 else 's'} ", styles["meta"])
    if ptype == "ADVERT" and s.get("adv_name"):
        name = s["adv_name"]
        line.append(name, name_color(name))
        line.append(f" ({CONTACT_TYPES.get(s.get('adv_type'), '?')}, {s.get('adv_key', '')[:8]})", styles["dim"])
        if distance:
            line.append(f" {distance}", styles["meta"])
    elif ptype == "GRP_TXT":
        if s.get("message"):
            nick, sep, text = s["message"].partition(": ")
            line.append(s.get("chan_name", "?") + " ", "bold")
            if sep:
                line.append("<").append(nick, name_color(nick)).append("> ").append(text)
            else:
                line.append(s["message"])
        else:
            line.append(f"channel {s.get('chan_hash', '??')} (not joined)", styles["dim"])
    else:
        line.append(f"{s['len']} bytes", styles["dim"])
    if s["hops"]:
        names = [n or h for h, n in zip(s["hops"], s["hop_names"])]
        if len(names) > 8:  # keep very long routes readable: first three … last three
            names = names[:3] + [f"…{len(names) - 6} more…"] + names[-3:]
        line.append(f"  via {','.join(names)}", styles["meta"])
    return line
