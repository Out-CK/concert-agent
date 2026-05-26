-- Table 1: Raw web content collected during Concert Runs
CREATE TABLE IF NOT EXISTS event_web_database (
    id              BIGSERIAL PRIMARY KEY,
    web_batch_id    TEXT NOT NULL,           -- Format: MMDDYYYY (e.g. "05252026")
    source_url      TEXT NOT NULL,
    query_used      TEXT,                    -- The search query that produced this result
    round           INTEGER NOT NULL,        -- 1 = Search round, 2 = Extract round
    content         TEXT,                    -- Full markdown/text content of the page
    collected_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ewdb_web_batch_id ON event_web_database(web_batch_id);

-- Table 2: Structured, deduplicated upcoming concert entries
CREATE TABLE IF NOT EXISTS event_entry_database (
    id                          BIGSERIAL PRIMARY KEY,
    event_entry_id              TEXT UNIQUE NOT NULL,  -- Format: "000000000001"
    entry_batch_id              TEXT NOT NULL,         -- Format: "MMDDYYYY_HHMMSS"
    event_title                 TEXT NOT NULL,
    description                 TEXT,
    artist                      TEXT NOT NULL,
    venue                       TEXT NOT NULL,
    event_type                  TEXT DEFAULT 'concert',
    multi_day_event             BOOLEAN DEFAULT FALSE,
    date                        TEXT NOT NULL,         -- Format: "MM-DD-YYYY"
    start_time                  TEXT,                  -- Format: "00:00am" or "00:00pm"
    end_time                    TEXT,
    -- Ticket source slots (up to 4 per entry after deduplication merge)
    tickets_source_1            TEXT,
    tickets_webpage_contents_1  TEXT,
    tickets_source_2            TEXT,
    tickets_webpage_contents_2  TEXT,
    tickets_source_3            TEXT,
    tickets_webpage_contents_3  TEXT,
    tickets_source_4            TEXT,
    tickets_webpage_contents_4  TEXT,
    -- Non-ticket source slots (up to 4 per entry after deduplication merge)
    no_tickets_source_1         TEXT,
    no_tickets_webpage_contents_1 TEXT,
    no_tickets_source_2         TEXT,
    no_tickets_webpage_contents_2 TEXT,
    no_tickets_source_3         TEXT,
    no_tickets_webpage_contents_3 TEXT,
    no_tickets_source_4         TEXT,
    no_tickets_webpage_contents_4 TEXT,
    -- Raw source content used to build this entry
    webpage_contents            TEXT,
    address                     TEXT,                  -- Full street address of the venue
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eedb_artist_venue_date
    ON event_entry_database(artist, venue, date);

CREATE INDEX IF NOT EXISTS idx_eedb_date ON event_entry_database(date);

-- Table 3: Archived past events (same schema as event_entry_database)
CREATE TABLE IF NOT EXISTS past_event_entry_database (
    LIKE event_entry_database INCLUDING ALL
);

ALTER TABLE past_event_entry_database ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ DEFAULT NOW();
