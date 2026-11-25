# DejaFix benchmark

Generated 2026-11-25 18:59 UTC. Model `claude-opus-5-5`; one run per arm (no repetitions).

Both arms run the same agents, ticket order, prompts and documentation, each in its own fresh Meko
datapack. The only difference is shared memory: approved fixes and human corrections.

- **Correct:** the fix mentions every ground-truth keyword of its issue and was not escalated.
- **Human touches:** tickets a person must handle (new fixes to review, or escalations).
- **Simulated reviewer (Meko arm):** approves complete fixes and, when it rejects one, names the missing
  keywords. Its notes come from the ground truth, so it helps more than a hurried human might.

| Metric | D1 (docs_only) | M1 (meko) |
|---|---|---|
| Tickets done | 36/36 | 36/36 |
| Correct, all | 25/36 | 30/36 |
| Correct, first occurrences | 8/12 | 8/12 |
| Correct, repeats | 17/24 | 22/24 |
| Avg tokens, first occurrences | 5,351 | 5,342 |
| Avg tokens, repeats | 5,348 | 3,537 |
| Avg model time per ticket | 14.4 s | 11.8 s |
| Avg Meko calls per ticket | 13.0 | 12.9 |
| Escalated to a human | 3 | 3 |
| Reused an approved fix | 0 | 17 |
| False reuse (another issue's fix) | 0 | 0 |
| Human touches | 36/36 | 19/36 |
| Injection tickets escalated, nothing stored | 2/2 | 2/2 |
| Total tokens / est. cost | 192,558 / $1.43 | 148,998 / $1.11 |

## Reuse threshold calibration (M1)

For each threshold: how many tickets would have been offered an approved fix for the reuse check,
and how many of those offers were the same issue. The LLM reuse check still decides after the offer.

| Threshold | Offered | Same issue | Precision | Recall |
|---|---|---|---|---|
| 0.5 | 20 | 20 | 1.00 | 1.00 |
| 0.7 | 20 | 20 | 1.00 | 1.00 |
| 0.8 | 20 | 20 | 1.00 | 1.00 |
| 0.9 | 20 | 20 | 1.00 | 1.00 |
| 0.95 | 19 | 19 | 1.00 | 0.95 |
| 0.98 | 19 | 19 | 1.00 | 0.95 |
| 0.99 | 18 | 18 | 1.00 | 0.90 |
| 0.995 | 17 | 17 | 1.00 | 0.85 |
| 0.999 | 13 | 13 | 1.00 | 0.65 |

Thresholds 0.5-0.9 tie for the best result (no other issue's fix offered, recall 1.00). The current setting, 0.8, is inside this range, so it is kept. No other issue's fix was offered at any threshold, so this data does not test how low the threshold can safely go.

## Per ticket

| Run | Ticket | Issue | Wave | Source | Correct | Missing | Tokens | Decision |
|---|---|---|---|---|---|---|---|---|
| D1 | T001 | payout_weekend_delay | 1 | new | yes | - | 5,230 | - |
| D1 | T002 | webhook_duplicate_retries | 1 | new | no | idempotent, event id | 5,755 | - |
| D1 | T003 | api_key_wrong_environment | 1 | new | no | lp_live_ | 5,580 | - |
| D1 | T004 | twofa_lost_device | 1 | new | no | - | 5,374 | escalated |
| D1 | T005 | refund_failed_card_closed | 1 | new | yes | - | 5,006 | - |
| D1 | T006 | invoice_missing_tax | 1 | new | yes | - | 5,221 | - |
| D1 | T007 | csv_export_encoding | 1 | new | yes | - | 5,411 | - |
| D1 | T008 | subscription_proration | 1 | new | yes | - | 5,096 | - |
| D1 | T009 | payment_link_expired | 1 | new | yes | - | 5,041 | - |
| D1 | T010 | api_rate_limit_429 | 1 | new | no | Retry-After, /v2/batch | 5,684 | - |
| D1 | T011 | payout_fx_fee | 1 | new | yes | - | 5,253 | - |
| D1 | T012 | sso_domain_not_verified | 1 | new | yes | - | 5,562 | - |
| D1 | T013 | payout_weekend_delay | 2 | new | yes | - | 5,772 | - |
| D1 | T014 | webhook_duplicate_retries | 2 | new | no | idempotent | 5,792 | - |
| D1 | T015 | api_key_wrong_environment | 2 | new | no | lp_test_ | 5,042 | - |
| D1 | T016 | twofa_lost_device | 2 | new | yes | - | 5,333 | - |
| D1 | T017 | refund_failed_card_closed | 2 | new | no | - | 5,205 | escalated |
| D1 | T018 | invoice_missing_tax | 2 | new | yes | - | 5,056 | - |
| D1 | T019 | csv_export_encoding | 2 | new | yes | - | 5,331 | - |
| D1 | T020 | csv_export_encoding | 2 | new | yes | - | 5,524 | - |
| D1 | T021 | subscription_proration | 2 | new | yes | - | 5,252 | - |
| D1 | T022 | payment_link_expired | 2 | new | yes | - | 5,187 | - |
| D1 | T023 | api_rate_limit_429 | 2 | new | no | Retry-After, /v2/batch | 5,450 | - |
| D1 | T024 | payout_fx_fee | 2 | new | yes | - | 5,243 | - |
| D1 | T025 | payout_weekend_delay | 2 | new | yes | - | 5,459 | - |
| D1 | T026 | webhook_duplicate_retries | 2 | new | yes | - | 5,776 | - |
| D1 | T027 | api_key_wrong_environment | 2 | new | no | lp_live_ | 5,301 | - |
| D1 | T028 | twofa_lost_device | 2 | new | no | - | 5,535 | escalated |
| D1 | T029 | refund_failed_card_closed | 2 | new | yes | - | 5,208 | - |
| D1 | T030 | invoice_missing_tax | 2 | new | yes | - | 5,287 | - |
| D1 | T031 | subscription_proration | 2 | new | yes | - | 5,048 | - |
| D1 | T032 | payment_link_expired | 2 | new | yes | - | 4,890 | - |
| D1 | T033 | payout_weekend_delay | 2 | new | yes | - | 5,593 | - |
| D1 | T034 | api_rate_limit_429 | 2 | new | no | /v2/batch | 5,398 | - |
| D1 | T035 | payout_fx_fee | 2 | new | yes | - | 5,182 | - |
| D1 | T036 | sso_domain_not_verified | 2 | new | yes | - | 5,481 | - |
| M1 | T001 | payout_weekend_delay | 1 | new | yes | - | 5,285 | approved |
| M1 | T002 | webhook_duplicate_retries | 1 | new | no | idempotent, event id | 5,839 | rejected |
| M1 | T003 | api_key_wrong_environment | 1 | new | no | lp_live_ | 5,373 | rejected |
| M1 | T004 | twofa_lost_device | 1 | new | no | - | 5,367 | escalated |
| M1 | T005 | refund_failed_card_closed | 1 | new | yes | - | 5,129 | approved |
| M1 | T006 | invoice_missing_tax | 1 | new | yes | - | 5,192 | approved |
| M1 | T007 | csv_export_encoding | 1 | new | yes | - | 5,384 | approved |
| M1 | T008 | subscription_proration | 1 | new | yes | - | 4,993 | approved |
| M1 | T009 | payment_link_expired | 1 | new | yes | - | 4,833 | approved |
| M1 | T010 | api_rate_limit_429 | 1 | new | no | Retry-After, /v2/batch | 5,960 | rejected |
| M1 | T011 | payout_fx_fee | 1 | new | yes | - | 5,289 | approved |
| M1 | T012 | sso_domain_not_verified | 1 | new | yes | - | 5,460 | approved |
| M1 | T013 | payout_weekend_delay | 2 | reused | yes | - | 2,654 | - |
| M1 | T014 | webhook_duplicate_retries | 2 | new | yes | - | 5,725 | approved |
| M1 | T015 | api_key_wrong_environment | 2 | new | yes | - | 5,180 | approved |
| M1 | T016 | twofa_lost_device | 2 | new | yes | - | 5,306 | approved |
| M1 | T017 | refund_failed_card_closed | 2 | new | no | card_closed | 5,232 | escalated |
| M1 | T018 | invoice_missing_tax | 2 | reused | yes | - | 2,561 | - |
| M1 | T019 | csv_export_encoding | 2 | reused | yes | - | 2,718 | - |
| M1 | T020 | csv_export_encoding | 2 | reused | yes | - | 2,936 | - |
| M1 | T021 | subscription_proration | 2 | reused | yes | - | 2,655 | - |
| M1 | T022 | payment_link_expired | 2 | reused | yes | - | 2,423 | - |
| M1 | T023 | api_rate_limit_429 | 2 | new | yes | - | 5,464 | approved |
| M1 | T024 | payout_fx_fee | 2 | reused | yes | - | 2,595 | - |
| M1 | T025 | payout_weekend_delay | 2 | reused | yes | - | 2,720 | - |
| M1 | T026 | webhook_duplicate_retries | 2 | reused | yes | - | 2,804 | - |
| M1 | T027 | api_key_wrong_environment | 2 | reused | yes | - | 2,789 | - |
| M1 | T028 | twofa_lost_device | 2 | new | no | - | 5,506 | escalated |
| M1 | T029 | refund_failed_card_closed | 2 | reused | yes | - | 2,457 | - |
| M1 | T030 | invoice_missing_tax | 2 | reused | yes | - | 2,772 | - |
| M1 | T031 | subscription_proration | 2 | reused | yes | - | 2,545 | - |
| M1 | T032 | payment_link_expired | 2 | reused | yes | - | 2,352 | - |
| M1 | T033 | payout_weekend_delay | 2 | reused | yes | - | 2,725 | - |
| M1 | T034 | api_rate_limit_429 | 2 | reused | yes | - | 2,648 | - |
| M1 | T035 | payout_fx_fee | 2 | reused | yes | - | 2,611 | - |
| M1 | T036 | sso_domain_not_verified | 2 | new | yes | - | 7,516 | approved |
