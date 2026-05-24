-- ============================================================
-- Job Search Bot — PostgreSQL Schema
-- Run once in PgAdmin > Query Tool
-- ============================================================

-- ── 0. EXTENSIONS ────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto"; -- for gen_random_uuid()


-- ── 1. LOOKUP TABLES ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS profession_T (
    id          SERIAL PRIMARY KEY,
    profession  VARCHAR(120) NOT NULL UNIQUE,  -- Title Case enforced by app
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS variant_T (
    id           SERIAL PRIMARY KEY,
    profession_id INT REFERENCES profession_T(id) ON DELETE CASCADE,
    variant      VARCHAR(120) NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (profession_id, variant)             -- no duplicate variants per profession
);

CREATE TABLE IF NOT EXISTS seniority_T (
    id        SERIAL PRIMARY KEY,
    seniority VARCHAR(40) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS company_type_T (
    id           SERIAL PRIMARY KEY,
    company_type VARCHAR(40) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS location_T (
    id      SERIAL PRIMARY KEY,
    region  VARCHAR(60) NOT NULL,
    city    VARCHAR(80) NOT NULL,
    UNIQUE (region, city)
);

CREATE TABLE IF NOT EXISTS website_T (
    id      SERIAL PRIMARY KEY,
    website VARCHAR(200) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS frequency_T (
    id        SERIAL PRIMARY KEY,
    time_slot VARCHAR(5) NOT NULL UNIQUE   -- "08:00", "12:00", "17:00", "21:00"
);

CREATE TABLE IF NOT EXISTS work_type_T (
    id        SERIAL PRIMARY KEY,
    work_type VARCHAR(40) NOT NULL UNIQUE
);


-- ── 2. CORE USER TABLE ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_T (
    id           SERIAL PRIMARY KEY,
    first_name   VARCHAR(80)  NOT NULL,
    last_name    VARCHAR(80)  NOT NULL,
    email        VARCHAR(200) NOT NULL UNIQUE,
    work_type_id INT REFERENCES work_type_T(id),
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);


-- ── 3. JUNCTION TABLES (many-to-many) ────────────────────────

-- User ↔ Professions (up to 3, ordered by priority)
CREATE TABLE IF NOT EXISTS user_profession_T (
    id           SERIAL PRIMARY KEY,
    user_id      INT NOT NULL REFERENCES user_T(id) ON DELETE CASCADE,
    profession_id INT NOT NULL REFERENCES profession_T(id) ON DELETE CASCADE,
    priority     SMALLINT DEFAULT 1,           -- 1 = primary, 2/3 = secondary
    UNIQUE (user_id, profession_id)
);

-- User ↔ Variants (free text per user, not shared)
CREATE TABLE IF NOT EXISTS user_variant_T (
    id        SERIAL PRIMARY KEY,
    user_id   INT NOT NULL REFERENCES user_T(id) ON DELETE CASCADE,
    variant   VARCHAR(120) NOT NULL,
    UNIQUE (user_id, variant)
);

-- User ↔ Seniority levels
CREATE TABLE IF NOT EXISTS user_seniority_T (
    user_id      INT NOT NULL REFERENCES user_T(id) ON DELETE CASCADE,
    seniority_id INT NOT NULL REFERENCES seniority_T(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, seniority_id)
);

-- User ↔ Company types
CREATE TABLE IF NOT EXISTS user_company_type_T (
    user_id         INT NOT NULL REFERENCES user_T(id) ON DELETE CASCADE,
    company_type_id INT NOT NULL REFERENCES company_type_T(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, company_type_id)
);

-- User ↔ Cities
CREATE TABLE IF NOT EXISTS user_location_T (
    user_id     INT NOT NULL REFERENCES user_T(id) ON DELETE CASCADE,
    location_id INT NOT NULL REFERENCES location_T(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, location_id)
);

-- User ↔ Websites
CREATE TABLE IF NOT EXISTS user_website_T (
    user_id    INT NOT NULL REFERENCES user_T(id) ON DELETE CASCADE,
    website_id INT NOT NULL REFERENCES website_T(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, website_id)
);

-- User ↔ Frequency slots
CREATE TABLE IF NOT EXISTS user_frequency_T (
    user_id      INT NOT NULL REFERENCES user_T(id) ON DELETE CASCADE,
    frequency_id INT NOT NULL REFERENCES frequency_T(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, frequency_id)
);


-- ── 4. SEED DATA ─────────────────────────────────────────────

INSERT INTO seniority_T (seniority) VALUES
    ('Junior'), ('Senior'), ('Executive'), ('C-Level')
ON CONFLICT DO NOTHING;

INSERT INTO company_type_T (company_type) VALUES
    ('B2B'), ('B2C'), ('B2G')
ON CONFLICT DO NOTHING;

INSERT INTO work_type_T (work_type) VALUES
    ('On-site'), ('Hybrid'), ('Remote'), ('Remote or Hybrid'), ('Any')
ON CONFLICT DO NOTHING;

INSERT INTO frequency_T (time_slot) VALUES
    ('08:00'), ('12:00'), ('17:00'), ('21:00')
ON CONFLICT DO NOTHING;

INSERT INTO location_T (region, city) VALUES
    ('Center', 'Tel Aviv'),
    ('Center', 'Ramat Gan'),
    ('Center', 'Petah Tikva'),
    ('Center', 'Bnei Brak'),
    ('Center', 'Herzliya'),
    ('Center', 'Ra''anana'),
    ('Center', 'Hod HaSharon'),
    ('Center', 'Kfar Saba'),
    ('Center', 'Netanya'),
    ('Center', 'Rehovot'),
    ('Center', 'Rishon LeZion'),
    ('Center', 'Holon'),
    ('Center', 'Bat Yam'),
    ('Center', 'Lod'),
    ('Center', 'Ramla'),
    ('Jerusalem', 'Jerusalem'),
    ('Jerusalem', 'Modiin'),
    ('Jerusalem', 'Beit Shemesh'),
    ('North', 'Haifa'),
    ('North', 'Caesarea'),
    ('North', 'Yokneam'),
    ('North', 'Nazareth'),
    ('North', 'Tiberias'),
    ('South', 'Beer Sheva'),
    ('South', 'Ashdod'),
    ('South', 'Ashkelon'),
    ('South', 'Eilat'),
    ('Other', 'No preference')
ON CONFLICT DO NOTHING;

INSERT INTO website_T (website) VALUES
    ('linkedin.com'),
    ('il.indeed.com'),
    ('startupforstartup.com'),
    ('nbn.org.il'),
    ('jobshop.co.il')
ON CONFLICT DO NOTHING;

INSERT INTO profession_T (profession) VALUES
    ('Customer Success Manager'),
    ('Customer Operations Manager'),
    ('Account Manager'),
    ('Account Executive'),
    ('Sales Manager'),
    ('Business Development Manager'),
    ('Head of Business Development'),
    ('Marketing Manager'),
    ('Head of Marketing'),
    ('Head of Growth'),
    ('Product Manager'),
    ('Project Manager'),
    ('Operations Manager'),
    ('Administrative Assistant'),
    ('Executive Assistant'),
    ('Office Manager'),
    ('HR Manager'),
    ('Talent Acquisition Manager'),
    ('Data Analyst'),
    ('Business Analyst'),
    ('Software Engineer'),
    ('Full Stack Developer'),
    ('Frontend Developer'),
    ('Backend Developer')
ON CONFLICT DO NOTHING;

INSERT INTO variant_T (profession_id, variant) VALUES
    ((SELECT id FROM profession_T WHERE profession = 'Customer Success Manager'), 'Senior Customer Success Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Customer Success Manager'), 'CS Team Lead'),
    ((SELECT id FROM profession_T WHERE profession = 'Customer Success Manager'), 'Customer Success Lead'),
    ((SELECT id FROM profession_T WHERE profession = 'Customer Success Manager'), 'Account Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Customer Success Manager'), 'Client Success Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Customer Operations Manager'), 'Customer Ops Lead'),
    ((SELECT id FROM profession_T WHERE profession = 'Customer Operations Manager'), 'Operations Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Customer Operations Manager'), 'Account Operations Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Marketing Manager'), 'Senior Marketing Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Marketing Manager'), 'Head of Marketing'),
    ((SELECT id FROM profession_T WHERE profession = 'Marketing Manager'), 'Head of Growth'),
    ((SELECT id FROM profession_T WHERE profession = 'Marketing Manager'), 'Growth Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Head of Business Development'), 'Business Development Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Head of Business Development'), 'VP Business Development'),
    ((SELECT id FROM profession_T WHERE profession = 'Head of Business Development'), 'Partnerships Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Administrative Assistant'), 'Executive Assistant'),
    ((SELECT id FROM profession_T WHERE profession = 'Administrative Assistant'), 'Office Manager'),
    ((SELECT id FROM profession_T WHERE profession = 'Administrative Assistant'), 'Operations Coordinator')
