"""
Step 1 - Find 16S mock community datasets in ENA
================================================

What it does:
  1. Asks the ENA Portal API which amplicon runs mention a mock community
     (a sample with a known composition) or a commercial mock standard.
  2. Puts all answers into one table, one row per run.
  3. Adds flags that show which runs are likely 16S mock communities and
     which need a manual check.
  4. Makes a second table with one row per study.

Output files (in data/step1/):
  - raw_NN_<term>.tsv        what ENA returned for each search term
  - mock_runs.tsv            all runs, de-duplicated, with flags
  - mock_studies.tsv         one row per study; runs without a study
                             accession are grouped as "(no study accession)"
  - search_term_counts.tsv   runs found per search term, including zeros
  - download_gaps.tsv        searches where ENA returned fewer runs than it counted
  - manual_check_sample.tsv  15 'available' + 15 'needs_manual_check' runs for a
                             hand check; written ONLY if the file does not exist
                             yet, so a finished hand check is never overwritten

Run:
  python step1_find_mocks.py               full run: query ENA, then classify
  python step1_find_mocks.py --reclassify  re-apply the flags to an existing
                                           mock_runs.tsv without contacting ENA

Needs: requests, pandas   (pip install -r requirements.txt)
"""

import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

# --------------------------------------------------------------------------
# 1. Settings
# --------------------------------------------------------------------------

ENA_SEARCH = "https://www.ebi.ac.uk/ena/portal/api/search"
ENA_COUNT = "https://www.ebi.ac.uk/ena/portal/api/count"

OUT_DIR = Path("data/step1")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Search words sent to ENA.
# Lessons from test runs (21 Nov 2026):
#   - ENA's wildcard search (*word*) returns 0 for terms with a SPACE or a
#     HYPHEN ("mock community" -> 0, "MSA-1002" -> 0, "MSA1002" -> 3).
#   - ENA ignores upper/lower case ("mock" = "Mock" = "MOCK" = 23,444 runs),
#     so one spelling is enough.
#   - "zymo" alone is too broad (7,088 runs): Zymo Research also sells DNA
#     extraction kits, so many titles only mention the kit, not a mock.
# So: ask ENA broadly with single words, then filter precisely in Python
# (see RE_STRONG_MOCK below).
SEARCH_TERMS = [
    "mock",
    "ZymoBIOMICS",
    # ZymoBIOMICS mock community catalogue numbers
    "D6300", "D6305", "D6306", "D6310", "D6311", "D6331",
    "782D",         # BEI Resources mock community B (HM-782D / HM782D)
    "783D",         # HM-783D
    "MSA1000", "MSA1002", "MSA2002",      # ATCC mock communities
]

# Text fields in ENA where these words might appear. The allowed field names
# are listed at https://www.ebi.ac.uk/ena/portal/api/returnFields?result=read_run
TEXT_FIELDS = ["study_title", "sample_title", "experiment_title"]

# Columns we want back for every run.
RETURN_FIELDS = [
    "run_accession", "study_accession", "sample_accession",
    "study_title", "sample_title", "experiment_title",
    "library_strategy", "library_layout",
    "instrument_platform", "instrument_model",
    "read_count", "base_count", "first_public",
    "fastq_ftp", "fastq_bytes",
]


# --------------------------------------------------------------------------
# 2. Small helper functions
# --------------------------------------------------------------------------

def build_query(term: str) -> str:
    """Build one ENA query: amplicon runs whose titles contain the term.

    '*' means 'anything before or after', so "*mock*" also matches
    "ZymoBIOMICS mock community standard". Terms must be single words without
    spaces or hyphens: ENA returns 0 runs for "*mock community*".
    """
    text_part = " OR ".join(f'{field}="*{term}*"' for field in TEXT_FIELDS)
    return f'library_strategy="AMPLICON" AND ({text_part})'


