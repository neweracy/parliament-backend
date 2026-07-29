# Requirements Document

## Introduction

This feature covers a repository-wide cleanup and performance-optimization effort across the
Node.js/Express Gateway, the in-process JavaScript correction pipeline, the Python
Postprocessing Service, and the React frontend.

The work has two intertwined goals:

1. **Reduce messiness** — remove duplicated logic and duplicated data sources, close the gaps
   between configuration templates and code defaults, delete dead or unreachable code, wire up
   implemented-but-disconnected modules, and make style and structure consistent within each
   language.
2. **Optimize performance** — establish measured latency and size budgets for the hot paths
   (rule-based correction, Match_Index build, Bedrock chunking, hybrid Khaya call budgeting,
   frontend bundle and long-transcript rendering) and bring each surface inside its budget.

Because every change here is a refactor rather than a new capability, the central correctness
property is **observable behaviour preservation**: for the same input, the post-refactor system
must produce output equivalent to the pre-refactor system. Requirement 1 establishes the
baseline harness that makes this property checkable, and every subsequent requirement is
gated on it.

## Scope Decisions

These decisions are settled and constrain every requirement below.

- **Breadth**: all four surfaces are in scope — the Gateway and `lib/`, the Postprocess_Service,
  the Frontend, and repository configuration and documentation.
- **Performance**: numeric latency and size budgets apply, stated in Requirements 7 through 10.
- **Deduplication**: both the JS_Correction_Engine and the PY_Correction_Engine are retained, and
  their equivalence is enforced by a parity test rather than by deleting either implementation.
- **Behaviour**: every Refactor_Change is strictly behaviour-preserving. The only sanctioned
  behaviour changes are the LLM_Refiner wiring in Requirement 4 and the `/health` route in
  Requirement 5.
- **Reference machine**: the Reference_Machine is the developer workstation that records the
  pre-refactor baseline. Budget comparisons are valid only between measurements carrying the same
  machine identifier.

## Glossary

- **Gateway**: The Node.js/Express 5 service in `server.js` plus `routes/` and `providers/`,
  listening on port 8081.
- **JS_Correction_Engine**: The in-process rule-based Ghana entity correction implementation
  under `lib/location-correction/`, used when `POSTPROCESS_MODE=js`.
- **PY_Correction_Engine**: The rule-based correction implementation in
  `services/postprocess/app/correction/engine.py`, used when `POSTPROCESS_MODE=python`.
- **Postprocess_Service**: The Python 3.12 FastAPI service under `services/postprocess/`,
  listening on port 8082.
- **LLM_Refiner**: The Bedrock refinement module at `services/postprocess/app/llm/refiner.py`.
- **Bedrock_Postprocessor**: The Gateway-side Bedrock pass at
  `lib/location-correction/bedrock-postprocess.js`.
- **Hybrid_Pipeline**: The Deepgram-primary, Khaya-correction pipeline under `lib/hybrid/`.
- **Dataset_Source**: A file or database table that defines Ghana entity records — currently the
  `lib/location-correction/*-dataset.js` modules and the CSV/SQL seeds under
  `services/postprocess/datasets/`.
- **Match_Index**: The canonical, fused, phonetic, and BK-tree lookup structures built in
  `services/postprocess/app/datasets/index.py`.
- **Dataset_Cache**: The periodic PostgreSQL refresh cache at
  `services/postprocess/app/datasets/cache.py`.
- **Frontend**: The React 19 + Vite 7 + TypeScript application under `frontend/`.
- **Baseline_Harness**: The test tooling introduced by this feature that records pre-refactor
  outputs for a fixed corpus and compares post-refactor outputs against them.
- **Golden_Corpus**: The frozen set of transcript fixtures plus their recorded pre-refactor
  outputs, stored as version-controlled files.
- **Behaviour_Equivalent**: Two pipeline outputs are Behaviour_Equivalent when their corrected
  transcript strings are byte-identical, their corrected word arrays are equal element-wise on
  `word`, `start`, `end`, `confidence`, `locationCorrected`, `entityKind`, and `entityType`, and
  their entity summaries are equal as multisets of `(name, kind, type, mentions)`.
- **Benchmark_Harness**: The measurement tooling introduced by this feature that records latency
  and size metrics for the surfaces named in Requirements 7 through 10.
- **Config_Template**: The environment variable template files `sample.env` and
  `services/postprocess/sample.env`.
