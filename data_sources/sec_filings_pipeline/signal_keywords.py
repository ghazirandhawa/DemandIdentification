from __future__ import annotations

from typing import Any

# label, query (SEC-style boolean phrases), signal bucket, which description-tag columns get the label
SIGNAL_DEFINITIONS: list[dict[str, Any]] = [
    {"label": "Pain_FailedAI", "q": '"failed AI" OR "AI project failure" OR "AI pilot failed"', "signal_type": "pain", "desc_tags": ("strategy", "risk")},
    {"label": "Pain_AIStruggles", "q": '"AI struggles" OR "AI challenges" OR "abandoned AI project"', "signal_type": "pain", "desc_tags": ("strategy", "risk")},
    {"label": "Pain_DataQuality", "q": '"data quality" AND ("issues" OR "problems" OR "challenges")', "signal_type": "pain", "desc_tags": ("risk", "business")},
    {"label": "Pain_DataSilos", "q": '"data silos" OR "fragmented data" OR "disconnected data"', "signal_type": "pain", "desc_tags": ("risk", "business")},
    {"label": "Pain_DataGovernance", "q": '"data governance" AND ("failure" OR "challenges" OR "gaps")', "signal_type": "pain", "desc_tags": ("risk", "business")},
    {"label": "Pain_LegacySystems", "q": '"legacy systems" AND ("modernize" OR "replace" OR "outdated")', "signal_type": "pain", "desc_tags": ("risk", "business")},
    {"label": "Pain_TechDebt", "q": '"technical debt" AND ("data" OR "analytics" OR "infrastructure")', "signal_type": "pain", "desc_tags": ("risk", "strategy")},
    {"label": "Pain_ManualReporting", "q": '"manual reporting" OR ("spreadsheet" AND "inefficiency")', "signal_type": "pain", "desc_tags": ("risk", "products")},
    {"label": "Pain_ReportingBottleneck", "q": '"reporting bottleneck" OR "reporting challenges" OR "analytics gap"', "signal_type": "pain", "desc_tags": ("risk", "strategy")},
    {"label": "Pain_DataBreach", "q": '"data breach" AND ("company" OR "business" OR "enterprise")', "signal_type": "pain", "desc_tags": ("risk",)},
    {"label": "Pain_ComplianceFine", "q": '("regulatory fine" OR "compliance violation") AND "data"', "signal_type": "pain", "desc_tags": ("risk",)},
    {"label": "Strategic_DigitalTransformation", "q": '"digital transformation" AND ("initiative" OR "strategy" OR "program")', "signal_type": "strategic", "desc_tags": ("strategy", "business")},
    {"label": "Strategic_DataTransformation", "q": '"data transformation" OR "data modernization" OR "analytics transformation"', "signal_type": "strategic", "desc_tags": ("strategy", "products")},
    {"label": "Strategic_ERPMigration", "q": '"ERP migration" OR "ERP upgrade" OR "SAP implementation"', "signal_type": "strategic", "desc_tags": ("strategy", "products")},
    {"label": "Strategic_CloudMigration", "q": '"cloud migration" AND ("data" OR "analytics" OR "enterprise")', "signal_type": "strategic", "desc_tags": ("strategy", "products")},
    {"label": "Strategic_AIAdoption", "q": '"AI adoption" OR "AI strategy" OR "enterprise AI" OR "AI roadmap"', "signal_type": "strategic", "desc_tags": ("strategy", "products")},
    {"label": "Strategic_GenAI", "q": '"generative AI" AND ("enterprise" OR "business" OR "implementation")', "signal_type": "strategic", "desc_tags": ("strategy", "products")},
    {"label": "Strategic_CDO_Hire", "q": '"chief data officer" AND ("appointed" OR "hired" OR "named" OR "joins")', "signal_type": "strategic", "desc_tags": ("strategy", "business")},
    {"label": "Strategic_CTO_Hire", "q": '"chief technology officer" AND ("appointed" OR "hired" OR "new")', "signal_type": "strategic", "desc_tags": ("strategy", "business")},
    {"label": "Vertical_Manufacturing", "q": '"manufacturing" AND ("data analytics" OR "digital transformation" OR "Industry 4.0")', "signal_type": "strategic", "desc_tags": ("business",)},
    {"label": "Vertical_Manufacturing_Pain", "q": '"manufacturing" AND ("legacy systems" OR "manual processes" OR "data challenges")', "signal_type": "pain", "desc_tags": ("business", "risk")},
    {"label": "Vertical_Retail", "q": '"retail" AND ("data analytics" OR "customer data" OR "omnichannel")', "signal_type": "strategic", "desc_tags": ("products", "business")},
    {"label": "Vertical_Ecommerce_Pain", "q": '"ecommerce" AND ("data silos" OR "reporting" OR "analytics challenges")', "signal_type": "pain", "desc_tags": ("products", "risk")},
    {"label": "Vertical_LifeSciences", "q": '"life sciences" AND ("data management" OR "AI" OR "analytics")', "signal_type": "strategic", "desc_tags": ("business", "products")},
    {"label": "Vertical_Pharma_Pain", "q": '"pharmaceutical" AND ("data challenges" OR "compliance" OR "manual processes")', "signal_type": "pain", "desc_tags": ("business", "risk")},
    {"label": "Vertical_FnB", "q": '"food and beverage" AND ("supply chain analytics" OR "demand forecasting" OR "data")', "signal_type": "strategic", "desc_tags": ("business", "products")},
    {"label": "Vertical_Agency", "q": '"marketing agency" AND ("data analytics" OR "reporting automation" OR "ROI measurement")', "signal_type": "strategic", "desc_tags": ("business", "products")},
    {"label": "Vertical_Agency_Pain", "q": '"agency" AND ("client reporting" OR "manual reporting" OR "data fragmentation")', "signal_type": "pain", "desc_tags": ("products", "risk")},
    {"label": "Vertical_Startup_Data", "q": '"startup" AND ("data infrastructure" OR "data engineering" OR "MLOps")', "signal_type": "strategic", "desc_tags": ("business", "strategy")},
    {"label": "Vertical_SeriesA_AI", "q": '("Series A" OR "Series B") AND "AI" AND "data"', "signal_type": "strategic", "desc_tags": ("strategy", "business")},
]

