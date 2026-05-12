# Currency Workflow State Machine

This document summarizes the current worker progression for currency requests and the order in which pending waits are evaluated.

## Simple Token Workflow

| Step | Entry Condition | Action | Wait Recorded | Next Step |
| --- | --- | --- | --- | --- |
| 0 | request in progress | submit register_name_commitment | tx_confirm for commitment txid | 1 |
| 1 | commitment confirmed | submit register_identity | tx_confirm for identity txid | 2 |
| 2 | identity confirmed | send native funding to identity for define | opid_txid or tx_confirm (immediate promotion attempted) | 3 |
| 3 | funding confirmed | submit define_simple_token_currency | tx_confirm for define txid | 4 |
| 4 | define confirmed | mark request complete | none | done |

## Fractional Workflow

| Step | Entry Condition | Action | Wait Handling Priority | Next Step |
| --- | --- | --- | --- | --- |
| 0 | request in progress | if identity_exists then skip; else submit register_name_commitment | standard row wait states only | 1 or 2 |
| 1 | commitment confirmed or skipped | if identity_exists then skip; else submit register_identity | standard row wait states only | 2 |
| 2 | identity confirmed or skipped | no direct funding; explicitly defer all funding to step 4 | none | 3 |
| 3 | reserve processing gate | run reserve sub-state machine when create_reserves is true; else skip | standard row wait states only | 4 |
| 4 | funding planning stage | first resolve pending_funding_waits; if unresolved, do not submit new funding. when clear, compute shortfalls and batch submit by source identity | pending_funding_waits checked before any new send | 5 |
| 5 | define gate | resolve pending_funding_waits again, then require balances, then one extra sanity sweep before define | pending_funding_waits checked before balances and define | 6 |
| 6 | define confirmed | mark request complete | none | done |

## Fractional Reserve Sub-State Machine (Step 3)

| Reserve Phase | Entry Condition | Action | Wait Recorded | Next Phase |
| --- | --- | --- | --- | --- |
| 0 | reserve selected | if reserve identity exists, skip to phase 2; else submit reserve register_name_commitment | tx_confirm | 1 or 2 |
| 1 | reserve commitment confirmed | if reserve identity exists, skip to phase 2; else submit reserve register_identity | tx_confirm | 2 |
| 2 | reserve identity ready | submit native funding for reserve define | opid_txid or tx_confirm (immediate promotion attempted) | 3 |
| 3 | reserve funding confirmed | submit define_simple_token_currency for reserve | tx_confirm | 4 |
| 4 | reserve define confirmed | advance to next reserve | none | next reserve or step 4 |

## Wait Evaluation Order

| Location | Wait Type | Checked Before New RPC Submissions | Current Behavior |
| --- | --- | --- | --- |
| global process loop when status is waiting_confirm | tx_confirm | yes | in_progress resumes only after confirmations > 0 |
| global process loop when status is waiting_opid | opid_txid | yes | promoted to waiting_confirm when txid appears |
| fractional step 4 | progress.pending_funding_waits (tx_confirm and opid_txid) | yes | unresolved waits block new funding plan/sends |
| fractional step 5 | progress.pending_funding_waits (tx_confirm and opid_txid) | yes | unresolved waits block define and keep step 5 in progress |
| fractional reserve phase 2 | opid_txid from funding send | yes (immediate poll) | immediate promotion to tx_confirm when txid is already available |
