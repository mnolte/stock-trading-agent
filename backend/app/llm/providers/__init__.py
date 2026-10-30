"""One adapter per provider (docs/SPEC.md → Model providers).

Provider SDKs may be imported only in this package (hard rule 21); a
test fails when ``openai`` or ``anthropic`` is imported anywhere else.
"""
