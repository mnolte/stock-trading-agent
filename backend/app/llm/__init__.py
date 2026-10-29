"""One model client interface for every task (docs/SPEC.md → Model
providers: OpenAI and Anthropic).

Engine code talks to the interface only: send a prompt, a snapshot and a
JSON schema; get back the response (or a typed error). Provider SDKs are
imported only inside ``app/llm/providers/`` (hard rule 21).
"""
