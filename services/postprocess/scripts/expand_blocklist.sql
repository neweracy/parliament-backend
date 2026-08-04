-- Expand the block_list with common English words that cause false phonetic
-- and fuzzy matches with Ghanaian entity names in the correction engine.
--
-- These words are phonetically or edit-distance similar to constituency names,
-- MP names, or locations in the dataset but are ordinary English vocabulary
-- that should never be replaced.
--
-- Usage:
--   docker exec -i postprocess-postgres-1 psql -U postprocess -d postprocess < scripts/expand_blocklist.sql
--
-- Idempotent: uses INSERT ... ON CONFLICT DO NOTHING.

-- Common English words that phonetically match Ghanaian names
INSERT INTO block_list (token, list_kind, reason) VALUES
  -- Words that matched person names
  ('party', 'block', 'phonetic match to Agnes Naa Momo Lartey'),
  ('seven', 'block', 'phonetic match to Sege'),
  ('sense', 'block', 'phonetic match to Sege'),
  ('health', 'block', 'phonetic match to Alex Segbefia'),
  ('mercy', 'block', 'phonetic match to Ambrose Dery'),
  ('great', 'block', 'phonetic match to Mary Grant'),
  ('grant', 'block', 'phonetic match to Mary Grant'),
  ('among', 'block', 'phonetic match to Agona'),
  ('alone', 'block', 'phonetic match to Agona'),
  ('along', 'block', 'phonetic match to Agona'),
  ('simple', 'block', 'phonetic match to Winneba'),
  ('spoke', 'block', 'phonetic match to Kpone'),
  ('listen', 'block', 'phonetic match to Ato Austin'),
  ('thank', 'block', 'phonetic match to Ghana'),
  ('thanks', 'block', 'phonetic match to Ghana'),
  ('lay', 'block', 'phonetic match to La'),
  ('transportation', 'block', 'fuzzy match to Joseph Bukari Nikpe'),
  ('cab', 'block', 'substring match to Kabu/Carboo'),
  ('panic', 'block', 'phonetic match to People''s National Convention'),
  -- Parliamentary procedure terms
  ('majority', 'block', 'phonetic match to Osei Kyei-Mensah-Bonsu'),
  ('minority', 'block', 'phonetic match to Cassiel Ato Forson'),
  ('motion', 'block', 'generic parliamentary term'),
  ('bill', 'block', 'generic parliamentary term'),
  ('session', 'block', 'generic parliamentary term'),
  ('debate', 'block', 'generic parliamentary term'),
  ('vote', 'block', 'generic parliamentary term'),
  ('order', 'block', 'generic parliamentary term'),
  ('paper', 'block', 'generic parliamentary term'),
  ('page', 'block', 'generic parliamentary term'),
  ('house', 'block', 'generic parliamentary term'),
  ('chamber', 'block', 'generic parliamentary term'),
  ('committee', 'block', 'generic parliamentary term'),
  ('member', 'block', 'generic parliamentary term'),
  ('members', 'block', 'generic parliamentary term'),
  ('minister', 'block', 'generic parliamentary term - use title list instead'),
  ('deputy', 'block', 'generic parliamentary term'),
  ('president', 'block', 'generic parliamentary term'),
  ('general', 'block', 'generic parliamentary term'),
  ('national', 'block', 'generic parliamentary term'),
  ('government', 'block', 'generic parliamentary term'),
  ('parliament', 'block', 'generic parliamentary term'),
  ('constituency', 'block', 'generic parliamentary term'),
  ('republic', 'block', 'generic parliamentary term'),
  ('election', 'block', 'generic parliamentary term'),
  ('elections', 'block', 'generic parliamentary term'),
  -- Common English words with short edit distance to entities
  ('there', 'block', 'common word, fuzzy match risk'),
  ('their', 'block', 'common word, fuzzy match risk'),
  ('where', 'block', 'common word, fuzzy match risk'),
  ('these', 'block', 'common word, fuzzy match risk'),
  ('those', 'block', 'common word, fuzzy match risk'),
  ('other', 'block', 'common word, fuzzy match risk'),
  ('about', 'block', 'common word, fuzzy match risk'),
  ('would', 'block', 'common word, fuzzy match risk'),
  ('could', 'block', 'common word, fuzzy match risk'),
  ('should', 'block', 'common word, fuzzy match risk'),
  ('people', 'block', 'common word, fuzzy match risk'),
  ('during', 'block', 'common word, fuzzy match risk'),
  ('service', 'block', 'common word, fuzzy match risk'),
  ('leadership', 'block', 'common word, fuzzy match risk'),
  ('community', 'block', 'common word, fuzzy match risk'),
  ('development', 'block', 'common word, fuzzy match risk'),
  ('construction', 'block', 'common word, fuzzy match risk'),
  ('established', 'block', 'common word, fuzzy match risk'),
  ('particularly', 'block', 'common word, fuzzy match risk'),
  ('significantly', 'block', 'common word, fuzzy match risk'),
  ('immediately', 'block', 'common word, fuzzy match risk'),
  ('unfortunately', 'block', 'common word, fuzzy match risk'),
  ('approximately', 'block', 'common word, fuzzy match risk'),
  ('infrastructure', 'block', 'common word, fuzzy match risk'),
  ('contribution', 'block', 'common word, fuzzy match risk'),
  ('contributions', 'block', 'common word, fuzzy match risk'),
  ('administration', 'block', 'common word, fuzzy match risk'),
  ('statement', 'block', 'common word, fuzzy match risk'),
  ('important', 'block', 'common word, fuzzy match risk'),
  ('information', 'block', 'common word, fuzzy match risk'),
  ('governance', 'block', 'common word, fuzzy match risk'),
  ('democratic', 'block', 'common word, fuzzy match risk'),
  ('amendment', 'block', 'common word, fuzzy match risk'),
  ('amendments', 'block', 'common word, fuzzy match risk'),
  ('attention', 'block', 'common word, fuzzy match risk'),
  ('condition', 'block', 'common word, fuzzy match risk'),
  ('conditions', 'block', 'common word, fuzzy match risk'),
  ('education', 'block', 'common word, fuzzy match risk'),
  ('provision', 'block', 'common word, fuzzy match risk'),
  ('authority', 'block', 'common word, fuzzy match risk'),
  ('available', 'block', 'common word, fuzzy match risk'),
  ('district', 'block', 'common word, phonetic match to Bole'),
  ('please', 'block', 'common word, fuzzy match risk'),
  ('resume', 'block', 'common word, fuzzy match risk'),
  ('surprise', 'block', 'common word, fuzzy match risk'),
  ('reason', 'block', 'common word, fuzzy match risk'),
  ('priority', 'block', 'common word, fuzzy match risk')
