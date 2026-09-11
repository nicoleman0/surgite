#!/usr/bin/env bash
# Re-encrypt active provider keys under a new master key.
# Usage:
#   scripts/rotate-secrets.sh                       # generate a new key, persist
#   scripts/rotate-secrets.sh <new-raw-key>         # use a specific key
#   scripts/rotate-secrets.sh <new-fernet-key>      # 44-char urlsafe-b64 key

set -euo pipefail

cd "$(dirname "$0")/.."

KEY_FILE="${SECRETS_KEY_FILE:-.secrets_key}"

if [ ! -f "$KEY_FILE" ]; then
  echo "error: no key file at $KEY_FILE (cwd $(pwd)) — start the API at least once to generate one," >&2
  echo "       or set SECRETS_KEY_FILE if this deployment keeps it elsewhere" >&2
  exit 1
fi

NEW_INPUT="${1:-}"
if [ -z "$NEW_INPUT" ]; then
  echo "Generating a fresh Fernet key..."
  NEW_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
else
  NEW_KEY="$NEW_INPUT"
fi

OLD_KEY=$(cat "$KEY_FILE")

uv run --quiet python3 - "$OLD_KEY" "$NEW_KEY" <<'PYEOF'
import sys
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken

OLD_MATERIAL, NEW_MATERIAL = sys.argv[1], sys.argv[2]


def _derive(material: str) -> bytes:
    material = material.strip()
    try:
        decoded = base64.urlsafe_b64decode(material)
        if len(decoded) == 32:
            return material.encode("ascii")
    except (ValueError, base64.binascii.Error):
        pass
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


old_fernet = Fernet(_derive(OLD_MATERIAL))
new_fernet = Fernet(_derive(NEW_MATERIAL))

from surgite.db import GitConnectionRow, ProviderKeyRow, session_scope
from surgite.config import DATABASE_URL

if DATABASE_URL.startswith("sqlite"):
    print("refusing to rotate on a SQLite database (test DB?)", file=sys.stderr)
    sys.exit(2)

with session_scope() as s:
    for model, attr, active in (
        (ProviderKeyRow, "encrypted_key", ProviderKeyRow.revoked_at.is_(None)),
        (GitConnectionRow, "encrypted_secret", GitConnectionRow.encrypted_secret.is_not(None)),
    ):
        rows = s.query(model).filter(active).all()
        print(f"re-encrypting {len(rows)} active {model.__tablename__} row(s)")
        for row in rows:
            try:
                plain = old_fernet.decrypt(getattr(row, attr).encode("ascii"))
            except InvalidToken:
                print(f"  warning: {model.__tablename__} row {row.id} did not decrypt cleanly; leaving as-is", file=sys.stderr)
                continue
            setattr(row, attr, new_fernet.encrypt(plain).decode("ascii"))
    s.commit()
PYEOF

mkdir -p "$(dirname "$KEY_FILE")"
printf '%s\n' "$NEW_KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"

cat <<EOF

Rotation complete.

  - Old master: $(echo "$OLD_KEY" | head -c 8)…
  - New master: $(echo "$NEW_KEY" | head -c 8)…
  - Active rows re-encrypted: see script output above

Next steps:
  1. Restart the surgite API to load the new master key.
  2. Back up $KEY_FILE (chmod 600) somewhere safe.
  3. The old key is no longer valid; if you saved it elsewhere, delete it.
EOF
