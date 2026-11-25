# LumenPay Payment Links

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

Payment links let merchants accept payments without a website. Links are created under
**Payments > Payment links > New link**.

## Expiry
By default a payment link **expires 30 days** after it is created. Customers opening an
expired link see "This payment link has expired" and cannot pay.

Fix options:
- Create a new link (expired links cannot be reactivated), or
- When creating a link, set **Expiry > Never** under advanced options, so it stays valid
  until it is deactivated manually.

## Single-use links
A link marked **single use** stops accepting payments after the first successful payment
and then shows "This link is no longer active". This is not an error.
