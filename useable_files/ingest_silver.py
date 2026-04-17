"""
Build silver.* from bronze.* (truncate silver + reload).

Assumes bronze is already loaded (run ingest_bronze.py first).
"""

from __future__ import annotations

import sys

from ingest_utils import get_connection


def truncate_silver(cur) -> None:
    cur.execute("TRUNCATE TABLE silver.govcon_opportunities, silver.govcon_awards")
    cur.execute("TRUNCATE TABLE silver.companies CASCADE")


def load_companies(cur) -> None:
    cur.execute(
        """
        INSERT INTO silver.companies (
            name, name_lower, domain, website_url, linkedin_url, twitter_url, facebook_url,
            phone, industry, industries, estimated_employees, annual_revenue, revenue_printed,
            total_funding, funding_printed, latest_funding_stage, latest_funding_round_date,
            funding_events, founded_year, publicly_traded_symbol, keywords, technology_names,
            sic_codes, naics_codes, short_description, street_address, city, state, postal_code,
            country, logo_url, is_apollo, is_jooble, is_adzuna, is_govcon, apollo_org_id
        )
        SELECT DISTINCT ON (lower(trim(b.name)))
            b.name,
            lower(trim(b.name)),
            NULLIF(trim(b.primary_domain), ''),
            b.website_url,
            b.linkedin_url,
            b.twitter_url,
            b.facebook_url,
            b.phone,
            b.industry,
            b.industries,
            b.estimated_num_employees,
            b.organization_revenue,
            b.organization_revenue_printed,
            b.total_funding,
            b.total_funding_printed,
            b.latest_funding_stage,
            b.latest_funding_round_date,
            b.funding_events,
            b.founded_year,
            b.publicly_traded_symbol,
            b.keywords,
            b.technology_names,
            b.sic_codes,
            b.naics_codes,
            b.short_description,
            b.street_address,
            b.city,
            b.state,
            b.postal_code,
            b.country,
            b.logo_url,
            true,
            false,
            false,
            false,
            b.id
        FROM bronze.apollo_organizations b
        WHERE b.name IS NOT NULL AND length(trim(b.name)) > 0
        ORDER BY lower(trim(b.name)), b.estimated_num_employees DESC NULLS LAST, b.id
        """
    )

    cur.execute(
        """
        INSERT INTO silver.companies (name, name_lower, is_jooble)
        SELECT d.name, d.nl, true
        FROM (
            SELECT DISTINCT ON (lower(trim(company)))
                trim(company) AS name,
                lower(trim(company)) AS nl
            FROM bronze.jooble_jobs
            WHERE company IS NOT NULL AND length(trim(company)) > 0
            ORDER BY lower(trim(company)), trim(company)
        ) d
        ON CONFLICT (name_lower) DO UPDATE SET
            is_jooble = true
        """
    )

    cur.execute(
        """
        INSERT INTO silver.companies (name, name_lower, is_adzuna)
        SELECT d.name, d.nl, true
        FROM (
            SELECT DISTINCT ON (lower(trim(company_name)))
                trim(company_name) AS name,
                lower(trim(company_name)) AS nl
            FROM bronze.adzuna_jobs
            WHERE company_name IS NOT NULL AND length(trim(company_name)) > 0
            ORDER BY lower(trim(company_name)), trim(company_name)
        ) d
        ON CONFLICT (name_lower) DO UPDATE SET
            is_adzuna = true
        """
    )

    cur.execute(
        """
        INSERT INTO silver.companies (name, name_lower, is_apollo)
        SELECT d.name, d.nl, true
        FROM (
            SELECT DISTINCT ON (lower(trim(organization_name)))
                trim(organization_name) AS name,
                lower(trim(organization_name)) AS nl
            FROM bronze.apollo_job_postings
            WHERE organization_name IS NOT NULL AND length(trim(organization_name)) > 0
            ORDER BY lower(trim(organization_name)), trim(organization_name)
        ) d
        ON CONFLICT (name_lower) DO UPDATE SET
            is_apollo = true
        """
    )


def load_job_postings(cur) -> None:
    cur.execute(
        """
        INSERT INTO silver.job_postings (
            title, company_name, company_name_lower, company_id, location_raw,
            city, state, country, latitude, longitude, description,
            salary_min, salary_max, salary_text, job_type, category, posted_at,
            source_url, is_jooble, jooble_id, search_keyword
        )
        SELECT
            j.title,
            j.company,
            lower(trim(j.company)),
            c.company_id,
            j.location,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            j.snippet,
            NULL,
            NULL,
            NULLIF(trim(coalesce(j.salary, '')), ''),
            NULLIF(trim(coalesce(j.job_type, '')), ''),
            NULL,
            j.updated,
            j.link,
            true,
            j.id,
            j.search_keyword
        FROM bronze.jooble_jobs j
        LEFT JOIN silver.companies c ON lower(trim(j.company)) = c.name_lower
        """
    )

    cur.execute(
        """
        INSERT INTO silver.job_postings (
            title, company_name, company_name_lower, company_id, location_raw,
            city, state, country, latitude, longitude, description,
            salary_min, salary_max, salary_text, job_type, category, posted_at,
            source_url, is_adzuna, adzuna_id, search_keyword
        )
        SELECT
            a.title,
            a.company_name,
            lower(trim(a.company_name)),
            c.company_id,
            a.location_display,
            NULL,
            NULL,
            NULL,
            a.latitude,
            a.longitude,
            a.description,
            a.salary_min,
            a.salary_max,
            NULL,
            a.contract_time,
            a.category_label,
            a.created,
            a.redirect_url,
            true,
            a.id,
            a.search_keyword
        FROM bronze.adzuna_jobs a
        LEFT JOIN silver.companies c ON lower(trim(a.company_name)) = c.name_lower
        """
    )

    cur.execute(
        """
        INSERT INTO silver.job_postings (
            title, company_name, company_name_lower, company_id, location_raw,
            city, state, country, posted_at, source_url, is_apollo, apollo_posting_id,
            search_keyword
        )
        SELECT
            j.title,
            j.organization_name,
            lower(trim(j.organization_name)),
            c.company_id,
            concat_ws(', ', j.city, j.state, j.country),
            j.city,
            j.state,
            j.country,
            j.posted_at,
            j.url,
            true,
            j.id,
            j.icp_query_label
        FROM bronze.apollo_job_postings j
        LEFT JOIN silver.companies c ON c.apollo_org_id = j.organization_id
        """
    )


