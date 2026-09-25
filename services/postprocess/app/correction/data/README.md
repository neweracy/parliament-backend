# Correction data artifacts

## `english_lexicon.txt`

A bundled list of ordinary lowercase English word forms used by the
`Lexicon_Gate` (Requirement 2 of the `correction-precision-gating` spec) to
identify Spans that are ordinary English vocabulary rather than mangled
Ghanaian proper nouns.

- One lowercase, whitespace-free token per line.
- Contains at least 25,000 single-token forms, including plural, past-tense,
  present-participle, comparative, and superlative inflected forms.
- Ordinary-English words only — no proper nouns, so it does not collide with
  the Dataset_Cache entity/alias set (Deterministic strategies take precedence
  per Req 2.9 regardless).

### Source

Derived from the public-domain [dwyl/english-words](https://github.com/dwyl/english-words)
`words_alpha.txt` list (released under the Unlicense — public domain). The
raw list is filtered to purely alphabetic ASCII tokens of length >= 2 and
lowercased. The list is bundled and committed so neither the build nor the
test suite requires network access to load it.