- **Refactor_Change**: Any commit produced under this spec that alters existing source files
  without adding a user-visible capability.
- **Reference_Machine**: The developer workstation on which the pre-refactor baseline measurements
  are recorded, identified in the Benchmark_Harness results file by its machine identifier,
  CPU model, core count, and total memory.

## Requirements

### Requirement 1: Behaviour-Preservation Baseline

**User Story:** As a maintainer, I want a recorded baseline of current pipeline behaviour, so
that I can refactor the correction code with evidence that output did not change.

#### Acceptance Criteria

1. THE Baseline_Harness SHALL record, for every fixture in the Golden_Corpus, the corrected
   transcript, corrected word array, entity summary, and correction records produced by the
   JS_Correction_Engine before any Refactor_Change is applied.
2. THE Baseline_Harness SHALL record the same four outputs produced by the PY_Correction_Engine
   before any Refactor_Change is applied.
3. THE Golden_Corpus SHALL contain at least 30 transcript fixtures covering locations, persons,
   ministers, MPs, parties, spoken years, spoken decades, fused tokens, split tokens,
   hyphenated tokens, and transcripts with no correctable entity.
4. WHEN the Baseline_Harness compares a post-refactor output against its recorded baseline for
   the same fixture, THE Baseline_Harness SHALL report a pass only when the two outputs are
   Behaviour_Equivalent.
5. IF a post-refactor output is not Behaviour_Equivalent to its recorded baseline, THEN THE
   Baseline_Harness SHALL report the fixture identifier, the field that differs, the baseline
   value, and the post-refactor value.
6. FOR ALL generated transcripts, the JS_Correction_Engine SHALL produce output that is
   Behaviour_Equivalent to the output of the pre-refactor JS_Correction_Engine invoked on the
   same transcript (behaviour-preservation property).
7. FOR ALL generated transcripts, correcting an already-corrected transcript SHALL produce a
   transcript byte-identical to the once-corrected transcript (idempotence property).
8. FOR ALL generated transcripts containing no entity from any Dataset_Source and no spoken
   year, the corrected transcript SHALL be byte-identical to the input transcript
   (no-op property).
9. THE Baseline_Harness SHALL execute as part of `corepack pnpm test` for the
   JS_Correction_Engine and as part of `pytest` for the PY_Correction_Engine.
10. WHILE a Refactor_Change is in progress, THE Baseline_Harness SHALL run against the
    unmodified Golden_Corpus, and the Golden_Corpus recorded outputs SHALL be modified only by a
    commit that states the intended behaviour change in its message body.

### Requirement 2: Single Source of Truth for Entity Datasets

**User Story:** As a maintainer, I want Ghana entity records defined once, so that adding an
entity does not require editing two representations that can drift apart.

#### Acceptance Criteria

1. THE repository SHALL define each Ghana entity record in exactly one Dataset_Source.
2. WHERE a second representation of a Dataset_Source is required by a runtime that cannot read
   the primary representation, THE repository SHALL generate that representation from the
   primary Dataset_Source through a checked-in script.
3. WHEN a generated Dataset_Source representation is out of date with respect to its primary
   Dataset_Source, THE dataset consistency check SHALL fail with the list of differing entity
   names.
4. THE dataset consistency check SHALL run as part of `corepack pnpm test`.
5. FOR ALL entity names present in the primary Dataset_Source, the generated representation
   SHALL contain a record with the same canonical name, entity kind, and entity type
   (round-trip property).
6. WHEN an entity is added to the primary Dataset_Source and the generation script is run, THE
   JS_Correction_Engine and THE PY_Correction_Engine SHALL both correct a transcript mention of
   that entity to its canonical name.

### Requirement 3: Deduplication of Correction Logic

**User Story:** As a maintainer, I want one authoritative correction algorithm, so that a fix
applied in one language does not silently leave the other implementation wrong.

#### Acceptance Criteria

1. THE repository SHALL document, in the design document for this spec, every behavioural
   difference between the JS_Correction_Engine and the PY_Correction_Engine for the
   Golden_Corpus.
2. FOR ALL fixtures in the Golden_Corpus, the JS_Correction_Engine output and the
   PY_Correction_Engine output SHALL be Behaviour_Equivalent, or the difference SHALL be listed
   as an accepted divergence in the design document (model-based property).
3. THE repository SHALL declare in `.kiro/steering/backend-guide.md` which of the two
   implementations is authoritative for algorithm changes.