ON CONFLICT DO NOTHING;


-- ── 5. HELPER VIEW (used by the bot's load_users()) ──────────
-- Returns one row per user with all fields as arrays,
-- ready to be converted to the JSON format the bot expects.

CREATE OR REPLACE VIEW v_users AS
SELECT
    u.id,
    u.first_name,
    u.last_name,
    u.first_name || ' ' || u.last_name          AS full_name,
    u.email,
    u.is_active,
    wt.work_type,

    -- Professions as array, ordered by priority
    ARRAY(
        SELECT p.profession
        FROM user_profession_T up
        JOIN profession_T p ON p.id = up.profession_id
        WHERE up.user_id = u.id
        ORDER BY up.priority
    ) AS professions,

    -- Primary profession (priority = 1)
    (
        SELECT p.profession
        FROM user_profession_T up
        JOIN profession_T p ON p.id = up.profession_id
        WHERE up.user_id = u.id AND up.priority = 1
        LIMIT 1
    ) AS profession,

    -- Variants as array
    ARRAY(
        SELECT uv.variant
        FROM user_variant_T uv
        WHERE uv.user_id = u.id
    ) AS variants,

    -- Seniority as array
    ARRAY(
        SELECT s.seniority
        FROM user_seniority_T us
        JOIN seniority_T s ON s.id = us.seniority_id
        WHERE us.user_id = u.id
    ) AS seniority,

    -- Company types as array
    ARRAY(
        SELECT ct.company_type
        FROM user_company_type_T uc
        JOIN company_type_T ct ON ct.id = uc.company_type_id
        WHERE uc.user_id = u.id
    ) AS company_type,

    -- Cities as array
    ARRAY(
        SELECT l.city
        FROM user_location_T ul
        JOIN location_T l ON l.id = ul.location_id
        WHERE ul.user_id = u.id
    ) AS city,

    -- Websites as array
    ARRAY(
        SELECT w.website
        FROM user_website_T uw
        JOIN website_T w ON w.id = uw.website_id
        WHERE uw.user_id = u.id
    ) AS websites,

    -- Frequency slots as array
    ARRAY(
        SELECT f.time_slot
        FROM user_frequency_T uf
        JOIN frequency_T f ON f.id = uf.frequency_id
        WHERE uf.user_id = u.id
        ORDER BY f.time_slot
    ) AS frequency

FROM user_T u
LEFT JOIN work_type_T wt ON wt.id = u.work_type_id
WHERE u.is_active = TRUE;


-- ── 6. AUTO-UPDATE updated_at ON user_T ──────────────────────

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_updated_at ON user_T;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON user_T
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- ── DONE ─────────────────────────────────────────────────────
-- Tables created: user_T, profession_T, variant_T, seniority_T,
--   company_type_T, location_T, website_T, frequency_T, work_type_T
-- Junction tables: user_profession_T, user_variant_T,
--   user_seniority_T, user_company_type_T, user_location_T,
--   user_website_T, user_frequency_T
-- View: v_users (used by the bot)
-- Seed data: all lookup tables pre-populated
