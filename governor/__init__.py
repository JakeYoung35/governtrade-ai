"""Deterministic portfolio governor: sizes, ranks and caps agent proposals.

Pure decision logic only. No network, storage or clock access happens in this
package; callers pass frozen snapshots and the evaluation time explicitly.
"""