ON CONFLICT DO NOTHING;

-- Also add stopword entries for the most common false-positive triggers
-- (stopwords are rejected before ANY strategy runs, not just fuzzy/phonetic)
INSERT INTO block_list (token, list_kind, reason) VALUES
  ('party', 'stopword', 'common English word - not a name'),
  ('seven', 'stopword', 'number word - not a name'),
  ('eight', 'stopword', 'number word - not a name'),
  ('nine', 'stopword', 'number word - not a name'),
  ('sense', 'stopword', 'common English word - not a name'),
  ('health', 'stopword', 'common English word - not a name'),
  ('mercy', 'stopword', 'common English word - not a name'),
  ('great', 'stopword', 'common English word - not a name'),
  ('among', 'stopword', 'common English word - not a name'),
  ('alone', 'stopword', 'common English word - not a name'),
  ('spoke', 'stopword', 'common English word - not a name'),
  ('simple', 'stopword', 'common English word - not a name'),
  ('thank', 'stopword', 'common English word - not a name'),
  ('thanks', 'stopword', 'common English word - not a name'),
  ('listen', 'stopword', 'common English word - not a name'),
  ('panic', 'stopword', 'common English word - not a name'),
  ('transportation', 'stopword', 'common English word - not a name'),
  ('lay', 'stopword', 'common English word - not a name'),
  ('cab', 'stopword', 'common English word - not a name')
ON CONFLICT DO NOTHING;

-- Additional false positive: "sage" matches "Sege"
INSERT INTO block_list (token, list_kind, reason) VALUES
  ('sage', 'block', 'phonetic match to Sege'),
  ('sage', 'stopword', 'common English word - not Sege location')
ON CONFLICT DO NOTHING;
