"""Anthropic implementation of report.llm_interface.StructuredLLM -- the
ONLY file in this project that imports the `anthropic` SDK. Model selection
is entirely config-driven (LLM_MODEL via config.py, default 'claude-opus-5')
-- changing the model is a .env edit, never a code change here or anywhere
above this file.

Uses output_config.format (json_schema) for structured output rather than
tool-forced JSON or prefill -- prefill is unsupported on current-generation
models, and this guarantees response.content is valid JSON matching the
schema without a parse-and-retry loop.
"""

import json

import anthropic

from config import get_optional_env, get_required_env
from report.llm_interface import LLMResponse, StructuredLLM

DEFAULT_MODEL = "claude-opus-5"
_MAX_TOKENS = 4096


class AnthropicStructuredLLM(StructuredLLM):
    def __init__(self, model=None, api_key=None):
        self._model = model or get_optional_env("LLM_MODEL", DEFAULT_MODEL)
        self._client = anthropic.Anthropic(api_key=api_key or get_required_env("ANTHROPIC_API_KEY"))

    def generate_structured(self, system_prompt, user_prompt, schema):
        response = self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = next(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            parsed=json.loads(text),
            raw_text=text,
            model_used=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
