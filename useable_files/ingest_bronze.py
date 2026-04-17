"""
Load raw JSON extracts into bronze.* tables (truncate + full reload).

Expected files (same directory as this script):
  jooble_jobs_raw.json, adzuna_jobs_raw.json,
  apollo_organizations_enriched.json, apollo_people_enriched.json, apollo_job_postings.json,
  govcon_rfps_raw.json
"""

from __future__ import annotations

import json
import sys

from ingest_utils import (
    Json,
    as_float,
    as_int,
    execute_batch,
    get_connection,
    json_path,
    parse_date,
    parse_ts,
)

BRONZE_TRUNCATE_ORDER = [
    "bronze.govcon_awards",
    "bronze.govcon_opportunities",
    "bronze.apollo_job_postings",
    "bronze.apollo_people",
    "bronze.apollo_organizations",
    "bronze.adzuna_jobs",
    "bronze.jooble_jobs",
]


def truncate_bronze(cur) -> None:
    for tbl in BRONZE_TRUNCATE_ORDER:
        cur.execute(f"TRUNCATE TABLE {tbl}")


def _dedupe_orgs_by_id(orgs: list) -> list:
    """Enriched extract may append the same organization twice; last row wins."""
    by_id: dict[str, dict] = {}
    for o in orgs:
        oid = o.get("id")
        if oid is None:
            continue
        by_id[str(oid)] = o
    return list(by_id.values())


def _dedupe_people_by_id(people: list) -> list:
    by_id: dict[str, dict] = {}
    for p in people:
        pid = p.get("id")
        if pid is None:
            continue
        by_id[str(pid)] = p
    return list(by_id.values())


def _dedupe_job_postings_by_id(postings: list) -> list:
    by_id: dict[str, dict] = {}
    for j in postings:
        jid = j.get("id")
        if jid is None:
            continue
        by_id[str(jid)] = j
    return list(by_id.values())


def _dedupe_jooble_jobs(jobs: list) -> list:
    by_id: dict[int, dict] = {}
    for j in jobs:
        jid = j.get("id")
        if jid is None:
            continue
        by_id[int(jid)] = j
    return list(by_id.values())


def _dedupe_adzuna_jobs(jobs: list) -> list:
    by_id: dict[str, dict] = {}
    for j in jobs:
        jid = j.get("id")
        if jid is None:
            continue
        by_id[str(jid)] = j
    return list(by_id.values())


