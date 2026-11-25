# LumenPay Payouts

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

## Payout schedule
Card payments settle to the merchant's bank account on a **T+2 business days** schedule.
Business days exclude Saturdays, Sundays and bank holidays in the country of the payout
bank account. A payment captured on Friday is therefore paid out on Tuesday, and a
payment captured the day before a bank holiday is delayed by one more day.

The **Payouts > Calendar** page shows the expected arrival date of every pending payout,
including holiday shifts. A payout in the "Pending" state before its expected date is not
an incident and needs no action from support.

A payout is late only when it is still "Pending" **one business day after** its expected
date. Then support opens a payout investigation from **Payouts > Pending > Investigate**.

## Currency conversion
When the charge currency differs from the payout bank account's currency, LumenPay converts
the funds at the mid-market rate plus a **1.5% conversion fee**. The fee appears as
"FX fee" on the payout breakdown (**Payouts > [payout] > Breakdown**).

To avoid the fee, the merchant can add a second bank account in the charge currency under
**Settings > Payouts > Bank accounts > Add account**. With multi-currency settlement enabled,
each currency is paid to the matching account without conversion.

## Minimum payout
Payouts under 10.00 (in the account currency) roll over to the next payout day.
