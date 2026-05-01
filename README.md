# Nalanda Telegram Invoice Bot

Sends document photos to Claude AI, extracts fields, saves to Google Sheets.

## Setup

### Step 1 — Create Telegram Bot
1. Open Telegram → search @BotFather
2. Send /newbot
3. Name it: Nalanda Scanner
4. Username: nalanda_scanner_bot (or any available)
5. Copy the token it gives you

### Step 2 — Deploy to Railway
1. Go to railway.app → sign up with GitHub
2. Click New Project → Deploy from GitHub repo
3. Upload these files to a new GitHub repo first
4. Connect that repo to Railway

### Step 3 — Set Environment Variables in Railway
In your Railway project → Variables tab, add:
- TELEGRAM_TOKEN = (from BotFather)
- CLAUDE_API_KEY = (your sk-ant-... key)
- APPS_SCRIPT_URL = (your Apps Script URL)
- SHEET_PREFIX = Nalanda

### Step 4 — Set Webhook
Once deployed, Railway gives you a URL like:
https://nalanda-bot-production.up.railway.app

Open this in browser to register the webhook:
https://nalanda-bot-production.up.railway.app/set_webhook?url=https://nalanda-bot-production.up.railway.app/webhook

You will see: {"ok":true,"description":"Webhook was set"}

### Step 5 — Test
Open Telegram → find your bot → send /start
Then send any invoice photo!

## How it works
1. You send a photo to the bot (or group with bot)
2. Bot downloads the image
3. Sends to Claude API for analysis
4. Claude returns doc type + all fields as JSON
5. Bot saves to Google Sheets via Apps Script
6. Bot replies with a summary

## Commands
/start - Welcome message
/help  - Help
/status - Check API connections