def load_jooble(cur) -> int:
    path = json_path("jooble_jobs_raw.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    jobs = _dedupe_jooble_jobs(data.get("jobs") or [])
    sql = """
    INSERT INTO bronze.jooble_jobs (
        id, title, company, location, snippet, salary, job_type, source, link,
        updated, search_keyword, search_location, extraction_source, extracted_at
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    rows = []
    for j in jobs:
        rows.append(
            (
                int(j["id"]),
                j.get("title"),
                j.get("company"),
                j.get("location"),
                j.get("snippet"),
                j.get("salary") or None,
                j.get("type") or None,
                j.get("source"),
                j.get("link"),
                parse_ts(j.get("updated")),
                j.get("_search_keyword"),
                j.get("_search_location"),
                j.get("_extraction_source"),
                parse_ts(j.get("_extracted_at")),
            )
        )
    execute_batch(cur, sql, rows, page_size=500)
    return len(rows)


def load_adzuna(cur) -> int:
    path = json_path("adzuna_jobs_raw.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    jobs = _dedupe_adzuna_jobs(data.get("jobs") or [])
    sql = """
    INSERT INTO bronze.adzuna_jobs (
        id, title, company_name, location_display, location_area, description,
        salary_min, salary_max, salary_is_predicted, category_tag, category_label,
        contract_time, latitude, longitude, redirect_url, created,
        search_keyword, search_location, extraction_source, extracted_at
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    rows = []
    for j in jobs:
        loc = j.get("location") or {}
        cat = j.get("category") or {}
        comp = j.get("company") or {}
        rows.append(
            (
                str(j["id"]),
                j.get("title"),
                comp.get("display_name"),
                loc.get("display_name"),
                Json(loc) if loc else None,
                j.get("description"),
                as_float(j.get("salary_min")),
                as_float(j.get("salary_max")),
                str(j.get("salary_is_predicted")) if j.get("salary_is_predicted") is not None else None,
                cat.get("tag"),
                cat.get("label"),
                j.get("contract_time"),
                as_float(j.get("latitude")),
                as_float(j.get("longitude")),
                j.get("redirect_url"),
                parse_ts(j.get("created")),
                j.get("_search_keyword"),
                j.get("_search_location"),
                j.get("_extraction_source"),
                parse_ts(j.get("_extracted_at")),
            )
        )
    execute_batch(cur, sql, rows, page_size=500)
    return len(rows)


def load_apollo_organizations(cur) -> int:
    path = json_path("apollo_organizations_enriched.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw_orgs = data.get("organizations") or []
    orgs = _dedupe_orgs_by_id(raw_orgs)
    if len(orgs) < len(raw_orgs):
        print(f"  (deduped {len(raw_orgs) - len(orgs)} duplicate Apollo org id(s))")
    sql = """
    INSERT INTO bronze.apollo_organizations (
        id, name, website_url, linkedin_url, twitter_url, facebook_url, angellist_url,
        phone, founded_year, publicly_traded_symbol, publicly_traded_exchange, logo_url,
        primary_domain, industry, industries, estimated_num_employees, organization_revenue,
        organization_revenue_printed, total_funding, total_funding_printed, latest_funding_stage,
        latest_funding_round_date, funding_events, keywords, technology_names, sic_codes, naics_codes,
        short_description, street_address, city, state, postal_code, country, raw_address,
        owned_by_organization_id, suborganizations, num_suborganizations, retail_location_count
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    rows = []
    for o in orgs:
        phone = o.get("phone")
        if not phone and o.get("primary_phone"):
            pp = o["primary_phone"]
            if isinstance(pp, dict):
                phone = pp.get("number") or pp.get("sanitized_number")
        rows.append(
            (
                o.get("id"),
                o.get("name"),
                o.get("website_url"),
                o.get("linkedin_url"),
                o.get("twitter_url"),
                o.get("facebook_url"),
                o.get("angellist_url"),
                phone,
                as_int(o.get("founded_year")),
                o.get("publicly_traded_symbol"),
                o.get("publicly_traded_exchange"),
                o.get("logo_url"),
                o.get("primary_domain"),
                o.get("industry"),
                Json(o.get("industries")) if o.get("industries") is not None else None,
                as_int(o.get("estimated_num_employees")),
                as_float(o.get("organization_revenue")),
                o.get("organization_revenue_printed"),
                as_float(o.get("total_funding")),
                o.get("total_funding_printed"),
                o.get("latest_funding_stage"),
                o.get("latest_funding_round_date"),
                Json(o.get("funding_events")) if o.get("funding_events") is not None else None,
                Json(o.get("keywords")) if o.get("keywords") is not None else None,
                Json(o.get("technology_names")) if o.get("technology_names") is not None else None,
                Json(o.get("sic_codes")) if o.get("sic_codes") is not None else None,
                Json(o.get("naics_codes")) if o.get("naics_codes") is not None else None,
                o.get("short_description"),
                o.get("street_address"),
                o.get("city"),
                o.get("state"),
                o.get("postal_code"),
                o.get("country"),
                o.get("raw_address"),
                o.get("owned_by_organization_id"),
                Json(o.get("suborganizations")) if o.get("suborganizations") is not None else None,
                as_int(o.get("num_suborganizations")),
                as_int(o.get("retail_location_count")),
            )
        )
    execute_batch(cur, sql, rows, page_size=200)
    return len(rows)


def _flatten_person_org(p: dict) -> dict:
    org = p.get("organization") if isinstance(p.get("organization"), dict) else {}
    return {
        "organization_industry": org.get("industry"),
        "organization_estimated_employees": as_int(org.get("estimated_num_employees")),
        "organization_revenue": as_float(org.get("organization_revenue")),
        "organization_website": org.get("website_url"),
        "organization_linkedin": org.get("linkedin_url"),
        "organization_founded_year": as_int(org.get("founded_year")),
        "organization_short_description": org.get("short_description"),
        "organization_city": org.get("city"),
        "organization_state": org.get("state"),
        "organization_country": org.get("country"),
    }


def load_apollo_people(cur) -> int:
    path = json_path("apollo_people_enriched.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw_people = data.get("people") or []
    people = _dedupe_people_by_id(raw_people)
    if len(people) < len(raw_people):
        print(f"  (deduped {len(raw_people) - len(people)} duplicate Apollo person id(s))")
    cur.execute(
        "ALTER TABLE bronze.apollo_people ADD COLUMN IF NOT EXISTS phone TEXT"
    )
    sql = """
    INSERT INTO bronze.apollo_people (
        id, first_name, last_name, name, title, headline, linkedin_url, twitter_url,
        github_url, facebook_url, photo_url, phone, email, email_status, organization_id,
        organization_name, organization_industry, organization_estimated_employees,
        organization_revenue, organization_website, organization_linkedin, organization_founded_year,
        organization_short_description, organization_city, organization_state, organization_country,
        employment_history, street_address, city, state, country, postal_code, time_zone
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    rows = []
    for p in people:
        fo = _flatten_person_org(p)
        rows.append(
            (
                p.get("id"),
                p.get("first_name"),
                p.get("last_name"),
                p.get("name"),
                p.get("title"),
                p.get("headline"),
                p.get("linkedin_url"),
                p.get("twitter_url"),
                p.get("github_url"),
                p.get("facebook_url"),
                p.get("photo_url"),
                p.get("phone"),
                p.get("email"),
                p.get("email_status"),
                p.get("organization_id"),
                p.get("organization_name"),
                fo["organization_industry"],
                fo["organization_estimated_employees"],
                fo["organization_revenue"],
                fo["organization_website"],
                fo["organization_linkedin"],
                fo["organization_founded_year"],
                fo["organization_short_description"],
                fo["organization_city"],
                fo["organization_state"],
                fo["organization_country"],
                Json(p.get("employment_history")) if p.get("employment_history") is not None else None,
                p.get("street_address"),
                p.get("city"),
                p.get("state"),
                p.get("country"),
                p.get("postal_code"),
                p.get("time_zone"),
            )
        )
    execute_batch(cur, sql, rows, page_size=200)
    return len(rows)


def load_apollo_job_postings(cur) -> int:
    path = json_path("apollo_job_postings.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw_postings = data.get("job_postings") or []
    postings = _dedupe_job_postings_by_id(raw_postings)
    if len(postings) < len(raw_postings):
        print(f"  (deduped {len(raw_postings) - len(postings)} duplicate Apollo job posting id(s))")
    sql = """
    INSERT INTO bronze.apollo_job_postings (
        id, title, url, city, state, country, last_seen_at, posted_at,
        organization_id, organization_name, icp_query_label, extraction_source, extracted_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    rows = []
    for j in postings:
        rows.append(
            (
                j.get("id"),
                j.get("title"),
                j.get("url"),
                j.get("city"),
                j.get("state"),
                j.get("country"),
                parse_ts(j.get("last_seen_at")),
                parse_ts(j.get("posted_at")),
                j.get("_organization_id"),
                j.get("_organization_name"),
                j.get("_icp_query_label"),
                j.get("_extraction_source"),
                parse_ts(j.get("_extracted_at")),
            )
        )
    execute_batch(cur, sql, rows, page_size=500)
    return len(rows)


def load_govcon(cur) -> tuple[int, int]:
    path = json_path("govcon_rfps_raw.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    opp_sql = """
    INSERT INTO bronze.govcon_opportunities (
        notice_id, title, notice_type, agency, naics, psc, posted_date, response_deadline,
        sam_url, description_url, description_text, solicitation_number, set_aside_type,
        set_aside_description, organization_type, agency_path_code, contact_name, contact_email,
        contact_phone, contact_title, secondary_contact_name, secondary_contact_email,
        performance_city, performance_state, performance_country, performance_zip, active,
        archive_date, award_number, awardee_name, award_amount, award_date, ui_link, resource_links,
        query_label, signal_type, extraction_source, extracted_at
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    raw_opps = data.get("opportunities") or []
    opps_by_notice: dict[str, dict] = {}
    for o in raw_opps:
        nid = o.get("notice_id")
        if not nid:
            continue
        opps_by_notice[str(nid)] = o
    opportunities = list(opps_by_notice.values())
    if len(opportunities) < len(raw_opps):
        print(f"  (deduped {len(raw_opps) - len(opportunities)} duplicate GovCon notice_id(s))")

    opp_rows = []
    for o in opportunities:
        opp_rows.append(
            (
                o.get("notice_id"),
                o.get("title"),
                o.get("notice_type"),
                o.get("agency"),
                Json(o.get("naics")) if o.get("naics") is not None else None,
                Json(o.get("psc")) if o.get("psc") is not None else None,
                parse_date(o.get("posted_date")),
                parse_ts(o.get("response_deadline")),
                o.get("sam_url"),
                o.get("description_url"),
                o.get("description_text"),
                o.get("solicitation_number"),
                o.get("set_aside_type"),
                o.get("set_aside_description"),
                o.get("organization_type"),
                o.get("agency_path_code"),
                o.get("contact_name"),
                o.get("contact_email"),
                o.get("contact_phone"),
                o.get("contact_title"),
                o.get("secondary_contact_name"),
                o.get("secondary_contact_email"),
                o.get("performance_city_name"),
                o.get("performance_state_name"),
                o.get("performance_country_name"),
                o.get("performance_zip"),
                o.get("active"),
                parse_date(o.get("archive_date_detailed")),
                o.get("award_number"),
                o.get("awardee_name"),
                as_float(o.get("award_amount")),
                parse_date(o.get("award_date")),
                o.get("ui_link"),
                Json(o.get("resource_links_array")) if o.get("resource_links_array") is not None else None,
                o.get("_query_label"),
                o.get("_signal_type"),
                o.get("_extraction_source"),
                parse_ts(o.get("_extracted_at")),
            )
        )
    execute_batch(cur, opp_sql, opp_rows, page_size=200)

    aw_sql = """
    INSERT INTO bronze.govcon_awards (
        award_number, notice_id, title, agency, naics, solicitation_number, set_aside_type,
        awardee_name, awardee_uei, awardee_cage_code, awardee_city, awardee_state,
        award_amount, award_date, contact_name, contact_email, query_keyword,
        extraction_source, extracted_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    raw_awards = data.get("awards") or []
    awards_by_key: dict[tuple, dict] = {}
    for a in raw_awards:
        k = (a.get("award_number"), a.get("notice_id"))
        awards_by_key[k] = a
    awards = list(awards_by_key.values())
    if len(awards) < len(raw_awards):
        print(f"  (deduped {len(raw_awards) - len(awards)} duplicate GovCon award row(s))")

    aw_rows = []
    for a in awards:
        aw_rows.append(
            (
                a.get("award_number"),
                a.get("notice_id"),
                a.get("title"),
                a.get("agency"),
                Json(a.get("naics")) if a.get("naics") is not None else None,
                a.get("solicitation_number"),
                a.get("set_aside_type"),
                a.get("awardee_name"),
                a.get("awardee_uei"),
                a.get("awardee_cage_code"),
                a.get("awardee_city"),
                a.get("awardee_state"),
                as_float(a.get("award_amount")),
                parse_date(a.get("award_date")),
                a.get("contact_name"),
                a.get("contact_email"),
                a.get("_query_keyword"),
                a.get("_extraction_source"),
                parse_ts(a.get("_extracted_at")),
            )
        )
    execute_batch(cur, aw_sql, aw_rows, page_size=200)
    return len(opp_rows), len(aw_rows)


def main() -> None:
    print("Bronze load: connecting …")
    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        print("Truncating bronze tables …")
        truncate_bronze(cur)
        print("Loading Jooble …")
        n_j = load_jooble(cur)
        print(f"  → {n_j} rows")
        print("Loading Adzuna …")
        n_a = load_adzuna(cur)
        print(f"  → {n_a} rows")
        print("Loading Apollo organizations …")
        n_o = load_apollo_organizations(cur)
        print(f"  → {n_o} rows")
        print("Loading Apollo people …")
        n_p = load_apollo_people(cur)
        print(f"  → {n_p} rows")
        print("Loading Apollo job postings …")
        n_jp = load_apollo_job_postings(cur)
        print(f"  → {n_jp} rows")
        print("Loading GovCon …")
        n_go, n_ga = load_govcon(cur)
        print(f"  → {n_go} opportunities, {n_ga} awards")
        conn.commit()
        print("Bronze commit OK.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        print(f"Missing JSON file: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
