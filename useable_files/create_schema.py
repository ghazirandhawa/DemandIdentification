"""
Create layer schemas (bronze, silver, gold) and tables in CockroachDB.

Tables live in their layer schema without bronze_/silver_ name prefixes,
e.g. bronze.jooble_jobs, silver.companies.

Idempotent — safe to re-run (CREATE SCHEMA IF NOT EXISTS, CREATE TABLE IF NOT EXISTS).
"""

import psycopg2
from db_config import DB_CONFIG

DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS bronze.jooble_jobs (
    id              BIGINT PRIMARY KEY,
    title           TEXT,
    company         TEXT,
    location        TEXT,
    snippet         TEXT,
    salary          TEXT,
    job_type        TEXT,
    source          TEXT,
    link            TEXT,
    updated         TIMESTAMPTZ,
    search_keyword  TEXT,
    search_location TEXT,
    extraction_source TEXT,
    extracted_at    TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bronze.adzuna_jobs (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    company_name    TEXT,
    location_display TEXT,
    location_area   JSONB,
    description     TEXT,
    salary_min      FLOAT8,
    salary_max      FLOAT8,
    salary_is_predicted TEXT,
    category_tag    TEXT,
    category_label  TEXT,
    contract_time   TEXT,
    latitude        FLOAT8,
    longitude       FLOAT8,
    redirect_url    TEXT,
    created         TIMESTAMPTZ,
    search_keyword  TEXT,
    search_location TEXT,
    extraction_source TEXT,
    extracted_at    TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bronze.apollo_organizations (
    id                      TEXT PRIMARY KEY,
    name                    TEXT,
    website_url             TEXT,
    linkedin_url            TEXT,
    twitter_url             TEXT,
    facebook_url            TEXT,
    angellist_url           TEXT,
    phone                   TEXT,
    founded_year            INT4,
    publicly_traded_symbol  TEXT,
    publicly_traded_exchange TEXT,
    logo_url                TEXT,
    primary_domain          TEXT,
    industry                TEXT,
    industries              JSONB,
    estimated_num_employees INT4,
    organization_revenue    FLOAT8,
    organization_revenue_printed TEXT,
    total_funding           FLOAT8,
    total_funding_printed   TEXT,
    latest_funding_stage    TEXT,
    latest_funding_round_date TEXT,
    funding_events          JSONB,
    keywords                JSONB,
    technology_names        JSONB,
    sic_codes               JSONB,
    naics_codes             JSONB,
    short_description       TEXT,
    street_address          TEXT,
    city                    TEXT,
    state                   TEXT,
    postal_code             TEXT,
    country                 TEXT,
    raw_address             TEXT,
    owned_by_organization_id TEXT,
    suborganizations        JSONB,
    num_suborganizations    INT4,
    retail_location_count   INT4,
    ingested_at             TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bronze.apollo_people (
    id                  TEXT PRIMARY KEY,
    first_name          TEXT,
    last_name           TEXT,
    name                TEXT,
    title               TEXT,
    headline            TEXT,
    linkedin_url        TEXT,
    twitter_url         TEXT,
    github_url          TEXT,
    facebook_url        TEXT,
    photo_url           TEXT,
    phone               TEXT,
    email               TEXT,
    email_status        TEXT,
    organization_id     TEXT,
    organization_name   TEXT,
    organization_industry TEXT,
    organization_estimated_employees INT4,
    organization_revenue FLOAT8,
    organization_website TEXT,
    organization_linkedin TEXT,
    organization_founded_year INT4,
    organization_short_description TEXT,
    organization_city   TEXT,
    organization_state  TEXT,
    organization_country TEXT,
    employment_history  JSONB,
    street_address      TEXT,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    postal_code         TEXT,
    time_zone           TEXT,
    ingested_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bronze.apollo_job_postings (
    id                  TEXT PRIMARY KEY,
    title               TEXT,
    url                 TEXT,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    last_seen_at        TIMESTAMPTZ,
    posted_at           TIMESTAMPTZ,
    organization_id     TEXT,
    organization_name   TEXT,
    icp_query_label     TEXT,
    extraction_source   TEXT,
    extracted_at        TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bronze.govcon_opportunities (
    notice_id           TEXT PRIMARY KEY,
    title               TEXT,
    notice_type         TEXT,
    agency              TEXT,
    naics               JSONB,
    psc                 JSONB,
    posted_date         DATE,
    response_deadline   TIMESTAMPTZ,
    sam_url             TEXT,
    description_url     TEXT,
    description_text    TEXT,
    solicitation_number TEXT,
    set_aside_type      TEXT,
    set_aside_description TEXT,
    organization_type   TEXT,
    agency_path_code    TEXT,
    contact_name        TEXT,
    contact_email       TEXT,
    contact_phone       TEXT,
    contact_title       TEXT,
    secondary_contact_name TEXT,
    secondary_contact_email TEXT,
    performance_city    TEXT,
    performance_state   TEXT,
    performance_country TEXT,
    performance_zip     TEXT,
    active              TEXT,
    archive_date        DATE,
    award_number        TEXT,
    awardee_name        TEXT,
    award_amount        FLOAT8,
    award_date          DATE,
    ui_link             TEXT,
    resource_links      JSONB,
    query_label         TEXT,
    signal_type         TEXT,
    extraction_source   TEXT,
    extracted_at        TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bronze.govcon_awards (
    award_number        TEXT,
    notice_id           TEXT,
    title               TEXT,
    agency              TEXT,
    naics               JSONB,
    solicitation_number TEXT,
    set_aside_type      TEXT,
    awardee_name        TEXT,
    awardee_uei         TEXT,
    awardee_cage_code   TEXT,
    awardee_city        TEXT,
    awardee_state       TEXT,
    award_amount        FLOAT8,
    award_date          DATE,
    contact_name        TEXT,
    contact_email       TEXT,
    query_keyword       TEXT,
    extraction_source   TEXT,
    extracted_at        TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (award_number, notice_id)
);

CREATE TABLE IF NOT EXISTS silver.companies (
    company_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    name_lower          TEXT NOT NULL,
    domain              TEXT,
    website_url         TEXT,
    linkedin_url        TEXT,
    twitter_url         TEXT,
    facebook_url        TEXT,
    phone               TEXT,
    industry            TEXT,
    industries          JSONB,
    estimated_employees INT4,
    annual_revenue      FLOAT8,
    revenue_printed     TEXT,
    total_funding       FLOAT8,
    funding_printed     TEXT,
    latest_funding_stage TEXT,
    latest_funding_round_date TEXT,
    funding_events      JSONB,
    founded_year        INT4,
    publicly_traded_symbol TEXT,
    keywords            JSONB,
    technology_names    JSONB,
    sic_codes           JSONB,
    naics_codes         JSONB,
    short_description   TEXT,
    street_address      TEXT,
    city                TEXT,
    state               TEXT,
    postal_code         TEXT,
    country             TEXT,
    logo_url            TEXT,
    is_apollo           BOOL DEFAULT false,
    is_jooble           BOOL DEFAULT false,
    is_adzuna           BOOL DEFAULT false,
    is_govcon           BOOL DEFAULT false,
    apollo_org_id       TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (name_lower)
);

CREATE INDEX IF NOT EXISTS companies_domain_idx ON silver.companies (domain);
CREATE INDEX IF NOT EXISTS companies_industry_idx ON silver.companies (industry);
CREATE INDEX IF NOT EXISTS companies_state_idx ON silver.companies (state);

CREATE TABLE IF NOT EXISTS silver.job_postings (
    posting_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    company_name        TEXT,
    company_name_lower  TEXT,
    company_id          UUID REFERENCES silver.companies(company_id) ON DELETE SET NULL,
    location_raw        TEXT,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    latitude            FLOAT8,
    longitude           FLOAT8,
    description         TEXT,
    salary_min          FLOAT8,
    salary_max          FLOAT8,
    salary_text         TEXT,
    job_type            TEXT,
    category            TEXT,
    posted_at           TIMESTAMPTZ,
    source_url          TEXT,
    is_jooble           BOOL DEFAULT false,
    is_adzuna           BOOL DEFAULT false,
    is_apollo           BOOL DEFAULT false,
    jooble_id           BIGINT,
    adzuna_id           TEXT,
    apollo_posting_id   TEXT,
    search_keyword      TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS job_postings_jooble_id_key ON silver.job_postings (jooble_id) WHERE jooble_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS job_postings_adzuna_id_key ON silver.job_postings (adzuna_id) WHERE adzuna_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS job_postings_apollo_posting_id_key ON silver.job_postings (apollo_posting_id) WHERE apollo_posting_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS job_postings_company_idx ON silver.job_postings (company_id);
CREATE INDEX IF NOT EXISTS job_postings_state_idx ON silver.job_postings (state);
CREATE INDEX IF NOT EXISTS job_postings_posted_idx ON silver.job_postings (posted_at DESC);

CREATE TABLE IF NOT EXISTS silver.contacts (
    contact_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name          TEXT,
    last_name           TEXT,
    full_name           TEXT NOT NULL,
    title               TEXT,
    headline            TEXT,
    email               TEXT,
    email_status        TEXT,
    linkedin_url        TEXT,
    twitter_url         TEXT,
    phone               TEXT,
    photo_url           TEXT,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    time_zone           TEXT,
    company_id          UUID REFERENCES silver.companies(company_id) ON DELETE SET NULL,
    company_name        TEXT,
    employment_history  JSONB,
    is_apollo           BOOL DEFAULT true,
    apollo_person_id    TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (full_name, email)
);

CREATE INDEX IF NOT EXISTS contacts_company_idx ON silver.contacts (company_id);
CREATE INDEX IF NOT EXISTS contacts_email_idx ON silver.contacts (email);

CREATE TABLE IF NOT EXISTS silver.govcon_opportunities (
    opportunity_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notice_id           TEXT NOT NULL,
    title               TEXT NOT NULL,
    notice_type         TEXT,
    agency              TEXT,
    naics               JSONB,
    posted_date         DATE,
    response_deadline   TIMESTAMPTZ,
    description_text    TEXT,
    solicitation_number TEXT,
    set_aside_type      TEXT,
    contact_name        TEXT,
    contact_email       TEXT,
    performance_city    TEXT,
    performance_state   TEXT,
    performance_country TEXT,
    is_active           BOOL,
    sam_url             TEXT,
    is_govcon           BOOL DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (notice_id)
);

CREATE INDEX IF NOT EXISTS govcon_opps_posted_idx ON silver.govcon_opportunities (posted_date DESC);
CREATE INDEX IF NOT EXISTS govcon_opps_agency_idx ON silver.govcon_opportunities (agency);

CREATE TABLE IF NOT EXISTS silver.govcon_awards (
    award_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    award_number        TEXT NOT NULL,
    notice_id           TEXT,
    title               TEXT,
    agency              TEXT,
    naics               JSONB,
    solicitation_number TEXT,
    awardee_name        TEXT,
    award_amount        FLOAT8,
    award_date          DATE,
    contact_name        TEXT,
    contact_email       TEXT,
    is_govcon           BOOL DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (award_number, notice_id)
);

CREATE TABLE IF NOT EXISTS silver.demand_signals (
    signal_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_type         TEXT NOT NULL,
    signal_subtype      TEXT,
    title               TEXT NOT NULL,
    description         TEXT,
    company_name        TEXT,
    company_id          UUID REFERENCES silver.companies(company_id) ON DELETE SET NULL,
    location_city       TEXT,
    location_state      TEXT,
    location_country    TEXT,
    signal_date         TIMESTAMPTZ,
    source              TEXT NOT NULL,
    source_url          TEXT,
    relevance_keywords  JSONB,
    is_jooble           BOOL DEFAULT false,
    is_adzuna           BOOL DEFAULT false,
    is_apollo           BOOL DEFAULT false,
    is_govcon           BOOL DEFAULT false,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS demand_signals_type_idx ON silver.demand_signals (signal_type);
CREATE INDEX IF NOT EXISTS demand_signals_company_idx ON silver.demand_signals (company_id);
CREATE INDEX IF NOT EXISTS demand_signals_date_idx ON silver.demand_signals (signal_date DESC);

CREATE TABLE IF NOT EXISTS gold.opportunities (
    opportunity_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    silver_signal_id    UUID NOT NULL REFERENCES silver.demand_signals(signal_id) ON DELETE CASCADE,
    silver_company_id   UUID REFERENCES silver.companies(company_id) ON DELETE SET NULL,
    company_name        TEXT NOT NULL,
    company_industry    TEXT,
    geography           TEXT,
    signal_display        TEXT NOT NULL,
    signal_source         TEXT,
    problem_type          TEXT,
    icp_score             INT4 NOT NULL,
    priority              TEXT NOT NULL,
    stage                 TEXT NOT NULL DEFAULT 'Signals Identified',
    opportunity_kind      TEXT,
    signal_title          TEXT NOT NULL,
    signal_description    TEXT,
    source_url            TEXT,
    signal_date           TIMESTAMPTZ,
    is_jooble             BOOL DEFAULT false,
    is_adzuna             BOOL DEFAULT false,
    is_apollo             BOOL DEFAULT false,
    is_govcon             BOOL DEFAULT false,
    is_sec_filing         BOOL NOT NULL DEFAULT false,
    scoring_notes         TEXT,
    created_at            TIMESTAMPTZ DEFAULT now(),
    updated_at            TIMESTAMPTZ DEFAULT now(),
    UNIQUE (silver_signal_id)
);

CREATE INDEX IF NOT EXISTS gold_opps_company_idx ON gold.opportunities (silver_company_id);
CREATE INDEX IF NOT EXISTS gold_opps_priority_idx ON gold.opportunities (priority);
CREATE INDEX IF NOT EXISTS gold_opps_icp_idx ON gold.opportunities (icp_score DESC);
CREATE INDEX IF NOT EXISTS gold_opps_stage_idx ON gold.opportunities (stage);
CREATE INDEX IF NOT EXISTS gold_opps_problem_idx ON gold.opportunities (problem_type);
CREATE INDEX IF NOT EXISTS gold_opps_signal_date_idx ON gold.opportunities (signal_date DESC);

CREATE TABLE IF NOT EXISTS gold.opportunity_insights (
    insight_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id       UUID NOT NULL REFERENCES gold.opportunities(opportunity_id) ON DELETE CASCADE,
    business_problem      TEXT,
    why_opportunity         TEXT,
    solution_summary        TEXT,
    outreach_draft          TEXT,
    problem_category        TEXT,
    severity_score          INT4,
    impact_summary          TEXT,
    gemini_model            TEXT,
    prompt_version          TEXT,
    generated_at            TIMESTAMPTZ DEFAULT now(),
    UNIQUE (opportunity_id)
);

CREATE INDEX IF NOT EXISTS gold_insights_opp_idx ON gold.opportunity_insights (opportunity_id);
"""


def main():
    print("Connecting to CockroachDB ...")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()

    ddl_no_comments = "\n".join(
        line for line in DDL.splitlines()
        if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in ddl_no_comments.split(";") if s.strip()]

    for i, stmt in enumerate(statements, 1):
        clean = " ".join(stmt.split())[:88]
        print(f"  [{i}/{len(statements)}] {clean} ...")
        cur.execute(stmt)

    print(f"\nDone — executed {len(statements)} statements.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
