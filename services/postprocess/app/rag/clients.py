"""LangChain client factory for Bedrock LLM and Embeddings.

Provides factory functions for creating configured LangChain clients and a
credential probe that checks AWS credential availability at startup.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.4
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

import structlog
from botocore.config import Config as BotoConfig
from langchain_aws import BedrockEmbeddings, ChatBedrock

from app.aws_utils import probe_credentials
from app.config import Settings

logger = structlog.get_logger("rag.clients")


def create_chat_model(settings: Settings) -> ChatBedrock:
    """Create a configured ChatBedrock instance for RAG Q&A invocations.

    Uses ``rag_model_id`` when set, falling back to ``bedrock_model_id``.
    This lets the RAG pipeline run on a different model (e.g. Amazon Nova)
    while the LLM refiner keeps using the default Bedrock model.

    Uses the default AWS credential chain (environment variables, IAM role,
    config file, etc.) — never hardcodes credentials.

    Read timeout comes from `rag_model_timeout_s`, not from the refiner's
    `llm_chunk_timeout_ms`. Generating a cited answer over ten retrieved chunks
    is a much longer call than refining a single correction chunk, and sharing
    the refiner's 15s budget made any slower answer fail on retry at ~25s.

    Retries are disabled entirely. In botocore's client config, `max_attempts`
    counts *retries on top of* the initial call, so `max_attempts=0` normalizes
    to `total_max_attempts=1` — one call and nothing more. A read timeout here
    means the model was still generating, and re-sending the same prompt only
    doubles the wall-clock cost without improving the odds. With `read_timeout`
    bounded by `rag_model_timeout_s`, a single attempt keeps the worst case
    inside the gateway's abort window instead of exceeding it. The agent's own
    timeout and the circuit breaker handle failure.

    Args:
        settings: Application settings providing model_id, region, and timeout.

    Returns:
        A ChatBedrock instance configured with retries and timeout.
    """
    model_id = settings.rag_model_id or settings.bedrock_model_id
    read_timeout_s = settings.rag_model_timeout_s
    return ChatBedrock(
        model_id=model_id,
        region_name=settings.aws_region,
        config=BotoConfig(
            retries={"max_attempts": 0, "mode": "standard"},
            read_timeout=read_timeout_s,
            connect_timeout=10,
        ),
    )


def create_embeddings(settings: Settings) -> CachedEmbeddings:
    """Create a configured BedrockEmbeddings instance wrapped with LRU cache.

    Args:
        settings: Application settings providing the AWS region.

    Returns:
        A CachedEmbeddings instance that caches query embeddings in memory.
    """
    inner = BedrockEmbeddings(
        model_id="amazon.titan-embed-text-v2:0",
        region_name=settings.aws_region,
    )
    return CachedEmbeddings(inner)


class CachedEmbeddings:
    """LRU-cached wrapper around BedrockEmbeddings for query embeddings.

    Caches the vector result of `aembed_query` keyed by the query text hash.
    Cache entries expire after `ttl_seconds` to prevent stale results if the
    embedding model were ever updated. The cache is bounded to `max_size`
    entries and uses LRU eviction.

    This eliminates redundant Bedrock API calls when the same query is asked
    multiple times (common in agent loops that search the same terms).
    """

    def __init__(
        self,
        inner: BedrockEmbeddings,
        max_size: int = 256,
        ttl_seconds: float = 3600,
    ) -> None:
        self._inner = inner
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[list[float], float]] = OrderedDict()

    def _cache_key(self, text: str) -> str:
        """Produce a short hash key for the query text."""
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    async def aembed_query(self, text: str) -> list[float]:
        """Return cached embedding or call Bedrock and cache the result."""
        key = self._cache_key(text)
        now = time.time()

        # Check cache
        if key in self._cache:
            embedding, ts = self._cache[key]
            if now - ts < self._ttl_seconds:
                self._cache.move_to_end(key)
                logger.debug("rag.embedding_cache.hit", query_preview=text[:40])
                return embedding
            else:
                del self._cache[key]

        # Cache miss — call Bedrock
        embedding = await self._inner.aembed_query(text)

        # Store in cache with LRU eviction
        self._cache[key] = (embedding, now)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

        logger.debug("rag.embedding_cache.miss", query_preview=text[:40])
        return embedding

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Delegate document embedding to the inner client (not cached).

        Document embeddings are used during ingestion, which is a one-time
        operation per chunk, so caching them provides little benefit.
        """
        return await self._inner.aembed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """Sync embedding — delegates to inner (no cache for sync path)."""
        return self._inner.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Sync document embedding — delegates to inner."""
        return self._inner.embed_documents(texts)