def ena_count(query: str) -> int:
    """Ask ENA only HOW MANY runs match (fast check before downloading).

    Retries up to 5 times: ENA sometimes drops connections
    ("Connection reset by peer", 23 Nov 2026).
    """
    for attempt in range(1, 6):
        try:
            r = requests.post(ENA_COUNT,
                              data={"result": "read_run", "query": query},
                              timeout=60)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == 5:
                raise RuntimeError(
                    f"ENA did not answer after 5 tries ({e}). "
                    f"ENA may be down - try again later.") from e
            print(f"   ENA connection problem ({e}); retry {attempt}/4 in 15 s")
            time.sleep(15)
    # ENA returns plain text like "count\n123" or just "123"
    return int(r.text.strip().split()[-1])


GAPS = []   # searches where fewer runs arrived than ENA promised


def download_once(query: str) -> pd.DataFrame:
    """One plain download (limit=0 = everything). Fine for small searches."""
    from io import StringIO
    params = {
        "result": "read_run",
        "query": query,
        "fields": ",".join(RETURN_FIELDS),
        "format": "tsv",
        "limit": 0,
    }
    for attempt in range(1, 4):
        try:
            r = requests.post(ENA_SEARCH, data=params, timeout=600)
            break
        except requests.RequestException as e:      # dropped connection etc.
            if attempt == 3:
                raise RuntimeError(f"ENA download failed 3 times ({e})") from e
            print(f"   ENA connection problem ({e}); retry {attempt}/2 in 15 s")
            time.sleep(15)
    if r.status_code != 200:
        # Show ENA's own explanation, not just "400"
        raise RuntimeError(f"ENA error {r.status_code}: {r.text[:300]}")
    if not r.text.strip():
        return pd.DataFrame(columns=RETURN_FIELDS)
    return pd.read_csv(StringIO(r.text), sep="\t", dtype=str)


def fetch_date_range(query: str, start: date, end: date) -> pd.DataFrame:
    """Download runs published between start and end, and CHECK the count.

    If the download is incomplete, split the date range in half and try
    each half (smaller downloads are reliable). Single days that still fail
    are retried 3 times, then the script stops with a clear error.
    """
    dated = (f'{query} AND first_public>={start.isoformat()} '
             f'AND first_public<={end.isoformat()}')
    expected = ena_count(dated)
    if expected == 0:
        return pd.DataFrame(columns=RETURN_FIELDS)

    tries = 3 if start == end else 1
    for _ in range(tries):
        try:
            df = download_once(dated)
            if df["run_accession"].nunique() == expected:
                print(f"   {start} .. {end}: {expected} runs OK")
                return df
        except (requests.RequestException, RuntimeError) as e:
            print(f"   {start} .. {end}: problem ({e})")
        time.sleep(3)

    if start == end:
        raise RuntimeError(f"Could not download runs from {start} completely.")

    # Split in half and try again
    middle = start + (end - start) // 2
    return pd.concat([fetch_date_range(query, start, middle),
                      fetch_date_range(query, middle + timedelta(days=1), end)],
                     ignore_index=True)


def ena_search(query: str, expected: int) -> pd.DataFrame:
    """Download all matching runs, year by year, and check the total.

    Why? Asking ENA for everything at once (23,444 runs for 'mock') was
    silently cut off, and ENA rejected paging with 'offset' (error 400,
    21 Nov 2026). Year-by-year downloads use only features proven to work.
    """
    first_year, last_day = date(2000, 1, 1), date(date.today().year, 12, 31)

    if expected <= 3000:                       # small: one download is fine
        df = download_once(query)
    else:
        years = [fetch_date_range(query, date(y, 1, 1), date(y, 12, 31))
                 for y in range(first_year.year, last_day.year + 1)]
        df = pd.concat(years, ignore_index=True)

        # Leftovers: runs with a missing or odd release date never fall into
        # any year (21 Nov 2026: 55 of 23,444 'mock' runs). Ask for them
        # separately with NOT.
        leftover_q = (f'{query} AND NOT (first_public>={first_year.isoformat()}'
                      f' AND first_public<={last_day.isoformat()})')
        try:
            n_left = ena_count(leftover_q)
            if n_left:
                left = download_once(leftover_q)
                print(f"   runs without a normal release date: "
                      f"{left['run_accession'].nunique()} of {n_left}")
                df = pd.concat([df, left], ignore_index=True)
        except (requests.RequestException, RuntimeError, ValueError) as e:
            print(f"   could not fetch leftover runs ({e})")

    df = df.drop_duplicates("run_accession")

    # THE FINAL CHECK: did we get everything ENA promised?
    received = df["run_accession"].nunique()
    if received != expected:
        missing = expected - received
        print(f"   WARNING: ENA promised {expected} runs, received {received} "
              f"({missing} missing, {missing / expected:.2%}). Saved anyway; "
              f"the gap is recorded in download_gaps.tsv")
        GAPS.append({"query": query, "expected": expected,
                     "received": received, "missing": missing})
    return df


