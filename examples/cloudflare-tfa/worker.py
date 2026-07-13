"""Cloudflare Python Worker that captures Arlo 2FA codes for pyaarlo.

An Email Routing rule delivers the Arlo 2FA email to this worker's
`email` handler, which extracts the 6 digit code and stores it in a KV
namespace. pyaarlo's `cloudflare` TFA source polls the `fetch` handler
for the code during login.

The wire format matches pyaarlo's rest-api source envelope:

    GET  /get?email=<addr>    -> {"meta": {"code": 200},
                                  "data": {"code": "123456", "timestamp": ...}}
    POST /clear?email=<addr>  -> {"meta": {"code": 200}, "data": {}}

Both require `Authorization: Bearer <TFA_TOKEN>`.

See the README.md alongside this file for deployment instructions.
"""

import email
import email.utils
import hmac
import json
import re
import time
from urllib.parse import parse_qs, urlparse

try:
    # Only available inside the Workers runtime (Pyodide); guarded so the
    # pure functions below stay importable under CPython for unit tests.
    from workers import Response, WorkerEntrypoint
    from pyodide.ffi import to_js as _to_js
    from js import Object, Response as JsResponse
except ImportError:
    WorkerEntrypoint = object

# Same line-is-just-the-code regex as pyaarlo's IMAP source.
CODE_RE = re.compile(r"^\W*(\d{6})\W*$")

# Same sender filter as pyaarlo's IMAP source.
ALLOWED_SENDERS = {"do_not_reply@arlo.com"}

# How long a captured code stays available. Arlo codes expire quickly
# anyway; KV requires at least 60.
CODE_TTL = 600


def extract_code(raw_bytes):
    """Return the 6 digit code from a raw RFC 5322 message, or None."""
    msg = email.message_from_bytes(raw_bytes)
    for part in msg.walk():
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        for line in text.splitlines():
            code = CODE_RE.match(line.strip())
            if code is not None:
                return code.group(1)
    return None


def sender_allowed(from_header):
    """True if the From header's address is an expected Arlo sender."""
    address = email.utils.parseaddr(from_header or "")[1]
    return address.lower() in ALLOWED_SENDERS


def kv_key(address):
    return "code:" + (address or "").strip().lower()


def msg_field(message, name):
    """Read a field off the incoming email message.

    Depending on the runtime SDK version the message arrives as a JS
    proxy with attributes or as a plain dict.
    """
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _envelope(code=200, message=None, data=None):
    meta = {"code": code}
    if message is not None:
        meta["message"] = message
    return {"meta": meta, "data": data or {}}


class Default(WorkerEntrypoint):

    def _json(self, payload, status=200):
        return Response(
            json.dumps(payload),
            status=status,
            headers={"Content-Type": "application/json"},
        )

    async def email(self, message):
        # Email Routing has already run SPF/DKIM/DMARC checks; this is
        # defense in depth. `message.from` is a Python keyword, so read
        # the header instead.
        if not sender_allowed(msg_field(message, "headers").get("from")):
            return

        raw = await JsResponse.new(msg_field(message, "raw")).arrayBuffer()
        code = extract_code(raw.to_bytes())
        if code is None:
            return

        record = json.dumps({"code": code, "timestamp": int(time.time())})
        await self.env.TFA_CODES.put(
            kv_key(msg_field(message, "to")),
            record,
            _to_js({"expirationTtl": CODE_TTL}, dict_converter=Object.fromEntries),
        )

    async def fetch(self, request):
        token = request.headers.get("Authorization") or ""
        if not hmac.compare_digest(token, f"Bearer {self.env.TFA_TOKEN}"):
            return self._json(_envelope(403, "forbidden"), status=403)

        url = urlparse(request.url)
        address = parse_qs(url.query).get("email", [None])[0]
        if not address:
            return self._json(_envelope(400, "email required"), status=400)

        if url.path == "/get" and request.method == "GET":
            value = await self.env.TFA_CODES.get(kv_key(address))
            data = json.loads(value) if value else {}
            return self._json(_envelope(data=data))

        # GET kept alongside POST to make curl debugging easy.
        if url.path == "/clear" and request.method in ("GET", "POST"):
            await self.env.TFA_CODES.delete(kv_key(address))
            return self._json(_envelope())

        return self._json(_envelope(404, "not found"), status=404)
