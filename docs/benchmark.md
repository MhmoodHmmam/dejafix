# DejaFix benchmark

Generated 2026-11-28 01:00 UTC. Model `claude-opus-5-5`; 2 runs of docs_only, 3 runs of meko.

Both arms run the same agents, ticket order, prompts and documentation, each in its own fresh Meko
datapack. The only difference is shared memory: approved fixes and human corrections.

- **Correct:** the fix mentions every ground-truth keyword of its issue and was not escalated.
- **Human touches:** tickets a person must handle (new fixes to review, or escalations).
- **Simulated reviewer (Meko arm):** approves complete fixes and, when it rejects one, names the missing
  keywords. Its notes come from the ground truth, so it helps more than a hurried human might.
- **Lookalikes (wave 3):** tickets that read like one issue but are caused by another, run after the 36
  core tickets. They test whether an approved fix is reused for the wrong problem. They are counted only
  in the lookalike rows, tickets done and total tokens; every other row covers the core tickets.

| Metric | D1 (docs_only) | D2 (docs_only) | M1 (meko) | M2 (meko) | M3 (meko) |
|---|---|---|---|---|---|
| Tickets done | 36/36 | 42/42 | 36/36 | 42/42 | 42/42 |
| Correct, first occurrences and repeats | 25/36 | 26/36 | 30/36 | 28/36 | 30/36 |
| Correct, first occurrences | 8/12 | 8/12 | 8/12 | 9/12 | 9/12 |
| Correct, repeats | 17/24 | 18/24 | 22/24 | 19/24 | 21/24 |
| Correct on the next ticket after a correction | - | - | 3/3 | 1/4 | 2/3 |
| Correct, lookalikes | - | 4/6 | - | 6/6 | 6/6 |
| Lookalikes that reused the right fix | - | 0 | - | 5 | 6 |
| Lookalikes that reused another issue's fix | - | 0 | - | 0 | 0 |
| Avg tokens, first occurrences | 5,351 | 5,295 | 5,342 | 5,369 | 5,622 |
| Avg tokens, repeats | 5,348 | 5,329 | 3,537 | 3,670 | 3,681 |
| Avg model time per ticket | 14.4 s | 14.4 s | 11.8 s | 12.6 s | 12.2 s |
| Avg Meko calls per ticket | 13.0 | 13.0 | 12.9 | 13.0 | 13.9 |
| Escalated to a human | 3 | 3 | 3 | 3 | 3 |
| Reused an approved fix | 0 | 0 | 17 | 16 | 17 |
| False reuse (another issue's fix) | 0 | 0 | 0 | 0 | 0 |
| Human touches | 36/36 | 36/36 | 19/36 | 20/36 | 19/36 |
| Injection tickets escalated, nothing stored | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| Total tokens / est. cost | 192,558 / $1.43 | 224,749 / $1.67 | 148,998 / $1.11 | 174,862 / $1.33 | 172,618 / $1.29 |

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

## Reuse threshold calibration (M2)

For each threshold: how many tickets would have been offered an approved fix for the reuse check,
and how many of those offers were the same issue. The LLM reuse check still decides after the offer.

| Threshold | Offered | Same issue | Precision | Recall |
|---|---|---|---|---|
| 0.5 | 25 | 25 | 1.00 | 1.00 |
| 0.7 | 25 | 25 | 1.00 | 1.00 |
| 0.8 | 25 | 25 | 1.00 | 1.00 |
| 0.9 | 25 | 25 | 1.00 | 1.00 |
| 0.95 | 23 | 23 | 1.00 | 0.92 |
| 0.98 | 22 | 22 | 1.00 | 0.88 |
| 0.99 | 22 | 22 | 1.00 | 0.88 |
| 0.995 | 21 | 21 | 1.00 | 0.84 |
| 0.999 | 16 | 16 | 1.00 | 0.64 |

Thresholds 0.5-0.9 tie for the best result (no other issue's fix offered, recall 1.00). The current setting, 0.8, is inside this range, so it is kept. No other issue's fix was offered at any threshold, so this data does not test how low the threshold can safely go.

## Reuse threshold calibration (M3)

For each threshold: how many tickets would have been offered an approved fix for the reuse check,
and how many of those offers were the same issue. The LLM reuse check still decides after the offer.

| Threshold | Offered | Same issue | Precision | Recall |
|---|---|---|---|---|
| 0.5 | 26 | 26 | 1.00 | 1.00 |
| 0.7 | 26 | 26 | 1.00 | 1.00 |
| 0.8 | 26 | 26 | 1.00 | 1.00 |
| 0.9 | 26 | 26 | 1.00 | 1.00 |
| 0.95 | 25 | 25 | 1.00 | 0.96 |
| 0.98 | 23 | 23 | 1.00 | 0.88 |
| 0.99 | 21 | 21 | 1.00 | 0.81 |
| 0.995 | 19 | 19 | 1.00 | 0.73 |
| 0.999 | 14 | 14 | 1.00 | 0.54 |

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
| D2 | T001 | payout_weekend_delay | 1 | new | yes | - | 5,215 | - |
| D2 | T002 | webhook_duplicate_retries | 1 | new | no | idempotent, event id | 5,689 | - |
| D2 | T003 | api_key_wrong_environment | 1 | new | no | lp_live_ | 5,496 | - |
| D2 | T004 | twofa_lost_device | 1 | new | no | - | 5,476 | escalated |
| D2 | T005 | refund_failed_card_closed | 1 | new | yes | - | 5,013 | - |
| D2 | T006 | invoice_missing_tax | 1 | new | yes | - | 5,076 | - |
| D2 | T007 | csv_export_encoding | 1 | new | yes | - | 5,300 | - |
| D2 | T008 | subscription_proration | 1 | new | yes | - | 4,990 | - |
| D2 | T009 | payment_link_expired | 1 | new | yes | - | 4,897 | - |
| D2 | T010 | api_rate_limit_429 | 1 | new | no | Retry-After, /v2/batch | 5,770 | - |
| D2 | T011 | payout_fx_fee | 1 | new | yes | - | 5,306 | - |
| D2 | T012 | sso_domain_not_verified | 1 | new | yes | - | 5,313 | - |
| D2 | T013 | payout_weekend_delay | 2 | new | yes | - | 5,842 | - |
| D2 | T014 | webhook_duplicate_retries | 2 | new | yes | - | 5,633 | - |
| D2 | T015 | api_key_wrong_environment | 2 | new | no | lp_test_ | 4,945 | - |
| D2 | T016 | twofa_lost_device | 2 | new | yes | - | 5,284 | - |
| D2 | T017 | refund_failed_card_closed | 2 | new | no | card_closed | 5,245 | escalated |
| D2 | T018 | invoice_missing_tax | 2 | new | yes | - | 5,105 | - |
| D2 | T019 | csv_export_encoding | 2 | new | yes | - | 5,451 | - |
| D2 | T020 | csv_export_encoding | 2 | new | yes | - | 5,438 | - |
| D2 | T021 | subscription_proration | 2 | new | yes | - | 5,260 | - |
| D2 | T022 | payment_link_expired | 2 | new | yes | - | 4,862 | - |
| D2 | T023 | api_rate_limit_429 | 2 | new | no | Retry-After, /v2/batch | 5,459 | - |
| D2 | T024 | payout_fx_fee | 2 | new | yes | - | 5,289 | - |
| D2 | T025 | payout_weekend_delay | 2 | new | yes | - | 5,495 | - |
| D2 | T026 | webhook_duplicate_retries | 2 | new | yes | - | 5,951 | - |
| D2 | T027 | api_key_wrong_environment | 2 | new | no | lp_live_ | 5,318 | - |
| D2 | T028 | twofa_lost_device | 2 | new | no | - | 5,472 | escalated |
| D2 | T029 | refund_failed_card_closed | 2 | new | yes | - | 5,170 | - |
| D2 | T030 | invoice_missing_tax | 2 | new | yes | - | 5,293 | - |
| D2 | T031 | subscription_proration | 2 | new | yes | - | 5,120 | - |
| D2 | T032 | payment_link_expired | 2 | new | yes | - | 4,891 | - |
| D2 | T033 | payout_weekend_delay | 2 | new | yes | - | 5,471 | - |
| D2 | T034 | api_rate_limit_429 | 2 | new | no | /v2/batch | 5,388 | - |
| D2 | T035 | payout_fx_fee | 2 | new | yes | - | 5,115 | - |
| D2 | T036 | sso_domain_not_verified | 2 | new | yes | - | 5,390 | - |
| D2 | T037 | payout_fx_fee (looks like payout_weekend_delay) | 3 | new | yes | - | 5,360 | - |
| D2 | T038 | api_rate_limit_429 (looks like webhook_duplicate_retries) | 3 | new | no | Retry-After, /v2/batch | 5,978 | - |
| D2 | T039 | csv_export_encoding (looks like invoice_missing_tax) | 3 | new | yes | - | 5,625 | - |
| D2 | T040 | sso_domain_not_verified (looks like twofa_lost_device) | 3 | new | yes | - | 5,553 | - |
| D2 | T041 | subscription_proration (looks like refund_failed_card_closed) | 3 | new | no | - | 5,475 | escalated |
| D2 | T042 | payment_link_expired (looks like api_key_wrong_environment) | 3 | new | yes | - | 5,330 | - |
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
| M2 | T001 | payout_weekend_delay | 1 | new | yes | - | 5,375 | approved |
| M2 | T002 | webhook_duplicate_retries | 1 | new | yes | - | 6,064 | approved |
| M2 | T003 | api_key_wrong_environment | 1 | new | no | lp_live_ | 5,584 | rejected |
| M2 | T004 | twofa_lost_device | 1 | new | no | - | 5,398 | escalated |
| M2 | T005 | refund_failed_card_closed | 1 | new | yes | - | 4,998 | approved |
| M2 | T006 | invoice_missing_tax | 1 | new | yes | - | 5,094 | approved |
| M2 | T007 | csv_export_encoding | 1 | new | yes | - | 5,351 | approved |
| M2 | T008 | subscription_proration | 1 | new | yes | - | 5,250 | approved |
| M2 | T009 | payment_link_expired | 1 | new | yes | - | 4,937 | approved |
| M2 | T010 | api_rate_limit_429 | 1 | new | no | Retry-After, /v2/batch | 5,593 | rejected |
| M2 | T011 | payout_fx_fee | 1 | new | yes | - | 5,174 | approved |
| M2 | T012 | sso_domain_not_verified | 1 | new | yes | - | 5,609 | approved |
| M2 | T013 | payout_weekend_delay | 2 | reused | yes | - | 2,963 | - |
| M2 | T014 | webhook_duplicate_retries | 2 | reused | yes | - | 2,950 | - |
| M2 | T015 | api_key_wrong_environment | 2 | new | no | lp_test_ | 5,211 | rejected |
| M2 | T016 | twofa_lost_device | 2 | new | yes | - | 5,334 | approved |
| M2 | T017 | refund_failed_card_closed | 2 | new | no | card_closed | 5,165 | escalated |
| M2 | T018 | invoice_missing_tax | 2 | reused | yes | - | 2,548 | - |
| M2 | T019 | csv_export_encoding | 2 | reused | yes | - | 2,751 | - |
| M2 | T020 | csv_export_encoding | 2 | reused | yes | - | 2,948 | - |
| M2 | T021 | subscription_proration | 2 | reused | yes | - | 2,578 | - |
| M2 | T022 | payment_link_expired | 2 | reused | yes | - | 2,406 | - |
| M2 | T023 | api_rate_limit_429 | 2 | new | no | Retry-After, /v2/batch | 5,499 | rejected |
| M2 | T024 | payout_fx_fee | 2 | reused | yes | - | 2,531 | - |
| M2 | T025 | payout_weekend_delay | 2 | reused | yes | - | 2,569 | - |
| M2 | T026 | webhook_duplicate_retries | 2 | reused | yes | - | 2,801 | - |
| M2 | T027 | api_key_wrong_environment | 2 | new | no | lp_live_ | 5,125 | rejected |
| M2 | T028 | twofa_lost_device | 2 | new | no | - | 5,743 | escalated |
| M2 | T029 | refund_failed_card_closed | 2 | reused | yes | - | 2,484 | - |
| M2 | T030 | invoice_missing_tax | 2 | reused | yes | - | 2,696 | - |
| M2 | T031 | subscription_proration | 2 | reused | yes | - | 2,508 | - |
| M2 | T032 | payment_link_expired | 2 | reused | yes | - | 2,429 | - |
| M2 | T033 | payout_weekend_delay | 2 | reused | yes | - | 2,820 | - |
| M2 | T034 | api_rate_limit_429 | 2 | new | yes | - | 5,865 | approved |
| M2 | T035 | payout_fx_fee | 2 | reused | yes | - | 2,587 | - |
| M2 | T036 | sso_domain_not_verified | 2 | new | yes | - | 7,563 | approved |
| M2 | T037 | payout_fx_fee (looks like payout_weekend_delay) | 3 | reused | yes | - | 2,825 | - |
| M2 | T038 | api_rate_limit_429 (looks like webhook_duplicate_retries) | 3 | reused | yes | - | 3,153 | - |
| M2 | T039 | csv_export_encoding (looks like invoice_missing_tax) | 3 | reused | yes | - | 2,927 | - |
| M2 | T040 | sso_domain_not_verified (looks like twofa_lost_device) | 3 | new | yes | - | 8,192 | approved |
| M2 | T041 | subscription_proration (looks like refund_failed_card_closed) | 3 | reused | yes | - | 2,679 | - |
| M2 | T042 | payment_link_expired (looks like api_key_wrong_environment) | 3 | reused | yes | - | 2,585 | - |
| M3 | T001 | payout_weekend_delay | 1 | new | yes | - | 5,456 | approved |
| M3 | T002 | webhook_duplicate_retries | 1 | new | yes | - | 6,289 | approved |
| M3 | T003 | api_key_wrong_environment | 1 | new | no | lp_live_ | 5,646 | rejected |
| M3 | T004 | twofa_lost_device | 1 | new | no | - | 6,280 | escalated |
| M3 | T005 | refund_failed_card_closed | 1 | new | yes | - | 5,168 | approved |
| M3 | T006 | invoice_missing_tax | 1 | new | yes | - | 5,356 | approved |
| M3 | T007 | csv_export_encoding | 1 | new | yes | - | 5,572 | approved |
| M3 | T008 | subscription_proration | 1 | new | yes | - | 5,216 | approved |
| M3 | T009 | payment_link_expired | 1 | new | yes | - | 5,165 | approved |
| M3 | T010 | api_rate_limit_429 | 1 | new | no | Retry-After, /v2/batch | 5,933 | rejected |
| M3 | T011 | payout_fx_fee | 1 | new | yes | - | 5,493 | approved |
| M3 | T012 | sso_domain_not_verified | 1 | new | yes | - | 5,891 | approved |
| M3 | T013 | payout_weekend_delay | 2 | reused | yes | - | 2,715 | - |
| M3 | T014 | webhook_duplicate_retries | 2 | reused | yes | - | 2,808 | - |
| M3 | T015 | api_key_wrong_environment | 2 | new | no | lp_test_ | 5,991 | rejected |
| M3 | T016 | twofa_lost_device | 2 | new | yes | - | 5,570 | approved |
| M3 | T017 | refund_failed_card_closed | 2 | new | no | - | 5,458 | escalated |
| M3 | T018 | invoice_missing_tax | 2 | reused | yes | - | 2,564 | - |
| M3 | T019 | csv_export_encoding | 2 | reused | yes | - | 2,791 | - |
| M3 | T020 | csv_export_encoding | 2 | reused | yes | - | 2,849 | - |
| M3 | T021 | subscription_proration | 2 | reused | yes | - | 2,558 | - |
| M3 | T022 | payment_link_expired | 2 | reused | yes | - | 2,473 | - |
| M3 | T023 | api_rate_limit_429 | 2 | new | yes | - | 5,998 | approved |
| M3 | T024 | payout_fx_fee | 2 | reused | yes | - | 2,579 | - |
| M3 | T025 | payout_weekend_delay | 2 | reused | yes | - | 2,728 | - |
| M3 | T026 | webhook_duplicate_retries | 2 | reused | yes | - | 2,788 | - |
| M3 | T027 | api_key_wrong_environment | 2 | new | yes | - | 6,576 | approved |
| M3 | T028 | twofa_lost_device | 2 | new | no | - | 5,884 | escalated |
| M3 | T029 | refund_failed_card_closed | 2 | reused | yes | - | 2,480 | - |
| M3 | T030 | invoice_missing_tax | 2 | reused | yes | - | 2,725 | - |
| M3 | T031 | subscription_proration | 2 | reused | yes | - | 2,506 | - |
| M3 | T032 | payment_link_expired | 2 | reused | yes | - | 2,466 | - |
| M3 | T033 | payout_weekend_delay | 2 | reused | yes | - | 2,691 | - |
| M3 | T034 | api_rate_limit_429 | 2 | reused | yes | - | 2,796 | - |
| M3 | T035 | payout_fx_fee | 2 | reused | yes | - | 2,588 | - |
| M3 | T036 | sso_domain_not_verified | 2 | new | yes | - | 7,754 | approved |
| M3 | T037 | payout_fx_fee (looks like payout_weekend_delay) | 3 | reused | yes | - | 3,052 | - |
| M3 | T038 | api_rate_limit_429 (looks like webhook_duplicate_retries) | 3 | reused | yes | - | 2,888 | - |
| M3 | T039 | csv_export_encoding (looks like invoice_missing_tax) | 3 | reused | yes | - | 2,901 | - |
| M3 | T040 | sso_domain_not_verified (looks like twofa_lost_device) | 3 | reused | yes | - | 2,855 | - |
| M3 | T041 | subscription_proration (looks like refund_failed_card_closed) | 3 | reused | yes | - | 2,540 | - |
| M3 | T042 | payment_link_expired (looks like api_key_wrong_environment) | 3 | reused | yes | - | 2,581 | - |