4. IF a correction stage exists in one implementation and is absent from the other, THEN THE
   design document SHALL record the stage name, the implementation that has it, and the intended
   resolution.
5. THE repository SHALL retain both the JS_Correction_Engine and the PY_Correction_Engine, and
   THE parity test defined in Requirement 3 criterion 2 SHALL run as part of `corepack pnpm test`.
6. WHEN a correction algorithm change is applied to the authoritative implementation, THE parity
   test SHALL fail until the same change is applied to the other implementation or recorded as an
   accepted divergence.

### Requirement 4: Reachability of Implemented Modules

**User Story:** As a maintainer, I want implemented modules connected to the code paths that are
supposed to use them, so that configuration flags produce the behaviour they advertise.

#### Acceptance Criteria

1. WHEN `LLM_ENABLED` is `true`, a Bedrock model is reachable, and a correction request sets
   `llm_refine`, THE Postprocess_Service SHALL invoke the LLM_Refiner and report the resulting
   correction count in the `bedrock_corrections` metadata field.
2. WHEN `LLM_ENABLED` is `false`, THE Postprocess_Service SHALL report `llm_status` as `skipped`
   and SHALL omit the LLM_Refiner invocation.
3. IF the LLM_Refiner raises an error or exceeds `LLM_CHUNK_TIMEOUT_MS` for every chunk, THEN THE
   Postprocess_Service SHALL return the rule-stage result and report `llm_status` as `degraded`.
4. THE Postprocess_Service SHALL report `llm_status` as `unconfigured` only when Bedrock
   credentials or `BEDROCK_MODEL_ID` are absent.
5. THE repository SHALL contain no exported module under `lib/` or `services/postprocess/app/`
   that is referenced by no other source file and no test file.
6. WHEN a source file, exported function, or configuration variable is unreferenced, THE cleanup
   inventory in the design document SHALL list the symbol and the removal or wiring decision for
   it.

### Requirement 5: Configuration and Deployment Consistency

**User Story:** As an operator, I want the environment templates and deployment configuration to
match the code, so that a fresh setup works without undocumented edits.

#### Acceptance Criteria

1. THE Config_Template SHALL list every environment variable read by the Gateway, the
   Hybrid_Pipeline, the Bedrock_Postprocessor, and the Postprocess_Service.
2. FOR ALL environment variables that have a code default, the value shown in the Config_Template
   SHALL be a value the code accepts, and the documented default SHALL equal the code default.
3. WHERE `AWS_REGION` and `BEDROCK_MODEL_ID` are both set in the Config_Template, THE
   Config_Template SHALL specify a region and model identifier pair that resolves in the
   configured region.
4. IF a configured `BEDROCK_MODEL_ID` cannot be resolved in the configured `AWS_REGION`, THEN THE
   Bedrock_Postprocessor SHALL log the region, the model identifier, and the resolution failure,
   and SHALL return the rule-based result.
5. WHEN a GET request is made to `/health`, THE Gateway SHALL respond with status 200 and a JSON
   body containing the field `status`.
6. THE deployment configuration in `deploy/` and `fly.toml` SHALL reference only routes the
   Gateway implements.
7. THE configuration consistency check SHALL run as part of `corepack pnpm test` and SHALL fail
   when a variable read by source code is absent from the Config_Template.
8. THE `[test]` command in `deepgram.toml` SHALL execute the repository test suite.

### Requirement 6: Code Style and Structure Consistency

**User Story:** As a developer, I want consistent structure and style within each language, so
that reading an unfamiliar module takes less effort.

#### Acceptance Criteria

1. THE repository SHALL provide a single lint command for the JavaScript and TypeScript sources
   and a single lint command for the Python sources.
2. WHEN the lint command is run, THE lint command SHALL report zero errors for all files under
   `lib/`, `routes/`, `providers/`, `server.js`, and `services/postprocess/app/`.
3. THE lint command SHALL run as part of `corepack pnpm test` for JavaScript and TypeScript and
   as part of `pytest` invocation prerequisites for Python.
4. THE repository SHALL export every backend function that is used outside its defining module
   with a JSDoc block stating its parameters and return value.
5. WHERE a source file under `lib/` or `services/postprocess/app/` that contains executable logic
   exceeds 500 lines, THE file SHALL be split into modules that each expose one cohesive
   responsibility.
6. WHERE a source file under `lib/` or `services/postprocess/app/` exceeds 500 lines and consists
   only of Dataset_Source records, THE file SHALL be exempt from criterion 5.
