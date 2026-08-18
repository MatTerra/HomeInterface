#!/usr/bin/env bash
#
# Ship the working tree to the panel over ssh and restart it.
#
#   bash deploy/deploy.sh              # sync + deps if needed + restart
#   bash deploy/deploy.sh --logs       # ... then follow the journal
#   bash deploy/deploy.sh --dry-run    # say what it would send, send nothing
#
# What it sends is the *working tree* of every git-tracked file, not HEAD:
# deploying is how you find out whether a change works on the real panel, and
# having to commit first would put a run of broken commits in the history.
#
# Two files on the panel are its own and are never overwritten:
#   config/app.yaml   holds the touch calibration, which is per-device
#   .venv/            built on the Pi, for the Pi
# Pass --with-config when you actually mean to replace the panel's config.
#
set -euo pipefail

HOST=${HOST:-pitft}          # an ssh_config alias, see ~/.ssh/config
DEST=${DEST:-HomeInterface}  # relative paths are under the remote $HOME
SERVICE=${SERVICE:-homeinterface}

# Excluded from every deploy: screenshots are megabytes of PNG the panel has no
# use for, and .claude is local agent state.
EXCLUDE_RE='^(shots/|\.claude/)'

with_config=0
force_deps=0
restart=1
install_unit=0
prune=0
follow=0
dry_run=0

usage() {
    sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'USAGE'

Options:
  --host NAME       ssh target (default: pitft, or $HOST)
  --dest PATH       remote directory (default: HomeInterface, or $DEST)
  --with-config     also send config/app.yaml (overwrites the calibration)
  --deps            reinstall requirements.txt even if it did not change
  --unit            install deploy/homeinterface.service and daemon-reload
  --prune           delete remote files that this deploy no longer ships
  --no-restart      leave the running service alone
  --logs            follow journalctl after restarting
  --dry-run         list what would be sent, then stop
  -h, --help        this
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST=$2; shift 2 ;;
        --dest) DEST=$2; shift 2 ;;
        --with-config) with_config=1; shift ;;
        --deps) force_deps=1; shift ;;
        --unit) install_unit=1; shift ;;
        --prune) prune=1; shift ;;
        --no-restart) restart=0; shift ;;
        --logs) follow=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "deploy: unknown option $1" >&2; usage >&2; exit 2 ;;
    esac
done

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mdeploy:\033[0m %s\n' "$*" >&2; exit 1; }

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"

# -- the file list ---------------------------------------------------------
# git is the manifest: anything untracked is either build output, a local
# experiment, or a secret, and none of those belong on the panel.
files=()
while IFS= read -r path; do
    [[ $path =~ $EXCLUDE_RE ]] && continue
    [ "$with_config" = 0 ] && [ "$path" = "config/app.yaml" ] && continue
    files+=("$path")
done < <(git ls-files)
[ ${#files[@]} -gt 0 ] || die "git ls-files came back empty"

if [ "$dry_run" = 1 ]; then
    printf '%s\n' "${files[@]}"
    say "${#files[@]} files, would go to $HOST:$DEST (nothing sent)"
    exit 0
fi

say "$HOST:$DEST  ·  ${#files[@]} files"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" true \
    || die "cannot reach $HOST over ssh (BatchMode: needs a key, not a password)"

# -- send ------------------------------------------------------------------
# One stream, one ssh round trip: tar the tracked paths straight into the
# remote directory. Timestamps come across so an unchanged file stays
# unchanged, which is what keeps __pycache__ valid on a slow board.
printf '%s\0' "${files[@]}" \
    | tar --null --files-from=- --create --gzip \
    | ssh "$HOST" "mkdir -p '$DEST' && tar -xzf - -C '$DEST'"
say "files unpacked"

# -- prune stale files -----------------------------------------------------
# tar only ever adds. A file deleted or renamed here lingers there, and a
# stale .py that still imports cleanly is the kind of thing that costs an
# afternoon - so the shipped list is kept on the panel and diffed next time.
manifest=$(printf '%s\n' "${files[@]}")
if [ "$prune" = 1 ]; then
    printf '%s\n' "$manifest" | ssh "$HOST" "cat > '$DEST/.deploy-manifest.new'"
    ssh "$HOST" bash -s -- "$DEST" <<'REMOTE'
set -eu
cd "$1"
if [ -f .deploy-manifest ]; then
    # comm needs sorted input; -23 is "in the old list only"
    stale=$(comm -23 <(sort .deploy-manifest) <(sort .deploy-manifest.new))
    if [ -n "$stale" ]; then
        printf '%s\n' "$stale" | while IFS= read -r path; do
            [ -f "$path" ] && rm -f -- "$path" && echo "    removed $path"
        done
        # tidy up any directory the deletions emptied, repo root excepted
        find . -mindepth 1 -type d -empty -not -path './.venv/*' -delete 2>/dev/null || true
    fi
fi
mv .deploy-manifest.new .deploy-manifest
REMOTE
    say "pruned"
else
    printf '%s\n' "$manifest" | ssh "$HOST" "cat > '$DEST/.deploy-manifest'"
fi

# -- venv + dependencies ---------------------------------------------------
# pip install on a Pi 3 is slow enough to be worth skipping, so the hash of
# the requirements file that was last installed is kept next to the venv.
req_sha=$(sha256sum requirements.txt | cut -d' ' -f1)
ssh "$HOST" bash -s -- "$DEST" "$req_sha" "$force_deps" <<'REMOTE'
set -eu
cd "$1"; sha=$2; force=$3
if [ ! -x .venv/bin/python ]; then
    echo "    creating .venv"
    python3 -m venv .venv
    force=1
fi
if [ "$force" = 1 ] || [ "$(cat .deploy-reqs 2>/dev/null || true)" != "$sha" ]; then
    echo "    pip install -r requirements.txt"
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet -r requirements.txt
    printf '%s' "$sha" > .deploy-reqs
else
    echo "    dependencies unchanged"
fi
REMOTE

# -- systemd unit ----------------------------------------------------------
if [ "$install_unit" = 1 ]; then
    ssh "$HOST" bash -s -- "$DEST" "$SERVICE" <<'REMOTE'
set -eu
sudo install -m 644 "$1/deploy/homeinterface.service" "/etc/systemd/system/$2.service"
sudo systemctl daemon-reload
sudo systemctl enable "$2" >/dev/null
REMOTE
    say "unit installed and enabled"
fi

# -- restart ---------------------------------------------------------------
if [ "$restart" = 1 ]; then
    ssh "$HOST" bash -s -- "$SERVICE" <<'REMOTE'
set -eu
sudo systemctl restart "$1"
sleep 2
state=$(systemctl is-active "$1" || true)
echo "    $1: $state"
# A unit that dies on start goes active -> failed within a second or two, and
# the only useful thing at that point is the traceback.
if [ "$state" != "active" ]; then
    journalctl -u "$1" -n 30 --no-pager
    exit 1
fi
REMOTE
    say "service restarted"
fi

if [ "$follow" = 1 ]; then
    say "following journal (Ctrl-C to stop)"
    exec ssh -t "$HOST" "journalctl -u '$SERVICE' -f -n 30"
fi

say "done"
