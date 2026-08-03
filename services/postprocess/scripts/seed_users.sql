-- seed_users.sql
-- Populates the users table with one sample user per role.
-- Run: psql $DATABASE_URL -f scripts/seed_users.sql

INSERT INTO users (id, email, name, role, status, department, last_active) VALUES
  ('usr-admin-001',        'admin@parliament.gov.gh',        'Kwame Adjei',     'Admin',        'Active', 'IT Department',       now() - interval '5 minutes'),
  ('usr-chief-editor-001', 'chief.editor@parliament.gov.gh', 'Sarah Mensah',    'Chief Editor', 'Active', 'Hansard Department',  now() - interval '10 minutes'),
  ('usr-supervisor-001',   'supervisor@parliament.gov.gh',   'Kofi Arhin',      'Supervisor',   'Active', 'Hansard Department',  now() - interval '30 minutes'),
  ('usr-editor-001',       'editor@parliament.gov.gh',       'Ama Boateng',     'Editor',       'Active', 'Hansard Department',  now() - interval '1 hour'),
  ('usr-viewer-001',       'viewer@parliament.gov.gh',       'Nana Agyeman',    'Viewer',       'Active', 'Clerk''s Office',     now() - interval '2 hours')
ON CONFLICT (id) DO NOTHING;
