-- Users and the projects they've accessed (based on login events).
SELECT
  e.username,
  COALESCE(pk.label, e.project_key_hash) AS project_label,
  e.project_key_hash,
  MIN(e.created_at) AS first_seen,
  MAX(e.created_at) AS last_seen
FROM event_logs e
LEFT JOIN project_keys pk
  ON pk.hash = e.project_key_hash
WHERE e.event_type = 'login'
  AND e.username IS NOT NULL
GROUP BY
  e.username,
  COALESCE(pk.label, e.project_key_hash),
  e.project_key_hash
ORDER BY last_seen DESC;

-- All registered usernames (from the registration form).
SELECT
  username,
  name,
  affiliation,
  email,
  submitted_at
FROM registration_submissions
ORDER BY submitted_at DESC;
