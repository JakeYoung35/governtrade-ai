"""The bounded order and event window in /api/state, and the paged reads behind Activity, Approvals and the drawer."""
import copy
import datetime as dt
import json
import unittest

import order_views
from order_views import counts, daily_outcomes, filter_orders, governor_details, list_order, route, time_zone
from tests.python.test_governor_service import ServiceTest

UTC = dt.timezone.utc
BLOCKED_EVALUATION = {'time': '2026-10-13T11:00:00+00:00', 'outcome': 'BLOCK', 'layers': {'agent_mandate': 'BLOCK'},
                      'checks': [{'name': 'Your rules', 'outcome': 'ALLOW', 'reason': ''},
                                 {'name': 'Agent mandate', 'outcome': 'BLOCK', 'reason': 'Trade exceeds the agent daily limit.'}]}


def order(order_id, **extra):
    return {'id': order_id, 'agent': 'a', 'accountId': 'account', 'status': 'filled', 'decision': 'ALLOW',
            'time': '2026-10-14T10:00:00+00:00', 'market': {'id': 'KXBTC-1', 'name': 'Bitcoin above $100k', 'agent': 'crypto',
                                                       'price': 0.5, 'quoteAt': '2026-10-14T10:00:00+00:00'},
            'request': 'Momentum.', 'direction': 'Yes', 'amount': 5, 'evaluations': [], 'filled': False, **extra}


ORDERS = [
    order('o1', filled=True, automatic=True),
    order('o2', agent='b', status='awaiting approval', decision='REQUIRE_APPROVAL', time='2026-10-13T09:00:00+00:00',
          market={'id': 'KXNFL-1', 'name': 'Eagles win', 'agent': 'sports'}, evaluations=[{'outcome': 'REQUIRE_APPROVAL'}]),
    order('o3', status='blocked', decision='BLOCK', time='2026-10-13T11:00:00+00:00', evaluations=[BLOCKED_EVALUATION]),
    order('o4', filled=True, automatic=False, evaluations=[{'outcome': 'REQUIRE_APPROVAL'}, {'outcome': 'ALLOW'}]),
    order('o5', status='denied', decision='REQUIRE_APPROVAL', time='not a time'),
]
NAMES = {'a': 'Rally', 'b': 'Scout'}


class FilterTests(unittest.TestCase):
    """A port of the browser's filters (formerly static/js/activity.js), now run on the server."""

    def ids(self, **filters):
        return [o['id'] for o in filter_orders(ORDERS, filters, NAMES)]

    def test_each_filter(self):
        self.assertEqual(self.ids(), ['o1', 'o2', 'o3', 'o4', 'o5'])
        self.assertEqual(self.ids(status='blocked'), ['o3'])
        self.assertEqual(self.ids(agent='b'), ['o2'])
        self.assertEqual(self.ids(category='sports'), ['o2'])
        self.assertEqual(self.ids(decision='BLOCK'), ['o3'])
        self.assertEqual(self.ids(status=''), ['o1', 'o2', 'o3', 'o4', 'o5'])  # an empty filter is no filter
        # An order without a category counts as "other" instead of dropping out of every filter.
        self.assertEqual([o['id'] for o in filter_orders([order('x', market={'id': 'm'})], {'category': 'other'}, NAMES)], ['x'])

    def test_search_matches_what_a_person_would_type(self):
        self.assertEqual(self.ids(query='eagles'), ['o2'])
        self.assertEqual(self.ids(query='scout'), ['o2'])  # the agent's name
        self.assertEqual(self.ids(query='O4'), ['o4'])  # the order ID, in any case
        self.assertEqual(self.ids(query='rally bitcoin'), ['o1', 'o3', 'o4', 'o5'])  # every word must match
        self.assertEqual(self.ids(query='status'), [])  # field names are not searched
        self.assertEqual(self.ids(query='denied'), ['o5'])  # statuses are
        self.assertEqual(self.ids(query='eagles', agent='a'), [])
        self.assertEqual(self.ids(query='   '), ['o1', 'o2', 'o3', 'o4', 'o5'])

    def test_search_reads_the_full_reasoning_not_the_preview(self):
        long = order('long', request='x' * 5000 + ' ensemble')
        self.assertEqual([o['id'] for o in filter_orders([long], {'query': 'ensemble'}, NAMES)], ['long'])


