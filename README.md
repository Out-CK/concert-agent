# Concert Agent

## Overview

Concert Agent is an automated NYC concert discovery system. It runs daily, searches the web for upcoming concerts using the Nimble API, parses structured event data with Claude AI, deduplicates results, and stores everything in a Supabase database. A scheduler fires the full pipeline every morning at 9:00 AM Eastern Time.

## Architecture

| Module | Purpose |
|---|---|
| `agent/concert_agent.py` | Top-level orchestrator — runs the full 12-step Concert Run pipeline |
| `agent/search_plan.py` | LLM subagent — generates 40 search queries per run |
| `agent/link_finder.py` | LLM subagent — extracts concert-specific URLs from raw web content |
| `agent/web_batch_parser.py` | LLM subagent — parses raw pages into structured Event Entries |
| `agent/duplicate_finder.py` | LLM subagent — deduplicates within a batch and against the DB |
| `agent/past_event_archiver.py` | Moves expired Event Entries to the archive table |
| `tools/nimble_search_tool.py` | LangChain tool wrapping the Nimble Search API |
| `tools/nimble_extract_tool.py` | LangChain tool wrapping the Nimble Extract API |
| `db/operations.py` | All Supabase read/write functions |
| `scheduler/job_scheduler.py` | APScheduler cron job (daily, 9 AM ET) |
| `utils/id_generator.py` | Globally-unique, sequential `event_entry_id` generation |
| `utils/logger.py` | Structured logging setup |

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/OUT-CK/concert-agent.git
cd concert-agent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in all required values
```

### 4. Database Setup

Open the [Supabase SQL editor](https://supabase.com/dashboard) for your project and run the contents of `db/schema.sql`. This creates the three required tables:

- `event_web_database` — raw web content from each Concert Run
- `event_entry_database` — structured, deduplicated upcoming events
- `past_event_entry_database` — archived past events

## Usage

```bash
# Trigger a Concert Run immediately
python main.py --run-now

# Start the daily scheduler (blocks until Ctrl+C)
python main.py --schedule
```

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude AI calls |
| `NIMBLE_API_KEY` | Nimble API key for web search and extraction |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (full DB access) |
| `GITHUB_TOKEN` | GitHub personal access token (for repo setup script only) |
| `GITHUB_ORG` | GitHub organization name (default: `OUT-CK`) |
| `GITHUB_REPO` | GitHub repository name (default: `concert-agent`) |
| `LOG_LEVEL` | Optional. `DEBUG` for verbose output, `INFO` by default |

## Database Schema

### `event_web_database`
Stores raw web content collected during each Concert Run. Keyed by `web_batch_id` (format: `MMDDYYYY`). `round=1` for search results, `round=2` for link-finder extractions.

### `event_entry_database`
Stores all clean, structured, upcoming concert events. Each row has a unique `event_entry_id` (12-digit zero-padded), up to 4 ticket source slots, and up to 4 non-ticket source slots per entry.

### `past_event_entry_database`
Identical schema to `event_entry_database` plus an `archived_at` timestamp. Events are moved here when their date passes.
