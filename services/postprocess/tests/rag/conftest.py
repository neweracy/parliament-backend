"""Shared fixtures for RAG tests — mocks for LangChain components."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from app.config import Settings


@pytest.fixture
def mock_settings():
    """Create a Settings instance with test defaults."""
    return Settings(
        service_token="test-token",
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        llm_enabled=True,
        aws_region="us-east-1",
        bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        llm_chunk_timeout_ms=15000,
    )


@pytest.fixture
def mock_chat_model():
    """Mock ChatBedrock that returns a canned AIMessage."""
    chat_model = AsyncMock()
    chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(
            content=(
                "Test answer [1].\n\n"
                "RECOMMENDATIONS:\n"
                "- Follow-up question 1 | Reason 1\n"
                "- Follow-up question 2 | Reason 2\n"
                "- Follow-up question 3 | Reason 3"
            )
        )
    )
    return chat_model


@pytest.fixture
def mock_embeddings():
    """Mock BedrockEmbeddings that returns fixed vectors."""
    embeddings = AsyncMock()
    embeddings.aembed_query = AsyncMock(return_value=[0.1] * 1024)
    embeddings.aembed_documents = AsyncMock(return_value=[[0.1] * 1024])
    return embeddings


@pytest.fixture
def mock_session_factory():
    """Mock async session factory for DB operations."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: [], fetchone=lambda: None))

    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=context_manager)
    return factory


@pytest.fixture
def sample_documents():
    """Sample Document objects for testing."""
    return [
        Document(
            page_content="The budget was discussed in parliament.",
            metadata={
                "chunk_id": 1,
                "transcript_id": 100,
                "speaker": "Hon. Doe",
                "start_s": 10.0,
                "end_s": 25.0,
                "entity_names": ["budget", "parliament"],
                "score": 0.85,
                "record_title": "Budget Debate",
                "sitting_title": "3rd Sitting",
                "date": "2024-01-15",
            },
        ),
        Document(
            page_content="Education funding was a key priority.",
            metadata={
                "chunk_id": 2,
                "transcript_id": 101,
                "speaker": "Hon. Smith",
                "start_s": 30.0,
                "end_s": 45.0,
                "entity_names": ["education", "funding"],
                "score": 0.72,
                "record_title": "Education Committee",
                "sitting_title": "4th Sitting",
                "date": "2024-01-16",
            },
        ),
        Document(
            page_content="The minister responded to questions.",
            metadata={
                "chunk_id": 3,
                "transcript_id": 100,
                "speaker": "Minister Johnson",
                "start_s": 50.0,
                "end_s": 65.0,
                "entity_names": ["minister"],
                "score": 0.60,
                "record_title": "Budget Debate",
                "sitting_title": "3rd Sitting",
                "date": "2024-01-15",
            },
        ),
    ]


@pytest.fixture
def sample_retrieved_chunks():
    """Sample RetrievedChunk objects for testing."""
    from app.rag.retriever import RetrievedChunk

    return [
        RetrievedChunk(
            chunk_id=1,
            text="The budget was discussed in parliament.",
            relevance_score=0.85,
            transcript_id=100,
            speaker="Hon. Doe",
            start_s=10.0,
            end_s=25.0,
            matched_entities=["budget", "parliament"],
            record_title="Budget Debate",
            sitting_title="3rd Sitting",
            date="2024-01-15",
        ),
        RetrievedChunk(
            chunk_id=2,
            text="Education funding was a key priority.",
            relevance_score=0.72,
            transcript_id=101,
            speaker="Hon. Smith",
            start_s=30.0,
            end_s=45.0,
            matched_entities=["education", "funding"],
            record_title="Education Committee",
            sitting_title="4th Sitting",
            date="2024-01-16",
        ),
    ]
