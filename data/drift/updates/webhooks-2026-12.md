# LumenPay product update, December 2026: faster webhook timeout and an Idempotency-Key header

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product. This update is
> uploaded only to the drift benchmark's datapacks (`manage.py drift_benchmark`), never to the app's
> own datapack or the main benchmark, so their documentation stays as it was.

## Delivery and retries: what changed
This replaces "Delivery and retries" in *LumenPay Webhooks*. Since 1 December 2026:

1. An endpoint must answer with a 2xx status **within 5 seconds** (it was 10 seconds). A slower
   answer counts as a failed delivery, and the event is sent again.
2. Every delivery carries an **`Idempotency-Key`** header, the same on each retry of an event.
   Store it and skip any delivery whose `Idempotency-Key` you have already processed. The event
   `id` in the body is no longer guaranteed to stay the same across retries.
3. Answer first, then do slow work (inventory sync, label printing, emails) in a background job.
