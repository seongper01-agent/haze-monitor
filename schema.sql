-- Run this in Supabase SQL Editor to create the readings table
-- https://supabase.com/dashboard/project/icvorijpqdhqrwwjcoaa/sql/new

CREATE TABLE IF NOT EXISTS readings (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    region TEXT NOT NULL,
    psi_24h INTEGER,
    pm25_24h INTEGER,
    pm25_1h INTEGER,
    UNIQUE(timestamp, region)
);

CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_region ON readings(region);

-- Enable Row Level Security (optional but recommended)
ALTER TABLE readings ENABLE ROW LEVEL SECURITY;

-- Allow anon reads
CREATE POLICY "anon can read" ON readings
    FOR SELECT USING (true);

-- Allow service_role writes
CREATE POLICY "service can insert" ON readings
    FOR INSERT WITH CHECK (true);
CREATE POLICY "service can update" ON readings
    FOR UPDATE USING (true);