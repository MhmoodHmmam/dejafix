# LumenPay product update, December 2026: card payouts settle T+1

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product. This update is
> uploaded only to the drift benchmark's datapacks (`manage.py drift_benchmark`), never to the app's
> own datapack or the main benchmark, so their documentation stays as it was.

## Payout schedule: what changed
This replaces "Payout schedule" in *LumenPay Payouts*. Since 1 December 2026, card payments settle
to the merchant's bank account on a **T+1 business day** schedule, not T+2.

- Weekends and bank holidays are still not business days. A payment captured on Friday is paid
  out on Monday; one captured on Thursday is paid out on Friday.
- The expected date of every payout is shown in **Payouts > Calendar**.
- A payout that has not arrived one business day after its date in the calendar should be
  investigated.
