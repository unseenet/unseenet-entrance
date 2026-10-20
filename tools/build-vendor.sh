#!/bin/sh
# Rebuild web/vendor/noble.js from pinned npm versions.
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
cd "$tmp"
npm init -y >/dev/null
npm i -s @noble/ed25519@3.2.0 @noble/hashes@2.4.0 esbuild >/dev/null
cat > entry.js <<'JS'
import * as ed from '@noble/ed25519';
import { sha256, sha512 } from '@noble/hashes/sha2.js';
ed.hashes.sha512 = sha512;
export { ed, sha256, sha512 };
JS
npx esbuild entry.js --bundle --format=esm --minify --legal-comments=inline --outfile="$here/js/vendor/noble.js"
rm -rf "$tmp"
