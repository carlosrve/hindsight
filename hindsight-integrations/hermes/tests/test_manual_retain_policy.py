"""Directed release acceptance with simulated clients; no live API or LLM."""

import pytest


@pytest.mark.parametrize("retain_async", [False, True])
def test_manual_retain_must_forward_the_configured_policy(provider, retain_async):
    instance, client = provider({"retain_async": retain_async})
    try:
        instance._tool_retain({"content": "Synthetic manual memory"})
        assert client.retains[-1].get("retain_async") is retain_async
    finally:
        instance.shutdown()
