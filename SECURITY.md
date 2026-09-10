# Security notes

This project gives a browser tab real mouse and keyboard control of a real
Windows PC. Treat it accordingly.

## Rotate the old TURN credentials

Earlier commits of this repository contained working Metered TURN relay
credentials, hardcoded in `viewer/index.html` and `host-agent/host.py`. They
have been removed from the current code, but **git history still contains
them**, and anyone who has ever had a copy of this repo has them.

Go to the Metered dashboard and revoke or regenerate that key. Deleting the
lines was not enough. Making the repository private is not enough either — the
credentials were already exposed while it was public.

If you want them gone from history as well, rewrite it with
[git-filter-repo](https://github.com/newren/git-filter-repo) and force-push.
Everyone with a clone has to re-clone afterwards. Rotating the credentials is
the part that actually matters; scrubbing history is cleanup.

## What the access key protects

`ACCESS_KEY` is a single shared secret. The host refuses to start without one.
From it, both ends derive:

* the room id the signaling server sees, `sha256("rd-room|" + room + "|" + key)`
* the directory entry name, `sha256("rd-dir|" + code + "|" + key)`
* the challenge response, `HMAC-SHA256(key, "rd-auth|" + room + "|" + nonce)`

So the key is never transmitted, the server never sees your room code, a
stranger cannot resolve a code to your tunnel URL, and nobody can register as a
fake host in your room to collect your keystrokes. Wrong answers are rate
limited and five of them drop the connection.

Anyone holding the key has full control of the host machine. Share it out of
band, not alongside the room code, and change it when access should end.

## What it does not protect

* **The signaling server sees your screen in WebSocket streaming mode.** GPU
  H.264 and JPEG frames are relayed through that process. WebRTC mode is
  peer-to-peer and does not have this property. Run your own server.
* **A TURN relay sees encrypted media only**, but the relay operator does learn
  both endpoints' addresses and how much you send.
* **Cloudflare's free tunnel terminates TLS.** `trycloudflare.com` URLs are
  also unauthenticated, which is exactly why the access key exists.
* **No sandbox.** The host injects input as the logged-in user with that user's
  full privileges. There is no restricted mode and no per-action confirmation.
* **No recording or audit log.** Nothing on the host tells you afterwards what a
  viewer did.

## Before running this

* Don't run the host on a machine holding anything you would not hand over.
* Don't leave it running unattended.
* Keep the signaling server private, or at least keep the URL to yourself. It
  is unauthenticated by design; the host is what does the authenticating.
* Injected input can trip kernel-level anti-cheat. Assume a ban risk in
  competitive online games.
