"""Read models for orders and events. /api/state carries only a bounded window of them (the newest orders, the ones
waiting for approval, counts and the newest events), because a workspace's history grows without limit and the
browser polls the state every few seconds. Activity, Approvals and the order drawer read the rest through route().
Nothing here changes the workspace."""
import copy
import datetime
import re
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ORDER_CATEGORIES = ('crypto', 'sports', 'economics', 'politics', 'culture', 'other')
RECENT_ORDERS = 20      # Home's recent decisions table and new-order moods
PENDING_ORDERS = 50     # approval cards in the state; Approvals reads past them through /api/orders
RECENT_EVENTS = 20      # Activity's first page of events
PAGE_LIMIT = 100        # the most rows one /api/orders or /api/events read returns
REASONING_PREVIEW = 280  # a card shows three lines of reasoning; the full text is one /api/orders/<id> away
DAILY_DAYS = 7
# Statuses that can still change at the venue or with the user. Matches the front end's polling (order-state.js).
IN_FLIGHT = {'awaiting approval', 'resting', 'submitted', 'partially filled', 'open', 'submission unknown', 'unknown'}
# Order fields a list row needs; anything else (venue IDs, idempotency details) stays in the full order.
LIST_FIELDS = ('id', 'agent', 'accountId', 'amount', 'direction', 'time', 'filled', 'decision', 'status', 'automatic',
               'actualCost', 'governorProposalId')
GOVERNOR_SUMMARY = ('decision', 'approvedBudgetUsd', 'maxBudgetUsd')  # enough to tell a trimmed order
ZONE_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}')  # e.g. America/Argentina/Buenos_Aires
DIGITS = re.compile(r'[0-9]{1,10}')


def order_category(o):
    """The market category Activity filters by. Kalshi quotes carry it as market['agent'] (a legacy name, not the
    agent); any other source, older order or unknown value is 'other', so no order drops out of the filter."""
    market = o.get('market') or {}
    value = market.get('category') or market.get('agent')
    return value if value in ORDER_CATEGORIES else 'other'


def reviewed(o):
    return any(e.get('outcome') == 'REQUIRE_APPROVAL' for e in o.get('evaluations') or [])


def block_reason(o):
    """The first check that blocked the order in its latest evaluation, e.g. 'Agent mandate: Trade exceeds the agent
    daily limit.', or None."""
    evaluations = o.get('evaluations') or []
    checks = (evaluations[-1].get('checks') or []) if evaluations else []
    check = next((c for c in checks if c.get('outcome') == 'BLOCK'), None)
    return f"{check.get('name')}: {check.get('reason')}" if check else None


def full_order(o, governors):
    """Everything the order drawer shows: the stored order with its category and allowlisted governor details."""
    public = copy.deepcopy(o)
    public['category'] = order_category(o)
    public.pop('governor', None)
    if o.get('governorProposalId') in governors:
        public['governor'] = copy.deepcopy(governors[o['governorProposalId']])
    return public


def list_order(o, governors):
    """A row for lists and cards: no evaluations, the reasoning cut to a preview, and the full governor sizing only
    while the order waits for approval (its card shows the sizing rail)."""
    row = {key: copy.deepcopy(o[key]) for key in LIST_FIELDS if key in o}
    market = o.get('market') or {}
    row['market'] = {key: market[key] for key in ('id', 'name') if key in market}
    row['category'] = order_category(o)
    row['reviewed'] = reviewed(o)
    reason = block_reason(o) if o.get('decision') == 'BLOCK' else None
    if reason:
        row['blockReason'] = reason
    request = o.get('request')
    if request:
        row['request'] = request[:REASONING_PREVIEW]
        row['requestLength'] = len(request)
    governor = governors.get(o.get('governorProposalId'))
    if o.get('status') == 'awaiting approval':
        if 'governedTerms' in o:
            row['governedTerms'] = copy.deepcopy(o['governedTerms'])
        if governor:
            row['governor'] = copy.deepcopy(governor)
    elif governor:
        row['governor'] = {key: governor.get(key) for key in GOVERNOR_SUMMARY}
    return row


