# Synology Chat

Connect Hermes to Synology Chat via a **Bot Integration** — supporting channel chats, direct messages, and media uploads with your self-hosted Synology Chat server.

## Overview

The Synology Chat adapter allows you to self-host Hermes as a chat bot on your Synology NAS:

- Receive incoming messages from Synology Chat using **Outgoing Webhooks/Bot Callbacks** (routed to a local `aiohttp` web server)
- Send replies asynchronously to specific users or channels via Synology Chat's **Chatbot/Incoming Webapi**
- Secure your bot using user allowlists (`SYNOLOGY_CHAT_ALLOWED_USERS`)

## Prerequisites

1. **Create a Synology Chat Bot**:
   - Open Synology Chat in your web browser.
   - Click your profile photo in the top-right corner and select **Integration**.
   - Navigate to **Bots** on the left menu and click **Create**.
   - Fill in the Bot details (Name, Description, Avatar).
   - Copy the generated **Outgoing Token** — this will be your `SYNOLOGY_CHAT_BOT_TOKEN`.
   - Set the **Outgoing URL** to point to your Hermes gateway webhooks endpoint: `http://<your-hermes-host>:<port>/webhooks/synology` (e.g. `http://192.168.1.100:8645/webhooks/synology`).

2. **Dependencies**:
   - The adapter uses standard Python libraries and `aiohttp` (which is already included as part of the core gateway requirements).

## Configuration

### Interactive setup

```bash
hermes gateway setup
```

Select **Synology Chat** from the platform list and follow the prompts.

### Manual configuration

Set the required environment variables in your `~/.hermes/.env` file:

```bash
SYNOLOGY_CHAT_BOT_TOKEN=your_copied_outgoing_token
SYNOLOGY_CHAT_API_URL=https://your-synology-nas:5001
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `SYNOLOGY_CHAT_BOT_TOKEN` | Outgoing/Verification Token from Synology Chat Bot setup (required) | — |
| `SYNOLOGY_CHAT_API_URL` | Base URL of your Synology NAS (including port and protocol) (required) | — |
| `SYNOLOGY_CHAT_WEBHOOK_PORT` | The port the local webhook server will listen on | `8645` |
| `SYNOLOGY_CHAT_WEBHOOK_HOST` | The bind host the local webhook server will listen on | `0.0.0.0` |
| `SYNOLOGY_CHAT_ALLOWED_USERS` | Comma-separated list of Synology Chat user IDs allowed to interact with the bot | open (all users) |
| `SYNOLOGY_CHAT_ALLOW_ALL_USERS` | Set to `true` to disable authorization checks and allow everyone | `false` |
| `SYNOLOGY_CHAT_HOME_CHANNEL` | Default channel or user ID for notification/cron delivery (e.g., `channel:12` or `dm:5`) | — |

## Target Formatting

When sending automated cron notifications, or utilizing the `send_message` tool, target Synology Chat channels and users using these formatted IDs:

- **Channels**: `channel:<channel_id>` (e.g. `channel:10`)
- **Direct Messages**: `dm:<user_id>` (e.g. `dm:5`)

## Troubleshooting

### Webhook payload is rejected with 403 Forbidden
Make sure the **Outgoing Token** generated in Synology Chat's Integration settings exactly matches your configured `SYNOLOGY_CHAT_BOT_TOKEN` in your environment.

### NAS cannot reach your Outgoing URL
Ensure that your Hermes host's port (default `8645`) is open, accessible, and not blocked by a firewall on either the Synology NAS or the host running the Hermes Gateway.