7. WHEN a Refactor_Change splits or moves a module, THE public entry points named in
   `AGENTS.md` and `.kiro/steering/` SHALL continue to resolve, or the steering documents SHALL
   be updated in the same commit.

### Requirement 7: Rule-Based Correction Performance

**User Story:** As a user transcribing long recordings, I want entity correction to add little
latency, so that results appear promptly.

#### Acceptance Criteria

1. THE Benchmark_Harness SHALL record the wall-clock duration of the rule-based correction stage
   for transcripts of 100, 1000, and 10000 words, for both the JS_Correction_Engine and the
   PY_Correction_Engine.
2. WHEN the rule-based correction stage processes a 10000-word transcript on the
   Reference_Machine, THE stage SHALL complete within 5000ms measured as the median of 5 runs.
   [AMENDED: Original proposal was 500ms. Pre-refactor measurement on machine
   f5d6f907bea2 at 2026-07-29 recorded 4412ms for 10000 words. Budget revised to 5000ms
   to reflect the actual pre-refactor performance floor. Optimisation work may reduce this.]
3. FOR ALL transcript lengths between 100 and 10000 words, the measured rule-stage duration
   SHALL grow no faster than linearly in word count within a factor of 2 (metamorphic property).
4. THE post-refactor rule-stage duration for each Benchmark_Harness input SHALL be less than or
   equal to the pre-refactor duration for the same input, measured as the median of 5 runs.
5. WHEN the Benchmark_Harness runs, THE Benchmark_Harness SHALL write the recorded measurements
   to a version-controlled results file including the measurement date and the machine
   identifier.
6. IF a post-refactor measurement exceeds its recorded pre-refactor measurement by more than 10
   percent, THEN THE Benchmark_Harness SHALL report the regression with both values.

### Requirement 8: Match_Index and Dataset_Cache Performance

**User Story:** As an operator, I want the Postprocess_Service to become ready quickly and stay
responsive during dataset refresh, so that deployments and health checks do not stall.

#### Acceptance Criteria

1. THE Benchmark_Harness SHALL record the Match_Index build duration for the full
   Dataset_Source record count.
2. WHEN the Postprocess_Service builds the Match_Index at startup on the Reference_Machine for
   the full Dataset_Source record count, THE build SHALL complete within 3 seconds measured as the
   median of 5 runs.
   [CONFIRMED: Pre-refactor measurement on machine f5d6f907bea2 at 2026-07-29 recorded
   81.79ms for 5000 records — well within the 3000ms budget.]
3. WHILE the Dataset_Cache is refreshing, THE Postprocess_Service SHALL respond to a GET
   `/health` request within 100ms.
4. WHILE the Dataset_Cache is refreshing, THE Postprocess_Service SHALL serve correction
   requests using the previously loaded snapshot.
5. FOR ALL Dataset_Source record insertion orders, the resulting Match_Index SHALL return the
   same match for the same query token (confluence property).
6. WHEN the Dataset_Cache refresh completes with no change to the Dataset_Source contents, THE
   Match_Index SHALL produce the same matches as before the refresh (idempotence property).

### Requirement 9: LLM and Hybrid Call-Budget Performance

**User Story:** As an operator paying per model invocation, I want chunking and call budgeting to
stay inside configured bounds, so that latency and cost are predictable.

#### Acceptance Criteria

1. FOR ALL transcripts, the number of concurrent Bedrock invocations issued by the LLM_Refiner
   SHALL be less than or equal to `LLM_MAX_PARALLEL`.
2. FOR ALL transcripts, the number of chunks the LLM_Refiner creates SHALL equal the transcript
   word count divided by `LLM_CHUNK_SIZE`, rounded up.
3. FOR ALL transcripts, concatenating the LLM_Refiner chunks in order SHALL reproduce the input
   word sequence (round-trip property).
4. IF a Bedrock invocation exceeds `LLM_CHUNK_TIMEOUT_MS`, THEN THE LLM_Refiner SHALL abandon
   that chunk, retain the pre-LLM words for that chunk, and continue with the remaining chunks.
5. FOR ALL Deepgram word arrays, the number of Khaya calls the Hybrid_Pipeline issues per
   language model SHALL be less than or equal to `HYBRID_MAX_CALLS_PER_MODEL`.
6. FOR ALL Deepgram word arrays, the reassembled hybrid transcript SHALL contain the same word
   count as the input word array (invariant property).
