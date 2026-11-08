"""The outside world, each behind a Protocol with a Fake (architecture §4).

Fakes are the defaults so the whole loop runs offline; one env var per adapter goes live.
Adapters take explicit constructor arguments and never read settings themselves.
"""
