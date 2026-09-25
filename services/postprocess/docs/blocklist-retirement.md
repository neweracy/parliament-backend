# Block_List Retirement and Manual-Override Retention

Spec: `correction-precision-gating` · Task 6.5 · Requirements 11.3, 11.4, 11.6,
11.7, 11.8, 11.9

Status: **Block_List retired as the primary defence; retained as a manual
override escape hatch.** No table, data, migration, or runtime lookup is
deleted by this spec. What is retired is the *practice* of growing the list
per sitting.

---

## 1. What changed

The hand-curated Block_List used to be the engine's front-line defence against
false-positive corrections. Every time an ordinary English word was rewritten
into a Ghanaian name — "thank" → Ghana, "sage" → Sege, "district" → Bole,
"transportation" → Joseph Bukari Nikpe, "later" → Lartey — the fix was to append
another row to the Block_List. The list grew to roughly 130 hand-written entries
and grew again with every new sitting.

That practice is retired. The list is replaced as the *primary* defence by the
precision gates introduced by this spec:

- **Confidence_Gate** (Req 1) — a Span the ASR heard clearly (Span_Confidence at
  or above the High_Confidence_Threshold, default 0.90) is restricted to the
  Deterministic strategies, so a confident ordinary English word never reaches
  approximate matching.
- **Lexicon_Gate** (Req 2) — a Span that is an ordinary English word (present in
  the bundled English_Lexicon, absent from the alias set) is kept out of
  approximate matching without a hand-written row.
- **Evidence-scaled scoring** (Req 3) — approximate-match confidence is computed
  from measured evidence (string similarity, phonetic key length, key fanout,
  relative edit distance) rather than a flat per-strategy constant, so the
  existing acceptance threshold rejects weak matches.
- **Context_Gate** (Req 7) — an approximate person-name correction requires
  contextual support (a title prefix, "Member for", a nearby constituency, or an
  uppercase capitalization prior) before it is applied.

The knowledge encoded in the ~130 rows is preserved as tests, not deleted. See
section 4.

## 2. What is retained — the manual-override escape hatch (Req 11.3, 11.4)

The Block_List **table, its Dataset_Cache loading path, and its runtime lookup
are retained unchanged** so an operator can hard-block a specific token without a
code change and without a redeployment (Req 11.3):

- The `is_blocked(token, snapshot)` accessor
  (`app/correction/blocklist.py`) is retained. It lowercases the token and tests
  membership against `snapshot.block_list`, so the check is case-insensitive
  (Req 11.4).
- The guard is wired into **every Approximate_Strategy** in
  `app/correction/strategies.py`:
  - `match_phonetic` — rejects a blocked token before the phonetic lookup.
  - `match_fuzzy` — rejects a blocked token before fuzzy scoring (the original
    Req 4.1 guard).
  - `match_component` — rejects a blocked token before component matching.
  - `match_substring` — the legacy substring path retains its guard.
- A blocked token is therefore rejected for every approximate strategy,
  case-insensitively, **regardless of Span_Confidence, Evidence_Score, or
  Context_Signal** (Req 11.4). The guard sits inside each approximate strategy,
  ahead of any scoring or gating, so no confidence value, evidence score, or
  context can override it.
- The **Deterministic strategies** (`exact`, `fused`, `joined`, `initials`) are
  never guarded by the Block_List and continue to evaluate for a blocked token
  (Req 11.8). Blocking a token suppresses only *approximate* rewriting of it; an
  exact key hit is still applied.
- Because the Block_List is loaded on the Dataset_Cache refresh timer, an
  operator who adds or removes a row sees the change take effect within one
  refresh interval, with no service restart (Req 11.9).

The Block_List is thus demoted from primary defence to a rarely-used manual
override. It should **not grow per sitting** — a new false positive is a signal
to check the gates (or add a Negative_Set fixture), not to append a row.

## 3. Retirement procedure — removing an entry (Req 11.6)

