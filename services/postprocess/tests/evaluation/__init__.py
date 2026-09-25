"""Evaluation_Harness fixtures and loaders (Req 10, 11, 15).

This package holds the DATA and LOADERS the offline Evaluation_Harness consumes:

* :mod:`tests.evaluation.dataset` — the fixed in-memory Dataset_Cache snapshot
  (a curated subset of the committed Ghanaian entity datasets) that the harness
  runs the REAL Correction_Engine against, plus a loader for the bundled
  English_Lexicon.
* :mod:`tests.evaluation.corpus` — the labelled Evaluation_Corpus (Negative_Set
  and Positive_Set, Req 10.1-10.3) and the former-Block_List Negative_Set
  fixtures (Req 11.1).
* :mod:`tests.evaluation.benchmark` — the deterministic Benchmark_Set generator
  and loader (Req 15.2) used for Rule_Latency benchmarking.

Precision/recall measurement (task 6.4.2) and latency benchmarking (task 6.4.3)
are separate tasks that consume these fixtures. This package creates the data
and its loaders only.

Every fixture is loaded from locally bundled data with no network request and
no AWS call (Req 10.8), and every loader returns identical data on repeated
calls (Req 15.2 — the Benchmark_Set is identical on every run).
"""

from __future__ import annotations