def load_contacts(cur) -> None:
    cur.execute(
        """
        INSERT INTO silver.contacts (
            first_name, last_name, full_name, title, headline, email, email_status,
            linkedin_url, twitter_url, phone, photo_url, city, state, country, time_zone,
            company_id, company_name, employment_history, is_apollo, apollo_person_id
        )
        SELECT
            s.first_name,
            s.last_name,
            s.full_name,
            s.title,
            s.headline,
            s.email,
            s.email_status,
            s.linkedin_url,
            s.twitter_url,
            s.phone,
            s.photo_url,
            s.city,
            s.state,
            s.country,
            s.time_zone,
            s.company_id,
            s.company_name,
            s.employment_history,
            s.is_apollo,
            s.apollo_person_id
        FROM (
            SELECT
                p.first_name AS first_name,
                p.last_name AS last_name,
                p.name AS full_name,
                p.title AS title,
                p.headline AS headline,
                p.email AS email,
                p.email_status AS email_status,
                p.linkedin_url AS linkedin_url,
                p.twitter_url AS twitter_url,
                p.phone AS phone,
                p.photo_url AS photo_url,
                p.city AS city,
                p.state AS state,
                p.country AS country,
                p.time_zone AS time_zone,
                c.company_id AS company_id,
                p.organization_name AS company_name,
                p.employment_history AS employment_history,
                true AS is_apollo,
                p.id AS apollo_person_id
            FROM bronze.apollo_people p
            LEFT JOIN silver.companies c ON c.apollo_org_id = p.organization_id
            WHERE p.name IS NOT NULL AND trim(p.name) <> ''
        ) s
        ON CONFLICT (full_name, email) DO NOTHING
        """
    )


def load_govcon_silver(cur) -> None:
    cur.execute(
        """
        INSERT INTO silver.govcon_opportunities (
            notice_id, title, notice_type, agency, naics, posted_date, response_deadline,
            description_text, solicitation_number, set_aside_type, contact_name, contact_email,
            performance_city, performance_state, performance_country, is_active, sam_url
        )
        SELECT
            notice_id,
            title,
            notice_type,
            agency,
            naics,
            posted_date,
            response_deadline,
            description_text,
            solicitation_number,
            set_aside_type,
            contact_name,
            contact_email,
            performance_city,
            performance_state,
            performance_country,
            (upper(trim(coalesce(active, ''))) = 'YES'),
            coalesce(ui_link, sam_url)
        FROM bronze.govcon_opportunities
        """
    )

    cur.execute(
        """
        INSERT INTO silver.govcon_awards (
            award_number, notice_id, title, agency, naics, solicitation_number,
            awardee_name, award_amount, award_date, contact_name, contact_email
        )
        SELECT
            award_number,
            notice_id,
            title,
            agency,
            naics,
            solicitation_number,
            awardee_name,
            award_amount,
            award_date,
            contact_name,
            contact_email
        FROM bronze.govcon_awards
        """
    )


def load_demand_signals(cur) -> None:
    cur.execute(
        """
        INSERT INTO silver.demand_signals (
            signal_type, signal_subtype, title, description, company_name, company_id,
            location_city, location_state, location_country, signal_date, source, source_url,
            is_jooble, is_adzuna, is_apollo, is_govcon
        )
        SELECT
            'hiring',
            'job_posting',
            jp.title,
            left(coalesce(jp.description, ''), 2000),
            jp.company_name,
            jp.company_id,
            jp.city,
            jp.state,
            jp.country,
            jp.posted_at,
            CASE
                WHEN jp.is_jooble THEN 'jooble'
                WHEN jp.is_adzuna THEN 'adzuna'
                WHEN jp.is_apollo THEN 'apollo'
                ELSE 'job_board'
            END,
            jp.source_url,
            jp.is_jooble,
            jp.is_adzuna,
            jp.is_apollo,
            false
        FROM silver.job_postings jp
        """
    )

    cur.execute(
        """
        INSERT INTO silver.demand_signals (
            signal_type, signal_subtype, title, description, company_name,
            location_city, location_state, location_country, signal_date, source, source_url,
            is_govcon
        )
        SELECT
            'formal_rfp',
            coalesce(g.notice_type, 'solicitation'),
            g.title,
            left(coalesce(g.description_text, ''), 2000),
            g.agency,
            g.performance_city,
            g.performance_state,
            g.performance_country,
            g.posted_date::timestamptz,
            'govcon',
            g.sam_url,
            true
        FROM silver.govcon_opportunities g
        """
    )


def main() -> None:
    print("Silver load: connecting …")
    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        print("Truncating silver …")
        truncate_silver(cur)
        print("Loading companies …")
        load_companies(cur)
        print("Loading job postings …")
        load_job_postings(cur)
        print("Loading contacts …")
        load_contacts(cur)
        print("Loading GovCon (silver) …")
        load_govcon_silver(cur)
        print("Loading demand signals …")
        load_demand_signals(cur)
        conn.commit()
        print("Silver commit OK.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