def parse_time(value):
    try:
        moment = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def agent_activity(orders):
    """Per agent: how many of its orders wait for approval, and its latest order (its creature's mood reads both)."""
    activity = {}
    for o in orders:
        item = activity.setdefault(o.get('agent'), {'awaiting': 0, 'lastOrder': None})
        if o.get('status') == 'awaiting approval':
            item['awaiting'] += 1
        moment, last = parse_time(o.get('time')), item['lastOrder']
        if moment and (last is None or moment > parse_time(last['time'])):
            item['lastOrder'] = {'time': o['time'], 'decision': o.get('decision'), 'status': o.get('status')}
    return activity


def window(s, governors):
    """The order and event fields of /api/state: bounded however long the workspace's history is."""
    orders, events = s.get('orders') or [], s.get('events') or []
    pending = [o for o in orders if o.get('status') == 'awaiting approval']
    return {
        'recentOrders': [list_order(o, governors) for o in orders[:RECENT_ORDERS]],
        'pendingOrders': [list_order(o, governors) for o in pending[:PENDING_ORDERS]],
        'orderStats': {'total': len(orders), 'awaiting': len(pending),
                       'inFlight': sum(o.get('status') in IN_FLIGHT for o in orders)},
        'events': copy.deepcopy(events[:RECENT_EVENTS]),
        'eventCount': len(events),
    }


def filter_orders(orders, filters, agent_names):
    """Activity's filters. Search looks at what a person would type: the market, the order ID, the agent's name, its
    full reasoning, the side and the status, word by word in any case; never field names."""
    words = (filters.get('query') or '').lower().split()
    result = []
    for o in orders:
        if filters.get('status') and o.get('status') != filters['status']:
            continue
        if filters.get('agent') and o.get('agent') != filters['agent']:
            continue
        if filters.get('category') and order_category(o) != filters['category']:
            continue
        if filters.get('decision') and o.get('decision') != filters['decision']:
            continue
        if words:
            market = o.get('market') or {}
            text = ' '.join(str(v) for v in (market.get('name'), market.get('id'), o.get('id'), agent_names.get(o.get('agent')),
                                             o.get('request'), o.get('direction'), o.get('status')) if v).lower()
            if not all(word in text for word in words):
                continue
        result.append(o)
    return result


def counts(orders):
    return {'requested': len(orders),
            'automatic': sum(bool(o.get('automatic') and o.get('filled')) for o in orders),
            'awaiting': sum(o.get('status') == 'awaiting approval' for o in orders),
            'blocked': sum(o.get('status') in ('blocked', 'denied') for o in orders)}


def daily_outcomes(orders, zone, days=DAILY_DAYS):
    """The last `days` days that had proposals, oldest first, as calendar days in `zone` (the viewer's time zone):
    [{day: 'YYYY-MM-DD', filled, review, blocked}]. An approved-then-filled order counts as both review and filled."""
    by_day = {}
    for o in orders:
        moment = parse_time(o.get('time'))
        if not moment:
            continue
        day = moment.astimezone(zone).date().isoformat()
        item = by_day.setdefault(day, {'day': day, 'filled': 0, 'review': 0, 'blocked': 0})
        item['filled'] += bool(o.get('filled'))
        item['review'] += reviewed(o)
        item['blocked'] += o.get('status') in ('blocked', 'denied')
    return [by_day[day] for day in sorted(by_day)][-days:]


def time_zone(name):
    """The viewer's IANA time zone, or UTC when the browser sent none or one this server does not know. The name
    becomes a file lookup, so only short IANA-shaped names are tried (a directory or an overlong name raises OSError)."""
    if not isinstance(name, str) or len(name) > 64 or not ZONE_NAME.fullmatch(name):
        return datetime.timezone.utc, 'UTC'
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return datetime.timezone.utc, 'UTC'


class BadRequest(ValueError):
    pass


