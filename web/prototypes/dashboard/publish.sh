#!/usr/bin/env bash
# User-approved public synthetic preview, isolated from login and live product.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$(cd "$SCRIPT_DIR/../../../../dist" && pwd)"
TARGET=/var/www/sites/korra21-dashboard-v1
test -f "$BUILD_DIR/index.html"
test -d "$BUILD_DIR/assets"
if [[ -L "$TARGET" ]]; then
  echo "Refusing to replace a symlink at the preview destination" >&2
  exit 1
fi
if [[ -e "$TARGET" && ! -f "$TARGET/.korra-dashboard-preview" ]]; then
  echo "Destination is not owned by this preview; refusing publication" >&2
  exit 1
fi
# No deletion: old hashed assets remain usable by already-open browser tabs.
# Only static asset types are copied, never source, maps, credentials or logs.
install -d -m 755 "$TARGET"
while IFS= read -r -d '' asset; do
  relative="${asset#"$BUILD_DIR/"}"
  install -d -m 755 "$TARGET/$(dirname "$relative")"
  install -m 644 "$asset" "$TARGET/$relative"
done < <(find "$BUILD_DIR" -type f \( -name '*.js' -o -name '*.css' -o -name '*.woff2' -o -name '*.woff' -o -name '*.png' -o -name '*.webp' -o -name '*.svg' -o -name '*.ico' \) -print0)
# Keep the bundled font's license with its redistributed files. This exact
# allowlisted path must not turn into a general text/source publication rule.
install -m 644 "$BUILD_DIR/fonts/Onest-OFL.txt" "$TARGET/fonts/Onest-OFL.txt"
touch "$TARGET/.korra-dashboard-preview"
install -m 644 "$BUILD_DIR/index.html" "$TARGET/index.html"
curl -fsS http://127.0.0.1/s/korra21-dashboard-v1/ -o /dev/null
echo 'http://95.181.173.107/s/korra21-dashboard-v1/'
