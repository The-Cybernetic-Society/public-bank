#!/usr/bin/env python3
"""
cns_allocate.py — compute funding plans from a proposal list and a per-person
support matrix, under the Community Fund Allocation rules:

  * each proposal gets 0 or at least its minimum
  * funded amounts are capped at the ideal (pass --no-cap to lift this)
  * the whole budget is spent

Four rules, each a named plan:

  mes          Method of Equal Shares (proportional; Peters, Pierczyński, Skowron 2021)
               + a documented completion step so the full budget is spent
  nash         maximise the product of per-person utilities (Nash welfare)
  utilitarian  maximise the sum of per-person utilities
  maximin      maximise the lowest per-person utility

Per-person utility for a plan x:  u_i(x) = Σ_j  s_ij · x_j / ideal_j
(support-weighted fraction of each proposal's ideal that got funded).

Inputs (CSV):
  proposals.csv   id,name,minimum,ideal
  support.csv     person, then one column per proposal id, cells 0–3

Usage:
  python3 cns_allocate.py --proposals proposals.csv --support support.csv --budget 500000
  python3 cns_allocate.py --demo                      # synthetic data
  python3 cns_allocate.py --check plan.json --proposals proposals.csv --budget 500000

Dependencies: numpy, scipy (>=1.9 for milp).  MIT.
"""
from __future__ import annotations
import argparse, csv, itertools, json, math, sys
from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import linprog, minimize

EPS = 1e-9


# ----------------------------------------------------------------------------
# data
# ----------------------------------------------------------------------------
@dataclass
class Proposal:
    id: str
    name: str
    minimum: float
    ideal: float


@dataclass
class Plan:
    rule: str
    amounts: dict[str, float]               # proposal id -> amount
    receipts: dict[str, dict[str, float]] = field(default_factory=dict)  # person -> {proposal: charge}
    notes: list[str] = field(default_factory=list)

    def funded(self) -> list[str]:
        return [p for p, a in self.amounts.items() if a > EPS]


def read_proposals(path: str) -> list[Proposal]:
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            mn = float(row.get("minimum") or 0)
            ideal = float(row["ideal"])
            if ideal < mn:
                sys.exit(f"proposal {row['id']}: ideal {ideal} < minimum {mn}")
            out.append(Proposal(row["id"].strip(), row.get("name", row["id"]).strip(), mn, ideal))
    return out


def read_support(path: str, proposals: list[Proposal]) -> tuple[list[str], np.ndarray]:
    """Wide CSV: person, <proposal id>, <proposal id>, ... with values 0–3."""
    ids = [p.id for p in proposals]
    people, rows = [], []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        missing = [i for i in ids if i not in r.fieldnames]
        if missing:
            sys.exit(f"support.csv missing columns for proposals: {missing}")
        for row in r:
            people.append(row["person"].strip())
            rows.append([float(row[i] or 0) for i in ids])
    S = np.array(rows, dtype=float)
    if (S < 0).any():
        sys.exit("support values must be >= 0")
    return people, S


# ----------------------------------------------------------------------------
# validity check (usable on anyone's plan, including LLM-written ones)
# ----------------------------------------------------------------------------
def check_plan(amounts: dict[str, float], proposals: list[Proposal], budget: float,
               cap_at_ideal: bool = True) -> list[str]:
    errs = []
    by_id = {p.id: p for p in proposals}
    unknown = set(amounts) - set(by_id)
    if unknown:
        errs.append(f"unknown proposal ids: {sorted(unknown)}")
    total = 0.0
    for pid, p in by_id.items():
        a = float(amounts.get(pid, 0))
        total += a
        if a < -EPS:
            errs.append(f"{pid}: negative amount")
        elif EPS < a < p.minimum - EPS:
            errs.append(f"{pid}: {a:.2f} is below minimum {p.minimum:.2f} (must be 0 or >= minimum)")
        elif cap_at_ideal and a > p.ideal + EPS:
            errs.append(f"{pid}: {a:.2f} exceeds ideal {p.ideal:.2f}")
    if abs(total - budget) > 0.5:
        errs.append(f"plan spends {total:.2f}, budget is {budget:.2f} (must spend it all)")
    return errs


# ----------------------------------------------------------------------------
# utilities
# ----------------------------------------------------------------------------
def utilities(amounts: dict[str, float], proposals: list[Proposal], S: np.ndarray) -> np.ndarray:
    frac = np.array([amounts.get(p.id, 0.0) / p.ideal for p in proposals])
    return S @ frac


