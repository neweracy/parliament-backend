"""Tests for app/rag/clients.py — LangChain client factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_aws import BedrockEmbeddings, ChatBedrock

from app.rag.clients import create_chat_model, create_embeddings, probe_credentials


def test_create_chat_model(mock_settings):
    """create_chat_model returns a ChatBedrock with the configured model_id and region."""
    model = create_chat_model(mock_settings)
    assert isinstance(model, ChatBedrock)
    assert model.model_id == mock_settings.bedrock_model_id
    assert model.region_name == mock_settings.aws_region
    # Verify retry/timeout config is set via botocore Config
    assert model.config is not None
    # max_attempts dropped from 2 to 1 (botocore counts retries on top of the
    # initial call, so total_max_attempts is 2 rather than the previous 3).
    # A read timeout means the model was still generating, so extra re-sends
    # only burn the agent's wall-clock budget.
    assert model.config.retries["total_max_attempts"] == 2
    # Read timeout comes from rag_model_timeout_s, not the refiner's
    # llm_chunk_timeout_ms (15s), which was too short for cited RAG answers.
    assert model.config.read_timeout == mock_settings.rag_model_timeout_s
    assert model.config.connect_timeout == 10


def test_create_embeddings(mock_settings):
    """create_embeddings returns a BedrockEmbeddings with Titan V2 model."""
    emb = create_embeddings(mock_settings)
    assert isinstance(emb, BedrockEmbeddings)
    assert emb.model_id == "amazon.titan-embed-text-v2:0"
    assert emb.region_name == mock_settings.aws_region


@patch("app.rag.clients.botocore.session.get_session")
def test_probe_credentials_found(mock_get_session):
    """probe_credentials returns True when credentials are resolvable."""
    mock_creds = MagicMock()
    mock_creds.get_frozen_credentials.return_value = MagicMock(access_key="AKIA...")
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = mock_creds
    mock_get_session.return_value = mock_session

    assert probe_credentials() is True


@patch("app.rag.clients.botocore.session.get_session")
def test_probe_credentials_not_found(mock_get_session):
    """probe_credentials returns False when no credentials are available."""
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = None
    mock_get_session.return_value = mock_session

    assert probe_credentials() is False


@patch("app.rag.clients.botocore.session.get_session")
def test_probe_credentials_exception(mock_get_session):
    """probe_credentials returns False when an exception occurs."""
    mock_get_session.side_effect = Exception("Something went wrong")

    assert probe_credentials() is False
