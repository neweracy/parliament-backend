-- Enable the pg_trgm extension for fuzzy text matching in the Dataset_Store.
-- Executed automatically on first database initialization via docker-entrypoint-initdb.d.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
