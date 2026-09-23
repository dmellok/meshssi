import pytest

import meshssi.basemap as basemap


@pytest.fixture(autouse=True)
def offline_basemap(tmp_path, monkeypatch):
    """No network in tests (which also exercises the offline fallback), and never the user's tile cache."""

    def no_network(url, timeout=10):
        raise OSError("offline in tests")

    monkeypatch.setattr(basemap, "fetch_url", no_network)
    monkeypatch.setattr(basemap, "CACHE_DIR", tmp_path / "tiles")
