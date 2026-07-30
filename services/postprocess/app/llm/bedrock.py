"""Amazon Bedrock client for LLM refinement.

Uses the default boto3 credential chain (IAM role, environment variables, etc.)
— never hardcodes AWS access keys. Probes credential availability once at
startup to determine whether LLM refinement is available.

Requirements: 12.1, 12.2, 12.3, 12.5, 11.2, 11.8
"""

from __future__ import annotations

import json

import boto3
import botocore.session
from botocore.config import Config

from app.config import Settings

# Module-level flag set once at import/startup time
_credentials_available: bool = False


def probe_credentials() -> bool:
    """Check whether AWS credentials are resolvable.

    Uses ``botocore.session.get_session().get_credentials()`` to determine
    if credentials exist through the default credential chain (environment
    variables, IAM role, config file, etc.).

    Returns True if credentials are found, False otherwise.
    Must never throw — catches all exceptions and returns False.
    """
    try:
        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is None:
            return False
        # Attempt to resolve frozen credentials to verify they're real
        resolved = credentials.get_frozen_credentials()
        return resolved is not None and resolved.access_key is not None
    except Exception:
        return False


class BedrockClient:
    """Thin wrapper around boto3 Bedrock Runtime for Claude invocations.

    Creates a ``boto3.client("bedrock-runtime")`` using the default credential
    chain with botocore Config for timeouts and bounded retries.
    """

    def __init__(self, settings: Settings) -> None:
        self._model_id = settings.bedrock_model_id
        self._region = settings.aws_region

        timeout_seconds = settings.llm_chunk_timeout_ms / 1000.0

        config = Config(
            region_name=self._region,
            read_timeout=timeout_seconds,
            connect_timeout=min(timeout_seconds, 10.0),
            retries={"max_attempts": 2, "mode": "standard"},
        )

        # No explicit credentials — uses the default credential chain
        # (environment variables, IAM role, config file, etc.)
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            config=config,
        )

        # Cache whether credentials were available at construction time
        self._configured = probe_credentials()

    @property
    def is_configured(self) -> bool:
        """Return whether credentials were found at probe time."""
        return self._configured

    @property
    def model_id(self) -> str:
        """Return the configured Bedrock model identifier."""
        return self._model_id

    def invoke(self, system_prompt: str, user_content: str) -> str:
        """Invoke the configured Bedrock model with Claude messages API format.

        Args:
            system_prompt: The system-level instructions for the model.
            user_content: The user message content to process.

        Returns:
            The text content from the model response.

        Raises:
            botocore.exceptions.ClientError: On AWS API failures.
            KeyError/IndexError: On unexpected response structure.
        """
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        })

        response = self._client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )

        response_body = json.loads(response["body"].read())
        return response_body["content"][0]["text"]
