# Transcript RAG — Research and Design Options

Research notes for adding a Retrieval-Augmented Generation capability over the transcripts this
system produces, so a user can ask questions across a corpus of past transcriptions ("what did
the Minister say about Ningo-Prampram?") and get answers grounded in cited, timestamped source
passages.

This is a research document, not an approved design. It records what the system already provides,
what is genuinely missing, the decisions worth making deliberately, and the evidence behind each
recommendation. Content from external sources is paraphrased and linked rather than quoted.

---

## 1. What already exists

The groundwork is unusually good, because the Postprocessing Service already carries most of the
infrastructure a RAG system needs.

| Capability | Where | Reusable for RAG? |
|---|---|---|
| PostgreSQL with `pg_trgm` | `services/postprocess/migrations/versions/001_initial_schema.py` | Yes — the same instance can host `pgvector` |
| Bedrock client via boto3, default credential chain | `services/postprocess/app/llm/bedrock.py` | Yes — embeddings and generation both live here |
| Retrieval abstraction with two modes | `services/postprocess/app/llm/retrieval.py` (`LLM_RETRIEVAL_MODE`) | Partly — see note below |
| Chunking + bounded parallel waves + timeout handling | `services/postprocess/app/llm/refiner.py` | Yes — the ingestion path needs the same shape |
| Word-level timestamps (`start`, `end`, `confidence`) | Deepgram response, preserved through the pipeline | Yes — this is what makes citations possible |
| Entity recognition per transcript (`entities[]`, `entityKind`, `entityType`) | `app/pipeline.py` | Yes — high-value structured metadata for filtering |
| Async queue + background writer pattern | `app/history/writer.py` | Yes — ingestion should not block a transcription response |
| Structured logging, EMF metrics, correlation IDs | `app/obs/` | Yes |

Two details worth noting:

- `LLM_RETRIEVAL_MODE` already accepts `knowledge_base`, but the Bedrock Knowledge Base path in
  `retrieval.py` raises `NotImplementedError` and deliberately falls through to the `pg_trgm`
  Dataset_Store path with a single warning. So the *seam* for a managed retrieval backend exists;
  the implementation does not.
- That existing retrieval is **entity-record retrieval for prompt grounding**, not passage
  retrieval over transcripts. It answers "which Ghana entities are relevant to this chunk", which
  is a different job from "which past transcript passages answer this question". The new system is
  additive, not a replacement.

---

## 2. The blocking gap: transcripts are not persisted server-side

This is the finding that matters most, and it changes the shape of the work.

There is currently **no durable server-side store of transcripts**. Specifically:

- The `/api/transcription` response returns `transcript`, `words`, `entities`, `metadata`, and
  `raw`, and then the server forgets all of it.
- Transcript history and projects are persisted in the **browser's localStorage** via the frontend
  service modules (`frontend/src/services/history-repo`, `project-repo`), per the project steering
  docs — not in any backend database.
- The repo's `transcripts/` directory is **gitignored** and only holds ad-hoc local files (the one
  I wrote during testing is local-only).
- `correction_history` stores a `text_hash` rather than transcript text, deliberately, for privacy.

A RAG system needs a corpus. So **transcript persistence is a prerequisite phase**, not a
sub-task, and it carries its own product decisions (ownership, retention, multi-user scoping,
whether existing browser-side history is migrated or abandoned). Any estimate that treats "add
RAG" as one feature will be wrong because of this.

---

## 3. Key design decisions

### 3.1 Vector store: `pgvector` in the existing PostgreSQL

**Recommendation: `pgvector` in the same PostgreSQL instance the Dataset_Store already uses.**

