"""LangChain client factory for Bedrock LLM and Embeddings.

Provides factory functions for creating configured LangChain clients and a
credential probe that checks AWS credential availability at startup.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.4
"""

from __future__ import annotations

import botocore.session
from botocore.config import Config as BotoConfig
from langchain_aws import BedrockEmbeddings, ChatBedrock

from app.config import Settings


def create_chat_model(settings: Settings) -> ChatBedrock:
    """Create a configured ChatBedrock instance for Claude invocations.

    Uses the default AWS credential chain (environment variables, IAM role,
    config file, etc.) — never hardcodes credentials.

    Args:
        settings: Application settings providing model_id, region, and timeout.

    Returns:
        A ChatBedrock instance configured with retries and timeout.
    """
    timeout_s = int(settings.llm_chunk_timeout_ms / 1000)
    return ChatBedrock(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
        config=BotoConfig(
            retries={"max_attempts": 2, "mode": "standard"},
            read_timeout=timeout_s,
            connect_timeout=timeout_s,
        ),
    )


def create_embeddings(settings: Settings) -> BedrockEmbeddings:
    """Create a configured BedrockEmbeddings instance for Titan Text Embeddings V2.

    Args:
        settings: Application settings providing the AWS region.

    Returns:
        A BedrockEmbeddings instance for generating document embeddings.
    """
    return BedrockEmbeddings(
        model_id="amazon.titan-embed-text-v2:0",
        region_name=settings.aws_region,
    )


def probe_credentials() -> bool:
    """Check whether AWS credentials are resolvable.

    Uses ``botocore.session.get_session().get_credentials()`` to determine
    if credentials exist through the default credential chain (environment
    variables, IAM role, config file, etc.).

    Returns:
        True if credentials are found and have an access key, False otherwise.
        Must never throw — catches all exceptions and returns False.
    """
    try:
        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is None:
            return False
        resolved = credentials.get_frozen_credentials()
        return resolved is not None and resolved.access_key is not None
    except Exception:
        return False
