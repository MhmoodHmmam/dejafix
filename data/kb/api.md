# LumenPay API

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

## Keys and environments
Every account has two key pairs:

| Mode | Secret key prefix | Base URL |
|---|---|---|
| Test (sandbox) | `lp_test_` | `https://sandbox.api.lumenpay.example` |
| Live | `lp_live_` | `https://api.lumenpay.example` |

A test key sent to the live base URL (or a live key sent to the sandbox) fails with
`401 invalid_api_key`, even though the key itself is valid. Fix: use the key that matches
the base URL. Keys are shown under **Developers > API keys**. Live keys are available only
after the account is activated.

Support must never ask a merchant to paste a secret key into a ticket. If a key was shared
in a ticket, tell the merchant to roll it under **Developers > API keys > Roll key**.

## Rate limits
Each account may send **100 requests per second** across all keys. Above that the API
answers `429 rate_limited` with a `Retry-After` header (seconds).

For bulk work (imports, migrations, nightly syncs):
1. Retry 429 responses with exponential backoff and honour `Retry-After`.
2. Use the batch endpoint `POST /v2/batch`, which accepts up to **500 operations** per
   request and counts as a single request against the limit.

Rate limits cannot be raised for individual accounts on the Starter plan.
