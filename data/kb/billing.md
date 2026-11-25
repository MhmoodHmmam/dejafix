# LumenPay Invoices, Tax and Subscriptions

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

## Tax on invoices
Invoices include VAT or sales tax only when the merchant's **tax region** is set. If the
region is empty, invoices are issued without tax lines.

Fix:
1. Set the region and tax ID under **Settings > Tax > Tax region**.
2. New invoices include tax from then on.
3. Invoices already issued cannot be edited: issue a **credit note** for each one
   (**Invoices > [invoice] > Credit note**), then issue a corrected invoice.

## Subscription plan changes and proration
When a subscriber changes plan in the middle of a billing period, LumenPay **prorates** by
default: the unused time on the old plan is credited and the remaining time on the new plan
is charged immediately. This is why a customer may see an unexpected extra charge right
after upgrading.

To avoid immediate charges, set **Plan changes > Apply at period end** on the product. The
new plan then starts at the next renewal with no proration. Existing prorations can be
reversed with a credit note.
