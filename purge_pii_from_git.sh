#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# purge_pii_from_git.sh
#
# Removes ALL seen_jobs_cache_*.json files from the entire git history,
# including commits where they contained real email addresses in filenames.
#
# Run this ONCE on your local clone, then force-push.
# Anyone else with a clone must re-clone afterward.
#
# PREREQUISITES:
#   brew install git-filter-repo    # macOS
#   pip install git-filter-repo     # or via pip
#
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

echo "=== Step 1: Check git-filter-repo is installed ==="
if ! command -v git-filter-repo &>/dev/null; then
    echo "ERROR: git-filter-repo not found."
    echo "Install: brew install git-filter-repo  OR  pip install git-filter-repo"
    exit 1
fi

echo ""
echo "=== Step 2: Confirm you are in the repo root ==="
git rev-parse --git-dir > /dev/null 2>&1 || { echo "Not a git repo. cd to your repo first."; exit 1; }
echo "Repo root: $(git rev-parse --show-toplevel)"

echo ""
echo "=== Step 3: Remove cache files from ALL history ==="
# This rewrites every commit that ever contained a file matching the pattern.
git filter-repo --invert-paths --path-glob 'seen_jobs_cache_*.json' --force

echo ""
echo "=== Step 4: Remove any remaining local cache files ==="
find . -maxdepth 1 -name 'seen_jobs_cache_*.json' -exec rm -v {} \;

echo ""
echo "=== Step 5: Verify .gitignore blocks them ==="
if grep -q 'seen_jobs_cache_\*.json' .gitignore; then
    echo "✓ .gitignore already covers seen_jobs_cache_*.json"
else
    echo "seen_jobs_cache_*.json" >> .gitignore
    git add .gitignore
    git commit -m "chore: ignore per-user cache files"
    echo "✓ Added to .gitignore and committed"
fi

echo ""
echo "=== Step 6: Force-push ALL branches ==="
echo "WARNING: this rewrites remote history. Everyone with a clone must re-clone."
read -rp "Type YES to force-push master now: " confirm
if [[ "$confirm" == "YES" ]]; then
    git push origin master --force
    echo "✓ Force-pushed."
else
    echo "Skipped. When ready, run:"
    echo "  git push origin master --force"
fi

echo ""
echo "=== Done ==="
echo "Next steps:"
echo "  1. Notify your team (if any) to re-clone — their local history is now stale."
echo "  2. Check GitHub: go to the repo, search for 'seen_jobs_cache' — files should be gone."
echo "  3. If the repo is public, contact GitHub Support to clear their cache:"
echo "     https://support.github.com/contact"
