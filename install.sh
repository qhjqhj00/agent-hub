#!/bin/sh
# agent-hub installer. Puts `agenthub` in ~/.local/bin; nothing else to install but python3 (3.8+).
#
# First machine (from GitHub):
#   curl -fsSL https://raw.githubusercontent.com/qhjqhj00/agent-hub/main/install.sh | sh
#   agenthub up                        # hub + this machine, dashboard on http://127.0.0.1:8787
#
# Every other machine (served by your hub; `agenthub nodes add NAME` prints this line):
#   curl -fsSL http://HUB:8787/install.sh | sh -s -- --join http://HUB:8787 NODE_TOKEN
#
# Options: --join URL TOKEN   connect to that hub as a node (agent setup + hubd service)
#          --up               start a hub here right away (`agenthub up --service`)
#          --no-service       with --join: do not install the hubd user service
set -e
HUB_URL="__HUB_URL__"
REPO="${AGENTHUB_REPO:-qhjqhj00/agent-hub}"
REF="${AGENTHUB_REF:-main}"
BIN="$HOME/.local/bin"
HOMEDIR="${AGENTHUB_HOME:-$HOME/.agent-hub}"
JOIN_URL=""; JOIN_TOKEN=""; UP=""; NOSERVICE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --join) JOIN_URL="$2"; JOIN_TOKEN="$3"; shift 3 ;;
    --up) UP=1; shift ;;
    --no-service) NOSERVICE="--no-service"; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "agent-hub needs python3 (3.8 or newer)" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || { echo "python3 is too old (need 3.8+)" >&2; exit 1; }
mkdir -p "$BIN" "$HOMEDIR"

case "$HUB_URL" in
  http*)
    # served by a hub: the single-file client, same version as that hub
    curl -fsSL "$HUB_URL/client/agenthub.pyz" -o "$BIN/agenthub.tmp"
    chmod +x "$BIN/agenthub.tmp" && mv "$BIN/agenthub.tmp" "$BIN/agenthub"
    echo "installed agenthub (from $HUB_URL) -> $BIN/agenthub"
    ;;
  *)
    # from GitHub: the full source (the hub needs its dashboard and plugin files)
    SRC="$HOMEDIR/src"
    TMP="$HOMEDIR/src.new"
    rm -rf "$TMP" && mkdir -p "$TMP"
    curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" | tar -xz -C "$TMP" --strip-components 1
    rm -rf "$SRC" && mv "$TMP" "$SRC"
    cat > "$BIN/agenthub" <<EOF
#!/bin/sh
exec python3 "$SRC/bin/agenthub" "\$@"
EOF
    chmod +x "$BIN/agenthub"
    echo "installed agenthub ($REPO@$REF) -> $BIN/agenthub"
    ;;
esac

case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: add $BIN to your PATH (e.g. in ~/.profile)"; export PATH="$BIN:$PATH" ;; esac
command -v tmux >/dev/null 2>&1 || echo "note: install tmux so the hub can start agents on this machine when work arrives"

if [ -n "$JOIN_URL" ]; then
  "$BIN/agenthub" join "$JOIN_URL" "$JOIN_TOKEN" $NOSERVICE
elif [ -n "$UP" ]; then
  "$BIN/agenthub" up --service
else
  echo
  echo "next:  agenthub up            # start a hub here (single machine: that is all)"
  echo "       agenthub join URL TOKEN  # or join an existing hub"
fi
