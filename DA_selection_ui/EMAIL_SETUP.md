# Notification Setup (ntfy)

This app sends a lightweight notification whenever a user submits a final selection.
Notifications are delivered via ntfy (push to phone/desktop) instead of SMTP.

## Required environment variables

Set these in your `.env` (or export in the shell):

```
NTFY_TOPIC=your-secret-topic
```

Optional overrides:

```
# Use a full URL instead of topic + server (takes precedence)
NTFY_URL=https://ntfy.sh/your-secret-topic

# Change the server (defaults to https://ntfy.sh)
NTFY_SERVER=https://ntfy.sh

# Add auth for private topics (Bearer token)
NTFY_TOKEN=your_token

# Customize notification metadata
NTFY_TITLE=Discovery Agent
NTFY_PRIORITY=5

# Limit message size (default 4000 chars)
NTFY_MAX_LEN=4000
```

Notes:
- Treat the topic name like a password; anyone with the topic can read messages.
- If you set `NTFY_URL`, it will be used as-is and `NTFY_TOPIC` is ignored.
- `NTFY_TOKEN` is optional for public topics but required for private ones.

---

## Quick start

1) Pick a topic name (e.g., `da-selection-<random>`).
2) Subscribe to it in the ntfy app or web UI.
3) Set `NTFY_TOPIC` in your `.env`.
4) Restart the app and submit a selection to trigger a notification.

---

## Troubleshooting

- **No notification received**: Check that `NTFY_TOPIC` (or `NTFY_URL`) is set.
- **Unauthorized**: Confirm `NTFY_TOKEN` matches the topic's auth configuration.
- **Message too long**: Adjust `NTFY_MAX_LEN` to keep notifications readable.
