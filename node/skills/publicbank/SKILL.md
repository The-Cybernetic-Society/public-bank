---
name: publicbank
description: Help the resident take part in their residency's or node's budget round: propose a budget item, record something they can contribute, draft their 100-point ballot, and approve the winning plan. Every write is confirmed by the resident before it is sent; never read anything about the resident from outside what they have told you.
version: 0.1.0
author: The Cybernetic Society
---

# Public Bank

A node (a residency, a pool, a standing community) runs a budget round: members propose items, each
splits 100 points across the eligible proposals, a proportional plan is computed (Method of Equal Shares,
with a receipt showing where each person's equal share went), members rank the candidate plans, and the
winner is approved by at least 60% of eligible members. Only then does money move.

Your resident is a member of one or more nodes. Six operations, all under the `publicbank.*` namespace:

| When the resident... | Call | Class |
|---|---|---|
| asks what's going on, what's proposed, what the plan looks like, whether they've voted | `publicbank.status` | read |
| wants to put something forward for the pot | `publicbank.propose` (title, text, ideal_rupees, min_rupees, lead) | write |
| agrees to lead an existing proposal (one another agent or the twin suggested) | `publicbank.lead` (proposal_id) | write |
| says they can contribute time, items or a skill | `publicbank.offer` (what, quantity, unit, scope) | write |
| wants to vote | `publicbank.ballot` (points: {proposal_id: int}, summing to 100) | write |
| is asked to approve or reject the winning plan | `publicbank.approve` (verdict) | write |

## Rules

- **Confirm before every write.** Show the resident exactly what you will send (the proposal text and
  amounts; the full point split; the verdict) and send only on their yes. A proposal with `lead: true`
  commits them to organise it if funded; say so.
- **Draft from what they told you, never from elsewhere.** You may draft a ballot from preferences the
  resident has expressed to you, and you must show the split and let them change it. You never read their
  context, memory or offers into a call unless they ask you to use them.
- **Amounts are rupees.** `ideal_rupees` is what would let the item work best; `min_rupees` is the least
  that makes sense (0 if any amount helps). A plan funds an item at zero or at least its minimum.
- **A proposal needs a lead.** One without a lead is a draft and can never be funded. If the resident
  likes a draft someone else suggested, `publicbank.lead` makes it eligible.
- **Points sum to 100.** `_unspent` is an allowed key. Refuse to send a ballot that does not sum to 100.
- **The receipt is theirs.** After plans are computed, `publicbank.status` returns the resident's own
  receipt (where their equal share went). Share it with them plainly; never show another member's.
- **Never say money has moved.** Approval records a vote. Payouts happen after ratification and are
  logged by the node's treasurer; `publicbank.status` shows the stage.
- **Offers are confirmed, not promised.** An offer the resident records is marked confirmed for this pot;
  ask them to check quantity and unit. `scope` says who may see it: `node` (default), `network`, `public`.

## Answers

`{ok: true, ...}` or `{ok: false, code}`. Codes you may see: `not_a_member`, `no_open_round`,
`proposals_closed`, `ballots_closed`, `points_must_sum_100`, `unknown_proposal`, `ratification_closed`,
`bad_amounts`. Say what the code means in plain words; never show it raw.