The reasoning is mostly about avoiding a second data system. The correction pipeline already needs
PostgreSQL, so vectors can sit beside `entity_record` and `correction_history`, letting a query
filter by recognised entity and search semantically in a single statement — no cross-service join
or eventual-consistency gap. That co-location argument is the one most consistently made in
favour of [keeping vectors in Postgres rather than adding a separate managed vector database](https://appmaster.io/blog/pgvector-vs-managed-vector-db),
where the real question is where you want complexity to live rather than which store is faster.

On index choice, current guidance is fairly consistent: prefer **HNSW over IVFFlat** for
production read latency, accepting slower index builds in exchange for substantially faster
queries at high recall ([pgvector hybrid retrieval practice](https://markaicode.com/architecture/pgvector-hybrid-retrieval-architecture/),
[DigitalOcean's advanced Postgres vector guidance](https://docs.digitalocean.com/products/vector-databases/postgresql/concepts/advanced-workloads/)).
DigitalOcean's advice is to start with pgvector HNSW for corpora under a few million rows and only
move to specialised tooling when ingest rate or index build time actually becomes the bottleneck —
which this corpus, at roughly a few hundred to a few thousand transcripts, is nowhere near.

Requires PostgreSQL 15+ and pgvector 0.7.0+ for HNSW; the compose stack currently pins
`postgres:16`, so this is satisfied.

**Alternative considered — Bedrock Knowledge Bases.** Attractive because it covers ingestion,
chunking, embedding, and retrieval in one managed service rather than only the vector-store piece
([Bedrock KB vs self-managed comparison](https://markaicode.com/vs/aws-bedrock-vs-milvus/)), and
because `LLM_RETRIEVAL_MODE=knowledge_base` already anticipates it. Two things argue against it as
the default here: the loss of control over chunking, which section 3.3 argues is the highest-leverage
decision for transcripts specifically; and cost floor, where the commonly-cited pain point is
OpenSearch Serverless as the backing store — one migration write-up reports [cutting Bedrock KB costs
by roughly 90% by moving from OpenSearch Serverless to Aurora Serverless v2 with pgvector](https://ercanermis.com/cutting-amazon-bedrock-knowledge-base-costs-by-90-migrating-from-opensearch-serverless-to-aurora-serverless-v2-with-pgvector/).
Worth revisiting if operational burden becomes the dominant concern. AWS publishes its own
[vector database comparison](https://docs.aws.amazon.com/prescriptive-guidance/latest/choosing-an-aws-vector-database-for-rag-use-cases/vector-db-comparison.html)
for this decision.

### 3.2 Hybrid retrieval, not vector-only

**Recommendation: dense vector search combined with PostgreSQL full-text search, fused with
Reciprocal Rank Fusion.**

Reported gains for hybrid over vector-only land around
[15–25% recall improvement using RRF](https://markaicode.com/architecture/hybrid-retrieval-architecture-with-postgres/),
with commonly-cited starting parameters of `k=60` for RRF and capping the lexical side at the top
100 results.

This matters more than usual for *this* corpus. Ghanaian proper nouns — `Ningo-Prampram`,
`Kpone-Katamanso`, `Afenyo-Markin` — are exactly the tokens where embedding models are weakest
(rare, hyphenated, often absent from training data) and where lexical matching is strongest. A
user searching for a constituency name wants exact-match behaviour. Vector-only retrieval would
degrade precisely on the domain this application exists to serve.

There is a third lever available almost for free: the pipeline already produces a resolved
`entities[]` array per transcript. Filtering candidate chunks by recognised entity before ranking
is a structured pre-filter that most RAG systems have to build an entity extractor to get.

### 3.3 Chunking: timestamp- and speaker-aware, not fixed-size

This is where transcripts differ most from documents, and where a naive implementation will fail.

The consensus framing is blunt: chunking is the single highest-leverage decision in a retrieval
pipeline, because [a poorly chunked document cannot be retrieved well regardless of how good the
rest of the system is](https://medium.com/@anilpise7/chunking-strategies-why-how-you-split-documents-makes-or-breaks-your-rag-system-6d7aa76a6d88).
Oracle's guidance is more specific and directly applicable: retrieval fails when a chunk is cut
away from the context that gives it meaning, so a chunk should carry its
[timestamps, speaker labels, and source identifiers as metadata](https://blogs.oracle.com/developers/rag-chunking-and-parsing-for-tables-pdfs-transcripts-and-media)
rather than being a bare span of text. Cohere makes the transcript-specific point that
content-independent splitting is the wrong default here, because
[you generally want one speaker's content kept together](https://docs.cohere.com/page/chunking-strategies).

Concrete recommendations for this system:

- **Segment on natural boundaries first** — speaker turn, then long pause — before applying any
  size limit. Deepgram word timings make pause detection trivial; the hybrid pipeline already
  computes gap-based grouping in `lib/hybrid/segment-grouper.js` and is worth reading for prior art.
- **Target 100–200 words per chunk with overlap.** A hierarchical scheme is well attested for long
  recordings: fine chunks of ~100–200 words for precise retrieval, plus summarised ~500-word
  segments as a middle layer, plus a whole-transcript summary
  ([long-form transcript QA approach](https://www.rohan-paul.com/i/161787715/why-generate-both-the-final-answer-and-the-relevant-timestamps-separately)).
  Given transcripts here run ~600–950 words, the two-level version (fine chunks + one transcript
  summary) is probably sufficient; three levels is likely over-engineering at this corpus size.
- **Store `start`/`end` seconds on every chunk.** This is what turns an answer into a citation the
  user can click and play. It is also the cheapest trust mechanism available.
- **Index the corrected text, and keep the raw text.** The response already carries both. Indexing
  corrected text means a search for `Ningo-Prampram` matches audio where the ASR produced
  `ningoprampram` — the correction pipeline effectively acts as a query-time normaliser. Retaining
  raw text preserves an audit trail and protects against the known false-positive problem (below).

One caveat carried over from the correction work: the engine currently over-corrects
(`plans` → `Prang`, `spoke` → `Kpone`). Indexing only corrected text would bake those errors into
the search index. Storing both, and preferring corrected text for embedding while keeping raw text
retrievable, keeps that recoverable. The `transcription-learning-loop` spec addresses the
underlying precision problem.

### 3.4 Embedding model

Since Bedrock is already wired, **Amazon Titan Text Embeddings V2** is the low-friction default —
same client, same credential chain, same IAM pattern as the existing `bedrock:InvokeModel` grant.
Titan V2 supports Matryoshka-style dimension reduction (1024/512/256), which trades a little
quality for meaningfully smaller index size.

Two honest caveats:

- Model choice should be [a deliberate decision across quality on your own data, language
  coverage, cost, governance, and deployment model](https://www.sphereinc.com/blogs/rag-embedding-models-enterprise)
  rather than a default. This corpus is unusual: Ghanaian English with Twi/Ewe/Ga code-switching
  and dense local proper nouns. General-purpose benchmark rankings will not predict performance
  here.
- One Bedrock comparison using LLM-as-judge scoring over ~60k documents concluded that
  [different embedding models had complementary strengths and were best used in combination](https://dev.classmethod.jp/en/articles/bedrock-nova-titan-embedding-comparison/)
  rather than one being universally better.

**Therefore: treat the embedding model as a configurable, swappable choice from day one.** Store
the model identifier and dimension alongside every vector so a re-embedding migration is possible
without a schema change. Then measure on a labelled set drawn from real transcripts before
committing. The hybrid design in 3.2 also reduces the cost of getting this wrong, since lexical
matching covers the proper-noun cases embeddings handle worst.

### 3.5 Evaluation must be built with the feature, not after

RAG systems fail in a way that is specifically hard to notice: a fluent, correct-looking answer can
come from junk retrieval plus a hallucinating model, and end-to-end scoring will call it a pass.
The discipline is to [score retrieval and generation separately so a failure localises to the layer
that caused it](https://futureagi.com/blog/what-is-rag-evaluation-2026/) — treating evaluation as a
bisection problem rather than a single quality number.

Minimum metric set:

| Layer | Metrics |
|---|---|
| Retrieval | context precision, context recall, hit-rate@k, MRR |
| Generation | faithfulness / groundedness, answer relevance |
| Citation | do returned timestamps actually contain the claim? |
| Operational | p50/p95 latency per stage, cost per query |

[RAGAS](https://markaicode.com/rag-evaluation-ragas-metrics-production/) is the usual open-source
starting point. The diagnostic value is concrete: high faithfulness with low context recall points
at the retriever, not the prompt.

A practical note for this repo: it already has strong evaluation habits — a Regression_Corpus, a
Parity_Harness, `accepted-divergences.json`, property-based tests, and a benchmark harness with
baseline comparison. A RAG eval set should follow the same pattern: a versioned fixture of
question/expected-passage pairs, run in CI, gated against regression. That is a natural fit rather
than a new practice.

---

## 4. Caching

Caching is where a RAG system's cost and latency profile is decided, and also where a subtle
correctness failure can be introduced. The important framing is that "caching" here means four
different things with **very different risk profiles**, and they should be adopted in order of
increasing risk rather than treated as one feature.

| Layer | What is keyed | Correctness risk | Verdict |
|---|---|---|---|
| 1. Bedrock prompt caching | stable prompt prefix | none (provider-native) | Adopt first |
| 2. Embedding cache | exact text + model id | none (deterministic) | Adopt |
| 3. Retrieval result cache | normalised query + filters | staleness only | Adopt with invalidation |
| 4. Semantic answer cache | *approximate* query similarity | **can return a wrong answer** | Defer; needs a verification gate |

### 4.1 Bedrock prompt caching — the free win, and it applies today

Bedrock supports caching repeated prompt prefixes between requests so the model skips recomputing
them. AWS positions this as reducing
[cost by up to 90% and latency by up to 85% on supported models](https://aws.amazon.com/bedrock/prompt-caching/),
and the Well-Architected generative AI lens lists it as a
[named cost best practice](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gencost03-bp03.html).

Mechanics that constrain the design ([Bedrock prompt caching docs](https://docs.aws.amazon.com/en_us/bedrock/latest/userguide/prompt-caching.html)):

- There is a **minimum tokens per cache checkpoint** (commonly 1K, model-dependent) — prefixes
  below it will not cache, so a short system prompt gains nothing.
- A **maximum number of cache checkpoints per request** (commonly 4).
- TTL was 5 minutes by default; Bedrock now also supports a
  [1-hour cache duration](https://aws.amazon.com/about-aws/whats-new/2026/01/amazon-bedrock-one-hour-duration-prompt-caching/),
  which suits bursty interactive question-answering far better.
- The cost structure is **asymmetric — a cache miss costs more than an uncached call**
  ([practitioner framework](https://repost.aws/articles/ARap6ZjOKdSAGaQKZ1QU2qQg/optimizing-amazon-bedrock-costs-at-scale-a-practitioner-s-framework-for-high-volume-workloads)).
  So caching a prefix that rarely repeats actively loses money. Only cache genuinely stable
  prefixes.

**This is worth doing independently of RAG.** The existing `LLM_Refiner` sends a 13-rule system
prompt (`app/llm/prompt.py`) that is byte-identical on every chunk of every transcript, and it
already fans out `LLM_MAX_PARALLEL=3` chunks per wave. That stable prefix is close to the textbook
case for prompt caching. The variable part — the per-chunk retrieved entity block — must stay
*after* the cached prefix for the prefix to match, which the current prompt structure should be
checked against. Prompt ordering, not the cache flag, is the actual work.

For RAG answering, the same applies: a stable answering-instruction prefix caches, the retrieved
passages do not.

### 4.2 Embedding cache — deterministic, so safe

An embedding is a pure function of `(text, model_id, dimension)`. Caching it cannot produce a wrong
answer, only a stale one if the model changes — and including `model_id` in the key removes even
that. This is the least risky cache in the whole design.

Two distinct uses:

- **Ingestion side:** avoid re-embedding unchanged chunks on re-index. Materially useful given the
  design in 3.4 anticipates model swaps and re-embedding backfills; unchanged text under an
  unchanged model never needs a second API call.
- **Query side:** repeated or common questions skip the embedding round trip. Reported effects
  cluster around a [60% p50 latency reduction for repetitive queries via an in-memory LRU](https://markaicode.com/architecture/llamacpp-semantic-search-architecture/)
  and similar figures for [an embedding cache with TTL](https://markaicode.com/architecture/fastapi-rag-architecture/).
  Treat the specific percentages as indicative rather than predictive — they depend entirely on
  query repetition, which is unknown for this application until it has users.

Because chunk embeddings are already persisted in `transcript_chunk.embedding`, the ingestion-side
cache is arguably just "don't re-embed rows whose text hash and model id are unchanged" — a
`content_hash` column rather than a cache service.

### 4.3 Retrieval result cache — useful, but invalidation is the real problem

Caching the ranked chunk list for a normalised query cuts both vector search and any reranking
work. Commonly cited: [caching query embeddings and top-K results to cut reranker cost](https://markaicode.com/architecture/enterprise-semantic-search-architecture/),
with short TTLs (5 minutes is typical) and invalidation on source-document update.

The honest difficulty is not the cache, it is knowing when it is wrong. The most useful account
I found on this argues that in domains where the corpus is versioned,
[invalidation matters more than the similarity threshold](https://medium.com/@nejc.fosnaric/semantic-caching-for-legal-rag-why-invalidation-matters-more-than-similarity-7208e4e022fc) —
their legal RAG platform had good grounded answers but ~20s latency, and the hard part of caching
turned out to be deciding what to invalidate, not what to store.

This system has a specific, concrete version of that problem: **the corpus grows every time someone
transcribes something.** A cached result for "what was said about Ningo-Prampram" becomes wrong the
moment a new transcript mentioning it is indexed — not stale-but-acceptable, but missing evidence
the user would expect. Two mitigations fit the existing architecture:

- Include a **corpus version** in the cache key (max `transcript.id`, or a monotonic counter bumped
  on ingest). New transcript, new key, old entries age out naturally. This is the same pattern the
  Dataset_Cache already uses with its `version` timestamp on `DatasetSnapshot`.
- Keep TTLs short (minutes). The prize here is bursty repeated querying, not long-term storage.

Also worth noting the freshness dimension is an active research area rather than settled practice —
see [risk-constrained freshness-aware semantic caching](https://arxiv.org/html/2607.04281v1).

### 4.4 Semantic answer cache — the one to be careful with

This is caching the *generated answer* and serving it for a merely **similar** future question. It
is the biggest latency win and the only layer that can make the system confidently wrong.

The distinction worth internalising: an exact-match cache can be stale but is never wrong — the key
matches or it does not. A semantic cache
[deliberately trades that guarantee away, returning answers for keys it has never seen](https://tianpan.co/blog/2026-05-17-semantic-cache-confidently-wrong-answer),
and the correctness risk is one most teams never quantify.

The research backs that up specifically for RAG. Output-level semantic answer caches are described
as fragile because
[similar prompts can map to different correct answers, retrieved evidence drifts as the corpus is updated, and cached responses can be hijacked by collision attacks](https://arxiv.org/html/2605.27494v1).
And the common implementation — one static cosine threshold for every request — is shown to give
[no formal correctness guarantee, unpredictable error rates, and suboptimal hit rates](https://arxiv.org/html/2502.03771v4).
Mitigations exist (adaptive per-request thresholds; verification pipelines that replace a bare
cosine threshold and are reported to drive hard-negative false positives from 33.3% to 0% in
[one library](https://pypi.org/project/semanticmemo/)), but they are additional machinery, not a
config flag.

For this application the failure is easy to picture and hard to defend: "what did the Minister say
about Kasoa?" and "what did the Minister say about Kpone?" are highly similar as sentences and have
completely different correct answers. Ghanaian place names are short, phonetically close, and
frequently the entire semantic payload of the question — exactly the conditions under which a
similarity threshold misfires. Combined with the known over-correction problem, this is a plausible
route to a fabricated-looking citation.

**Recommendation: do not ship layer 4 in the initial system.** Layers 1–3 capture most of the cost
and latency benefit at effectively zero correctness risk. If layer 4 is added later, it needs: a
tuned threshold measured against a labelled set, a verification step confirming the cached answer's
citations still exist in the current corpus, corpus-version keying, and a metric for cache-induced
wrong answers. That last one is the item teams skip.

### 4.5 Where the cache lives

**There is no Redis or Memcached in this stack today.** The compose file runs PostgreSQL, the
Postprocessing Service, and the Gateway; nothing else. So "add a cache" is also "add an
infrastructure dependency," and that should be a conscious decision rather than a side effect.

Options, cheapest first:

1. **In-process LRU inside the Postprocessing Service.** No new infrastructure. The catch is real:
   `UVICORN_WORKERS` defaults to 2 and each worker holds its own memory (the design already notes
   this is why the default is 2 rather than one-per-core), so caches are duplicated per worker and
   hit rate is diluted. Fine for embedding caches, weak for anything wanting a shared view.
2. **PostgreSQL as the cache store.** Unfashionable but coherent here: a table keyed by content
   hash with a TTL column, in the database that is already a required dependency, transactionally
   consistent with the corpus it caches. Slower than Redis per lookup, but a vector query is
   already 10–30ms, so the relative overhead is smaller than it first appears. Strongest fit for
   the ingestion-side embedding cache, which is really deduplication rather than caching.
3. **Redis.** The conventional answer, with published RAG guidance to match
   ([Redis on production RAG](https://redis.io/blog/rag-at-scale/)). Justified once there is
   measured cross-worker cache pressure — not before.

Recommended sequence: prompt caching (no infrastructure) → `content_hash` dedup in Postgres (no
infrastructure) → in-process LRU for query embeddings → Redis only when metrics show it is needed.

One general caveat that argues for measuring rather than assuming: a cache **miss** adds latency
rather than removing it — one report puts the miss penalty around
[20–40ms on p50 while a hit drops 1.2s to 80ms](https://markaicode.com/architecture/production-llm-architecture/).
Strongly favourable at a high hit rate, and a straight loss at a low one. Since query repetition is
unknown for this application, layers 3 and 4 should be instrumented behind a flag and evaluated on
real traffic rather than enabled on the strength of published percentages.

### 4.6 What to measure

Caching needs its own metrics or it becomes an invisible correctness variable. Following the
existing EMF-based metrics in `app/obs/metrics.py`:

- Hit rate and miss rate per layer, separately (an aggregate hides which layer is working).
- Cache-attributed latency delta at p50 and p95, including the miss penalty.
- Bedrock cache-read versus cache-write token counts, since the miss path costs more.
- Invalidation events, and the age of served entries.
- For layer 4 if ever enabled: a sampled audit comparing cached answers against freshly generated
  ones, reported as a cache-induced error rate.

---

## 5. Sketch: schema shape

Illustrative, to make the design concrete. Not final.

```sql
-- Prerequisite (Phase 0): durable transcripts
CREATE TABLE transcript (
  id              bigserial PRIMARY KEY,
  correlation_id  text,
  source_name     text,
  provider        text,              -- deepgram | khaya | hybrid
  duration_s      double precision,
  raw_text        text NOT NULL,     -- pre-correction
  corrected_text  text NOT NULL,     -- post-correction
  entities        jsonb,             -- reuse the existing entities[] summary
  metadata        jsonb,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Retrievable passages
CREATE TABLE transcript_chunk (
  id             bigserial PRIMARY KEY,
  transcript_id  bigint NOT NULL REFERENCES transcript(id) ON DELETE CASCADE,
  ordinal        int NOT NULL,
  text           text NOT NULL,      -- corrected; raw recoverable via word span
  start_s        double precision,   -- citation target
  end_s          double precision,
  speaker        text,
  entity_names   text[],             -- structured pre-filter
  embedding      vector(1024),       -- dimension follows the model
  model_id       text NOT NULL,      -- enables re-embedding migrations
  tsv            tsvector,           -- lexical half of hybrid retrieval
  UNIQUE (transcript_id, ordinal)
);

CREATE INDEX ON transcript_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON transcript_chunk USING gin (tsv);
CREATE INDEX ON transcript_chunk USING gin (entity_names);
```

Carrying `model_id` per row is deliberate: it makes a model swap an incremental backfill rather
than a migration, which section 3.4 argues is likely.

---

## 6. Suggested phasing

Each phase is independently useful, which matters because phase 0 alone has real value.

**Phase 0 — Transcript persistence.** Server-side storage of transcripts with entities and word
timings. No retrieval yet. Unblocks everything, and on its own gives durable cross-device history
that browser localStorage cannot.

**Phase 1 — Ingestion and indexing.** Timestamp- and speaker-aware chunking, Bedrock embeddings,
`pgvector` HNSW index, `tsvector` for lexical search. Reuse the existing async-queue-plus-background-writer
pattern so indexing never blocks a transcription response.

**Phase 2 — Retrieval API.** Hybrid dense + lexical search with RRF, entity pre-filtering, returning
ranked chunks with timestamps. Testable without any LLM in the loop, which makes retrieval quality
measurable in isolation — the bisection discipline from 3.5.

**Phase 3 — Grounded answering.** Claude via the existing Bedrock client, with mandatory citations
back to transcript and timestamp. Must degrade to "no supporting passage found" rather than
answering unsupported, consistent with this codebase's existing convention that LLM and
Postprocessing failures degrade rather than throw.

**Phase 4 — Evaluation harness.** Versioned question/passage fixtures, retrieval and generation
metrics scored separately, CI gating in the style of the existing Parity_Harness.

**Phase 4.5 — Caching (layers 1–3).** Bedrock prompt caching, `content_hash` embedding dedup, and
a corpus-version-keyed retrieval cache, each behind a flag with hit-rate metrics. Deliberately
*after* the evaluation harness, so the effect of caching on answer quality is measurable rather
than assumed. Layer 1 is the exception and can land at any time — it also improves the existing
`LLM_Refiner` independently of RAG.

**Phase 5 — Frontend.** Search and Q&A surface with click-to-play citations, reusing the existing
MD3 token layer and the `WaveformPlayer` seek capability.

---

## 7. Open questions

1. **Scope of the corpus** — all transcripts globally, or scoped per user/project? This drives
   authentication and row-level filtering in Phase 0 and is hard to retrofit.
2. **Migration of existing localStorage history** — import into the new store, or start fresh?
3. **Retention** — how long are transcripts kept? The learning-loop spec settled on a 90-day
   snippet window for feedback context; full-transcript retention is a separate, larger decision
   with different privacy weight, since this stores complete transcript text where
   `correction_history` deliberately stores only a hash.
4. **Multi-language** — do Twi/Ewe/Ga passages need a different embedding model or a translation
   step? Current retrieval assumes English-dominant text.
5. **Hybrid-pipeline transcripts** — the hybrid route returns `segments[]` rather than `words[]`,
   and the frontend normalises them differently. Chunking needs to handle both shapes or the
   corpus will be inconsistent.
6. **Does the over-correction problem gate this?** Indexing corrected text spreads known false
   positives into search. Worth deciding whether the learning loop lands first.

---

## 8. Sources

- [RAG chunking and parsing for transcripts and media](https://blogs.oracle.com/developers/rag-chunking-and-parsing-for-tables-pdfs-transcripts-and-media) — Oracle
- [Effective chunking strategies for RAG](https://docs.cohere.com/page/chunking-strategies) — Cohere
- [Why document splitting makes or breaks RAG](https://medium.com/@anilpise7/chunking-strategies-why-how-you-split-documents-makes-or-breaks-your-rag-system-6d7aa76a6d88)
- [RAG-powered QA for long-form transcripts](https://www.rohan-paul.com/i/161787715/why-generate-both-the-final-answer-and-the-relevant-timestamps-separately)
- [Forced alignment, word timestamps, and audio evidence search](https://mixpeek.com/guides/forced-alignment-audio-video-agent-search) — Mixpeek
- [pgvector hybrid retrieval architecture](https://markaicode.com/architecture/pgvector-hybrid-retrieval-architecture/)
- [Hybrid retrieval with Postgres and RRF](https://markaicode.com/architecture/hybrid-retrieval-architecture-with-postgres/)
- [Best practices for advanced PostgreSQL vector workloads](https://docs.digitalocean.com/products/vector-databases/postgresql/concepts/advanced-workloads/) — DigitalOcean
- [pgvector vs managed vector database](https://appmaster.io/blog/pgvector-vs-managed-vector-db)
- [AWS vector database comparison for RAG](https://docs.aws.amazon.com/prescriptive-guidance/latest/choosing-an-aws-vector-database-for-rag-use-cases/vector-db-comparison.html) — AWS
- [Cutting Bedrock Knowledge Base costs with Aurora + pgvector](https://ercanermis.com/cutting-amazon-bedrock-knowledge-base-costs-by-90-migrating-from-opensearch-serverless-to-aurora-serverless-v2-with-pgvector/)
- [Bedrock Knowledge Bases vs self-managed vector stacks](https://markaicode.com/vs/aws-bedrock-vs-milvus/)
- [Choosing RAG embedding models for enterprise](https://www.sphereinc.com/blogs/rag-embedding-models-enterprise) — Sphere
- [Comparing Bedrock embedding models with LLM-as-a-judge](https://dev.classmethod.jp/en/articles/bedrock-nova-titan-embedding-comparison/) — Classmethod
- [What is RAG evaluation](https://futureagi.com/blog/what-is-rag-evaluation-2026/) — Future AGI
- [RAG evaluation with RAGAS in production](https://markaicode.com/rag-evaluation-ragas-metrics-production/)
- [How to evaluate a RAG pipeline](https://mixpeek.com/guides/how-to-evaluate-a-rag-pipeline) — Mixpeek

Caching:

- [Prompt caching for faster model inference](https://docs.aws.amazon.com/en_us/bedrock/latest/userguide/prompt-caching.html) — AWS
- [Cache prompts between requests](https://aws.amazon.com/bedrock/prompt-caching/) — AWS
- [GENCOST03-BP03: implement prompt caching to reduce token costs](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gencost03-bp03.html) — AWS Well-Architected
- [Bedrock now supports 1-hour prompt caching duration](https://aws.amazon.com/about-aws/whats-new/2026/01/amazon-bedrock-one-hour-duration-prompt-caching/) — AWS
- [Optimizing Bedrock costs at scale](https://repost.aws/articles/ARap6ZjOKdSAGaQKZ1QU2qQg/optimizing-amazon-bedrock-costs-at-scale-a-practitioner-s-framework-for-high-volume-workloads) — AWS re:Post
- [The semantic cache that confidently returns the wrong answer](https://tianpan.co/blog/2026-05-17-semantic-cache-confidently-wrong-answer)
- [Grounded cache routing for RAG: when is it safe to reuse an answer?](https://arxiv.org/html/2605.27494v1) — arXiv
- [Adaptive semantic prompt caching with VectorQ](https://arxiv.org/html/2502.03771v4) — arXiv
- [Risk-constrained freshness-aware semantic caching](https://arxiv.org/html/2607.04281v1) — arXiv
- [Semantic caching for legal RAG: why invalidation matters more than similarity](https://medium.com/@nejc.fosnaric/semantic-caching-for-legal-rag-why-invalidation-matters-more-than-similarity-7208e4e022fc)
- [Zero-waste agentic RAG: caching architectures](https://towardsdatascience.com/zero-waste-agentic-rag-designing-caching-architectures-to-minimize-latency-and-llm-costs-at-scale/) — Towards Data Science
- [RAG at scale](https://redis.io/blog/rag-at-scale/) — Redis
- [Enterprise semantic search architecture](https://markaicode.com/architecture/enterprise-semantic-search-architecture/)
- [Production LLM architecture](https://markaicode.com/architecture/production-llm-architecture/)
- [SemanticMemo](https://pypi.org/project/semanticmemo/) — verification-pipeline alternative to cosine thresholds

Content from these sources was paraphrased and summarised for compliance with licensing
restrictions.
