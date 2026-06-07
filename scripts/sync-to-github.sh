#!/bin/sh
#
# sync-to-github.sh — mirror p4v-tui from Perforce to the public GitHub
# repo, scrubbing operator-private identifiers on the way out.
# Adapted from docker-monitor's sync-to-github.sh (DESIGN §B.22 / §B.149).
#
# STRATEGY (the whole point): Perforce holds the verbatim, accurate
# source (admin@shared, the real depot path, etc.). The PUBLIC mirror
# is a one-way derivative produced HERE: p4 sync → scrub_mirror.py →
# git add → commit → push. The operator's main working tree is NEVER
# touched — this runs against a SEPARATE p4 client + directory that is
# also the git working tree.
#
#   main client (private)   : playground @ ~/p4/playground/...   (untouched)
#   mirror p4 client        : <user>-p4v-tui-mirror @ ~/p4/mirror/p4v-tui/
#   git mirror tree (== p4) : ~/p4/mirror/p4v-tui/
#
# The scrub denylist + the real depot path live in scripts/mirror-scrub.json
# (gitignored — never reaches the mirror). Fail-CLOSED: a missing config
# aborts the push rather than leaking.
#
# USAGE:
#   sync-to-github.sh init      # first time: create mirror client+dir,
#                               # sync p4 head, scrub, single squashed
#                               # commit, force-push to the remote.
#   sync-to-github.sh sync      # subsequent: re-sync head, scrub, commit
#                               # the delta, push.
#   sync-to-github.sh status    # show mirror dir / remote / head CL
#   sync-to-github.sh dry-run   # scrub head into the mirror, show git diff,
#                               # DO NOT commit or push (inspect the scrub).
#
# REQUIRED ENV:
#   SYNC_GITHUB_REMOTE   # https://github.com/<you>/p4v-tui.git (or git@…)
# OPTIONAL ENV:
#   SYNC_GITHUB_BRANCH        (= main)
#   SYNC_GITHUB_MIRROR_DIR    (= ~/p4/mirror/p4v-tui)
#   SYNC_GITHUB_MIRROR_CLIENT (= <user>-p4v-tui-mirror)
#   SYNC_GITHUB_AUTHOR_NAME / _EMAIL   (else git config)
#   SYNC_P4_PATH              (else mirror-scrub.json :: p4_path, else
#                              //depot/p4v-tui/...)

set -e
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
SCRUB_TOOL="$SCRIPT_DIR/scrub_mirror.py"
SCRUB_CONFIG="$SCRIPT_DIR/mirror-scrub.json"

: "${SYNC_GITHUB_REMOTE:=}"
: "${SYNC_GITHUB_BRANCH:=main}"
: "${SYNC_GITHUB_MIRROR_DIR:=$HOME/p4/mirror/p4v-tui}"
: "${SYNC_GITHUB_MIRROR_CLIENT:=$(id -un)-p4v-tui-mirror}"
: "${SYNC_GITHUB_AUTHOR_NAME:=}"
: "${SYNC_GITHUB_AUTHOR_EMAIL:=}"

die()  { echo "ERROR: $*" >&2; exit 1; }
note() { echo "$*"; }

