# Evaluation_Harness fixtures (`tests/evaluation/`)

This package holds the **data and loaders** the offline Evaluation_Harness runs
against. It implements task **6.4.1** of the `correction-precision-gating` spec:
the Evaluation_Corpus, the former-Block_List fixtures, and the Benchmark_Set,
plus their loaders. It does **not** measure precision/recall (task 6.4.2) or
latency (task 6.4.3) — those tasks consume these fixtures.

Everything is loaded from committed, local data: no network request and no AWS
call (Req 10.8).

## Modules

| Module | Purpose | Requirements |
|--------|---------|--------------|
| `dataset.py` | Fixed in-memory `DatasetSnapshot` (curated subset of `datasets/*.json`) built through the production `build_index`, plus the bundled English_Lexicon loader. | 10.3, 10.8 |
| `corpus.py` | Labelled Evaluation_Corpus — `Negative_Set` (>=200) and `Positive_Set` (>=100). | 10.1, 10.2, 10.3, 11.1 |
| `blocklist_fixtures.py` | The former Block_List snapshot (>=130 block-kind entries) preserved as Negative_Set fixtures. | 11.1, 11.2 |
| `benchmark.py` | Deterministic Benchmark_Set generator + loader (20-50 fixtures, 2,000-20,000 Words each). | 15.2 |
| `test_fixtures.py` | Asserts the fixtures are deterministic and spec-compliant. | 10.1-10.3, 11.1, 15.2 |

## Evaluation_Corpus (`corpus.py`)

- **Negative_Set** — ordinary non-entity English embedded in synthesized
  parliamentary sentences, labelled `None`. It folds in the former Block_List
  tokens (see below) plus additional ordinary parliamentary vocabulary, comfortably
  clearing the 200-Span floor (Req 10.2). Confidence-bearing Words sit at/above the
  default High_Confidence_Threshold (0.90) so the Confidence_Gate / Lexicon_Gate
  keep them out of approximate matching.
- **Positive_Set** — a `(mangled_span, canonical_entity)` table expanded across
  three carrier templates each, clearing the 100-Span floor (Req 10.3). Every
  canonical label is present in the fixture snapshot. Words sit *below* the
  threshold so the gated engine admits the approximate repair; person spans carry
  a preceding Title_Prefix so the Context_Gate (Req 7) admits them.
- The two sets are **disjoint by Span text** (Req 10.2): any Negative_Set Span whose
  text also appears as a Positive_Set Span is dropped.
- Each Span carries its enclosing transcript text and the `confidence` /
  `punctuated_word` of covered Words (Req 10.1).

## Former Block_List fixtures (`blocklist_fixtures.py`)

The authoritative source is `scripts/expand_blocklist.sql`. Every
`list_kind = 'block'` entry is transcribed verbatim with its recorded reason,
plus additional recorded false-positive English words, giving **>=130 entries**
(Req 11.1). Each carries `token`, `reason`, and the `span_confidence` the harness
uses when evaluating it (set high, so gating — not the list — must keep it
unchanged, Req 11.2). Every token is ordinary English and must **not** be
corrected to an entity. The five recorded false positives named in Req 16.13
(`thank`, `sage`, `district`, `transportation`, `later`) are all covered.

The fixture snapshot in `dataset.py` deliberately carries an **empty** block list
so the harness proves gating handles these words *without* it (Req 11.2).

## Benchmark_Set (`benchmark.py`)

Used for Rule_Latency benchmarking (task 6.4.3). Req 15.2 requires 20-50
transcript fixtures, each 2,000-20,000 Words, with a confidence on >=90% of Words
and below 0.90 on >=25% of Words, **identical on every run**.

### How determinism is guaranteed

Determinism is a property of the construction, not something asserted after the
fact:

1. **Committed seed data only.** The single source of text is the compact
   `_SEED_VOCAB` tuple in `benchmark.py` (ordinary parliamentary English plus a
   few public-figure surnames). No external corpus, no I/O.
2. **Fixed seed + seeded PRNG.** Each fixture `idx` expands the seed vocab with a
   `random.Random(BENCHMARK_SEED + idx)` — a single committed master seed
   (`BENCHMARK_SEED = 20240117`). Replaying the same seed replays the same
   pseudo-random stream, so token choices, the positions that carry a confidence,
   and the low-vs-high split are all reproducible.
3. **Deterministic confidence placement.** The count of confidence-bearing Words
   (`ceil(count * 0.95)`) and low-confidence Words (`ceil(count * 0.30)`) is a
   fixed function of the word count, so the >=90% / >=25% floors hold on every run
   with margin.
4. **Cached loader.** `load_benchmark_set()` is `lru_cache`-d, so within a process
   two calls return the same immutable objects. `test_fixtures.py` also bypasses
   the cache (`__wrapped__`) to prove two fresh builds are equal by value.

Regenerating the set is a deliberate act: change `BENCHMARK_SEED` (or the shape
constants) and commit the new value. Leaving them fixed reproduces the identical
set forever.

### No PII

All generated text is synthesized parliamentary-style filler. The only proper
nouns are public-figure surnames already present in the committed
`datasets/*.json` — no private personal data is synthesized or included.

## Running the fixture tests

```bash
.venv/Scripts/python.exe -m pytest -v tests/evaluation
```
