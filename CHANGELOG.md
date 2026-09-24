# Changelog

## 0.4.0

### New
- **Click a name for actions.** Clicking a nick in the chat, the sidebar or the contact list opens a card with that node's details (type, route, distance, last heard) and buttons for what you can do.
  - For people: Message, Whois, Find route, Trace, Share card, Mention.
  - For repeaters: Log in…, Status, Neighbours, Watch, CLI command…
  - For rooms: Join room…
  - For sensors: Telemetry.
  - For heard-but-not-added nodes: Add contact.
  
  Actions that need more (a password, a CLI command) pre-fill the input line for you.
- **Command hints.** Typing `/` lists matching commands with their arguments and a description; click one to pick it. Once a command is chosen, its usage shows above the input, and commands that take a node suggest matching contacts to click.
- **Command palette (F1).** Search every command by name or description; picking one runs it, or pre-fills it if it needs arguments.
- **Easy layout** (`/layout easy`): a clickable window list with unread markers (F3 toggles it in either layout), a toolbar (Channels, Message, Map, Packets, Graphs, Repeaters, Themes, Commands), and a key-hint bar. `/layout classic` is the irssi look, and still the default.
- **A size counter on the input line.** While you type a message, it shows how many bytes of the packet limit you've used (e.g. `87/141`). It turns yellow at 75%, red at 90%, and shows `· 2 msgs` once the message will be split into several packets. It counts bytes, because that's the radio's limit (emoji use 4), and accounts for your name on channel messages.
- **Click a hop count to see the route and trace it.** Clicking the `2»` next to a message shows the repeaters it came through, by name ("ada 🦊 → Blue Mtns Rpt → Ridgeline Rpt → you"), then traces that route with SNR at every hop.
  - For channel messages this is the route the message actually took, when the radio logged the packet. Otherwise it says so.
  - In a DM window it shows and traces your stored route to that contact.
  - A message heard directly just says there were no repeaters.
- **The input line wraps.** Long messages grow the input onto more lines (up to 6) instead of scrolling sideways. ↑/↓ still recall history from the first or last line, and move between wrapped lines otherwise. Pasted line breaks become spaces.

### Fixed
- Misaligned rows and stray blocks when names or messages contain emoji joiners, variation selectors, skin tones, flags or zero-width characters. Terminals and Rich measured these differently, so they're now normalised for display. Right-to-left override characters, which could make a name display as a different one, are removed too.
- Names in the sidebar keep their colours now that they're clickable.

## 0.3.1

### Changed
- Hop counts are a column before the nick (` 2»`, green ` 0»` for direct) instead of a tag at the end of each message. SNR is hidden unless you turn on `ui.show_snr`, and `ui.show_hops` turns the column off.
- Long lines wrap with a hanging indent under the message, as irssi does, instead of running back under the timestamp.

### Fixed (from a code audit prompted by a reader's report about /setperm)
- `/setperm` asks for confirmation, naming the node, the user's name and key, and the level. It only accepts an exact contact name or a full key; a prefix is accepted only when removing someone, matching the firmware.
- **Wrong recipients:** commands that send or change things no longer guess between similar names.
  - Two contacts sharing a name, or a typo in a longer name ("Pat Smiht"), is refused with a list of candidates instead of picking one. Mid-word matches ("ora" → "Nora") are gone.
  - `"quotes"` pick a name exactly.
  - `/msg Public Works …` asks whether you meant the channel or the contact.
- **Passwords:**
  - `/login` never sends a password to the open DM window after a typo, and only logs into repeaters, rooms and sensors.
  - Saved room passwords are pinned to the room's key and only sent to that room server, not to any node that advertises a similar name.
  - `/rcmd password …` isn't kept in scrollback. In a repeater's window, `/rcmd` sends the whole line to that repeater.
- **Terminal safety:** control characters (escape sequences, OSC, line breaks) in node names and messages are stripped. A remote node can no longer change your terminal title, clear the screen, write your clipboard, or fake chat lines.
- **Config:** a config file that can't be parsed is never overwritten with defaults.
  - Names like "Café" or "ルーム" no longer produce unreadable files. Writes are atomic and the file is private (0600).
  - `/set` masks room passwords and the BLE PIN.
  - List settings stay text, so `/set chat.highlights [1, 2]` no longer breaks message handling.
- **Private files:**
  - `/export` and `/keybackup` refuse to overwrite files and create them private.
  - Scrollback and state directories are 0700.
  - Log files get unique names, so `#vic` and `@vic`, or two emoji-only channel names, no longer share history.
- **Confirmations and validation:**
  - `/part` asks first (with a warning for private channels), as do `/setperm` and `/accept` of several contacts.
  - `/join #name <key>` explains that hashtag channels ignore keys.
  - Checked before sending: `/pin` (6 digits), `/txpower` (up to the radio's max), `/coords` ranges, `/radio` ranges, on/off values, `/advert every` (at least 5 minutes), device setting ranges, and name lengths.
- **Radio I/O:**
  - Repeater requests (status, login, neighbours…) no longer take a DM's send confirmation, which used to make delivered DMs retry and show ✗.
  - Automatic reconnects now resync, and are what triggers the "another client is using the radio" warning.
  - Late acks turn ✗ into ✓.
  - An error on one chunk of a long DM fails the rest instead of leaving them pending.
  - Background errors are reported instead of closing the app.
- **Windows:** reloading channels (on reconnect, `/join`, or a channel changed elsewhere) keeps you on the window you're looking at. A renamed slot no longer leaves a stale window that `/part` would use to delete the wrong channel.
- **Daemon (`--serve`):**
  - Long contact lists no longer cause replies to reach the wrong client.
  - A replaced radio connection can't tear down the new one.
  - The daemon's own app start is always sent first.
  - Apps that all call themselves "mccli" (meshcore_py, including Home Assistant) get separate message cursors, per host.
  - Oversized frames and stalled clients are dropped.
- **Smaller fixes:**
  - An alias wrapping its own command (`/alias msg /msg #x`) works, and alias loops are reported.
  - `/lastlog` doesn't find its own output.
  - Long messages never produce empty chunks or split emoji sequences, and never hang.
  - Wrapped lines indent exactly under the message.
  - `/trace <name>` prefers contacts over hex-looking hashes.
  - Map tiles are size-limited, decoded off the UI thread, and only fetched over https.
  - Notifications work with emoji and accented text (and can't be used to inject script).

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
