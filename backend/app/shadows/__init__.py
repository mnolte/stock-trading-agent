"""Shadow strategies (docs/SPEC.md, Strategies and shadows, PR 13).

Shadows decide on the live cycle's stored input snapshots and trade a
virtual portfolio on cached 5-min bars. This package never imports or
receives a broker client: ``tests/test_shadow_isolation.py`` fails the
build if any module here can reach one.
"""