# Patterns for the warning flags
RE_16S = re.compile(r"16S", re.IGNORECASE)
# "mock-treated", "mock inoculated", "mock infection" = control plants/animals,
# NOT a mock community. These are the classic false hits. "mock control" is
# included too; it can also mean a positive control, so such runs go to the
# manual check rather than being excluded.
RE_FAKE_MOCK = re.compile(
    r"mock[\s-]*(?:treat|inocul|infect|transfect|control|vaccin)",
    re.IGNORECASE)
# Strong evidence that the run really is a mock community:
# "mock community", "community standard", or a known product code.
# One word may stand between "mock" and "community" ("mock DNA community",
# "mock bacterial community"): version 1 of this filter missed those, including
# the Human Microbiome Project mock runs (found 23 Nov 2026).
# NOTE: "zymo" alone is NOT evidence (could be a DNA extraction kit).
RE_STRONG_MOCK = re.compile(
    r"mock[\s_-]*(?:[a-z0-9]+[\s_-]+)?commun"
    r"|community[\s_-]*standard"
    r"|HM-?78[23]D"
    r"|MSA-?[12]00[0-9]"
    r"|D63(?:00|05|06|10|11|31)",
    re.IGNORECASE)
# Mentions Zymo at all (kit OR mock) - shown so you can check by hand
RE_ZYMO = re.compile(r"zymo", re.IGNORECASE)