The list is retired by removing table rows over time, not by dropping the table
or altering the schema (Req 11.7). No applied Alembic migration is modified.

Before removing a Block_List entry:

1. **Add a Negative_Set fixture covering the entry token.** The former
   block-kind entries already live as fixtures in
   `tests/evaluation/blocklist_fixtures.py`; a new token being retired must have
   a fixture there (token, recorded reason or empty, evaluation Span_Confidence).
2. **Run the Evaluation_Harness in the gated configuration
   and confirm the token is left unchanged.** The harness
   (`tests/evaluation/test_measure.py`) runs the real engine over the corpus with
   the Block_List **empty** and asserts at least 98% of the former Block_List
   tokens are unchanged (Req 11.2, 11.10). If the gated run leaves the token
   unchanged, the gates handle it and the row can be removed.
3. **If a later run changes the token, restore the entry.** Re-add the row to the
   Block_List (a data-only change; it takes effect within one refresh interval,
   Req 11.9) and open an issue to investigate why the gates did not hold. The
   fixture stays in place so the regression is caught by the harness.

Removing a row without steps 1–2 is not permitted: the fixture and the passing
gated run are the evidence that the gates, not the list, now handle the token.

## 4. Evidence that the gates handle the former list (harness results)

The former Block_List is preserved as Negative_Set fixtures and measured, not
asserted:

- `tests/evaluation/blocklist_fixtures.py` holds a single snapshot of the former
  block-kind entries — at least 130 ordinary English tokens (Req 11.1), each with
  its recorded reason (e.g. "thank" → "phonetic match to Ghana", "sage" →
  "phonetic match to Sege", "district" → "common word, phonetic match to Bole",
  "transportation" → "fuzzy match to Joseph Bukari Nikpe", "later" → "fuzzy match
  to Lartey"). The harness evaluates each at Span_Confidence 0.97 (at/above the
  default High_Confidence_Threshold), so the Confidence_Gate and Lexicon_Gate —
  not the list — keep them out of approximate matching.
- `tests/evaluation/dataset.build_fixture_snapshot` builds the harness snapshot
  with **no** block list, so any token left unchanged is handled by the gates
  alone.
- `TestBlockListRetirement.test_former_blocklist_unchanged_without_list` in
  `tests/evaluation/test_measure.py` asserts at least 98% of the former
  Block_List tokens are unchanged with the list empty (Req 11.2, 11.10).
- `TestBlockListRetirement.test_recorded_false_positives_not_miscorrected`
  asserts the five recorded false positives ("thank", "sage", "district",
  "transportation", "later"), supplied with a high Word_Confidence, are not
  mis-corrected under gating (Req 16.13).
- `TestStopwordParity.test_deterministic_corrections_preserved_under_gating`
  asserts the gates act only on approximate matches — Deterministic and
  stopword/word-stopword decisions match the Baseline (Req 11.5).

Together these prove the former Block_List entries are now handled by gating
rather than by the hand-curated list, which is exactly what allows the list to be
retired as the primary defence while retained as a manual override.

## 5. Confirmation that the manual override still works (Req 11.4, 11.8)

The retained escape hatch is confirmed in code and tests:

- **Code:** `is_blocked` (`app/correction/blocklist.py`) is called inside every
  approximate strategy in `app/correction/strategies.py` (phonetic, fuzzy,
  component, substring), ahead of any scoring or gating.
- **Tests:**
  - `tests/unit/test_phonetics_blocklist.py::TestIsBlocked` covers the accessor
    (case-insensitive membership).
  - `tests/unit/test_engine.py::TestBlockListManualOverride` confirms at the
    engine-dispatch level that a token present in `snapshot.block_list` is left
    uncorrected — it is rejected for the approximate strategies regardless of the
    per-word confidence supplied, while a Deterministic (exact) match on a
    blocked token still applies (Req 11.4, 11.8).

An operator adding a row to the Block_List table therefore still hard-blocks
approximate correction of that token within one refresh interval, without a code
change or restart.
