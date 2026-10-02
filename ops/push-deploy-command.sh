#!/usr/bin/env bash
set -euo pipefail

if [[ "${SSH_ORIGINAL_COMMAND:-}" != "deploy" ]]; then
    echo "Only the deploy command is permitted." >&2
    exit 64
fi

WORK_TREE=/home/discord/radio-ravnica
WEB_ROOT=/home/discord/rr-tournaments-web
LOCK_FILE=/home/discord/.rr-production-deploy.lock
STASH_OID=""

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "A deployment is already running."
    exit 0
fi

cd "$WORK_TREE"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    OLD_STASH_OID="$(git rev-parse -q --verify refs/stash || true)"
    GIT_AUTHOR_NAME="RR deploy" \
        GIT_AUTHOR_EMAIL="rr-deploy@localhost" \
        GIT_COMMITTER_NAME="RR deploy" \
        GIT_COMMITTER_EMAIL="rr-deploy@localhost" \
        git stash push --include-untracked -m "Preserve local changes before push deployment"
    NEW_STASH_OID="$(git rev-parse -q --verify refs/stash || true)"
    if [[ -n "$NEW_STASH_OID" && "$NEW_STASH_OID" != "$OLD_STASH_OID" ]]; then
        STASH_OID="$NEW_STASH_OID"
    fi
fi

if ! git fetch --prune origin main; then
    if [[ -n "$STASH_OID" ]]; then git stash apply --index "$STASH_OID"; fi
    exit 1
fi

git switch main
if ! git merge --ff-only origin/main; then
    if [[ -n "$STASH_OID" ]]; then git stash apply --index "$STASH_OID"; fi
    exit 1
fi

if [[ -n "$STASH_OID" ]]; then
    if ! git stash apply --index "$STASH_OID"; then
        echo "Could not reapply preserved local changes; stash retained and services were not restarted." >&2
        exit 1
    fi
    if git cat-file -e "HEAD:discord_bot_shared/__init__.py" 2>/dev/null; then
        git restore --source=HEAD --staged --worktree -- discord_bot_shared/__init__.py
    fi
    git stash drop stash@{0} >/dev/null
    STASH_OID=""
fi

rsync -a --delete \
    --exclude='/.git/' \
    --exclude='/venv/' \
    --exclude='/data/' \
    --exclude='/tmp/' \
    --exclude='/.env' \
    --exclude='/config.env' \
    "$WORK_TREE/admin_web/" "$WEB_ROOT/"

"$WORK_TREE/venv/bin/pip" install --upgrade -r "$WORK_TREE/requirements.txt"
"$WEB_ROOT/venv/bin/pip" install --upgrade -r "$WEB_ROOT/requirements.txt"

sudo -n /usr/bin/systemctl restart rr-discord-bot.service rr-tournaments-web.service
echo "Deployed RR commit $(git rev-parse --short HEAD)."