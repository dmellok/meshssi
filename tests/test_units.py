import time

from meshssi import geo, packets
from meshssi.config import Config, dumps
from meshssi.store import Store
from meshssi.themes import THEMES
from meshssi.util import ago, expand_shortcodes, sparkline, split_utf8


def test_split_utf8_respects_byte_limit_and_words():
    text = "héllo wörld 🐢 " * 30
    chunks = split_utf8(text, 50)
    assert all(len(c.encode()) <= 50 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_split_utf8_short_and_unbroken():
    assert split_utf8("hi", 150) == ["hi"]
    long_word = "x" * 320
    assert [len(c) for c in split_utf8(long_word, 150)] == [150, 150, 20]


def test_ago_and_sparkline():
    assert ago(None) == "never"
    assert ago(time.time() - 125) == "2m ago"
    assert sparkline([1, 2, 3, 4], 10) == "▁▃▆█"
    assert sparkline([], 5) == ""


def test_shortcodes():
    assert expand_shortcodes("nice :thumbs_up: :notacode:") == "nice 👍 :notacode:"


def test_config_roundtrip_and_coercion(tmp_path):
    cfg = Config(tmp_path / "config.toml")
    assert cfg.set("chat.dm_retries", "5") == 5
    assert cfg.set("notify.bell", "off") is False
    assert cfg.set("chat.highlights", "meetup, pizza") == ["meetup", "pizza"]
    cfg["plugin.pingbot"]["reply"] = "pong!"
    cfg.save()
    again = Config(tmp_path / "config.toml")
    assert again.get("chat.dm_retries") == 5
    assert again.get("notify.bell") is False
    assert again["plugin.pingbot"]["reply"] == "pong!"
    assert "[plugin.pingbot]" in dumps(again.data)


def test_config_rejects_unknown_keys(tmp_path):
    cfg = Config(tmp_path / "config.toml")
    try:
        cfg.set("chat.nonsense", "1")
    except KeyError:
        return
    raise AssertionError("unknown key accepted")


def test_store_replays_acks(tmp_path):
    st = Store("abcdef123456", root=tmp_path)
    st.append("dm:x", {"k": "msg", "id": "a", "text": "one", "st": "pending"})
    st.append("dm:x", {"k": "msg", "id": "b", "text": "two", "st": "pending"})
    st.append("dm:x", {"k": "ack", "ref": "a", "st": "ok"})
    recs = {r["id"]: r for r in st.load("dm:x")}
    assert recs["a"]["st"] == "ok"
    assert recs["b"]["st"] == "fail"  # never acked before the app closed


def test_geo():
    from meshssi.basemap import View

    syd, mel = (-33.8688, 151.2093), (-37.8136, 144.9631)
    assert 700 < geo.distance_km(*syd, *mel) < 730
    assert geo.compass(geo.bearing(*syd, *mel)) == "SW"
    assert geo.describe(syd, 0, 0) == ""
    view = View.fit([syd, mel], 60, 20)
    out, off = geo.render_map(view, (*syd, "me"), [{"name": "mel", "type": 2, "lat": mel[0], "lon": mel[1]}],
                              lambda n: "cyan", THEMES["irssi"])
    assert "mel" in out.plain and "◉" in out.plain and off == 0


def test_packet_summary_and_render():
    p = {"recv_time": time.time(), "snr": 6.5, "rssi": -80, "route_typename": "FLOOD", "payload_typename": "ADVERT",
         "path_hash_size": 1, "path": "a1b2", "payload_length": 100, "adv_name": "Hill Rpt", "adv_type": 2,
         "adv_key": "ff" * 32}
    s = packets.summarize(p, lambda h: "Alpha" if h == "a1" else None)
    assert s["hops"] == ["a1", "b2"] and s["hop_names"] == ["Alpha", None]
    line = packets.render(s, THEMES["irssi"], lambda n: "cyan").plain
    assert "ADVERT" in line and "Hill Rpt" in line and "via Alpha,b2" in line and "2 hops" in line


def test_every_theme_is_complete_and_valid():
    from rich.style import Style
    from textual.color import Color

    from meshssi.themes import ROLES

    assert len(THEMES) >= 25
    for name, t in THEMES.items():
        for role in ROLES + ("good", "warn", "bad", "status_ok", "status_bad", "graph", "ptype"):
            assert role in t, (name, role)
        for key in ("background", "foreground", "bar_bg", "bar_fg", "sidebar_bg", "sidebar_border", "dim"):
            Color.parse(t[key])  # textual widget colours
        styles = [v for k, v in t.items() if isinstance(v, str)] + t["act"] + t["nicks"] + t["graph"] + list(t["ptype"].values())
        for s in styles:
            Style.parse(s)  # rich text styles


def test_names_match_without_emoji_or_by_emoji_name():
    from meshssi.util import name_forms, name_matches

    assert "turtle hops" in name_forms("Turtle Hops 🐢")
    assert "fox face" in name_forms("🦊")
    for typed in ("tur", "turtle hops", "hops", "turtle", ":turtle", "turtle hops turtle"):
        assert name_matches("Turtle Hops 🐢", typed), typed
    assert name_matches("ada 🦊", "fox") and name_matches("ada 🦊", "fox face")
    assert not name_matches("Nora 🌿", "fox")


def test_map_leaves_far_outliers_off_the_plot():
    me = (-37.81, 144.96)
    near = [{"name": f"n{i}", "type": 1, "lat": -37.8 + i * 0.01, "lon": 144.95 + i * 0.01} for i in range(6)]
    far = {"name": "Goulburn", "type": 1, "lat": -34.75, "lon": 149.72}
    local, n_far = geo.local_points(me, near + [far])
    assert n_far == 1 and far not in local


def test_mvt_decode_and_basemap_drawing():
    """Encode a tiny vector tile by hand, decode it, and draw it: water fill, a road and a place name."""
    from meshssi.basemap import Canvas, View, decode_tile, draw

    def varint(n):
        out = b""
        while True:
            b, n = n & 0x7F, n >> 7
            out += bytes([b | (0x80 if n else 0)])
            if not n:
                return out

    def field(num, wire, payload):
        key = varint(num << 3 | wire)
        return key + (varint(payload) if wire == 0 else varint(len(payload)) + payload)

    def zz(n):
        return (n << 1) ^ (n >> 31)

    def geom(cmds):
        return b"".join(varint(c) for c in cmds)

    def feature(gtype, tags, cmds):
        return field(2, 2, b"".join(varint(t) for t in tags)) + field(3, 0, gtype) + field(4, 2, geom(cmds))

    def layer(name, features, keys, values):
        body = field(15, 0, 2) + field(1, 2, name.encode())
        body += b"".join(field(2, 2, f) for f in features)
        body += b"".join(field(3, 2, k.encode()) for k in keys)
        body += b"".join(field(4, 2, field(1, 2, v.encode())) for v in values)
        return field(3, 2, body + field(5, 0, 4096))

    square = [1 << 3 | 1, zz(0), zz(0), 3 << 3 | 2, zz(2048), zz(0), zz(0), zz(2048), zz(-2048), zz(0), 7 | 1 << 3]
    road = [1 << 3 | 1, zz(0), zz(3000), 1 << 3 | 2, zz(4096), zz(0)]
    town = [1 << 3 | 1, zz(3000), zz(1000)]
    tile = (layer("water", [feature(3, [], square)], [], [])
            + layer("transportation", [feature(2, [0, 0], road)], ["class"], ["motorway"])
            + layer("place", [feature(1, [0, 0, 1, 1], town)], ["class", "name"], ["town", "Testville"]))
    decoded = decode_tile(tile)
    assert decoded["water"][0]["parts"][0][:3] == [(0, 0), (2048, 0), (2048, 2048)]
    assert decoded["transportation"][0]["props"] == {"class": "motorway"}
    view = View(0.5, 0.5, 60.0, 30, 15)  # the whole tile (z0) spans 60 dots
    cv = draw(view, [(0, 0, 0, decoded)], THEMES["irssi"]["map"])
    assert isinstance(cv, Canvas)
    assert any(any(row) for row in cv.bg)  # water filled
    assert any(any(row) for row in cv.dots)  # road drawn
    assert any(text == "Testville" for _, _, text, _ in cv.labels)