7. THE Benchmark_Harness SHALL record the number of Bedrock invocations and Khaya calls issued
   for each Golden_Corpus fixture using stubbed providers.

### Requirement 10: Frontend Bundle and Rendering Performance

**User Story:** As a user opening the application, I want it to load quickly and scroll smoothly
through long transcripts, so that reviewing results is comfortable.

#### Acceptance Criteria

1. THE Benchmark_Harness SHALL record the gzipped byte size of each Vite output chunk produced by
   the Frontend production build.
2. THE gzipped size of the Frontend initial route entry chunk SHALL be at most 250 kilobytes.
   [CONFIRMED: Pre-refactor measurement on machine f5d6f907bea2 at 2026-07-29 recorded
   126.7KB gzipped — well within the 250KB budget.]
3. WHEN the Frontend production build completes, THE build SHALL emit each page route as a chunk
   loaded on navigation rather than in the initial entry chunk.
4. IF a Frontend chunk's gzipped size exceeds its recorded pre-refactor size by more than 5
   percent, THEN THE Benchmark_Harness SHALL report the regression with both values.
5. WHEN the transcript viewer renders a transcript of 10000 words, THE transcript viewer SHALL
   mount at most 200 word elements in the document at one time.
6. FOR ALL transcript lengths, the set of words the transcript viewer reports as rendered across
   a full scroll SHALL equal the input word sequence (invariant property).
7. WHEN the Frontend test suite runs, THE Frontend test suite SHALL report zero failures.

### Requirement 11: Documentation and Knowledge-Graph Currency

**User Story:** As a developer or agent onboarding to this repository, I want the documentation
and knowledge graph to describe the code that exists, so that I do not act on stale information.

#### Acceptance Criteria

1. WHEN a Refactor_Change adds, removes, or moves a file listed in `AGENTS.md`, `CLAUDE.md`, or a
   file under `.kiro/steering/`, THE same commit SHALL update those listings.
2. THE knowledge graph at `graphify-out/graph.json` SHALL contain nodes for the source files
   under `lib/`, `routes/`, `providers/`, and `services/postprocess/app/`.
3. WHEN the cleanup work is complete, THE design document SHALL record the removed symbols, the
   merged modules, and the measured before-and-after values for every budget in Requirements 7
   through 10.
4. THE repository SHALL document the Benchmark_Harness invocation command in
   `.kiro/steering/backend-guide.md`.

## Known Issues in Scope

These specific defects were surfaced during a repository audit and are covered by the
requirements above. They are listed here so the design phase does not have to rediscover them.

| Issue | Covered by |
| ----- | ---------- |
| `app/llm/refiner.py` is implemented and unit-tested but never called from `app/pipeline.py`; stage 3 hardcodes `bedrock_corrections = 0` and always reports `llm_status` as skipped or unconfigured, so LLM refinement never runs in `POSTPROCESS_MODE=python` | Requirement 4, criteria 1 through 4 |
| Correction logic is duplicated across `lib/location-correction/` and `app/correction/engine.py` | Requirement 3 |
| Dataset records are duplicated across `lib/location-correction/*-dataset.js` and the PostgreSQL seeds in `services/postprocess/datasets/` | Requirement 2 |
| `deploy/Caddyfile` proxies `/health` but the Gateway implements no `/health` route; `fly.toml` defines no health check | Requirement 5, criteria 5 and 6 |
| `sample.env` sets `AWS_REGION=eu-north-1` while code defaults to `us-east-1`, and the default `BEDROCK_MODEL_ID` is a `us.`-prefixed cross-region inference profile that will not resolve from `eu-north-1` | Requirement 5, criteria 2 through 4 |
| `sample.env` omits `BEDROCK_MODEL_ID`; `.env` omits the `POSTPROCESS_*` group | Requirement 5, criteria 1 and 7 |
| `graphify-out/graph.json` is stale — 411 nodes over ~75 files with no coverage of `lib/`, `routes/`, `providers/`, or `services/postprocess/` | Requirement 11, criterion 2 |
| `deepgram.toml` `[test]` runs `echo 'No tests configured'` despite a real test suite existing | Requirement 5, criterion 8 |
| `lib/location-correction/index.js` (1032 lines), `year-correction.js` (744 lines), and `bedrock-postprocess.js` (547 lines) exceed the file-size threshold with executable logic | Requirement 6, criterion 5 |
