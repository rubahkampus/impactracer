"""
LLM Client -- Google Gemini Structured-Output Wrapper
======================================================

RESPONSIBILITY
    Single, shared entry point for all three LLM calls in the pipeline
    (interpreter, validator, synthesizer).  Wraps the google-genai SDK
    (>= 1.0) to provide:

      - Structured JSON output enforced via response_schema.
      - Deterministic behaviour via temperature=0.0 (greedy decoding).
      - Automatic JSON -> Pydantic model coercion via response.parsed
        with a model_validate_json fallback for robustness.

USAGE
    from impactracer.llm_client import GeminiClient
    from impactracer.models import CRInterpretation

    client = GeminiClient(settings)
    result: CRInterpretation = client.parse(
        system_prompt="...",
        user_prompt=cr_text,
        schema=CRInterpretation,
    )

DESIGN NOTES
    Gemini does not expose an explicit seed parameter (unlike OpenAI).
    Determinism is achieved solely through temperature=0.0, which
    activates greedy decoding.  Per Google documentation, greedy
    decoding produces identical outputs for identical inputs on a
    stable model checkpoint.

    The google-genai SDK (>= 1.0) populates response.parsed as a
    Pydantic model instance when response_schema is a BaseModel
    subclass.  A model_validate_json fallback is retained for safety.

ARCHITECTURAL CONSTRAINTS
    1. All three LLM calls use this class exclusively.  No other
       module may instantiate a genai.Client directly.
    2. temperature is read from Settings (enforced 0.0 per NFR-07).
    3. google_api_key is read from Settings / .env.  Never hardcoded.
    4. Zero BFS or retrieval logic lives here.  Pure I/O wrapper.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

if TYPE_CHECKING:
    from impactracer.config import Settings

T = TypeVar("T", bound=BaseModel)


class GeminiClient:
    """Thread-safe wrapper around google-genai for structured LLM calls.

    Instantiate once per pipeline run (in runner.py) and pass to all
    three pipeline functions.  The underlying genai.Client is stateless
    between calls so a single instance is safe to share.
    """

    def __init__(self, settings: "Settings") -> None:
        if not settings.google_api_key:
            raise ValueError(
                "GOOGLE_API_KEY is not set.  Add it to your .env file."
            )
        self._client = genai.Client(api_key=settings.google_api_key)
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Type[T],
    ) -> T:
        """Make a single structured LLM call and return a validated model.

        Sends (system_prompt, user_prompt) to Gemini with
        response_schema=schema so the model is constrained to return
        only fields defined in the Pydantic schema.

        Args:
            system_prompt: Role and instruction context.
            user_prompt:   The document or query to process.
            schema:        A Pydantic BaseModel subclass.  The LLM
                           response will be coerced into an instance
                           of this class.

        Returns:
            A validated instance of ``schema``.

        Raises:
            google.genai.errors.APIError:  Network or quota failure.
            pydantic.ValidationError:      If the LLM response cannot
                                           be coerced into the schema
                                           (should not happen in strict
                                           structured-output mode).
        """
        response = self._client.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=self._temperature,
            ),
        )

        # SDK >= 1.0 populates response.parsed as a Pydantic model
        # instance when response_schema is a BaseModel subclass.
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, schema):
            return parsed  # type: ignore[return-value]

        # Fallback: manually validate the raw JSON text.
        # This handles older SDK behaviour or unexpected None in parsed.
        return schema.model_validate_json(response.text)
