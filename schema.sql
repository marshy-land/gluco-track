-- Database schema for Gluco Track

CREATE TABLE IF NOT EXISTS glucose_readings (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    value DOUBLE PRECISION NOT NULL, -- glucose reading in mg/dL
    type VARCHAR(20) NOT NULL,       -- 'historic', 'scan', or 'live'
    device VARCHAR(100),
    serial_number VARCHAR(100),
    record_type INTEGER,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Unique index to prevent duplicate records
CREATE UNIQUE INDEX IF NOT EXISTS idx_glucose_readings_unique 
ON glucose_readings (timestamp, value);

-- Indexes for efficient querying by timestamp
CREATE INDEX IF NOT EXISTS idx_glucose_readings_timestamp ON glucose_readings (timestamp DESC);

-- Insulin doses table
CREATE TABLE IF NOT EXISTS insulin_doses (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    rapid_acting DOUBLE PRECISION,  -- Rapid-Acting Insulin (units)
    long_acting DOUBLE PRECISION,   -- Long-Acting Insulin (units)
    meal DOUBLE PRECISION,          -- Meal Insulin (units)
    correction DOUBLE PRECISION,    -- Correction Insulin (units)
    user_change DOUBLE PRECISION,   -- User Change Insulin (units)
    device VARCHAR(100),
    serial_number VARCHAR(100),
    is_imputed BOOLEAN DEFAULT FALSE,
    confidence_score DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Unique index to prevent duplicate insulin entries
CREATE UNIQUE INDEX IF NOT EXISTS idx_insulin_doses_unique 
ON insulin_doses (timestamp, rapid_acting, long_acting, meal, correction, user_change);

-- Index for efficient querying by timestamp
CREATE INDEX IF NOT EXISTS idx_insulin_doses_timestamp ON insulin_doses (timestamp DESC);

-- Food / Carbohydrate logs table
CREATE TABLE IF NOT EXISTS food_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    carbs_g DOUBLE PRECISION,      -- Grams of carbohydrates
    food_type VARCHAR(255),        -- Optional description of food
    is_imputed BOOLEAN DEFAULT FALSE,
    confidence_score DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Unique index to prevent duplicate food entries
CREATE UNIQUE INDEX IF NOT EXISTS idx_food_logs_unique 
ON food_logs (timestamp, carbs_g, food_type);

-- Index for efficient querying by timestamp
CREATE INDEX IF NOT EXISTS idx_food_logs_timestamp ON food_logs (timestamp DESC);

-- System settings and persistent state (e.g. heuristics parameters)
CREATE TABLE IF NOT EXISTS system_settings (
    key VARCHAR(255) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Add synced_to_libreview column
ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS synced_to_libreview BOOLEAN DEFAULT FALSE;
ALTER TABLE food_logs ADD COLUMN IF NOT EXISTS synced_to_libreview BOOLEAN DEFAULT FALSE;

-- Google Health / Fitness Data
CREATE TABLE IF NOT EXISTS health_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) UNIQUE NOT NULL, -- Google Fit Session ID
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    session_type VARCHAR(100) NOT NULL, -- e.g., 'sleep', 'sleep.light', 'sleep.deep', 'activity'
    session_name VARCHAR(255),
    duration_minutes DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_health_sessions_type ON health_sessions (session_type);
CREATE INDEX IF NOT EXISTS idx_health_sessions_start ON health_sessions (start_time DESC);

CREATE TABLE IF NOT EXISTS health_metrics (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    metric_type VARCHAR(100) NOT NULL, -- e.g., 'steps', 'heart_rate.resting'
    value DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_health_metrics_unique ON health_metrics (timestamp, metric_type);
CREATE INDEX IF NOT EXISTS idx_health_metrics_timestamp ON health_metrics (timestamp DESC);
