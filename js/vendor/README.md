# Vendored code

`noble.js` is an esbuild bundle (ESM, minified) of
[@noble/ed25519](https://github.com/paulmillr/noble-ed25519) 3.2.0 and
[@noble/hashes](https://github.com/paulmillr/noble-hashes) 2.4.0 (sha256, sha512),
both MIT-licensed by Paul Miller. It is kept in the repository so the page
loads nothing from elsewhere (UN-RFC 0043 section 6.4). Rebuild with
`tools/build-vendor.sh`.