# Real depot path: env → mirror-scrub.json :: p4_path → generic default.
# Kept out of this (tracked) script so the literal path isn't committed.
if [ -z "${SYNC_P4_PATH:-}" ]; then
    if [ -r "$SCRUB_CONFIG" ]; then
        SYNC_P4_PATH=$(python3 -c "
import json,sys
try:
    v=json.load(open(sys.argv[1])).get('p4_path','')
    sys.stdout.write(v.strip() if isinstance(v,str) else '')
except Exception: pass
" "$SCRUB_CONFIG" 2>/dev/null) || true
    fi
    : "${SYNC_P4_PATH:=//depot/p4v-tui/...}"
fi

require_remote() {
    [ -n "$SYNC_GITHUB_REMOTE" ] \
        || die "SYNC_GITHUB_REMOTE is required (the GitHub repo URL)."
}

run_scrub() {
    # Scrub the mirror tree in place, BEFORE git add. Fail-closed:
    # scrub_mirror.py exits non-zero if the config is missing → set -e
    # aborts the whole sync (better to block than leak).
    [ -x "$SCRUB_TOOL" ] || die "scrub tool missing/!exec: $SCRUB_TOOL"
    [ -r "$SCRUB_CONFIG" ] || die "scrub config missing: $SCRUB_CONFIG \
(refusing to push — fail-closed)."
    python3 "$SCRUB_TOOL" --dir "$SYNC_GITHUB_MIRROR_DIR" \
        --config "$SCRUB_CONFIG"
}

git_id_args() {
    a=""
    [ -n "$SYNC_GITHUB_AUTHOR_NAME" ]  && a="$a -c user.name=$SYNC_GITHUB_AUTHOR_NAME"
    [ -n "$SYNC_GITHUB_AUTHOR_EMAIL" ] && a="$a -c user.email=$SYNC_GITHUB_AUTHOR_EMAIL"
    echo "$a"
}

head_cl() {
    p4 -c "$SYNC_GITHUB_MIRROR_CLIENT" changes -m1 -s submitted \
        "$SYNC_P4_PATH" 2>/dev/null | awk '/^Change/ {print $2; exit}'
}

ensure_mirror_client() {
    # Create the dedicated mirror client (idempotent) mapping the real
    # depot path into the mirror dir. Never touches the main client.
    if ! p4 clients -e "$SYNC_GITHUB_MIRROR_CLIENT" 2>/dev/null \
            | grep -q "$SYNC_GITHUB_MIRROR_CLIENT"; then
        note "[mirror] creating p4 client $SYNC_GITHUB_MIRROR_CLIENT"
        p4 client -i <<EOF
Client: $SYNC_GITHUB_MIRROR_CLIENT
Owner: $(id -un)
Description: p4v-tui public github mirror (scrub-on-export)
Root: $SYNC_GITHUB_MIRROR_DIR
View:
	$SYNC_P4_PATH //$SYNC_GITHUB_MIRROR_CLIENT/...
EOF
    fi
}

sync_head() {
    # Force-sync the mirror dir to p4 head (the dir is a strict
    # derivative — operator never edits it, so -f is safe and avoids
    # "can't clobber writable file" after a prior scrub rewrote blobs).
    p4 -c "$SYNC_GITHUB_MIRROR_CLIENT" sync -f "$SYNC_P4_PATH" >/dev/null
}

cmd_init() {
    require_remote
    [ -e "$SYNC_GITHUB_MIRROR_DIR" ] && die "$SYNC_GITHUB_MIRROR_DIR \
already exists — use 'sync', or remove it to re-init."
    mkdir -p "$SYNC_GITHUB_MIRROR_DIR"
    ensure_mirror_client
    note "[init] syncing p4 head into mirror…"
    sync_head
    note "[init] scrubbing…"
    run_scrub
    cd "$SYNC_GITHUB_MIRROR_DIR"
    git init -q -b "$SYNC_GITHUB_BRANCH"
    git remote add origin "$SYNC_GITHUB_REMOTE"
    git add -A
    head=$(head_cl)
    # shellcheck disable=SC2046
    git $(git_id_args) commit -q \
        -m "Public import of p4v-tui (p4 CL ${head:-head})" \
        -m "Scrubbed export of the private Perforce source. See \
docs/mirror-workflow.md."
    note "[init] force-pushing to $SYNC_GITHUB_REMOTE …"
    git push -u --force origin "$SYNC_GITHUB_BRANCH"
    note "[init] done."
}

cmd_sync() {
    require_remote
    [ -d "$SYNC_GITHUB_MIRROR_DIR/.git" ] || die "not initialized — run \
'$0 init' first."
    ensure_mirror_client
    sync_head
    run_scrub
    cd "$SYNC_GITHUB_MIRROR_DIR"
    git add -A
    if git diff --cached --quiet; then
        note "[sync] nothing changed since last mirror. Done."
        return 0
    fi
    head=$(head_cl)
    # shellcheck disable=SC2046
    git $(git_id_args) commit -q \
        -m "Sync from Perforce (p4 CL ${head:-head})" \
        -m "Scrubbed export. See docs/mirror-workflow.md."
    note "[sync] pushing…"
    git push origin "$SYNC_GITHUB_BRANCH"
    note "[sync] done."
}

cmd_status() {
    echo "Mirror dir    : $SYNC_GITHUB_MIRROR_DIR"
    echo "Mirror client : $SYNC_GITHUB_MIRROR_CLIENT"
    echo "P4 path       : $SYNC_P4_PATH"
    echo "Remote        : ${SYNC_GITHUB_REMOTE:-(unset)}"
    echo "Branch        : $SYNC_GITHUB_BRANCH"
    echo "P4 head CL    : $(head_cl 2>/dev/null || echo '?')"
    if [ -d "$SYNC_GITHUB_MIRROR_DIR/.git" ]; then
        echo "Mirror git    : initialized"
    else
        echo "Mirror git    : NOT initialized (run '$0 init')"
    fi
}

cmd_dry_run() {
    ensure_mirror_client
    mkdir -p "$SYNC_GITHUB_MIRROR_DIR"
    sync_head
    run_scrub
    if [ -d "$SYNC_GITHUB_MIRROR_DIR/.git" ]; then
        cd "$SYNC_GITHUB_MIRROR_DIR"
        git add -A
        note "[dry-run] staged diff (NOT committed/pushed):"
        git --no-pager diff --cached --stat
    else
        note "[dry-run] scrubbed into $SYNC_GITHUB_MIRROR_DIR (no git yet)."
    fi
    note "[dry-run] inspect the tree above; nothing was pushed."
}

mode="${1:-status}"
case "$mode" in
    init)    cmd_init ;;
    sync)    cmd_sync ;;
    status)  cmd_status ;;
    dry-run) cmd_dry_run ;;
    -h|--help|help)
        sed -n '/^# USAGE/,/^set -e/p' "$0" | sed -e 's/^#//' -e 's/^ //'
        ;;
    *) die "unknown mode '$mode' — see '$0 --help'" ;;
esac
