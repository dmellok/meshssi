# Changelog

## 0.3.0

### New
- **An OpenStreetMap map background.** `/map` now draws nodes over a braille rendering of real OpenStreetMap data: water and coastline, rivers, roads that get more detailed as you zoom in, parks and place names, in your theme's colours.
  - While the map is showing and the input line is empty, the arrow keys pan, `+` / `-` zoom, `0` fits everything back in, and `c` centres on you. `/map in|out|fit|center <node>` does the same by command.
  - Tiles come from OpenFreeMap, with no account or key needed. They're cached in `~/.cache/meshssi/tiles/`, so areas you've viewed work offline.
  - It degrades gracefully. With no network or no tiles you get the plain map, a bad tile is skipped and retried later, and fonts without braille can use `/map style dots`. `/map basemap off` turns it off.
- **21 new themes, 26 in all.** New: dracula, nord, solarized-dark/light, catppuccin (mocha and latte), tokyonight, one-dark, monokai, everforest, rose-pine, kanagawa, gruvbox-light, github-dark/light, bitchx, mirc, matrix, amber, synthwave and high-contrast.
  - `/theme` shows a live swatch of each one, and `/theme next` / `/theme prev` flip through them.
  - Graphs, packet types, signal quality and scrollbars all follow the theme now.
- **Names without emoji.** Tab completion and contact lookups work with the emoji left out ("turtle hops") or typed by its name ("turtle", "fox_face"). Any word of a name matches too ("hops").
- **A one-line installer** for macOS and Linux:
  `curl -fsSL https://github.com/dmellok/meshssi/releases/latest/download/install.sh | sh`
- **A licence:** meshssi is now under the AGPL-3.0-or-later.

### Improved
- Stored routes name each repeater ("1 hop via Docklands Rpt") instead of showing hashes.
- `/split` has a header saying which window it shows.
- The map keeps far-off outliers in the table, so the local mesh fills the screen.
- Long names in the sidebar are cut short with "…" instead of wrapping.
- The packet monitor shows `@[name]` mentions as `@name`, like the chat windows.
- Map labels handle two-cell emoji properly.

## 0.2.0
- Live packet monitor (`/rf`), node map, signal graphs, and a record of every node heard over the air
- `/trace` with SNR at each hop, `/discover`, path discovery, flood scopes, and heard-by counts on your own channel messages
- DM retries that fall back to flood routing, with round-trip times
- Repeater and room admin, and a dashboard of repeaters you `/watch`
- Device maintenance: radio presets, scheduled adverts, contact QR codes, import/export, key backup and restore, firmware check
- irssi conveniences: `/lastlog`, `/ignore`, `/hilight`, `/away`, aliases, split windows, unread marker, themes, desktop notifications
- `--serve` daemon mode to share one radio between apps; Bluetooth support; `--demo` mode; Python plugins; tests and CI

## 0.1.0
- First version: an irssi-style chat client for MeshCore companions, with device maintenance commands
