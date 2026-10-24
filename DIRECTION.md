# Public Bank — direction

*Working notes, October 2026. The spec grows out of what actually runs. Background: [Cybernetic Sutra, Vol. I](https://www.cyberneticsutra.com), whose five-organ architecture (Citizens, Digital Twin, Optimizer, Public Goods Hub, Auditor) this protocol instantiates for budgets.*

## What we are building

A **protocol for participatory budgeting that recurses**, not a budgeting app.

Groups that already exist (a residency, an Edge City cohort, a village, a DAO, a panchayat) decide their own budgets their own way. The protocol defines only what a group must be able to *say* to pool money and decisions with other groups. Any group that can emit four objects can join a super-node. A super-node is itself a node, so it can join a bigger one.

The reference implementation is deliberately minimal: a form, a script, a phone. The point is that a group with no infrastructure can run it. Richer systems (the Agent Village app, NYU's experiment, a DAO tool) join through adapters that emit the same four objects.

## Why

- Adoption happens at the schema, not the app. A gram panchayat clerk can emit four JSON objects from a spreadsheet; they will not install a stack.
- Interoperability is the claim nobody else is making. One group running Equal Shares and another running a referendum, feeding one shared pot under written rules, with money actually moving, is a result no single-system experiment produces.
- The super-node is where the hard questions live (who funds the level above, who votes there with what weight, what stops the top overriding the bottom). They need answering on real money before any government conversation.

## The four objects (v0, JSON, versioned)

| Object | Carries | Emitted by |
|---|---|---|
| `node` | id, name, member count, how the node ratifies (rule + threshold), coordinator contact (not stored in shared pots) | the group, once |
| `contribution` | node id, super-node id, amount, period | the group, per round |
| `proposal` | id, co-signing node ids, min, ideal, lead ref, text | any member of a co-signing node |
| `decision` | node id, proposal id, verdict (fund / decline / amount), how reached (rule id, turnout, eligible) | the group, per proposal |

Everything inside the group (how proposals are collected, what ballot, which allocation rule, which app) is the group's business and never crosses the interface.

## Super-node rules (opt-in, fixed per pool, three parameters)

1. **Contribution**: each member node sends a fixed fraction of its pot upward (default 20%).
2. **Weight**: a member node votes in the super-node with weight equal to its member count, using its members' ballots already cast (no second ballot).
3. **Ratification**: a super-node decision pays out only when every affected member node ratifies it by its own rule at its own threshold.

Unspent money at round end is returned pro rata or rolled to the next round, chosen at pool creation.

## Reference node (this repo)

- Proposal form → frozen list (id, min, ideal, lead).
- 100-point ballot per member → support matrix.
- `cns_allocate.py` (in [edge-city-nyu](https://github.com/The-Cybernetic-Society/edge-city-nyu)): Method of Equal Shares (proportional, with a documented completion rule) plus Nash, utilitarian and maximin as named alternatives; a `--check` that validates any plan against the rules.
- Published plan with per-person receipts; yes/no ratification at 60% of eligible.
- Payout log: plan id → payee → amount → settlement reference (UPI transaction id or cash receipt).
- Exports the four objects.

## First deployment: Edge City India, Oct 24 – Nov 1 2026

- Member nodes: residencies and cohorts that opt in, each with a coordinator, each on the reference node (forms) unless they prefer their own process.
- Super-node: a village pot funded by each member's contribution; cross-node proposals need co-signing leads; ratified per member node.
- Edge City's Agent Village node: a gated page in the Agent Village app (`BUDGET_EXPERIMENT_COHORT`) that runs the same round for app residents and emits the four objects. Odin may propose at the super-node; it never decides.
- NYU's allocation experiment, if they share a plan export, joins as a sibling node's decision.
- Everything the reference node and the spec produce is open source (licence TBD, MIT or Apache-2.0). Edge City's app and data repos carry no licence; nothing from them is copied here.

## Not in scope this week

Agent integration, feasibility modelling beyond budget arithmetic, a second site, Bhutan. The second site (same protocol, a community outside Edge City) is the next step after Goa; Bhutan (GNH domains as the preference vector) is the one after that.

## Open questions

- Weighting by member count vs. by contribution: member count is the honest default; contribution-weighting is plutocratic and is listed only to be rejected explicitly.
- Whether a node may belong to more than one super-node in the same round.
- Identity across nodes: a person in two residencies votes in both; the super-node counts nodes, not people, so this is allowed by design. Say so.
- Minimum viable audit: what a third party needs to verify a round from the exported objects alone.