# ----------------------------------------------------------------------------
# rule 1: Method of Equal Shares, then completion to full budget
# ----------------------------------------------------------------------------
def mes(proposals: list[Proposal], people: list[str], S: np.ndarray, budget: float,
        cap_at_ideal: bool = True) -> Plan:
    n, m = S.shape
    share = budget / n
    bal = np.full(n, share)                      # each person's remaining notional money
    amounts = {p.id: 0.0 for p in proposals}
    charges = np.zeros((n, m))
    notes = []

    # ---- phase 1: standard MES at minimum costs decides WHAT is funded -------
    # cost of "turning on" proposal j is its minimum (or a small unit if no minimum)
    cost = np.array([p.minimum if p.minimum > 0 else min(p.ideal, share) for p in proposals])
    remaining = set(range(m))
    while True:
        best = None
        for j in remaining:
            u = S[:, j]
            sup = u > 0
            if not sup.any() or bal[sup].sum() < cost[j] - EPS:
                continue
            # smallest rho with  Σ_i min(bal_i, rho·u_i) >= cost_j
            lo, hi = 0.0, cost[j] / u[sup].min() + 1
            for _ in range(80):
                mid = (lo + hi) / 2
                if np.minimum(bal[sup], mid * u[sup]).sum() >= cost[j]:
                    hi = mid
                else:
                    lo = mid
            rho = hi
            if best is None or rho < best[0]:
                best = (rho, j)
        if best is None:
            break
        rho, j = best
        pay = np.minimum(bal, rho * S[:, j]) * (S[:, j] > 0)
        # fix rounding so charges sum exactly to cost
        pay *= cost[j] / pay.sum()
        bal -= pay
        charges[:, j] += pay
        amounts[proposals[j].id] = cost[j]
        remaining.remove(j)
    notes.append(f"MES phase 1 funded {len(amounts) - len(remaining)} of {m} proposals at their minimums; "
                 f"{bal.sum():.2f} of the budget left unallocated by MES.")

    # ---- phase 2: completion — spend the remainder ---------------------------
    # (a) top up funded proposals toward their ideals, in proportion to total
    #     support, charging supporters in proportion to their remaining balance.
    def top_up(targets: list[int], label: str):
        nonlocal bal
        left = bal.sum()
        rounds = 0
        while left > 0.5 and targets and rounds < 50:
            rounds += 1
            w = np.array([S[:, j].sum() for j in targets])
            if w.sum() <= 0:
                w = np.ones(len(targets))
            headroom = np.array([(proposals[j].ideal if cap_at_ideal else np.inf) - amounts[proposals[j].id]
                                 for j in targets])
            give = np.minimum(left * w / w.sum(), headroom)
            if give.sum() <= EPS:
                break
            for j, g in zip(targets, give):
                if g <= EPS:
                    continue
                sup = S[:, j] > 0
                weights = bal * sup
                if weights.sum() <= EPS:        # supporters are broke: everyone chips in
                    weights = bal.copy()
                pay = g * weights / weights.sum()
                bal -= pay
                charges[:, j] += pay
                amounts[proposals[j].id] += g
            left = bal.sum()
            targets = [j for j in targets
                       if not cap_at_ideal or amounts[proposals[j].id] < proposals[j].ideal - EPS]
        return left

    funded = [j for j in range(m) if amounts[proposals[j].id] > EPS]
    left = top_up(funded, "funded")
    if left > 0.5:
        notes.append(f"{left:.2f} left after topping funded proposals to ideal; "
                     f"adding unfunded proposals by support until spent.")
    # (b) still money left: bring in unfunded proposals, most-supported first,
    #     at their minimum if affordable, then top those up too.
    for j in sorted(remaining, key=lambda j: -S[:, j].sum()):
        if left < cost[j] - EPS:
            continue
        weights = bal.copy()
        pay = cost[j] * weights / weights.sum()
        bal -= pay
        charges[:, j] += pay
        amounts[proposals[j].id] = cost[j]
        funded.append(j)
        left = top_up(funded, "all")
        if left <= 0.5:
            break
    if left > 0.5:
        if cap_at_ideal:
            notes.append(f"{left:.2f} could not be spent without exceeding ideals; "
                         f"rerun with --no-cap or raise ideals. Plan is INVALID as is.")
        # distribute over ideal only if allowed
        else:
            top_up(funded, "all")

    receipts = {people[i]: {proposals[j].id: round(float(charges[i, j]), 2)
                            for j in range(m) if charges[i, j] > 0.005}
                for i in range(n)}
    return Plan("mes", {k: round(v, 2) for k, v in amounts.items()}, receipts, notes)


