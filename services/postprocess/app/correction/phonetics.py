"""Phonetic key generation optimized for West African/Ghanaian ASR transcription.

Re-exports `_phonetic_key` from the Match_Index module as the public API
`phonetic_key` for the Correction_Engine to use.

The function collapses common ASR substitution patterns (digraph folding,
vowel merging, double-consonant stripping) so that phonetically similar
inputs map to the same key — enabling phonetic matching of misspelled
entity names.

Requirements: 3.4
"""

from __future__ import annotations

from app.datasets.index import _phonetic_key


def phonetic_key(s: str) -> str:
    """Generate a phonetic key optimized for West African/Ghanaian ASR names.

    Collapses common ASR substitution patterns — identical to the JS
    ``phoneticKey`` function:

    - Strip spaces, hyphens, and apostrophes; lowercase
    - ph → f
    - gh (not at end) → g
    - ck → k
    - ei/ey → e, ou/oo → u
    - aa → a, ee → e, ii → i
    - Collapse doubled consonants

    Examples::

        phonetic_key("Ningo-Prampram") → "ningoprampram" (after folding)
        phonetic_key("Koumasi") → "kumasi"
        phonetic_key("Ghanah") → "gana"
    """
    return _phonetic_key(s)
