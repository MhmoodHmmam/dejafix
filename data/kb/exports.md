# LumenPay Data Exports

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

Reports and payment lists can be exported as CSV from any list page (**Export > CSV**).

## Garbled characters in Excel
Exports are UTF-8. Microsoft Excel on Windows opens UTF-8 CSV files without a byte order
mark using the legacy code page, so non-ASCII text (Arabic, accented letters, currency
symbols) appears garbled, for example `Ø§Ù„Ø¹Ù…ÙŠÙ„` instead of Arabic text.

Fix, either of:
1. Choose **Export > CSV (Excel, UTF-8 with BOM)**. Excel then detects the encoding.
2. In Excel, use **Data > From Text/CSV** and select **65001: Unicode (UTF-8)** as the file
   origin.

The data in LumenPay is not corrupted; only the way Excel opens the file is wrong.