# ----------------------------------------------------------------------------
# rules 2–4: optimisation over the feasible plan space
#   enumerate funded subsets (fine for <= ~14 proposals), solve each subset's
#   continuous problem, keep the best.
# ----------------------------------------------------------------------------
def _subsets(proposals: list[Proposal], budget: float, cap_at_ideal: bool):
    m = len(proposals)
    if m > 16:
        sys.exit("more than 16 proposals: switch to a MILP solver (see README)")
    mins = np.array([p.minimum for p in proposals])
    caps = np.array([p.ideal if cap_at_ideal else np.inf for p in proposals])
    for bits in itertools.product([0, 1], repeat=m):
        y = np.array(bits, dtype=bool)
        if not y.any():
            continue
        lo = (mins * y).sum()
        hi = (caps * y).sum() if np.isfinite(caps[y]).all() else np.inf
        if lo - EPS <= budget <= hi + EPS:
            yield y, mins, caps


def _solve_subset(obj: str, y, mins, caps, proposals, S, budget):
    idx = np.where(y)[0]
    k = len(idx)
    ideal = np.array([proposals[j].ideal for j in idx])
    A = S[:, idx] / ideal                      # u = A @ x
    lo = np.maximum(mins[idx], 0.0)
    hi = caps[idx]
    hi = np.where(np.isfinite(hi), hi, budget)
    bounds = list(zip(lo, hi))
    A_eq, b_eq = np.ones((1, k)), [budget]

    if obj == "utilitarian":
        c = -(A.sum(axis=0))
        r = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
        return (A @ r.x).sum(), r.x if r.success else None
    if obj == "maximin":
        # variables x (k) and t; maximise t s.t. A x >= t
        c = np.zeros(k + 1); c[-1] = -1
        A_ub = np.hstack([-A, np.ones((A.shape[0], 1))])
        r = linprog(c, A_ub=A_ub, b_ub=np.zeros(A.shape[0]),
                    A_eq=np.hstack([A_eq, [[0]]]), b_eq=b_eq,
                    bounds=bounds + [(None, None)], method="highs")
        return (r.x[-1] if r.success else -np.inf), (r.x[:-1] if r.success else None)
    if obj == "nash":
        def f(x):
            return -np.log(A @ x + 1e-3).sum()
        x0 = lo + (budget - lo.sum()) * (hi - lo) / max((hi - lo).sum(), EPS)
        r = minimize(f, x0, bounds=bounds, constraints=[{"type": "eq", "fun": lambda x: x.sum() - budget}],
                     method="SLSQP", options={"maxiter": 300})
        return (-r.fun if r.success else -np.inf), (r.x if r.success else None)
    raise ValueError(obj)


def optimise(obj: str, proposals, people, S, budget, cap_at_ideal=True) -> Plan:
    best_val, best_x, best_y = -np.inf, None, None
    for y, mins, caps in _subsets(proposals, budget, cap_at_ideal):
        val, x = _solve_subset(obj, y, mins, caps, proposals, S, budget)
        if x is not None and val > best_val + 1e-9:
            best_val, best_x, best_y = val, x, y
    if best_x is None:
        return Plan(obj, {}, notes=["no feasible plan (check minimums/ideals vs budget)"])
    amounts = {p.id: 0.0 for p in proposals}
    for j, a in zip(np.where(best_y)[0], best_x):
        amounts[proposals[j].id] = round(float(a), 2)
    return Plan(obj, amounts, notes=[f"objective value {best_val:.4f} over {int(best_y.sum())} funded proposals"])


# ----------------------------------------------------------------------------
# reporting
# ----------------------------------------------------------------------------
def describe(plan: Plan, proposals, people, S, budget) -> dict:
    u = utilities(plan.amounts, proposals, S)
    errs = check_plan(plan.amounts, proposals, budget)
    return {
        "rule": plan.rule,
        "valid": not errs,
        "errors": errs,
        "amounts": plan.amounts,
        "funded": [p.name for p in proposals if plan.amounts.get(p.id, 0) > EPS],
        "per_person_utility": {people[i]: round(float(u[i]), 3) for i in range(len(people))},
        "utility_sum": round(float(u.sum()), 3),
        "utility_min": round(float(u.min()), 3),
        "nash_log_sum": round(float(np.log(u + 1e-3).sum()), 3),
        "receipts": plan.receipts,
        "notes": plan.notes,
    }


def l1(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    return sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys)


