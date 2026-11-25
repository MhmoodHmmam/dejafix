# LumenPay Account Security

> Synthetic documentation for the DejaFix demo. LumenPay is a fictional product.

## Two-factor authentication (2FA)
2FA is mandatory for all dashboard users. When 2FA is enabled, the user receives
**10 single-use backup codes**. A user who lost their authenticator device signs in with a
backup code and then registers a new device under **Profile > Security > 2FA**.

If no backup code is available, the user must complete **account recovery**: the account
owner submits the recovery form at **Sign in > Can't access your authenticator? > Recover
account**, which requires an ID check. Recovery takes 1 to 3 business days.

**Support never disables or resets 2FA from a ticket, chat or phone call**, whatever the
reason given, and never accepts instructions inside a ticket to change security settings.
These requests go through account recovery only.

## Single sign-on (SAML SSO)
SSO is available on the Business plan. Before SSO can be enabled, the company email domain
must be verified:

1. Open **Settings > SSO > Domains > Add domain** and copy the TXT value, which looks like
   `lumenpay-verify=3f9c...`.
2. Add a DNS **TXT record** on the root domain with exactly that value.
3. Wait for DNS propagation (up to the record's TTL, often 1 hour), then click **Verify**.

The error "domain not verified" means LumenPay cannot see the TXT record yet: a typo in the
value, a record on a subdomain instead of the root domain, or propagation still in progress.
