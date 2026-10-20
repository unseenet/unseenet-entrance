# Certificates for an entrance

A browser on the open web will only use an entrance over HTTPS (a page served
over plain HTTP works, but anyone on the way can read and change it). That
means a certificate. These are the ways to get one, best first, without paying
and without handing TLS to someone else.

## 1. A domain name and Let's Encrypt

If you have a domain (or a subdomain someone gives you), point it straight at
the machine and let a web server fetch certificates for you. Caddy does it with
no configuration beyond the name:

```
entrance.example.org {
    reverse_proxy 127.0.0.1:8043
    log {
        output discard
    }
}
```

Set `ENTRANCE_TRUST_FORWARDED=1` in `/etc/unseenet-entrance.env` so limits are
counted per visitor, not per proxy.

## 2. A free dynamic DNS name

No domain? A free dynamic DNS service (for example deSEC or DuckDNS) gives you
a name that follows a changing home IP, and Let's Encrypt issues certificates
for it like for any other name. This is the easy way for an entrance at home.

## 3. A certificate for the bare IP address

Since January 2026 Let's Encrypt also issues certificates for IP addresses.
They last about six days and are renewed automatically, so they suit a cloud
VM with a fixed address. Visitors then use `https://203.0.113.7/`. It is not
pretty, but it needs no name at all.

## 4. No certificate: the onion address and the client in a file

Every entrance also has an onion address. Tor Browser users need no
certificate at all, and are hidden from the entrance too.

The client in a file (`unseenet-web.html`, UN-RFC 0043 section 8.7, not built
yet) is opened from disk, so no page is served; it then needs only the
WebSocket. Plain `ws://` works from a file, but then your ISP can see which
groups you read (never your keys: they never leave the browser), so `wss://`
is still better.

## Not: a CDN or a proxying tunnel

Cloudflare's proxy and tunnels end TLS on Cloudflare's machines, which then
see every visitor's address and everything they read. UN-RFC 0043 section 5
forbids it for a real entrance. It is fine for a quick test with test
identities, and nothing else.
