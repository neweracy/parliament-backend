-- Sync the rbac_config table to the canonical matrix in lib/permissions.js.
--
-- require-permission.js resolves permissions from this table first and only
-- falls back to the static map when a role's row is missing or empty, so the
-- table must agree with lib/permissions.js or enforcement diverges from what
-- the login response advertises.
--
-- Usage:
--   docker exec -i postprocess-postgres-1 psql -U postprocess -d postprocess < scripts/sync-rbac-config.sql
--
-- Idempotent: upserts by role and always refreshes updated_at so the 60s
-- staleness probe in lib/rbac-config.js picks the change up.
--
-- Hierarchy (strictly monotonic — each role is a superset of the one below):
--   Viewer (3) < Editor (8) < Supervisor (12) < Chief Editor (17) < Admin (18)

BEGIN;

-- Viewer — read-only access to published material.
INSERT INTO rbac_config (role, permissions, updated_at) VALUES (
  'Viewer',
  ARRAY[
    'view_records',
    'search_hansard',
    'export_published'
  ]::text[],
  now()
)
ON CONFLICT (role) DO UPDATE
  SET permissions = EXCLUDED.permissions, updated_at = now();

-- Editor — transcript production: upload audio, edit, submit for review.
INSERT INTO rbac_config (role, permissions, updated_at) VALUES (
  'Editor',
  ARRAY[
    'view_records',
    'search_hansard',
    'export_published',
    'upload_audio',
    'edit_record',
    'rename_speakers',
    'submit_for_review',
    'export_drafts'
  ]::text[],
  now()
)
ON CONFLICT (role) DO UPDATE
  SET permissions = EXCLUDED.permissions, updated_at = now();

-- Supervisor — quality control: review submitted work, approve certification.
INSERT INTO rbac_config (role, permissions, updated_at) VALUES (
  'Supervisor',
  ARRAY[
    'view_records',
    'search_hansard',
    'export_published',
    'upload_audio',
    'edit_record',
    'rename_speakers',
    'submit_for_review',
    'export_drafts',
    'review_record',
    'approve_certification',
    'view_audit_trail',
    'export_hansard'
  ]::text[],
  now()
)
ON CONFLICT (role) DO UPDATE
  SET permissions = EXCLUDED.permissions, updated_at = now();

-- Chief Editor — editorial management: sittings, assignment, certification,
-- templates, and user administration. Deliberately NOT system_config.
INSERT INTO rbac_config (role, permissions, updated_at) VALUES (
  'Chief Editor',
  ARRAY[
    'view_records',
    'search_hansard',
    'export_published',
    'upload_audio',
    'edit_record',
    'rename_speakers',
    'submit_for_review',
    'export_drafts',
    'review_record',
    'approve_certification',
    'view_audit_trail',
    'export_hansard',
    'create_sitting',
    'assign_editor',
    'certify_record',
    'manage_templates',
    'manage_users'
  ]::text[],
  now()
)
ON CONFLICT (role) DO UPDATE
  SET permissions = EXCLUDED.permissions, updated_at = now();

-- Admin — full access, including system_config.
INSERT INTO rbac_config (role, permissions, updated_at) VALUES (
  'Admin',
  ARRAY[
    'view_records',
    'search_hansard',
    'export_published',
    'upload_audio',
    'edit_record',
    'rename_speakers',
    'submit_for_review',
    'export_drafts',
    'review_record',
    'approve_certification',
    'view_audit_trail',
    'export_hansard',
    'create_sitting',
    'assign_editor',
    'certify_record',
    'manage_templates',
    'manage_users',
    'system_config'
  ]::text[],
  now()
)
ON CONFLICT (role) DO UPDATE
  SET permissions = EXCLUDED.permissions, updated_at = now();

COMMIT;

-- Verify: counts must be 3 / 8 / 12 / 17 / 18.
SELECT role, cardinality(permissions) AS permission_count
FROM rbac_config
ORDER BY cardinality(permissions);