class SummaryTests(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(counts(ORDERS), {'requested': 5, 'automatic': 1, 'awaiting': 1, 'blocked': 2})
        self.assertEqual(counts([]), {'requested': 0, 'automatic': 0, 'awaiting': 0, 'blocked': 0})

    def test_daily_outcomes_in_utc(self):
        # Days in order; an approved-then-filled order counts in both; bad timestamps are skipped.
        self.assertEqual(daily_outcomes(ORDERS, UTC), [
            {'day': '2026-10-13', 'filled': 0, 'review': 1, 'blocked': 1},
            {'day': '2026-10-14', 'filled': 2, 'review': 1, 'blocked': 0}])
        many = [order(f'd{i}', time=f'2026-10-{i + 1:02d}T00:00:00+00:00') for i in range(10)]
        self.assertEqual([d['day'] for d in daily_outcomes(many, UTC)],
                         ['2026-10-04', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10'])
        self.assertEqual(daily_outcomes([], UTC), [])

    def test_days_are_the_viewers_calendar_days(self):
        late = [order('late', time='2026-10-14T02:00:00+00:00')]  # still the 13th in Los Angeles
        zone, name = time_zone('America/Los_Angeles')
        self.assertEqual(name, 'America/Los_Angeles')
        self.assertEqual([d['day'] for d in daily_outcomes(late, zone)], ['2026-10-13'])
        self.assertEqual([d['day'] for d in daily_outcomes(late, time_zone('Asia/Tokyo')[0])], ['2026-10-14'])
        for name in ('UTC', 'Etc/GMT+5', 'America/Argentina/Buenos_Aires'):
            self.assertEqual(time_zone(name)[1], name)

    def test_an_unknown_time_zone_falls_back_to_utc(self):
        # The name becomes a file lookup: directories, overlong names and path tricks must not raise.
        for name in ('', 'Not/AZone', '../../etc/passwd', None, 'America', 'a' * 5000, 'UTC\x00', 'Europe/London ',
                     'America/../America/New_York', '²'):
            with self.subTest(name=name):
                self.assertEqual(time_zone(name), (UTC, 'UTC'))

    def test_a_time_without_an_offset_is_utc(self):
        self.assertEqual([d['day'] for d in daily_outcomes([order('naive', time='2026-10-14T23:30:00')], UTC)], ['2026-10-14'])


class ListOrderTests(unittest.TestCase):
    GOVERNORS = {'g1': {'decision': 'resized', 'approvedBudgetUsd': '49.68', 'maxBudgetUsd': '100', 'targetStake': '73.18',
                        'headroom': {'cashLimit': '1000'}, 'reasonCodes': ['marketExposureCap']}}

    def test_a_row_has_no_evaluations_and_a_reasoning_preview(self):
        stored = order('o', request='y' * 8000, evaluations=[{'outcome': 'REQUIRE_APPROVAL'}], committed=True, venueOrderId='v-1')
        before = copy.deepcopy(stored)
        row = list_order(stored, {})
        self.assertEqual(stored, before)  # never changes the stored order
        self.assertNotIn('evaluations', row)
        self.assertNotIn('venueOrderId', row)
        self.assertEqual(row['request'], 'y' * order_views.REASONING_PREVIEW)
        self.assertEqual(row['requestLength'], 8000)
        self.assertTrue(row['reviewed'])
        self.assertEqual(row['market'], {'id': 'KXBTC-1', 'name': 'Bitcoin above $100k'})
        self.assertEqual(row['category'], 'crypto')

    def test_short_or_missing_reasoning(self):
        self.assertEqual(list_order(order('o'), {})['request'], 'Momentum.')
        self.assertEqual(list_order(order('o'), {})['requestLength'], 9)
        self.assertNotIn('request', list_order(order('o', request=''), {}))

    def test_a_blocked_row_says_why(self):
        self.assertEqual(list_order(ORDERS[2], {})['blockReason'], 'Agent mandate: Trade exceeds the agent daily limit.')
        self.assertNotIn('blockReason', list_order(ORDERS[0], {}))

    def test_full_governor_sizing_only_while_waiting(self):
        waiting = order('w', status='awaiting approval', governorProposalId='g1', governedTerms={'approveBy': '2026-10-14T10:05:00+00:00'})
        row = list_order(waiting, self.GOVERNORS)
        self.assertEqual(row['governor'], self.GOVERNORS['g1'])
        self.assertEqual(row['governedTerms'], {'approveBy': '2026-10-14T10:05:00+00:00'})
        done = list_order({**waiting, 'status': 'filled'}, self.GOVERNORS)
        self.assertEqual(done['governor'], {'decision': 'resized', 'approvedBudgetUsd': '49.68', 'maxBudgetUsd': '100'})
        self.assertNotIn('governedTerms', done)
        self.assertNotIn('governor', list_order(order('plain'), self.GOVERNORS))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.s = {'orders': copy.deepcopy(ORDERS), 'events': [{'id': f'e{i}', 'time': '2026-10-14T10:00:00+00:00', 'text': str(i),
                                                                'kind': 'UPDATED', 'trade': None} for i in range(45)],
                  'agentConfigs': [{'id': 'a', 'name': 'Rally'}, {'id': 'b', 'name': 'Scout'}]}

    def get(self, path):
        before = copy.deepcopy(self.s)
        status, body = route(self.s, {}, path)
        self.assertEqual(self.s, before)
        json.dumps(body, allow_nan=False)  # what server.py sends
        return status, body

    def test_route_paths(self):
        for path in ('/api/orders', '/api/orders/o1', '/api/activity', '/api/events'):
            self.assertTrue(order_views.route_path(path), path)
        for path in ('/api/orders/', '/api/state', '/api/ordersx', '/api/event'):
            self.assertFalse(order_views.route_path(path), path)

    def test_orders_are_filtered_and_paged(self):
        status, body = self.get('/api/orders?status=blocked')
        self.assertEqual(status, 200)
        self.assertEqual(([o['id'] for o in body['orders']], body['matching'], body['total']), (['o3'], 1, 5))
        self.assertNotIn('evaluations', body['orders'][0])
        status, body = self.get('/api/orders?query=rally%20bitcoin&offset=1&limit=2')
        self.assertEqual(([o['id'] for o in body['orders']], body['matching'], body['offset']), (['o3', 'o4'], 4, 1))
        self.assertEqual(self.get('/api/orders?offset=10')[1]['orders'], [])

    def test_bad_paging_is_refused(self):
        # Only ASCII digits: str.isdigit() would take '²' (and then fail) or '٣' (an Arabic-Indic 3).
        for query in ('limit=0', 'limit=101', 'limit=-1', 'offset=x', 'offset=1.5', 'limit=1e3', 'limit=%C2%B2', 'offset=%D9%A3',
                      'offset=99999999999'):
            with self.subTest(query=query):
                status, body = self.get(f'/api/orders?{query}')
                self.assertEqual(status, 400)
                self.assertIn('whole number', body['error'])

    def test_one_full_order(self):
        status, body = self.get('/api/orders/o3')
        self.assertEqual(status, 200)
        self.assertEqual(body['order']['evaluations'], [BLOCKED_EVALUATION])
        self.assertEqual(body['order']['market']['price'], 0.5)
        self.assertEqual(body['order']['category'], 'crypto')
        self.assertEqual(self.get('/api/orders/missing'), (404, {'error': 'This order is not in your workspace.'}))
        self.s['orders'].append(order('a/b c'))
        self.assertEqual(self.get('/api/orders/a%2Fb%20c')[1]['order']['id'], 'a/b c')

    def test_activity_summary(self):
        status, body = self.get('/api/activity?tz=America/Los_Angeles')
        self.assertEqual(status, 200)
        self.assertEqual(body['counts'], {'requested': 5, 'automatic': 1, 'awaiting': 1, 'blocked': 2})
        self.assertEqual(body['timeZone'], 'America/Los_Angeles')
        self.assertEqual([d['day'] for d in body['daily']], ['2026-10-13', '2026-10-14'])
        self.assertEqual(self.get('/api/activity')[1]['timeZone'], 'UTC')

    def test_events_are_paged(self):
        body = self.get('/api/events?offset=20')[1]
        self.assertEqual(([e['id'] for e in body['events']][:2], len(body['events']), body['total']), (['e20', 'e21'], 20, 45))
        self.assertEqual(len(self.get('/api/events?offset=40&limit=100')[1]['events']), 5)

    def test_a_database_answers_past_the_state_windows(self):
        """A Postgres workspace loads only recent events and governor records; route() asks the store for older ones."""
        class History:
            def __init__(self):
                self.asked = []

            def events_page(self, offset, limit):
                return [{'id': f'db{offset}'}], 900

            def governor_records(self, proposal_ids):
                self.asked.append(proposal_ids)
                return ([{'governorProposalId': 'old', 'proposal': {'requestedBudget': '100'}}],
                        [{'proposalId': 'old', 'decision': 'resized', 'approvedBudgetUsd': '40'}])
        history = History()
        self.assertEqual(route(self.s, {}, '/api/events?offset=600', history)[1], {'events': [{'id': 'db600'}], 'total': 900, 'offset': 600})
        self.s['orders'][0]['governorProposalId'] = 'old'
        self.s['orders'][1]['governorProposalId'] = 'recent'
        recent = {'recent': {'decision': 'approved', 'approvedBudgetUsd': '5', 'maxBudgetUsd': '5'}}
        rows = route(self.s, recent, '/api/orders', history)[1]['orders']
        self.assertEqual(rows[0]['governor'], {'decision': 'resized', 'approvedBudgetUsd': '40', 'maxBudgetUsd': '100'})
        self.assertEqual(rows[1]['governor'], recent['recent'])
        self.assertEqual(history.asked, [['old']])  # only what the state lacks
        drawer = route(self.s, recent, f"/api/orders/{self.s['orders'][0]['id']}", history)[1]['order']
        self.assertEqual(drawer['governor']['approvedBudgetUsd'], '40')
        self.assertNotIn('governor', route(self.s, recent, '/api/orders')[1]['orders'][0])  # a JSON workspace has no history

    def test_a_database_fills_in_a_governor_the_state_holds_only_half_of(self):
        """Requests and decisions load in separate windows, so the state can hold a delayed proposal's recent
        decision without its older request (or the reverse); the database supplies the missing half."""
        class History:
            def __init__(self):
                self.asked = []

            def governor_records(self, proposal_ids):
                self.asked.append(proposal_ids)
                return ([{'governorProposalId': p, 'proposal': {'requestedBudget': '100', 'estimatedYesProbability': '0.6'}}
                         for p in proposal_ids],
                        [{'proposalId': p, 'decision': 'resized', 'approvedBudgetUsd': '40'} for p in proposal_ids])
        history = History()
        for o, proposal_id in zip(self.s['orders'], ('no-request', 'no-decision', 'whole')):
            o['governorProposalId'] = proposal_id
        loaded = governor_details(
            [{'governorProposalId': p, 'proposal': {'requestedBudget': '100', 'estimatedYesProbability': '0.6'}}
             for p in ('no-decision', 'whole')],
            [{'proposalId': p, 'decision': 'resized', 'approvedBudgetUsd': '40'} for p in ('no-request', 'whole')])
        self.assertIsNone(loaded['no-request']['maxBudgetUsd'])
        for proposal_id in ('no-request', 'no-decision'):
            drawer = route(self.s, loaded, f"/api/orders/{next(o['id'] for o in self.s['orders'] if o['governorProposalId'] == proposal_id)}", history)
            governor = drawer[1]['order']['governor']
            self.assertEqual((governor['maxBudgetUsd'], governor['estimatedYesProbability'], governor['decision'],
                              governor['approvedBudgetUsd']), ('100', '0.6', 'resized', '40'))
        self.assertEqual(history.asked, [['no-request'], ['no-decision']])
        route(self.s, loaded, '/api/orders', history)
        self.assertEqual(history.asked[-1], ['no-decision', 'no-request'])  # never the whole one


class StateWindowTests(ServiceTest):
    def test_the_state_carries_a_window_not_every_order(self):
        base = order('x', agent='agent', accountId='account')
        self.s['orders'] = [{**base, 'id': f'n{i}', 'status': 'awaiting approval' if i % 2 else 'filled',
                             'time': (dt.datetime(2026, 10, 14, tzinfo=UTC) - dt.timedelta(minutes=i)).isoformat()} for i in range(130)]
        self.s['orders'].append({**base, 'id': 'other-agent', 'agent': 'agent-2', 'status': 'blocked', 'decision': 'BLOCK',
                                 'time': '2026-10-01T00:00:00+00:00'})
        self.s['events'] = [{'id': f'e{i}', 'time': '2026-10-14T00:00:00+00:00', 'text': 't', 'kind': 'UPDATED', 'trade': None} for i in range(30)]
        self.s['keys'] = {'agent:k': 'n0'}
        before = copy.deepcopy(self.s)
        public = self.workspace.public(self.s)
        self.assertEqual(self.s, before)
        for key in ('orders', 'keys'):
            self.assertNotIn(key, public)
        self.assertEqual([o['id'] for o in public['recentOrders']], [f'n{i}' for i in range(order_views.RECENT_ORDERS)])
        self.assertEqual(len(public['pendingOrders']), order_views.PENDING_ORDERS)
        self.assertTrue(all(o['status'] == 'awaiting approval' for o in public['pendingOrders']))
        self.assertEqual(public['pendingOrders'][0]['id'], 'n1')  # newest first, like the stored list
        self.assertEqual(public['orderStats'], {'total': 131, 'awaiting': 65, 'inFlight': 65})
        self.assertEqual([e['id'] for e in public['events']], [f'e{i}' for i in range(order_views.RECENT_EVENTS)])
        self.assertEqual(public['eventCount'], 30)
        agents = {a['id']: a['activity'] for a in public['agentConfigs']}
        self.assertEqual(agents['agent'], {'awaiting': 65, 'lastOrder': {'time': '2026-10-14T00:00:00+00:00', 'decision': 'ALLOW', 'status': 'filled'}})
        self.assertEqual(agents['agent-2'], {'awaiting': 0, 'lastOrder': {'time': '2026-10-01T00:00:00+00:00', 'decision': 'BLOCK', 'status': 'blocked'}})

    def test_an_agent_without_orders(self):
        self.s['orders'] = []
        self.assertEqual({a['activity']['lastOrder'] for a in self.workspace.public(self.s)['agentConfigs']}, {None})

    def test_worst_case_state_stays_small(self):
        """1,200 orders with 8,000-character reasoning and full evaluations: the poll stays far under 300 KB."""
        self.submit()
        self.service.run_batch(self.s)
        template = self.s['orders'][0]
        reasoning = ('NOAA and three ensemble models agree on a warm front; https://www.weather.gov/forecasts/xml/ ' * 100)[:8000]
        statuses = ['awaiting approval', 'filled', 'blocked', 'denied', 'submission unknown', 'expired', 'partially filled', 'cancelled']
        self.s['orders'] = [{**copy.deepcopy(template), 'id': f'worst-{i:04d}-7f3c9a2e-41b8-4d6e-9c1a-{i:012d}', 'request': reasoning,
                             'status': statuses[i % 8], 'evaluations': [BLOCKED_EVALUATION] * 3} for i in range(1200)]
        self.s['events'] = [{'id': f'e{i}', 'time': '2026-10-14T00:00:00+00:00', 'text': 'x' * 200, 'kind': 'UPDATED', 'trade': None}
                            for i in range(300)]
        size = len(json.dumps(self.workspace.public(self.s)))
        self.assertLess(size, 300_000)


if __name__ == '__main__':
    unittest.main()
