# Provider Gating Decision — Unknown-confidence Lexicon_Gate for `khaya` and `hybrid`

Spec: `correction-precision-gating` · Task 2.12.1 · Requirements 1.5, 1.10, 2.6, 2.7
(Assumption 1)

Status: **DECISION RECORDED — requires a Requirement 2.6 + Assumption 1 amendment
before task 2.12.3 implements it.** No behaviour is changed by this task; tasks
2.12.2 and 2.12.3 carry the implementation.

This record mirrors the in-code decision style used for the `title_person`
classification (task 1.3, `app/correction/strategies.py` module docstring): state
the open item, make one reasoned decision, and record the rationale, the gates it
affects, and any contract/requirement conflict — rather than silently
implementing behaviour that contradicts the written requirement.

---

## 1. The open item

`CorrectionOptions.provider` (`Literal["deepgram", "khaya", "hybrid"]`, default
`"deepgram"`) already exists on `app/models/request.py` and is read nowhere in the
service — a third unused signal alongside per-word `confidence` and
`punctuated_word`.

The three providers differ structurally in what they can supply:

- **`deepgram`** returns a word list with per-word timestamps and per-word
  `confidence`. Span_Confidence is a real number for most Spans.
- **`khaya`** returns **text only** — no word list, no per-word timestamps, no
  per-word confidence. Every Span therefore has an **empty covered-Word set** or
  Words carrying no `confidence`, so Span_Confidence is **Unknown** for every Span
  (Req 1.5, and Req 1.10 for the empty-Words-list case).
- **`hybrid`** mixes the two; on any Span sourced from Khaya, Span_Confidence is
  likewise Unknown.

Now trace the gates for an Unknown-confidence Span:

- **Confidence_Gate (Req 1.2, 1.6):** inert. With Span_Confidence Unknown,
  `confidence_gate_blocks_approximate` returns `False`, so the gate never
  *excludes* approximate strategies. (This is correct and intended — Assumption 1
  says the Confidence_Gate "becomes inert" when confidence is absent.)
- **Lexicon_Gate (Req 2.6):** **fires unconditionally.** Req 2.6 reads: *"WHILE
  Span_Confidence is Unknown, THE Correction_Engine SHALL reject for every
  Approximate_Strategy member a single-token Span that is present in the
  English_Lexicon and absent from the Dataset_Cache alias set."* There is no
  confidence value available to reach the Lexicon_Override_Threshold exemption of
  Req 2.7 (that exemption is only reachable when Span_Confidence is *known* and
  strictly below the override). So on a Khaya/hybrid transcript, **every
  lexicon-member Span is unconditionally kept out of approximate matching.**

The consequence: a set of thresholds calibrated for Deepgram's acoustic
confidence is applied, in its most aggressive form, to Ghanaian-language content
processed by the provider (Khaya) that exists specifically to handle Ghanaian
languages — exactly where mangled Ghanaian names most need repair. The spec
handles *absent* confidence (Assumption 1) but never provider-specific
*calibration*. That is the gap this decision closes.

## 2. The decision

**No — a `khaya`/`hybrid` transcript with Unknown Span_Confidence should NOT
trigger the unconditional Lexicon_Gate rejection of Req 2.6. The
Unknown-confidence path is to be calibrated per provider.**

Concretely, for the `khaya` and `hybrid` providers, the Unknown-confidence branch
of the Lexicon_Gate is to be treated as a **calibrated** decision (a
provider-specific Unknown-confidence policy carried on the gate profile), not as
the blanket "reject every lexicon-member Span" that Req 2.6 mandates. The
`deepgram` provider keeps the current Req 2.6 behaviour exactly.

The mechanism (a frozen per-provider gate profile resolved in `Settings`, gated
behind a new boolean flag defaulting to disabled so an all-flags-off
configuration stays baseline-equivalent per Req 12.9) is specified by tasks
2.12.2 and 2.12.3. This task records only the decision and its consequences.

### Rationale

1. **Provider intent.** Khaya is the Ghanaian-language ASR. The whole point of
   the spec is to *keep* the engine's ability to fix mangled Ghanaian names while
   it stops rewriting ordinary English words. On the Khaya path, "Unknown
   confidence" is not weak evidence that a word was heard clearly — it is the
   structural absence of a signal the provider was never built to emit. Treating
   that absence as grounds to *suppress* approximate matching inverts the spec's
   intent precisely where it matters most.