_VERTICAL_LABELS = frozenset(
    {
        "Vertical_Manufacturing",
        "Vertical_Manufacturing_Pain",
        "Vertical_Retail",
        "Vertical_Ecommerce_Pain",
        "Vertical_LifeSciences",
        "Vertical_Pharma_Pain",
        "Vertical_FnB",
        "Vertical_Agency",
        "Vertical_Agency_Pain",
        "Vertical_Startup_Data",
        "Vertical_SeriesA_AI",
    }
)


def _split_top_level(s: str, delimiter_lower: str) -> list[str]:
    """Split on delimiter (case-insensitive) only outside parentheses."""
    dl = delimiter_lower.lower()
    dlen = len(delimiter_lower)
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    sl = s
    n = len(sl)
    while i < n:
        c = sl[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        if depth == 0 and i + dlen <= n and sl[i : i + dlen].lower() == dl:
            parts.append(s[start:i].strip())
            i += dlen
            start = i
            continue
        i += 1
    parts.append(s[start:].strip())
    return [p for p in parts if p]


def _matches_query(text_lower: str, q: str) -> bool:
    q = q.strip()
    while len(q) >= 2 and q[0] == "(" and q[-1] == ")":
        inner = q[1:-1].strip()
        if "(" in inner or ")" in inner:
            q = inner
            continue
        q = inner
    and_parts = _split_top_level(q, " and ")
    if len(and_parts) > 1:
        return all(_matches_query(text_lower, p) for p in and_parts)
    or_parts = _split_top_level(q, " or ")
    if len(or_parts) > 1:
        return any(_matches_query(text_lower, p) for p in or_parts)
    atom = q.strip().strip('"').strip("'")
    if not atom:
        return False
    return atom.lower() in text_lower


def evaluate_signal_hits(text: str) -> dict[str, list[str]]:
    """
    Return matched signal labels grouped by column:

    - signal_hits_pain / signal_hits_strategic / signal_hits_vertical (vertical = industry-style labels)
    - tags_business, tags_products, tags_strategy, tags_risk (per description-column relevance)
    """
    tl = (text or "").lower()
    pain: list[str] = []
    strategic: list[str] = []
    vertical: list[str] = []
    tags_business: list[str] = []
    tags_products: list[str] = []
    tags_strategy: list[str] = []
    tags_risk: list[str] = []
    seen: set[str] = set()

    for row in SIGNAL_DEFINITIONS:
        label = str(row["label"])
        q = str(row["q"])
        if not _matches_query(tl, q):
            continue
        if label in seen:
            continue
        seen.add(label)
        st = str(row.get("signal_type") or "strategic")
        if label in _VERTICAL_LABELS:
            vertical.append(label)
        if st == "pain":
            pain.append(label)
        elif st == "strategic":
            strategic.append(label)
        else:
            strategic.append(label)

        for d in row.get("desc_tags") or ():
            ds = str(d)
            if ds == "business":
                tags_business.append(label)
            elif ds == "products":
                tags_products.append(label)
            elif ds == "strategy":
                tags_strategy.append(label)
            elif ds == "risk":
                tags_risk.append(label)

    def _uniq(xs: list[str]) -> list[str]:
        o: list[str] = []
        s2: set[str] = set()
        for x in xs:
            if x not in s2:
                s2.add(x)
                o.append(x)
        return o

    return {
        "signal_hits_pain": _uniq(pain),
        "signal_hits_strategic": _uniq(strategic),
        "signal_hits_vertical": _uniq(vertical),
        "tags_business": _uniq(tags_business),
        "tags_products": _uniq(tags_products),
        "tags_strategy": _uniq(tags_strategy),
        "tags_risk": _uniq(tags_risk),
    }


def signal_hits_as_csv(d: dict[str, list[str]], key: str) -> str:
    return "|".join(d.get(key) or [])
