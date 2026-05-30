-- Optional credentials for TikTok web login (pilot)
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS password TEXT;