2. **The Req 2.7 override is unreachable, not merely unused.** Req 2.7 exempts a
   lexicon Span from the gate when its confidence is known and below the override.
   On Khaya/hybrid there is never a known confidence, so the exemption can never
   fire. The safety valve the spec designed for borderline words is structurally
   inaccessible for an entire provider. A provider-calibrated Unknown-confidence
   policy restores an equivalent safety valve.

3. **The English_Lexicon overlaps Ghanaian proper nouns.** Assumption 3 already
   acknowledges overlaps ("Grant", "Bole") and relies on Deterministic_Strategy
   precedence to protect them. But Deterministic strategies only fire on an exact
   key lookup; a *mangled* Ghanaian name (the case this whole spec exists to fix)
   reaches only the Approximate strategies — which Req 2.6 unconditionally blocks
   whenever the token also happens to be an English word and confidence is
   Unknown. On Deepgram this is bounded by the confidence signal; on Khaya it is
   unbounded.

4. **Baseline safety is preserved.** The `deepgram` profile keeps today's Req 2.6
   behaviour byte-for-byte, and the whole per-provider mechanism sits behind a
   flag defaulting to disabled (Req 12.9). Nothing changes for the current
   production path until an operator opts in.

## 3. Gates this decision affects

| Gate | Effect of this decision |
| --- | --- |
| **Lexicon_Gate** (Req 2.5–2.8, engine) | Primary. The Unknown-confidence branch (Req 2.6) becomes provider-calibrated for `khaya`/`hybrid`; unchanged for `deepgram`. |
| **Confidence_Gate** (Req 1.2, 1.6, `confidence.py`) | Indirect. Already inert under Unknown confidence (returns `False`); this decision does not change that, but the provider value is threaded through the same `GateContext` so 2.12.3 can branch both gates from one place. |
| Deterministic strategies (`exact`, `fused`, `joined`, `initials`, `title_person`) | Unaffected — never gated by confidence or lexicon (Req 2.9, 2.14). |
| Other Approximate sub-gates (`phonetic_key`, `phonetic_similarity`, `key_fanout`, `absolute_distance`, `relative_distance`, `candidate_bound`, `component_ambiguity`) | Not directly changed here, though a provider profile may later carry `min_phonetic_similarity` / `max_relative_distance` overrides (task 2.12.2). |
| `context`, `sitting_scope`, `llm_veto` | Unaffected by this decision. |

## 4. Requirement / assumption conflict — MUST be amended before implementation

This decision **contradicts Requirement 2.6 as written.** Req 2.6 mandates the
unconditional rejection *whenever* Span_Confidence is Unknown, with no provider
qualification. It also sits against **Assumption 1**, which states plainly:

> "Where a provider omits [confidence and punctuated_word], the Confidence_Gate
> becomes inert and the Lexicon_Gate applies **unconditionally**, per Requirements
> 1 and 2."

Both must be amended **before** task 2.12.3 branches gate evaluation on provider.
Recommended amendments:

- **Requirement 2.6** — qualify the unconditional Unknown-confidence rejection as
  the behaviour for a provider that *can* supply confidence (i.e. `deepgram`), and
  add a criterion permitting a provider-calibrated Unknown-confidence policy for
  providers that structurally cannot supply per-word confidence (`khaya`,
  `hybrid`), resolved from the per-provider gate profile.
- **Assumption 1** — replace "the Lexicon_Gate applies unconditionally" with a
  statement that a provider structurally unable to supply confidence uses a
  provider-calibrated Unknown-confidence policy rather than blanket rejection, and
  cross-reference the new Req 2.6 criterion.

Until those amendments land, tasks 2.12.2 and 2.12.3 must keep the mechanism
behind a flag defaulting to disabled, and the `deepgram` profile must reproduce
Req 2.6 exactly, so no shipped behaviour contradicts the current requirement text.

**This task does not implement the behaviour change.** It records the decision,
the rationale, the affected gates, and the amendment flag only. Do not silently
implement behaviour that contradicts Req 2.6.

## 5. A note on observability (forward reference to task 2.12.4)

Per-provider *metric* slicing would need a Req 13.3 amendment, because Req 13.3
restricts the gate-rejection metric's dimensions to `gate` plus dimension names
already emitted. Task 2.12.4 therefore routes provider visibility through
debug-level per-Span rejection logs (Req 13.5) and the Evaluation_Harness report
(task 2.12.5) instead of adding a metric dimension. If per-provider metric slicing
is later judged worthwhile, it is a flagged change requiring a Req 13.3 amendment
— not to be added silently.
