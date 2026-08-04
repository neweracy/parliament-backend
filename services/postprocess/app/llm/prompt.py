"""System prompt construction for the LLM_Refiner.

Ports the 13 correction rules verbatim from the JavaScript system prompt in
``lib/location-correction/bedrock-postprocess.js``, builds the entity reference
section from per-chunk retrieved records, and estimates prompt token count.

Requirements: 12.6, 12.7, 12.8, 12.9, 12.10, 11.10, 11.11
"""

from __future__ import annotations

from app.models.entities import EntityRecord

# ---------------------------------------------------------------------------
# The 13 correction rules — ported verbatim from the JS getSystemPrompt()
# ---------------------------------------------------------------------------

_SYSTEM_PREAMBLE = """\
You are a post-processing assistant for Ghanaian parliamentary transcripts (Hansard).

Your job: Fix proper nouns in ASR output using the reference data below. The transcript has already been partially corrected by a rule-based system, but some low-confidence words remain incorrect."""

_CORRECTION_RULES = """\
CORRECTION RULES:
1. Use the reference data above as your source of truth for valid names, locations, parties
2. Fix misspelled proper nouns to their correct form from the reference data
3. Apply proper capitalization to all names, titles, places, and party names
4. If a word sounds phonetically similar to a name in the reference data, correct it — BUT only when the surrounding context CLEARLY indicates a person, place, or party was being named. A word in the middle of a normal English sentence is NOT a proper noun just because it sounds similar.
5. "honorable" or "hon" before a name = MP title, capitalize: "Honorable"
6. Party abbreviations (NDC, NPP, CPP) should stay as abbreviations, properly capitalized
7. Do NOT change words that are already correct
8. Do NOT add or remove words — only fix spelling/capitalization
9. Do NOT add punctuation or restructure sentences
10. CRITICAL: Do NOT convert common English words into entity names. Examples of FORBIDDEN changes:
    - "community" must NOT become "Tema Community 2" or any location
    - "later" must NOT become a person's name
    - "democratic" must NOT become "Democratic Freedom Party"
    - "general" must NOT become "Central"
    - "nation" must NOT become "Nanton"
    - "group" must NOT become a person's name
    - "promote" must NOT become a name
    - "evolved" must NOT become a name
    - "this group later evolved into" is normal English — leave it unchanged
    If a word functions as a normal English word in its sentence, NEVER replace it with an entity name, regardless of phonetic similarity.
11. Do NOT convert spoken numbers into numeric year format. For example: "twenty six" must NOT become "2006", "nineteen ninety two" must NOT become "1992". Year conversion is handled by a separate system — leave all number words as-is.
12. Do NOT replace parts of a person's name with a location name. For example: "Dankwa" in "Nana Addo Dankwa Akufo-Addo" must NOT become "Tarkwa". "Dramani" in "John Dramani Mahama" must NOT become "Damongo" or "Shama". Names already corrected by the rule-based system are authoritative — preserve them.
13. Return ONLY the corrected text with [Segment N]: labels matching the input format"""


def _format_entity_records(entity_records: list[EntityRecord]) -> str:
    """Format entity records into a compact REFERENCE_DATA block.

    Groups records by entity_kind and renders each as a compact line:
      - Locations: "canonical (type, region)" or "canonical (type)"
      - Persons: "canonical [role]" with aliases if present
      - Parties: "canonical (abbr)" or "canonical"
    """
    if not entity_records:
        return "<REFERENCE_DATA>\nNo specific entity data retrieved for this segment.\n</REFERENCE_DATA>"

    locations: list[str] = []
    persons: list[str] = []
    parties: list[str] = []

    for record in entity_records:
        kind = record.entity_kind.value if hasattr(record.entity_kind, "value") else str(record.entity_kind)

        if kind == "location":
            parts = [record.canonical]
            attrs: list[str] = []
            if record.entity_type:
                etype = record.entity_type.value if hasattr(record.entity_type, "value") else str(record.entity_type)
                attrs.append(etype)
            if record.region:
                attrs.append(record.region)
            if attrs:
                parts.append(f"({', '.join(attrs)})")
            locations.append(" ".join(parts))

        elif kind == "person":
            line = record.canonical
            if record.role:
                line += f" [{record.role}]"
            if record.aliases:
                line += f" (aliases: {', '.join(record.aliases[:3])})"
            persons.append(line)

        elif kind == "party":
            line = record.canonical
            # Party records may have an abbreviation stored in aliases
            if record.aliases:
                line += f" ({record.aliases[0]})"
            parties.append(line)

        else:
            # Unknown kind — still include it
            locations.append(record.canonical)

    sections: list[str] = []

    if locations:
        sections.append("<LOCATIONS>\n" + ", ".join(locations) + "\n</LOCATIONS>")

    if persons:
        sections.append("<OFFICIALS_AND_MPS>\n" + "\n".join(persons) + "\n</OFFICIALS_AND_MPS>")

    if parties:
        sections.append("<POLITICAL_PARTIES>\n" + "\n".join(parties) + "\n</POLITICAL_PARTIES>")

    return "<REFERENCE_DATA>\n" + "\n\n".join(sections) + "\n</REFERENCE_DATA>"


def build_system_prompt(entity_records: list[EntityRecord]) -> str:
    """Construct the full system prompt with entity reference section.

    Args:
        entity_records: The retrieved Entity_Records relevant to the chunk.
            Should already be capped at LLM_MAX_PROMPT_RECORDS.

    Returns:
        The complete system prompt string for a Bedrock invocation.
    """
    reference_block = _format_entity_records(entity_records)

    return f"""{_SYSTEM_PREAMBLE}

{reference_block}

{_CORRECTION_RULES}"""


def build_user_content(text: str, segment_number: int) -> str:
    """Format the chunk text as the user message for the LLM.

    Args:
        text: The chunk text to be refined.
        segment_number: The segment label number (1-based).

    Returns:
        The formatted user message string.
    """
    return f"[Segment {segment_number}]: {text}"


def estimate_prompt_tokens(system_prompt: str, user_content: str) -> int:
    """Estimate the prompt token count using a rough word-based approximation.

    Claude tokenizes at roughly 0.75 tokens per word for English text, but
    for safety and simplicity we use a conservative 1.3 tokens per word
    (accounting for proper nouns, punctuation, and XML tags).

    This is intentionally approximate — exact tokenization would require
    the model's tokenizer, which is not available. The value is used for
    monitoring prompt growth, not billing.

    Args:
        system_prompt: The system prompt text.
        user_content: The user message text.

    Returns:
        Estimated token count as an integer.
    """
    combined = system_prompt + " " + user_content
    # Split on whitespace for word count
    word_count = len(combined.split())
    # Conservative estimate: ~1.3 tokens per word on average
    return int(word_count * 1.3)
