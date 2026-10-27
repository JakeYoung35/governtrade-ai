"""Read models for Home: no mutation, credentials, network reads or trading."""
import copy
import datetime as dt
import unittest
import order_views
from governor_service import GovernorService
from tests.python.test_governor_service import ServiceTest
from governor.breaker import BreakerState
from governor import records

class HomeSummaryTests(ServiceTest):
    def test_unknown_balance_and_read_only(self):
        before = copy.deepcopy(self.s)
        public = self.workspace.public(self.s)
        self.assertEqual(self.s, before)
        self.assertIsNone(public['summary']['accountValue'])
        self.assertIsNone(public['accounts'][0]['summary']['atRisk'])
        self.assertNotIn('credentialRef', public['accounts'][0])

    def test_orders_carry_a_normalised_category(self):
        base = {'agent': 'agent', 'accountId': self.s['accounts'][0]['id'], 'direction': 'Yes', 'amount': 5,
                'status': 'blocked', 'time': '2026-10-14T16:00:00+00:00', 'evaluations': []}
        self.s['orders'] = [{**base, 'id': 'kalshi', 'market': {'id': 'm', 'agent': 'sports'}},
                            {**base, 'id': 'named', 'market': {'id': 'm', 'category': 'crypto'}},
                            {**base, 'id': 'unknown', 'market': {'id': 'm', 'agent': 'agent-7'}},
                            {**base, 'id': 'older', 'market': {'id': 'm'}}]
        public = self.workspace.public(self.s)
        self.assertEqual({o['id']: o['category'] for o in public['recentOrders']},
                         {'kalshi': 'sports', 'named': 'crypto', 'unknown': 'other', 'older': 'other'})
        self.assertNotIn('category', self.s['orders'][0])  # public() never changes stored orders

    def test_free_agent_quota_uses_the_servers_utc_day(self):
        from unittest.mock import patch
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        with patch('workspace.catalog', return_value={'governtrade-free': {'free': True, 'dailyMessages': 20}}):
            self.s['freeAgentUsage'] = {'day': today, 'messages': 19}
            self.assertEqual(self.workspace.public(self.s)['freeAgentQuota'], {'day': today, 'used': 19, 'limit': 20, 'left': 1})
            self.s['freeAgentUsage'] = {'day': '2000-01-01', 'messages': 20}  # an earlier day no longer counts
            self.assertEqual(self.workspace.public(self.s)['freeAgentQuota']['left'], 20)
            self.s['freeAgentUsage'] = {'day': today, 'messages': 25}
            self.assertEqual(self.workspace.public(self.s)['freeAgentQuota']['left'], 0)
        with patch('workspace.catalog', return_value={}):
            self.assertNotIn('freeAgentQuota', self.workspace.public(self.s))

    def test_governor_view_uses_requested_budget_and_allowlisted_trace(self):
        self.submit()
        self.service.run_batch(self.s)
        before = copy.deepcopy(self.s)
        public = self.workspace.public(self.s)
        stored = next(o for o in self.s['orders'] if o.get('governorProposalId'))
        # The drawer's full order carries the allowlisted trace; the state's row carries it only while it waits.
        status, body = order_views.route(self.s, self.service.order_governors(self.s), f"/api/orders/{stored['id']}")
        self.assertEqual(status, 200)
        g = body['order']['governor']
        self.assertEqual(float(g['maxBudgetUsd']), 20)
        self.assertIn('targetStake', g)
        self.assertIn('headroom', g)
        self.assertNotIn('fills', g)
        row = next(o for o in public['recentOrders'] if o['id'] == stored['id'])
        self.assertEqual(row['governor'], g if stored['status'] == 'awaiting approval'
                         else {k: g.get(k) for k in order_views.GOVERNOR_SUMMARY})
        self.assertFalse(any(k.startswith('governor') for k in public))
        self.assertEqual(self.s, before)

    def test_exposure_expiry_daily_spending_and_loss_rebase(self):
        now = dt.datetime(2026, 10, 14, 16, tzinfo=dt.timezone.utc)
        a = self.s['accounts'][0]
        a['balance'] = {'cash': 900, 'reserved': 0, 'positionValue': 100, 'asOf': now.isoformat(),
                        'positions': [{'ticker': 'm', 'position_fp': '10', 'market_exposure_dollars': '80'}]}
        a['baseline'] = {'day': '2026-10-14', 'equity': 1000}
        base = {'agent': 'agent', 'accountId': a['id'], 'market': {'id': 'm'}, 'direction': 'Yes',
                'amount': 20, 'status': 'awaiting approval', 'time': now.isoformat()}
        self.s['orders'] = [{**base, 'id': 'pending'}, {**base, 'id': 'expired',
            'governedTerms': {'approveBy': (now-dt.timedelta(seconds=1)).isoformat()}},
            {**base, 'id': 'filled', 'status': 'filled', 'committed': True, 'filled': True}]
        self.s['clock'] = '2026-10-13T16:00:00+00:00'
        before = copy.deepcopy(self.s)
        summary = self.service.account_summary(self.s, a, now)
        self.assertEqual(summary['spentToday'], 20)
        self.assertEqual(summary['atRisk'], 100)
        policy = self.service.policy_for(self.s)
        self.assertAlmostEqual(summary['lossHeadroom'], 1000 * float(policy.daily_loss_limit))
        self.assertEqual(summary['exposureCap'], 1000 * float(policy.total_cap))
        self.assertEqual(self.s, before)
        records.save_breaker(self.s, a['id'], BreakerState(rebase_day='2026-10-14', rebase_equity=950))
        rebased = self.service.account_summary(self.s, a, now)
        self.assertAlmostEqual(rebased['lossHeadroom'], 1000 - 950 * (1 - float(policy.daily_loss_limit)))
        a['baseline']['day'] = '2026-10-13'
        self.s.pop('governor', None)
        self.assertIsNone(self.service.account_summary(self.s, a, now)['lossHeadroom'])

    def test_loss_read_does_not_latch_and_unknown_total_is_not_partial(self):
        now = dt.datetime.now(dt.timezone.utc)
        a = self.s['accounts'][0]
        a['balance'] = {'cash': 1, 'positionValue': 0}
        a['baseline'] = {'day': now.date().isoformat(), 'equity': 1000}
        self.s['accounts'].append({**a, 'id': 'unknown', 'balance': None})
        before = copy.deepcopy(self.s)
        public = self.workspace.public(self.s)
        self.assertEqual(public['accounts'][0]['summary']['lossHeadroom'], 0)
        self.assertTrue(public['summary']['breakerLatched'])
        self.assertIsNone(public['summary']['accountValue'])
        self.assertEqual(before, self.s)

    TOTALS = ('accountValue', 'spentToday', 'dailyCap', 'atRisk', 'exposureCap', 'lossHeadroom')

    def healthy_account(self, **overrides):
        account = {**self.s['accounts'][0], 'status': 'connected', 'stale': False, 'error': None,
                   'balance': {'cash': 900, 'reserved': 0, 'positionValue': 100, 'positions': []}, **overrides}
        return account

    def own_summary(self, public, account_id):
        return next(a['summary'] for a in public['accounts'] if a['id'] == account_id)

    def test_errored_account_is_left_out_of_totals_and_counted(self):
        healthy = self.healthy_account(id='healthy')
        failed = self.healthy_account(id='failed', status='error', stale=True, balance=None,
                                      error='Balance sync failed.')
        self.s['accounts'] = [healthy, failed]
        public = self.workspace.public(self.s)
        own = self.own_summary(public, 'healthy')
        self.assertIsNotNone(own['accountValue'])
        for key in self.TOTALS:
            self.assertEqual(public['summary'][key], own[key])
        self.assertEqual(public['summary']['excludedAccounts'], 1)
        self.assertFalse(public['summary']['stale'])

    def test_disconnected_account_is_left_out_without_being_counted(self):
        healthy = self.healthy_account(id='healthy')
        disconnected = self.healthy_account(id='off', status='disconnected', stale=True, balance=None)
        self.s['accounts'] = [healthy, disconnected]
        public = self.workspace.public(self.s)
        own = self.own_summary(public, 'healthy')
        for key in self.TOTALS:
            self.assertEqual(public['summary'][key], own[key])
        self.assertEqual(public['summary']['excludedAccounts'], 0)

    def test_stale_account_is_counted_and_marks_totals_stale(self):
        healthy = self.healthy_account(id='healthy')
        stale = self.healthy_account(id='stale', stale=True, balance={'cash': 50, 'reserved': 0, 'positionValue': 0})
        self.s['accounts'] = [healthy, stale]
        public = self.workspace.public(self.s)
        self.assertEqual(public['summary']['accountValue'],
                         self.own_summary(public, 'healthy')['accountValue'] + 50)
        self.assertTrue(public['summary']['stale'])
        self.assertEqual(public['summary']['excludedAccounts'], 0)

    def test_only_errored_accounts_leave_totals_null(self):
        self.s['accounts'] = [self.healthy_account(id='one', status='error', stale=True, balance=None),
                              self.healthy_account(id='two', status='error', stale=True, balance=None)]
        public = self.workspace.public(self.s)
        for key in self.TOTALS:
            self.assertIsNone(public['summary'][key])
        self.assertEqual(public['summary']['excludedAccounts'], 2)

if __name__ == '__main__': unittest.main()
