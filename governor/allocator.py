"""Allocate one frozen batch of proposals against one snapshot, portfolio and policy.

Order of operations:
1. Portfolio gates and per-proposal eligibility (rules.py).
2. Rank eligible proposals by reliability * net edge, descending; proposal_id breaks ties.
3. Walk the ranking. One funded proposal per market: later opposite-side proposals are
   conflicts, same-side ones redundant. A proposal that ends up with zero contracts does
   not take the market's slot.
4. Cut each Kelly target to the tightest of cash, market, agent, event-group and total
   headroom, then buy whole contract steps up the ask ladder while price stays within the
   agent's ceiling, edge stays positive and all-in cost (price + exact fees) fits.

The result depends only on the arguments, so a stored batch replays exactly.
"""
from dataclasses import dataclass

from . import rules
from .fees import fee_per_contract, taker_fee
from .models import Decision, _require_utc
from .money import ZERO, round_down
from .policy import DEFAULT_POLICY
from .sizing import full_kelly, target_stake


@dataclass(frozen=True)
class _Fill:
    contracts: object
    cost: object  # price * contracts + fees, summed over levels
    fees: object
    limit_price: object
    levels: tuple
    depth_limited: bool  # stopped by book depth, price ceiling or edge rather than budget


class _Usage:
    """Loss-at-risk already committed, by market, agent, event group and in total."""

    def __init__(self, exposures, cash):
        self.market, self.agent, self.event = {}, {}, {}
        self.total = ZERO
        self.cash = cash
        for item in exposures:
            self.add(item.market_id, item.agent_id, item.event_group, item.amount)

    def add(self, market_id, agent_id, event_group, amount):
        self.market[market_id] = self.market.get(market_id, ZERO) + amount
        self.agent[agent_id] = self.agent.get(agent_id, ZERO) + amount
        self.event[event_group] = self.event.get(event_group, ZERO) + amount
        self.total += amount

    def headroom(self, proposal, event_group, equity, policy):
        def room(cap, used):
            return max(ZERO, cap * equity - used)
        return {
            rules.CASH_LIMIT: max(ZERO, self.cash),
            rules.MARKET_CAP: room(policy.market_cap, self.market.get(proposal.market_id, ZERO)),
            rules.AGENT_CAP: room(policy.agent_cap, self.agent.get(proposal.agent_id, ZERO)),
            rules.EVENT_GROUP_CAP: room(policy.event_group_cap, self.event.get(event_group, ZERO)),
            rules.TOTAL_CAP: room(policy.total_cap, self.total),
        }


def _walk_ladder(asks, ceiling, win_probability, budget, multiplier, step):
    contracts = spent = fees = ZERO
    limit_price = None
    levels = []
    budget_bound = False
    for level in asks:
        if ceiling is not None and level.price > ceiling:
            break
        if win_probability - (level.price + fee_per_contract(level.price, multiplier)) <= 0:
            break
        available = round_down(level.quantity, step)
        per_contract = level.price + fee_per_contract(level.price, multiplier)
        # Lower bound: the per-contract fee never understates the fee of a larger fill.
        n = min(available, round_down((budget - spent) / per_contract, step))
        while n + step <= available and level.price * (n + step) + taker_fee(n + step, level.price, multiplier) <= budget - spent:
            n += step
        if n > ZERO:
            fee = taker_fee(n, level.price, multiplier)
            spent += level.price * n + fee
            fees += fee
            contracts += n
            limit_price = level.price
            levels.append({"price": str(level.price), "contracts": str(n), "fee": str(fee)})
        if n < available:
            budget_bound = True
            break
    return _Fill(contracts, spent, fees, limit_price, tuple(levels), depth_limited=not budget_bound)


