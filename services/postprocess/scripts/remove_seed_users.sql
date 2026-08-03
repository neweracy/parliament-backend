-- remove_seed_users.sql
-- Removes all seeded sample users.
-- Run: psql $DATABASE_URL -f scripts/remove_seed_users.sql

DELETE FROM users WHERE id IN (
  'usr-admin-001',
  'usr-chief-editor-001',
  'usr-supervisor-001',
  'usr-editor-001',
  'usr-viewer-001'
);
