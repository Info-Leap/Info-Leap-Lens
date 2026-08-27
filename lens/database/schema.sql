-- LENS Long-Format Survey Schema
-- Designed for maximum flexibility across any number of categories,
-- questionnaires, and variables. Adding new survey data = new rows, not columns.

CREATE TABLE IF NOT EXISTS projects (
    project_id      VARCHAR(50) PRIMARY KEY,
    project_name    VARCHAR(200) NOT NULL,
    client_name     VARCHAR(200),
    wave            VARCHAR(50),
    fieldwork_start DATE,
    fieldwork_end   DATE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS respondents (
    respondent_id   VARCHAR(50) PRIMARY KEY,
    project_id      VARCHAR(50) REFERENCES projects(project_id),
    masked_name     VARCHAR(50) NOT NULL,     -- e.g. R_a3f9 — never real name
    city            VARCHAR(100),
    zone            VARCHAR(50),
    gender          VARCHAR(20),
    age_band        VARCHAR(20),
    sec_class       VARCHAR(10),              -- NCCS A/B/C
    category        VARCHAR(100),            -- e.g. Mixer Grinder
    respondent_type VARCHAR(20),             -- recent_buyer / intender
    is_affluent     BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS variables (
    variable_code   VARCHAR(100) PRIMARY KEY,
    project_id      VARCHAR(50) REFERENCES projects(project_id),
    category        VARCHAR(100),
    section         VARCHAR(100),            -- e.g. Brand Health, Need Attributes
    question_type   VARCHAR(50),             -- bq3a / bq3b / pq1 / aq4 etc.
    label_en        VARCHAR(500),
    label_hi        VARCHAR(500),
    scale_min       INTEGER,
    scale_max       INTEGER,
    feature_bucket  VARCHAR(100)             -- Product Performance / Design / etc.
);

CREATE TABLE IF NOT EXISTS responses (
    response_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    respondent_id   VARCHAR(50) REFERENCES respondents(respondent_id),
    variable_code   VARCHAR(100) REFERENCES variables(variable_code),
    value_numeric   NUMERIC,                 -- for importance scores, ratings
    value_text      VARCHAR(500),            -- for categorical responses
    value_boolean   BOOLEAN,                 -- for yes/no
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS need_attributes (
    attribute_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    variable_code   VARCHAR(100) REFERENCES variables(variable_code),
    category        VARCHAR(100),
    label_en        VARCHAR(500),
    label_hi        VARCHAR(500),
    feature_bucket  VARCHAR(100),
    is_active       BOOLEAN DEFAULT TRUE
);

-- Indexes for fast query routing
CREATE INDEX IF NOT EXISTS idx_responses_variable ON responses(variable_code);
CREATE INDEX IF NOT EXISTS idx_responses_respondent ON responses(respondent_id);
CREATE INDEX IF NOT EXISTS idx_variables_category ON variables(category);
CREATE INDEX IF NOT EXISTS idx_respondents_city ON respondents(city);
CREATE INDEX IF NOT EXISTS idx_respondents_category ON respondents(category);
