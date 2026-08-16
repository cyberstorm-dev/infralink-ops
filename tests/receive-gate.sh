#!/bin/sh
set -eu

root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT HUP INT TERM
remote="$root/remote.git"
client="$root/client"

test -x /usr/local/bin/infralink-gitea-entrypoint

git init --bare "$remote" >/dev/null
test -x "$remote/hooks/pre-receive.d/20-infralink-gitleaks"
mkdir -p "$remote/hooks/pre-receive.d"
cat >"$remote/hooks/pre-receive" <<'HOOK'
#!/bin/sh
set -eu
payload=$(cat)
for hook in "$GIT_DIR"/hooks/pre-receive.d/*; do
    test -x "$hook" || continue
    printf '%s\n' "$payload" | "$hook"
done
HOOK
chmod 0755 "$remote/hooks/pre-receive"

git init "$client" >/dev/null
git -C "$client" config user.name test
git -C "$client" config user.email test@example.invalid
git -C "$client" remote add origin "$remote"
printf '%s\n' normal >"$client/README"
git -C "$client" add README
git -C "$client" commit -m normal >/dev/null
git -C "$client" push -u origin HEAD:main >/dev/null

rm "$remote/hooks/pre-receive.d/20-infralink-gitleaks"
/usr/local/lib/infralink-gitea-hooks/install-receive-gate "$root"
test -x "$remote/hooks/pre-receive.d/20-infralink-gitleaks"

printf '%s\n' 'SLACK_TOKEN=xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwx' >"$client/leak.txt"
git -C "$client" add leak.txt
git -C "$client" commit -m secret >/dev/null
if git -C "$client" push origin HEAD:main >/dev/null 2>&1; then
    echo 'expected the receive gate to reject a known secret' >&2
    exit 1
fi
