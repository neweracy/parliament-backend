# Backend Architecture Recommendation — Honest Assessment

## The Truth About the Current Backend

Let me be frank: **the current Node.js/Express backend is reaching its limits**, but not for the reason you might think.

### What It Does Well

The Node.js backend is perfectly fine for:
- Serving API requests (Express handles this effortlessly)
- In-memory dataset lookups (214 persons, 2,190 aliases — this is tiny, ~500 KB in RAM)
- Proxying audio to Deepgram/Khaya (streaming I/O is Node's strength)
- The rule-based correction engine (string matching, Levenshtein distance — fast in any language)

### Where It's Struggling

The problems aren't about Node.js performance. They're about **code organization and the nature of the workload**:

1. **Single-file server.js is 700+ lines** — the correction pipeline, route handlers, formatting, and post-processing are all tangled together. This is a maintainability problem, not a performance problem.

2. **The correction pipeline is getting complex** — 3-word joins, title-person matching, phonetic matching, fuzzy matching, year correction, Bedrock alignment. This is NLP work stuffed into string manipulation. It works, but every new edge case (Tarkwa/Dankwa, page/Paga, general/Central) requires a new blocklist entry. You're playing whack-a-mole.

3. **Bedrock prompt engineering is fragile** — you're injecting the entire dataset into the system prompt (~2000 tokens). As datasets grow (you just went from 50 to 214 persons), this prompt will exceed token limits. And every time the LLM "helpfully" corrects something wrong, you add another rule to the prompt.

4. **The hybrid pipeline is complex audio processing** — slicing audio, racing language models, confidence scoring. Node can do this, but Python's ecosystem (numpy, pydub, librosa) makes it trivial.

### The Core Question

> Can the current backend handle this as it grows?

**For serving requests: yes.** Node.js + Express will handle 1,000s of concurrent transcription requests without breaking a sweat.

**For the NLP/correction pipeline: it's the wrong tool.** You're building what is essentially a Named Entity Recognition (NER) system from scratch using string matching. This approach doesn't scale — every new MP, every new location misspelling, every new ASR quirk requires manual dataset entries.

---

## What You Should Actually Build

### The Right Architecture: Split the Workload

```
┌─────────────────────────────────────────────────────────┐
│                    API Layer (Node.js)                    │
│  Express/Fastify — routes, auth, file handling, SSE     │
│  KEEP THIS. It's good at what it does.                  │
└────────────────────────────┬────────────────────────────┘
                             │ HTTP/gRPC
┌────────────────────────────▼────────────────────────────┐
│              NLP/Correction Service (Python)              │
│  FastAPI — entity recognition, correction, training      │
│  THIS IS WHAT YOU NEED.                                  │
└────────────────────────────┬────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
    ┌────▼────┐       ┌─────▼─────┐      ┌─────▼─────┐
    │ Dataset │       │  Bedrock  │      │  Khaya AI │
    │  Store  │       │  (Claude) │      │  (GhaNLP) │
    │(Postgres│       │           │      │           │
    │/DynamoDB)│      └───────────┘      └───────────┘
    └─────────┘
```

### Why This Split?

| Concern | Node.js (API Layer) | Python (NLP Service) |
|---------|--------------------|--------------------|
| Request routing | ✅ Excellent | Overkill |
| File upload handling | ✅ Excellent (Multer, streams) | Fine but not its strength |
| WebSocket/SSE | ✅ Native | Possible but awkward |
| String matching NER | Works, but you're reinventing the wheel | ✅ spaCy, fuzzywuzzy, rapidfuzz |
| Audio processing | Needs ffmpeg shelling out | ✅ pydub, librosa, numpy native |
| ML/fine-tuning | Not feasible | ✅ transformers, torch, scikit-learn |
| Bedrock SDK | Works fine | ✅ boto3 (first-class AWS SDK) |
| Dataset management | In-memory JS arrays (fragile) | ✅ SQLAlchemy/pandas + DB |

---

## Recommended Stack

### Option A: Python Microservice (Recommended)

Keep your Node.js frontend API. Add a Python service that handles all NLP:

**Framework: FastAPI**

Why FastAPI over Flask/Django:
- Async by default (handles concurrent Bedrock/Khaya calls natively)
- Type hints with Pydantic (your dataset schemas get validated)
- Auto-generated OpenAPI docs
- 10x faster than Flask for I/O-bound work
- Easy WebSocket support

**NLP Libraries:**
- `rapidfuzz` — 100x faster than your hand-rolled Levenshtein (C extension)
- `spaCy` — proper NER pipeline (train a custom model on your Ghana entities)
- `phonetics` — Soundex/Metaphone for phonetic matching (replaces your phoneticKey function)
- `boto3` — first-class Bedrock integration (better than the JS SDK for LLM work)
- `pydub` — audio slicing without shelling out to ffmpeg

**Dataset Storage:**
- PostgreSQL with `pg_trgm` extension — fuzzy text search built into the database
- Or DynamoDB if you want serverless

### Option B: Full Python Rewrite

If you're starting fresh, just use Python for everything:

**Framework: FastAPI + Uvicorn**

```python
# What your correction pipeline looks like in Python with proper tools:

from rapidfuzz import fuzz, process
from spacy import load

# This replaces your entire 1000-line correction engine:
nlp = load("models/ghana_ner")  # Custom trained NER model

def correct_transcript(text: str) -> CorrectionResult:
    doc = nlp(text)
    corrections = []
    for ent in doc.ents:
        if ent.label_ in ("PERSON", "LOCATION", "PARTY"):
            # Find best match in dataset using fuzzy matching
            match = process.extractOne(
                ent.text, 
                dataset[ent.label_], 
                scorer=fuzz.WRatio,
                score_cutoff=85
            )
            if match:
                corrections.append(Correction(
                    original=ent.text,
                    corrected=match[0],
                    confidence=match[1] / 100,
                ))
    return apply_corrections(text, corrections)
```

That's ~20 lines replacing ~1000 lines of JS string manipulation.

### Option C: Keep Node.js, Fix the Architecture (Least Disruption)

If you don't want to introduce Python:

1. **Extract the correction engine into a separate service** (still Node, but its own process)
2. **Use a proper fuzzy search library** — `fuse.js` or `flexsearch` instead of hand-rolled Levenshtein
3. **Move datasets to PostgreSQL** — query with `pg_trgm` similarity instead of in-memory loops
4. **Use Bedrock Knowledge Bases** instead of stuffing datasets into prompts

---

## How the Dataset Should Be Used

### Current Approach (What You Have)

```
Startup: Load all 214 persons + 2190 aliases into memory
Per request: Loop through ALL entries, compute Levenshtein for each
```

This is O(n × m) where n = words in transcript, m = total aliases. Right now that's ~228 words × 2190 aliases = ~500,000 comparisons per transcription. It's fast because the dataset is small. **When you add all ministers since 1957 (500+ people, 5000+ aliases), it'll be 1.1M comparisons.**

### Better Approach (What You Should Do)

**1. Trie/prefix tree for exact + fused matches (instant)**

```python
from pygtrie import CharTrie

# Build once at startup
trie = CharTrie()
for person in all_persons:
    trie[person.canonical.lower()] = person
    for alias in person.aliases:
        trie[alias.lower()] = person

# O(k) lookup where k = length of the query string
result = trie.get(word.lower())
```

**2. BK-tree for fuzzy/edit-distance matches (fast)**

A BK-tree indexes strings by edit distance, so you only check candidates within your threshold — not the entire dataset.

```python
from pybktree import BKTree

tree = BKTree(levenshtein_distance, all_aliases)
# Find all aliases within edit distance 2 of "akufado":
matches = tree.find("akufado", 2)  # Returns only close matches, not all 5000
```

**3. Trained NER model for context-aware matching (best)**

Instead of matching every word against every alias:
1. Train a spaCy NER model on your existing transcripts (you have labeled data — every `locationCorrected: true` word is a training example)
2. The model learns CONTEXT — "President ___" is likely a person, "the ___ region" is likely a location
3. Only run fuzzy matching on detected entities, not every word

This eliminates 99% of false positives (page→Paga, general→Central) because the model understands grammar.

**4. Bedrock Knowledge Base for LLM grounding**

Instead of injecting 2000 tokens of dataset into every prompt:
- Upload your datasets to S3
- Create a Bedrock Knowledge Base with vector embeddings
- At correction time, retrieve only the relevant 5-10 entities
- Send those to Claude with the transcript chunk

This scales to unlimited dataset size (100,000 entries) without prompt bloat.

---

## My Recommendation

### Short term (next 2 weeks): Keep Node, fix the immediate pain

- Move datasets to a JSON file loaded at startup (not hardcoded in code)
- Add a pre-computed index (Map) for O(1) exact alias lookups (you already do this)
- Add the BK-tree for fuzzy matching (npm `bk-tree` package exists)
- Move Bedrock to use IAM roles instead of env var keys

### Medium term (1-2 months): Add Python NLP service

- Create a FastAPI service for the correction pipeline
- Use `rapidfuzz` for fuzzy matching (100x faster)
- Move datasets to PostgreSQL with `pg_trgm`
- Keep Node.js as the API gateway (it's good at that)
- The Node API calls the Python service via internal HTTP

### Long term (3+ months): Train a custom NER model

- Use your existing corrected transcripts as training data
- Train a spaCy or Hugging Face model to recognize Ghana entities
- The model handles context (eliminates general→Central class of bugs entirely)
- Bedrock becomes a refinement step, not the primary correction engine

---

## Cost of Each Approach

| Approach | Dev Time | Monthly Cost | Accuracy | Scalability |
|----------|----------|-------------|----------|-------------|
| Current (Node + manual rules) | Ongoing (endless whack-a-mole) | $5-10 Bedrock | Good for known entities | Poor — every new name needs manual aliases |
| Python microservice + rapidfuzz | 2-3 weeks | $5-10 Bedrock + $5 compute | Better (faster fuzzy matching) | Good — database-driven |
| Python + trained NER model | 1-2 months | $5-10 Bedrock + $15 compute | Excellent (context-aware) | Excellent — learns from data |
| Bedrock Knowledge Base + RAG | 1 week | $15-20 Bedrock | Very good | Excellent — unlimited dataset |

---

## Final Verdict

**Don't rewrite everything in Python.** Keep your Node.js API — it's solid for what it does. But the NLP correction pipeline has outgrown string manipulation in JavaScript. 

The pragmatic path:
1. **Now:** Add a Python FastAPI service for correction (call it from Node via HTTP)
2. **Soon:** Move datasets to PostgreSQL, use `pg_trgm` for fuzzy search
3. **Later:** Train a custom NER model on your correction history

Your instinct about Python is correct — it's the right language for this kind of work. But you don't need to throw away the Node.js frontend/API layer. They can coexist.
