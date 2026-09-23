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
    syd, mel = (-33.8688, 151.2093), (-37.8136, 144.9631)
    assert 700 < geo.distance_km(*syd, *mel) < 730
    assert geo.compass(geo.bearing(*syd, *mel)) == "SW"
    assert geo.describe(syd, 0, 0) == ""
    out = geo.render_map((*syd, "me"), [{"name": "mel", "type": 2, "lat": mel[0], "lon": mel[1]}], 60, 20,
                         lambda n: "cyan", THEMES["irssi"])
    assert "mel" in out.plain and "◉" in out.plain


def test_packet_summary_and_render():
    p = {"recv_time": time.time(), "snr": 6.5, "rssi": -80, "route_typename": "FLOOD", "payload_typename": "ADVERT",
         "path_hash_size": 1, "path": "a1b2", "payload_length": 100, "adv_name": "Hill Rpt", "adv_type": 2,
         "adv_key": "ff" * 32}
    s = packets.summarize(p, lambda h: "Alpha" if h == "a1" else None)
    assert s["hops"] == ["a1", "b2"] and s["hop_names"] == ["Alpha", None]
    line = packets.render(s, THEMES["irssi"], lambda n: "cyan").plain
    assert "ADVERT" in line and "Hill Rpt" in line and "via Alpha,b2" in line and "2 hops" in line
