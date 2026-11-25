# LumenPay Webhooks

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

## Delivery and retries
LumenPay sends each event as an HTTPS POST to the merchant's endpoint. The endpoint must
answer with any **2xx status within 10 seconds**. A slower answer, a timeout or a non-2xx
status counts as a failed delivery, and LumenPay retries with exponential backoff for up to
3 days (about 15 attempts).

Because a slow endpoint is retried, it can receive the **same event several times** even
though it eventually processed the first attempt. This is the most common cause of
"duplicate webhooks", "orders fulfilled twice" or "emails sent twice".

## Recommended handling
1. Return `200 OK` immediately after verifying the signature, then process the event
   asynchronously (a job queue or background worker).
2. Make processing idempotent: store the event `id` (format `evt_...`) and skip events
   whose id was already processed.
3. Check delivery attempts and response times under **Developers > Webhooks > [endpoint] >
   Attempts**.

## Endpoint status
After 3 days of consecutive failures an endpoint is disabled and the account owner is
emailed. Re-enable it from the same page after fixing the endpoint.