def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add simple yes/no columns that tell you which rows need attention."""
    text = (df["study_title"].fillna("") + " | "
            + df["sample_title"].fillna("") + " | "
            + df["experiment_title"].fillna(""))

    df["mentions_16S"] = text.str.contains(RE_16S)
    df["possible_fake_mock"] = text.str.contains(RE_FAKE_MOCK)
    df["strong_mock_evidence"] = text.str.contains(RE_STRONG_MOCK)
    df["mentions_zymo"] = text.str.contains(RE_ZYMO)
    # Which exact words gave the evidence (helps your hand check)
    df["mock_evidence_text"] = text.apply(
        lambda t: "; ".join(sorted({m.group(0) for m in RE_STRONG_MOCK.finditer(t)})))

    # Files downloadable? Empty fastq_ftp = no files to download.
    df["has_fastq"] = df["fastq_ftp"].fillna("").str.len() > 0

    # Paired reads usually come as 2 files separated by ';'
    df["n_fastq_files"] = df["fastq_ftp"].fillna("").apply(
        lambda s: 0 if s == "" else len(s.split(";")))

    # Availability in the spirit of Jurburg et al. 2020.
    # 'mislabeled' cannot be decided automatically -> 'needs_manual_check'.
    def availability(row):
        if not row["has_fastq"]:
            return "not_available"
        if (row["possible_fake_mock"] or not row["mentions_16S"]
                or not row["strong_mock_evidence"]):
            return "needs_manual_check"
        return "available"

    df["availability"] = df.apply(availability, axis=1)
    return df


# --------------------------------------------------------------------------
# 3. Main workflow
# --------------------------------------------------------------------------

def main():
    all_tables = []
    counts = []

    for i, term in enumerate(SEARCH_TERMS, start=1):
        query = build_query(term)
        n = ena_count(query)
        print(f"{term!r:25} -> {n} runs")
        counts.append({"term": term, "n_runs": n, "query": query})

        if n == 0:
            continue

        df = ena_search(query, expected=n)
        df["found_by_term"] = term

        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", term)
        # Number in front: Windows treats raw_mock / raw_Mock as one file
        df.to_csv(OUT_DIR / f"raw_{i:02d}_{safe_name}.tsv", sep="\t",
                  index=False)
        all_tables.append(df)

        time.sleep(1)   # be polite to ENA's server

    # Keep a record of how many runs each word found (also the zeros!)
    pd.DataFrame(counts).to_csv(OUT_DIR / "search_term_counts.tsv",
                                sep="\t", index=False)
    # Record any download gaps honestly (empty file = no gaps)
    pd.DataFrame(GAPS, columns=["query", "expected", "received", "missing"]
                 ).to_csv(OUT_DIR / "download_gaps.tsv", sep="\t", index=False)

    if not all_tables:
        print("No results. Check that the ENA field names in TEXT_FIELDS are valid.")
        return

    runs = pd.concat(all_tables, ignore_index=True)

    # One run can be found by several words -> keep one row, but remember
    # ALL words that found it.
    terms_per_run = (runs.groupby("run_accession")["found_by_term"]
                     .apply(lambda s: "; ".join(sorted(set(s)))))
    runs = runs.drop_duplicates("run_accession").drop(columns="found_by_term")
    runs = runs.merge(terms_per_run, on="run_accession")

    runs = add_flags(runs)
    write_outputs(runs)


FLAG_COLUMNS = ["mentions_16S", "possible_fake_mock", "strong_mock_evidence",
                "mentions_zymo", "mock_evidence_text", "has_fastq",
                "n_fastq_files", "availability"]


def write_outputs(runs: pd.DataFrame) -> None:
    """Write mock_runs.tsv, mock_studies.tsv and (only if new) the hand-check sample."""
    runs.to_csv(OUT_DIR / "mock_runs.tsv", sep="\t", index=False)

    # One row per study: mocks are often a few control runs inside a big study.
    # Runs without a study accession (e.g. old Human Microbiome Project runs)
    # are kept as their own group instead of silently disappearing.
    by_study = runs.assign(study_accession=runs["study_accession"]
                           .fillna("(no study accession)"))
    studies = (by_study.groupby("study_accession")
               .agg(study_title=("study_title", "first"),
                    n_runs=("run_accession", "count"),
                    platforms=("instrument_model",
                               lambda s: "; ".join(sorted(set(s.dropna())))),
                    n_available=("availability",
                                 lambda s: (s == "available").sum()),
                    n_need_check=("availability",
                                  lambda s: (s == "needs_manual_check").sum()),
                    n_not_available=("availability",
                                     lambda s: (s == "not_available").sum()))
               .reset_index())
    studies.to_csv(OUT_DIR / "mock_studies.tsv", sep="\t", index=False)
    assert studies["n_runs"].sum() == len(runs), "study table lost runs"

    # 30 rows for a hand check: 15 'available' + 15 'needs_manual_check'
    # (fixed seed = same rows every time). Written only if the file does not
    # exist yet, so a finished hand check is never overwritten.
    sample_file = OUT_DIR / "manual_check_sample.tsv"
    if sample_file.exists():
        print(f"{sample_file} already exists - kept unchanged (hand check is safe).")
    else:
        parts = []
        for group in ["available", "needs_manual_check"]:
            g = runs[runs["availability"] == group]
            parts.append(g.sample(n=min(15, len(g)), random_state=42))
        sample = pd.concat(parts)
        sample["manual_is_real_mock"] = ""   # fill in: yes / no / unsure
        sample["manual_is_16S"] = ""         # fill in: yes / no / unsure
        sample["manual_note"] = ""
        sample.to_csv(sample_file, sep="\t", index=False)

    n_no_study = runs["study_accession"].isna().sum()
    print("\n--- Summary ---")
    print(f"Unique runs:    {len(runs)}")
    print(f"Unique studies: {runs['study_accession'].nunique()}"
          f" (+ {n_no_study} runs without a study accession)")
    print(runs["availability"].value_counts().to_string())
    print(f"\nFiles written to {OUT_DIR}/")


def reclassify() -> None:
    """Re-apply the flags to an existing mock_runs.tsv, without contacting ENA."""
    runs = pd.read_csv(OUT_DIR / "mock_runs.tsv", sep="\t", dtype=str)
    runs = runs.drop(columns=[c for c in FLAG_COLUMNS if c in runs.columns])
    write_outputs(add_flags(runs))


if __name__ == "__main__":
    if "--reclassify" in sys.argv:
        reclassify()
    else:
        main()
