"""LLM provider abstraction boundary. report/ai_synthesis.py (and everything
above it: orchestrator.py/validation.py/persistence.py) depends ONLY on
StructuredLLM/LLMResponse/build_llm() from this file -- never on the
`anthropic` SDK, never on a model string literal. Swapping providers or
models is a config change (LLM_PROVIDER/LLM_MODEL in .env) plus, for a new
provider, a new llm_<provider>.py implementing StructuredLLM -- zero changes
to ai_synthesis.py, orchestrator.py, validation.py, or persistence.py.

build_llm() lives here, not in llm_anthropic.py, so this module stays the
single provider-neutral entry point: the `anthropic` import only happens
inside the branch that actually selects it.
"""

from abc import ABC, abstractmethod

from config import get_optional_env


class LLMResponse:
    """Provider-neutral result of one structured-output call."""

    __slots__ = ("parsed", "raw_text", "model_used", "input_tokens", "output_tokens")

    def __init__(self, parsed, raw_text, model_used, input_tokens, output_tokens):
        self.parsed = parsed
        self.raw_text = raw_text
        self.model_used = model_used
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class StructuredLLM(ABC):
    @abstractmethod
    def generate_structured(self, system_prompt, user_prompt, schema):
        """Returns an LLMResponse whose `.parsed` is a dict conforming to
        `schema` (a JSON Schema object). Implementations are responsible for
        actually enforcing the schema (e.g. via the provider's structured
        output feature) -- callers do not re-validate JSON-shape here, only
        the domain rules in report.validation."""


def build_llm():
    """Factory: LLM_PROVIDER (env, default 'anthropic') selects the
    implementation. 'anthropic' (direct Anthropic Python SDK,
    ANTHROPIC_API_KEY, paid/PAYG) and 'claude_cli' (report.llm_claude_cli's
    subscription-authenticated `claude -p` subprocess adapter, no API key,
    no direct api.anthropic.com call) are supported. Any other value is a
    configuration error, raised loudly rather than silently falling back to
    a default provider.

    SUPER_NEWS_NO_PAID_API (env, same guard report.translation.
    build_translation_provider() already checks) is enforced HERE too: if
    it's truthy and the resolved provider is 'anthropic', this refuses to
    construct a paid client at all -- raises rather than silently spending.
    This is defense in depth on top of any caller (e.g. scripts/run_daily_
    full_pipeline_v2.py) that forces LLM_PROVIDER=claude_cli into its own
    subprocess environment: even a stray direct call into this factory
    while the guard is active can never reach a paid provider."""
    no_paid_api = (get_optional_env("SUPER_NEWS_NO_PAID_API", "") or "").strip().lower() in ("1", "true", "yes")
    provider = get_optional_env("LLM_PROVIDER", "anthropic")

    if provider == "claude_cli":
        from report.llm_claude_cli import ClaudeCLIStructuredLLM

        return ClaudeCLIStructuredLLM()

    if provider == "anthropic":
        if no_paid_api:
            raise RuntimeError(
                "SUPER_NEWS_NO_PAID_API=1 is set but LLM_PROVIDER='anthropic' (paid, direct "
                "api.anthropic.com) -- refusing to construct a paid LLM client. Set "
                "LLM_PROVIDER=claude_cli to use the authenticated Claude Code subscription "
                "CLI instead, or unset SUPER_NEWS_NO_PAID_API to allow paid calls."
            )
        from report.llm_anthropic import AnthropicStructuredLLM

        return AnthropicStructuredLLM()

    raise ValueError(f"Unsupported LLM_PROVIDER={provider!r}; only 'anthropic'/'claude_cli' are supported in V1.")