def decide(proposals, snapshot, portfolio, reliability, now, policy=DEFAULT_POLICY):
    """Return one Decision per proposal, sorted by proposal_id.

    reliability: agent_id -> ReliabilityScore. Agents without one get the policy prior.
    """
    proposals = list(proposals)
    ids = [p.proposal_id for p in proposals]
    if len(ids) != len(set(ids)):
        raise ValueError("A batch cannot contain the same proposal_id twice.")
    _require_utc(now, "now")
    equity = portfolio.equity
    decisions = {}

    def decision(p, outcome, reasons, score, trace, fill=None):
        decisions[p.proposal_id] = Decision(
            proposal_id=p.proposal_id, agent_id=p.agent_id, market_id=p.market_id, side=p.side,
            outcome=outcome, approved_budget=fill.cost if fill else ZERO,
            contracts=fill.contracts if fill else ZERO, limit_price=fill.limit_price if fill else None,
            reason_codes=tuple(reasons), reliability=score, policy_version=policy.version,
            snapshot_id=snapshot.snapshot_id,
            trace={"equity": str(equity), "requestedBudget": str(p.requested_budget),
                   "estimatedYesProbability": str(p.estimated_yes_probability), **trace})

    gate = rules.portfolio_gate(portfolio)
    candidates = []
    for p in proposals:
        record = reliability.get(p.agent_id)
        score = record.score if record else policy.reliability_prior
        samples = record.resolved_count if record else 0
        book = snapshot.books.get(p.market_id)
        reason = gate or rules.proposal_gate(p, book, portfolio, policy, now)
        if reason:
            decision(p, "rejected", [reason], score, {"stage": "eligibility", "reliabilitySamples": samples})
            continue
        best = book.best_ask(p.side)
        effective_cost = best.price + fee_per_contract(best.price, book.fee_multiplier)
        edge = p.win_probability - effective_cost
        candidates.append((score * edge, p, book, score, samples, best.price, effective_cost, edge))

    candidates.sort(key=lambda c: (-c[0], c[1].proposal_id))
    usage = _Usage(portfolio.exposures, portfolio.available_cash)
    winners = {}  # market_id -> winning proposal
    for rank, (rank_score, p, book, score, samples, best_price, effective_cost, edge) in enumerate(candidates, 1):
        trace = {"stage": "allocation", "rank": rank, "rankScore": str(rank_score), "bestAsk": str(best_price),
                 "effectiveCost": str(effective_cost), "winProbability": str(p.win_probability),
                 "netEdge": str(edge), "reliabilitySamples": samples}
        winner = winners.get(p.market_id)
        if winner:
            code = rules.REDUNDANT if winner.side == p.side else rules.CONFLICT
            decision(p, "rejected", [code], score, {**trace, "lostTo": winner.proposal_id})
            continue
        target = target_stake(equity=equity, win_probability=p.win_probability, effective_cost=effective_cost,
                              reliability=score, requested_budget=p.requested_budget,
                              kelly_fraction=policy.kelly_fraction)
        headroom = usage.headroom(p, book.event_group, equity, policy)
        limit = min([target, *headroom.values()])
        # Report every limit that cut the target, tightest first.
        binding = [code for code, room in sorted(headroom.items(), key=lambda kv: (kv[1], kv[0])) if room < target]
        fill = _walk_ladder(book.asks(p.side), p.max_entry_price, p.win_probability, limit,
                            book.fee_multiplier, book.contract_step)
        # Liquidity only explains a shortfall if budget for another step was left unused.
        next_step_cost = (fill.limit_price or best_price) * book.contract_step
        depth_cut = fill.depth_limited and limit - fill.cost >= next_step_cost
        trace.update(fullKelly=str(full_kelly(p.win_probability, effective_cost)), targetStake=str(target),
                     headroom={k: str(v) for k, v in headroom.items()}, stakeLimit=str(limit),
                     fills=list(fill.levels), totalFees=str(fill.fees), approvedCost=str(fill.cost))
        reasons = binding + ([rules.NO_LIQUIDITY] if depth_cut else [])
        if fill.contracts == ZERO:
            decision(p, "rejected", reasons + [rules.ZERO_SIZE], score, trace)
            continue
        winners[p.market_id] = p
        usage.add(p.market_id, p.agent_id, book.event_group, fill.cost)
        usage.cash -= fill.cost
        decision(p, "resized" if reasons else "approved", reasons, score, trace, fill=fill)

    return [decisions[i] for i in sorted(decisions)]
