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
