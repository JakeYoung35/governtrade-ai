"""Read models for Home: no mutation, credentials, network reads or trading."""
import copy
import datetime as dt
import unittest
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

    def test_governor_view_uses_requested_budget_and_allowlisted_trace(self):
        self.submit()
        self.service.run_batch(self.s)
        before = copy.deepcopy(self.s)
        public = self.workspace.public(self.s)
        order = next(o for o in public['orders'] if o.get('governorProposalId'))
        g = order['governor']
        self.assertEqual(float(g['maxBudgetUsd']), 20)
        self.assertIn('targetStake', g)
        self.assertIn('headroom', g)
        self.assertNotIn('fills', g)
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

if __name__ == '__main__': unittest.main()
