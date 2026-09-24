#!/bin/sh
# meshssi installer: puts the `meshssi` command on your PATH, in its own isolated environment.
#
#   curl -fsSL https://github.com/dmellok/meshssi/releases/latest/download/install.sh | sh
#
# Uses uv if you have it, else pipx; if you have neither, it installs uv (https://docs.astral.sh/uv/)
# first. uv also fetches a suitable Python if yours is older than 3.11.
#
# Environment overrides:
#   MESHSSI_VERSION=0.4.0      install a specific release
#   MESHSSI_SOURCE=<path|url>  install from a local wheel/checkout or any pip-installable URL
set -eu

VERSION="${MESHSSI_VERSION:-0.4.0}"
REPO="https://github.com/dmellok/meshssi"
SOURCE="${MESHSSI_SOURCE:-$REPO/releases/download/v$VERSION/meshssi-$VERSION-py3-none-any.whl}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
    Darwin|Linux) ;;
    *) fail "this installer supports macOS and Linux. On Windows: pipx install $SOURCE" ;;
esac

if command -v uv >/dev/null 2>&1; then
    say "Installing meshssi $VERSION with uv"
    uv tool install --force --python ">=3.11" "$SOURCE"
elif command -v pipx >/dev/null 2>&1; then
    say "Installing meshssi $VERSION with pipx"
    pipx install --force "$SOURCE"
else
    say "Neither uv nor pipx found; installing uv first (https://astral.sh/uv)"
    command -v curl >/dev/null 2>&1 || fail "curl is needed to install uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # the uv installer puts it in ~/.local/bin (or $XDG_BIN_HOME / $CARGO_HOME/bin on some setups)
    for dir in "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "${CARGO_HOME:-$HOME/.cargo}/bin"; do
        [ -n "$dir" ] && [ -x "$dir/uv" ] && PATH="$dir:$PATH" && break
    done
    command -v uv >/dev/null 2>&1 || fail "uv installed but not found on PATH; open a new terminal and re-run"
    say "Installing meshssi $VERSION with uv"
    uv tool install --force --python ">=3.11" "$SOURCE"
fi

if command -v meshssi >/dev/null 2>&1; then
    say "Done: $(meshssi --version)"
else
    BIN="$HOME/.local/bin"
    say "Installed. Add $BIN to your PATH to run it from anywhere, e.g.:"
    printf '    echo '\''export PATH="%s:$PATH"'\'' >> ~/.%src && exec $SHELL\n' "$BIN" "$(basename "${SHELL:-sh}")"
fi
cat <<'EOF'

  meshssi --demo            try it against a simulated mesh
  meshssi 192.168.1.50      connect to a WiFi companion (or /dev/tty…, or ble:<address>)
  meshssi --scan            find Bluetooth radios

  Update: re-run this installer.   Remove: uv tool uninstall meshssi  (or: pipx uninstall meshssi)
EOF
