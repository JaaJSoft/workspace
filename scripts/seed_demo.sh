#!/usr/bin/env bash
#
# Preset wrapper around scripts/seed_demo.py.
#
# Picks a sensible bundle of --users / --*-files / --*-events / --history-days
# for a given target size, so you don't have to remember the knobs. Any extra
# arguments are passed straight through to seed_demo.py, so you can still tweak
# a single value or add --purge / --seed / --no-chat, e.g.:
#
#   scripts/seed_demo.sh medium --purge --yes
#   scripts/seed_demo.sh large --seed 42
#   scripts/seed_demo.sh small --history-days 365 --no-avatars
#
# Runs with DEBUG=false so the dev SQL DEBUG logging doesn't drown the output
# (and to keep the run fast). Data is marked with the demo email domain and can
# be wiped with:  scripts/seed_demo.sh --purge   (see seed_demo.py --purge).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: seed_demo.sh <preset> [extra seed_demo.py args...]

Presets (approx. scale — files/events are randomized per user):

  tiny     3 users     ~30 files      quick smoke test
  small    10 users    ~300 files     local dev / screenshots
  medium   50 users    ~3k files      realistic company demo
  large    200 users   ~40k files     stress listings & queries
  xl       1000 users  ~400k files    heavy load (slow — minutes)

Examples:
  seed_demo.sh small --purge --yes        # wipe prior demo data, then seed
  seed_demo.sh medium --seed 42           # reproducible run
  seed_demo.sh large --no-avatars         # skip avatar generation (faster)

Any flag accepted by seed_demo.py may be appended and overrides the preset.
Run 'uv run python scripts/seed_demo.py --help' for the full flag list.
EOF
}

if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

preset="$1"
shift

case "$preset" in
    tiny)
        args=(--users 3 --min-files 2 --max-files 15 --max-depth 3
              --dms 3 --min-events 2 --max-events 8 --history-days 60)
        ;;
    small)
        args=(--users 10 --min-files 5 --max-files 40 --max-depth 3
              --dms 12 --min-events 3 --max-events 15 --history-days 120)
        ;;
    medium)
        args=(--users 50 --min-files 10 --max-files 90 --max-depth 4
              --dms 80 --min-messages 5 --max-messages 40
              --min-events 5 --max-events 30 --history-days 180)
        ;;
    large)
        args=(--users 200 --min-files 20 --max-files 220 --max-depth 5
              --dms 400 --min-messages 5 --max-messages 60
              --min-events 10 --max-events 60 --history-days 365)
        ;;
    xl)
        args=(--users 1000 --min-files 30 --max-files 400 --max-depth 6
              --dms 2000 --min-messages 5 --max-messages 80
              --min-events 20 --max-events 120 --history-days 540)
        ;;
    *)
        echo "Unknown preset: '$preset'" >&2
        echo >&2
        usage >&2
        exit 1
        ;;
esac

echo ">> preset '$preset': seed_demo.py ${args[*]} $*"
cd "$ROOT"
DEBUG=false exec uv run python scripts/seed_demo.py "${args[@]}" "$@"
