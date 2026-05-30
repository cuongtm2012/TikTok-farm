-- TikTok Farm — PostgreSQL schema (auto-run on first container start)

CREATE TABLE IF NOT EXISTS proxies (
    id SERIAL PRIMARY KEY,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    protocol TEXT DEFAULT 'http',
    username TEXT,
    password TEXT,
    status TEXT DEFAULT 'active',
    last_checked TIMESTAMP,
    fail_count INTEGER DEFAULT 0,
    UNIQUE (ip, port, protocol)
);

CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    proxy_id INTEGER REFERENCES proxies(id),
    status TEXT DEFAULT 'pending',
    followers INTEGER DEFAULT 0,
    following INTEGER DEFAULT 0,
    total_posts INTEGER DEFAULT 0,
    total_views INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,
    cookie_data TEXT,
    password TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    tiktok_post_id TEXT,
    content_path TEXT,
    caption TEXT,
    hashtags TEXT,
    affiliate_link TEXT,
    status TEXT DEFAULT 'pending',
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    scheduled_at TIMESTAMP,
    posted_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS farm_activities (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    activity_type TEXT,
    duration_seconds INTEGER,
    actions_count INTEGER,
    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    alert_type TEXT,
    message TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_posts_account_status ON posts(account_id, status);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
