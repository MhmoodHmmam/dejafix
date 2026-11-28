# LumenPay product update, December 2026: reissue invoices with tax in bulk

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product. This update is
> published only to the app's own datapack (`seed_knowledge --dir data/kb-updates`), never to the
> benchmark datapacks, so the benchmark's documentation stays as it was.

## Invoices sent without tax: what changed
This replaces step 3 of "Tax on invoices" in *LumenPay Invoices, Tax and Subscriptions*.
Credit-noting each invoice by hand is no longer needed.

Fix:
1. Set the region and tax ID under **Settings > Tax > Tax region**. New invoices include tax
   from then on.
2. For invoices already sent without tax, open **Invoices**, filter by **Tax: missing**, select
   them, and choose **Bulk actions > Reissue with tax**.
3. LumenPay issues a credit note for each selected invoice and a corrected invoice with the tax
   line, and emails the corrected invoice to the customer. Each pair is linked in the invoice
   history, so the audit trail stays complete.

Reissue with tax works on up to 500 invoices at a time and only for invoices issued in the
last 12 months. Older invoices still need a credit note and a corrected invoice by hand
(**Invoices > [invoice] > Credit note**).