def page(query, default=50):
    def number(key, fallback, low, high):
        raw = query.get(key, [''])[0]
        if raw == '':
            return fallback
        if not DIGITS.fullmatch(raw) or not low <= int(raw) <= high:  # ASCII only: str.isdigit() accepts '²' and '٣'
            raise BadRequest(f'{key} must be a whole number from {low} to {high}.')
        return int(raw)
    return number('offset', 0, 0, 10 ** 9), number('limit', default, 1, PAGE_LIMIT)


def route_path(path):
    """Whether route() answers this URL path."""
    return path in ('/api/orders', '/api/activity', '/api/events') or (path.startswith('/api/orders/') and path != '/api/orders/')


def governor_details(requests, decisions):
    """Allowlisted public governor details per governor proposal ID; never raw forecasts or batch inputs."""
    by_request, by_decision = {}, {}
    for r in requests:
        by_request.setdefault(r['governorProposalId'], r)
    for d in decisions:
        by_decision.setdefault(d['proposalId'], d)
    result = {}
    for proposal_id in by_request.keys() | by_decision.keys():
        proposal = by_request.get(proposal_id, {}).get('proposal', {})
        decision = by_decision.get(proposal_id, {})
        trace = decision.get('trace') or {}
        result[proposal_id] = {
            'maxBudgetUsd': proposal.get('requestedBudget'),
            'estimatedYesProbability': proposal.get('estimatedYesProbability'),
            **{k: decision.get(k) for k in ('decision', 'approvedBudgetUsd', 'contracts', 'limitPrice',
                                           'reasonCodes', 'reliabilityScore')},
            **{k: trace.get(k) for k in ('fullKelly', 'targetStake', 'headroom', 'stakeLimit')}}
    return result


def with_older_governors(governors, orders, history):
    """`governors` plus the details of any of `orders` whose governor records are older than the state's windows.
    Only a database keeps those (history, see route()); without one they are gone and the order shows none."""
    missing = sorted({o['governorProposalId'] for o in orders
                      if o.get('governorProposalId') and o['governorProposalId'] not in governors})
    if history is None or not missing:
        return governors
    return {**governors, **governor_details(*history.governor_records(missing))}


def route(s, governors, path, history=None):
    """The paged reads, for server.py and the UI fixture: (status, body) for a URL whose path route_path() accepts.
    `history` is the database the state was loaded from (store.PostgresStore), or None for a JSON workspace. The
    state holds only recent events and governor records; history answers for the older ones."""
    parts = urlsplit(path)
    query = parse_qs(parts.query)
    first = lambda key: query.get(key, [''])[0]
    orders = s.get('orders') or []
    try:
        if parts.path == '/api/orders':
            names = {a['id']: a.get('name') for a in s.get('agentConfigs') or []}
            filters = {key: first(key) for key in ('status', 'agent', 'category', 'decision', 'query')}
            matching = filter_orders(orders, filters, names)
            offset, limit = page(query)
            rows = matching[offset:offset + limit]
            governors = with_older_governors(governors, rows, history)
            return 200, {'orders': [list_order(o, governors) for o in rows],
                         'matching': len(matching), 'total': len(orders), 'offset': offset}
        if parts.path == '/api/activity':
            zone, name = time_zone(first('tz'))
            return 200, {'counts': counts(orders), 'daily': daily_outcomes(orders, zone), 'timeZone': name}
        if parts.path == '/api/events':
            offset, limit = page(query, RECENT_EVENTS)
            if history is not None:
                events, total = history.events_page(offset, limit)
            else:
                events = s.get('events') or []
                events, total = copy.deepcopy(events[offset:offset + limit]), len(events)
            return 200, {'events': events, 'total': total, 'offset': offset}
    except BadRequest as e:
        return 400, {'error': str(e)}
    order_id = unquote(parts.path[len('/api/orders/'):])
    order = next((o for o in orders if o.get('id') == order_id), None)
    if order is None:
        return 404, {'error': 'This order is not in your workspace.'}
    return 200, {'order': full_order(order, with_older_governors(governors, [order], history))}
