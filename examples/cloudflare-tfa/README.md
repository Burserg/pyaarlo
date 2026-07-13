# Cloudflare Email Worker 2FA for pyaarlo

A self-hosted alternative to pyaarlo's IMAP 2FA source. A Cloudflare
[Email Routing](https://developers.cloudflare.com/email-routing/) rule
delivers the Arlo 2FA email straight to this Python Worker, which extracts
the 6 digit code and holds it in Workers KV for a few minutes. During login
pyaarlo (with `tfa_source='cloudflare'`) polls the worker over HTTPS and
injects the code.

```
Arlo ──2FA email──▶ Email Routing rule ──▶ worker (email handler)
                                              │ extract 6 digit code
                                              ▼
                                          Workers KV (TTL 600s)
                                              ▲
pyaarlo ──GET /get (Authorization: Bearer)────┘
```

No mail provider, IMAP credentials or third party service involved; the
worker runs on the free Cloudflare plan.

## Prerequisites

* A Cloudflare account with a domain on it.
* Email Routing enabled for that domain (dashboard: your zone → Email →
  Email Routing → Get started; free).
* Node.js/npx for [wrangler](https://developers.cloudflare.com/workers/wrangler/).

## Deploy

From this directory:

```bash
# 1. Create the KV namespace and paste the returned id into wrangler.toml
npx wrangler kv namespace create TFA_CODES

# 2. Set the bearer token pyaarlo will authenticate with
openssl rand -hex 32          # generate one
npx wrangler secret put TFA_TOKEN

# 3. Deploy
npx wrangler deploy
```

Note the worker URL wrangler prints, e.g.
`https://pyaarlo-tfa.your-account.workers.dev`.

## Route the Arlo email to the worker

1. Dashboard → your zone → Email → Email Routing → Routing rules.
2. Create a custom address, e.g. `arlo-tfa@yourdomain.com`.
3. Action: **Send to a Worker** → `pyaarlo-tfa`.
4. Point your Arlo account's email 2FA factor at that address (my.arlo.com →
   Settings → Grant Access / Two-Step Verification), or use it as the
   account email.

## Configure pyaarlo

```python
arlo = pyaarlo.PyArlo(username=USERNAME, password=PASSWORD,
                      tfa_source='cloudflare', tfa_type='email',
                      tfa_host='pyaarlo-tfa.your-account.workers.dev',
                      tfa_username='arlo-tfa@yourdomain.com',
                      tfa_password='your-TFA_TOKEN-value',
                      tfa_total_timeout=120)
```

* `tfa_host` — the worker URL.
* `tfa_username` — the routed address (defaults to your Arlo username,
  which is correct when the account email itself is the routed address).
* `tfa_password` — the `TFA_TOKEN` secret, **not** your Arlo password.
  `tfa_host` and `tfa_password` must be set explicitly or the source
  refuses to start.
* `tfa_total_timeout=120` — recommended. KV writes can take up to ~60s to
  become visible in other locations (and "no code yet" lookups are edge
  cached for 60s), so the default 60s budget can plausibly miss the code.

## Test locally

```bash
echo 'TFA_TOKEN = "devtoken"' > .dev.vars
npx wrangler dev
```

Then, in another terminal:

```bash
# Simulate the routed Arlo email
curl -X POST 'http://localhost:8787/cdn-cgi/handler/email?from=do_not_reply@arlo.com&to=arlo-tfa@yourdomain.com' \
  -H 'Content-Type: message/rfc822' \
  --data-binary @sample-arlo.eml

# Fetch the captured code
curl -H 'Authorization: Bearer devtoken' \
  'http://localhost:8787/get?email=arlo-tfa@yourdomain.com'
# -> {"meta": {"code": 200}, "data": {"code": "123456", "timestamp": ...}}

# Clear it, then confirm it is gone
curl -X POST -H 'Authorization: Bearer devtoken' \
  'http://localhost:8787/clear?email=arlo-tfa@yourdomain.com'
curl -H 'Authorization: Bearer devtoken' \
  'http://localhost:8787/get?email=arlo-tfa@yourdomain.com'
# -> {"meta": {"code": 200}, "data": {}}

# Wrong token -> 403
curl -H 'Authorization: Bearer nope' \
  'http://localhost:8787/get?email=arlo-tfa@yourdomain.com'
```

After a real deploy, `npx wrangler tail` shows the email handler firing when
Arlo sends a code.

## Notes

* **Multiple Arlo accounts** work on one worker: create one routed address
  per account and pass it as that account's `tfa_username`. All accounts
  share the single `TFA_TOKEN`.
* **Security**: the email path carries no token, so the worst a spoofed
  email could do is overwrite a pending code (a nuisance, not a takeover)
  — and Email Routing's SPF/DKIM/DMARC checks plus the worker's sender
  check make even that unlikely. Codes expire from KV after 10 minutes.
* **Python Workers** are in open beta; this worker only uses the CPython
  stdlib (no extra packages), which keeps it on the stable path.
