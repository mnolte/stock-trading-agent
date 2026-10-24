"""LLM call history (docs/SPEC.md, LLM call history — PR 18).

One ``llm_calls`` row per request actually sent to OpenAI by live
decisions, shadow strategies or reviews. This package holds the pure
outcome rule, the row builder used by all three writers and the
backfill/helpers the migration and API share.
"""
