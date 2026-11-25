# LumenPay Refunds

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

## Issuing a refund
Refunds are issued from **Payments > [payment] > Refund**, fully or partially, up to 180
days after the charge. The refund is sent to the original payment method and normally
appears on the customer's statement in 5 to 10 business days.

## Failed refunds
A refund fails when the original card can no longer receive funds, most often because the
card was **closed, expired or replaced**. The refund then shows the status
`failed: card_closed` under **Refunds > Failed**.

To complete it, the merchant opens the failed refund and chooses **Send by bank transfer**,
then enters the customer's bank details (the customer provides them directly to the
merchant). LumenPay sends the funds within 3 business days. The original refund amount is
not charged twice.

## Limits
Refunds above 500.00 require the account owner's approval in the dashboard. Support cannot
issue or approve refunds on a merchant's behalf.