def print_report(reports: list[dict], proposals, budget):
    ids = [p.id for p in proposals]
    print(f"\nBudget {budget:,.0f}   proposals {len(proposals)}\n")
    head = f"{'rule':12}" + "".join(f"{i:>10}" for i in ids) + f"{'Σu':>8}{'min u':>8}{'valid':>7}"
    print(head)
    for r in reports:
        row = f"{r['rule']:12}" + "".join(f"{r['amounts'].get(i, 0):>10,.0f}" for i in ids)
        row += f"{r['utility_sum']:>8.2f}{r['utility_min']:>8.2f}{'yes' if r['valid'] else 'NO':>7}"
        print(row)
    print("\nPairwise L1 distance between plans (how different the slate is):")
    names = [r["rule"] for r in reports]
    print(f"{'':12}" + "".join(f"{n:>12}" for n in names))
    for a in reports:
        print(f"{a['rule']:12}" + "".join(f"{l1(a['amounts'], b['amounts']):>12,.0f}" for b in reports))
    for r in reports:
        if r["errors"]:
            print(f"\n[{r['rule']}] INVALID: " + "; ".join(r["errors"]))
        for n in r["notes"]:
            print(f"[{r['rule']}] {n}")
    mes_r = next((r for r in reports if r["rule"] == "mes"), None)
    if mes_r and mes_r["receipts"]:
        print("\nEqual Shares receipts (where each person's equal share went):")
        for person, rec in mes_r["receipts"].items():
            total = sum(rec.values())
            parts = ", ".join(f"{pid} {amt / total:.0%}" for pid, amt in sorted(rec.items(), key=lambda kv: -kv[1]))
            print(f"  {person:10} {parts}")


# ----------------------------------------------------------------------------
# demo data
# ----------------------------------------------------------------------------
def demo_data(seed=7):
    rng = np.random.default_rng(seed)
    proposals = [
        Proposal("water", "Drinking-water filter for the hall", 60_000, 90_000),
        Proposal("kids", "Kids' making workshop (3 sessions)", 40_000, 70_000),
        Proposal("scooter", "Shared e-scooter", 55_000, 55_000),
        Proposal("music", "Closing-night music & sound", 30_000, 80_000),
        Proposal("garden", "Compost + herb garden", 10_000, 25_000),
        Proposal("talks", "Local-speaker talk series", 15_000, 40_000),
        Proposal("signage", "Village wayfinding signs", 8_000, 15_000),
        Proposal("first_aid", "First-aid kits + training", 12_000, 20_000),
    ]
    people = [f"p{i:02d}" for i in range(1, 25)]
    # three loose blocs with different tastes, plus noise
    blocs = rng.integers(0, 3, len(people))
    taste = np.array([
        [3, 1, 0, 2, 2, 1, 1, 2],   # practical/community
        [1, 3, 0, 3, 1, 2, 0, 1],   # families/culture
        [1, 0, 3, 3, 0, 1, 1, 0],   # mobility/party
    ])
    S = np.clip(taste[blocs] + rng.integers(-1, 2, (len(people), len(proposals))), 0, 3).astype(float)
    return proposals, people, S, 300_000.0


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proposals")
    ap.add_argument("--support")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--rules", default="mes,nash,utilitarian,maximin")
    ap.add_argument("--no-cap", action="store_true", help="allow funding above a proposal's ideal")
    ap.add_argument("--out", help="write plans as JSON here")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--check", help="validate a plan JSON ({proposal_id: amount}) and exit")
    a = ap.parse_args()

    if a.demo:
        proposals, people, S, budget = demo_data()
        print("DEMO: 8 synthetic proposals, 24 people in 3 loose blocs, budget 300,000")
    else:
        if not (a.proposals and a.support and a.budget):
            ap.error("--proposals, --support and --budget are required (or --demo)")
        proposals = read_proposals(a.proposals)
        people, S = read_support(a.support, proposals)
        budget = a.budget

    cap = not a.no_cap

    if a.check:
        amounts = json.load(open(a.check))
        errs = check_plan(amounts, proposals, budget, cap)
        print("VALID" if not errs else "INVALID:\n  " + "\n  ".join(errs))
        sys.exit(0 if not errs else 1)

    plans = []
    for rule in a.rules.split(","):
        rule = rule.strip()
        if rule == "mes":
            plans.append(mes(proposals, people, S, budget, cap))
        elif rule in ("nash", "utilitarian", "maximin"):
            plans.append(optimise(rule, proposals, people, S, budget, cap))
        else:
            sys.exit(f"unknown rule {rule}")

    reports = [describe(p, proposals, people, S, budget) for p in plans]
    print_report(reports, proposals, budget)
    if a.out:
        def _np(o):
            if isinstance(o, (np.bool_,)): return bool(o)
            if isinstance(o, (np.integer,)): return int(o)
            if isinstance(o, (np.floating,)): return float(o)
            raise TypeError(type(o))
        json.dump({"budget": budget, "proposals": [p.__dict__ for p in proposals], "plans": reports},
                  open(a.out, "w"), indent=2, default=_np)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
