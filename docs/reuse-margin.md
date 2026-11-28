# Reuse threshold margin

Generated 2026-11-28 02:09 UTC by `manage.py reuse_margin M3` (read-only).

Every benchmark ticket's text (subject and body) was searched in M3's knowledge base, which
holds its 13 human-approved fixes. The table shows the best score of a fix for the ticket's real
issue and for any other issue; "-" means none came back in the top 20. A fix is offered for reuse at 0.8 or more, and a cheap check still decides.
Lookalikes (marked) are worded like another issue on purpose.

| Ticket | Real issue | Same-issue fix | Best other-issue fix | Other issue |
|---|---|---|---|---|
| T001 | payout_weekend_delay | 0.906 | - | - |
| T002 | webhook_duplicate_retries | - | - | - |
| T003 | api_key_wrong_environment | 0.995 | - | - |
| T004 | twofa_lost_device | 0.962 | - | - |
| T005 | refund_failed_card_closed | 0.997 | - | - |
| T006 | invoice_missing_tax | 0.880 | - | - |
| T007 | csv_export_encoding | 0.993 | - | - |
| T008 | subscription_proration | 0.950 | - | - |
| T009 | payment_link_expired | 0.994 | - | - |
| T010 | api_rate_limit_429 | 0.734 | - | - |
| T011 | payout_fx_fee | 0.985 | - | - |
| T012 | sso_domain_not_verified | 0.998 | - | - |
| T013 | payout_weekend_delay | 0.998 | - | - |
| T014 | webhook_duplicate_retries | 0.966 | - | - |
| T015 | api_key_wrong_environment | 0.994 | - | - |
| T016 | twofa_lost_device | 0.955 | - | - |
| T017 | refund_failed_card_closed | 0.944 | - | - |
| T018 | invoice_missing_tax | 0.901 | - | - |
| T019 | csv_export_encoding | 0.361 | - | - |
| T020 | csv_export_encoding | 0.004 | - | - |
| T021 | subscription_proration | - | - | - |
| T022 | payment_link_expired | 0.972 | - | - |
| T023 | api_rate_limit_429 | 0.993 | - | - |
| T024 | payout_fx_fee | 0.993 | - | - |
| T025 | payout_weekend_delay | 0.956 | - | - |
| T026 | webhook_duplicate_retries | - | - | - |
| T027 | api_key_wrong_environment | 0.997 | - | - |
| T028 | twofa_lost_device | 0.827 | - | - |
| T029 | refund_failed_card_closed | 0.954 | - | - |
| T030 | invoice_missing_tax | 0.273 | - | - |
| T031 | subscription_proration | 0.995 | - | - |
| T032 | payment_link_expired | 0.998 | - | - |
| T033 | payout_weekend_delay | - | - | - |
| T034 | api_rate_limit_429 | 0.662 | - | - |
| T035 | payout_fx_fee | 0.255 | - | - |
| T036 | sso_domain_not_verified | 0.997 | - | - |
| T037 (lookalike of payout_weekend_delay) | payout_fx_fee | - | 0.901 | payout_weekend_delay |
| T038 (lookalike of webhook_duplicate_retries) | api_rate_limit_429 | - | 0.632 | webhook_duplicate_retries |
| T039 (lookalike of invoice_missing_tax) | csv_export_encoding | 0.890 | - | - |
| T040 (lookalike of twofa_lost_device) | sso_domain_not_verified | - | 0.515 | twofa_lost_device |
| T041 (lookalike of refund_failed_card_closed) | subscription_proration | 0.255 | - | - |
| T042 (lookalike of api_key_wrong_environment) | payment_link_expired | 0.801 | - | - |

- Highest score of another issue's fix: 0.901 (threshold 0.8, margin -0.101).
- Lowest score of the same issue's fix: 0.004.
- Other-issue fixes at or above the threshold: 1 of 42 searches.
- Tickets searched: 42; one run's datapack. Raw ticket text carries more of the misleading words than
  the English problem signature the resolver really searches with, so this is the stricter test.

## What the resolver searched with during the run

During M3 each ticket was searched by its triage signature, and the best approved-fix scores were
recorded (same issue, other issue). The reuse check then decided.

- Tickets where another issue's fix came back at all: 0 of 42.
- Highest such score: -; at or above the threshold: 0.
- Wrong fixes actually reused: 0.

| Lookalike | Same-issue fix | Other-issue fix | Reused | Correct |
|---|---|---|---|---|
| T037 | 0.998 | - | yes | yes |
| T038 | 0.956 | - | yes | yes |
| T039 | 1.000 | - | yes | yes |
| T040 | 0.998 | - | yes | yes |
| T041 | 1.000 | - | yes | yes |
| T042 | 0.992 | - | yes | yes |
