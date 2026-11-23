# 16S-mock-curation

Finding, checking and curating 16S rRNA **mock-community** datasets from public sequence archives.

> **Status:** work in progress. Step 1 (ENA search and classification) is complete; steps 2–6 are planned.

## Why mock communities?

A mock community is a sample with a **known composition**: a defined mix of bacterial strains (or their DNA),
often a commercial standard such as the ZymoBIOMICS Microbial Community Standard. Because the true answer is
known, mock communities show how well a sequencing and analysis pipeline recovers it, and how technical choices
(primers, target region, sequencing platform) affect comparability between studies.

Many studies deposit mock-community runs in the INSDC archives (ENA, SRA, DDBJ), but they are rarely labelled
consistently. This project asks: **how many usable 16S mock-community datasets exist in the public archives, how
well are they described, and what does it take to compile them into a reference dataset?**

It builds on the data-availability questions raised by Jurburg et al. (2020) and on earlier collections such as
mockrobiota (Bokulich et al. 2016), but searches the archive directly and records why each run was or was not
accepted.

## Pipeline

| Step | What it does | Status |
| --- | --- | --- |
| 1 | Search ENA for amplicon runs that mention mock communities or commercial standards; classify and flag each run | Done |
| 2 | Link each study to its publication(s) via Europe PMC; extract primers, target region, platform and mock product from methods sections (rules first, then an LLM), checked against hand-labelled papers | Planned (in development) |
| 3 | Process a subset of runs with DADA2 (primer removal, filtering, error model, ASVs, chimera removal, SILVA taxonomy) | Planned |
| 4 | Compare observed composition with the known recipe; test how region and platform affect accuracy | Planned |
| 5 | Export results in MIxS-aligned, MiCoDa- and GBIF-ready tables | Planned |
| 6 | Wrap the workflow in Snakemake, runnable with one command | Planned |

## Step 1 results (ENA search of 21 November 2026)

| Measure | Filter v1 (21 Nov) | Filter v2 (23 Nov, current) |
| --- | --- | --- |
| Amplicon runs found | 24,967 | 24,967 |
| Studies | 1,373 + 224 runs without a study accession | same |
| **Available**: strong mock evidence, mentions 16S, reads downloadable | 4,154 | **4,484** |
| **Needs manual check**: weak evidence, possible "mock-treated" host, or no 16S mention | 20,570 | 20,240 |
| **Not available**: no FASTQ files in ENA | 243 | 243 |

These classes are an **automated candidate classification** based on ENA titles and patterns, not a biological
validation: "available" means a run is a likely 16S mock community with downloadable reads, not that its reads
have been checked against the known composition (that is steps 3–4).

Filter v2 also accepts one word between "mock" and "community" ("mock microbial community", "mock bacterial
community"), a gap found in the hand check below. This moved 330 runs from "needs manual check" to "available";
the ENA data itself was not downloaded again (`--reclassify`).

**Retrieval gap:** ENA counted 23,444 runs for the term "mock" but returned 23,389; the 55 missing runs (0.23%)
have no normal release date and are recorded in `download_gaps.tsv`.

**Runs without a study accession:** 224 runs have no study accession in ENA: 23 Human Microbiome Project
mock-community runs from 2010 (no downloadable reads) and 201 runs from a 2013 fungal gut study. They are kept in
`mock_studies.tsv` as "(no study accession)" rather than dropped.

**How runs are classified.** Search terms are sent to the ENA Portal API (`library_strategy="AMPLICON"`,
matched against study, sample and experiment titles). Each run then gets flags in Python:

- `strong_mock_evidence`: "mock community" (one word may stand between, e.g. "mock microbial community"),
  "community standard", or a product code
  (ZymoBIOMICS D6300/D6305/D6306/D6310/D6311/D6331, BEI HM-782D/HM-783D, ATCC MSA-1000/1002/2002)
- `possible_fake_mock`: "mock-treated", "mock-inoculated", "mock-infected" and similar (control hosts, not mock communities)
- `mentions_16S` and `mentions_zymo` ("Zymo" alone often means a DNA extraction kit, not a standard)
- `has_fastq`: reads can be downloaded

The availability categories follow the spirit of Jurburg et al. (2020), but the numbers are **not directly
comparable**: that study started from publications, while this one starts from runs already in the archive.

## Lessons about the ENA Portal API

These were found while building step 1 and are handled in the code:

1. **Wildcard searches fail on spaces and hyphens.** `"*mock community*"` and `"*MSA-1002*"` returned 0 runs,
   while `"*MSA1002*"` returned 3. The script therefore searches single words and filters precisely in Python.
2. **Searches ignore upper and lower case.** `mock`, `Mock` and `MOCK` all returned 23,444 runs.
3. **Product names can be traps.** `zymo` matched 7,088 runs, many of which only mention a Zymo DNA extraction kit.
   Catalogue numbers are far more specific.
4. **Large downloads can be silently incomplete.** A single request for all 23,444 "mock" runs returned fewer
   rows without any error, and paging with `offset` was rejected (HTTP 400). Searches with more than 3,000 runs
   are now downloaded year by year, each year checked against ENA's count and split further if incomplete.
   Every search, large or small, ends with a final check of runs received against ENA's count; any shortfall is
   recorded in `download_gaps.tsv`.
5. **Some runs have no normal release date.** 55 runs counted by ENA did not fall into any year and could not be
   retrieved; they are recorded in `download_gaps.tsv` instead of being silently dropped.

## Validation: hand check

`data/step1/manual_check_sample.tsv` holds a stratified sample of 30 runs (15 "available", 15 "needs manual
check", fixed random seed), checked by reading the titles and, where needed, the ENA record.

- **Available (15 runs):** 14 were confirmed as mock communities, and 13 of them as **16S** mock communities.
  The two exceptions: SRR1781705, a "mock" made of synthetic DNA templates rather than real organisms, and
  SRR6336707, a mock community from a study that sequenced 16S, 18S and ITS, so the target of this run is unclear.
- **Needs manual check (15 runs):** included the expected false hits (a mockingbird study, mock-treated mice and
  roots, a Zymo extraction kit), but also **4 clear mock communities the filter missed**: SRR20340758
  ("mock bacterial community"), SRR10966512 ("ATCC mock samples"), SRR37578827 ("ZymoBIOMICS Fecal Reference")
  and SRR34332017 ("ZymoBIOMICS HMW DNA Standard"), plus several
  likely ones with short sample names such as "Mock E". Four were mock communities of non-bacterial targets
  (fish, algae 18S, springtails, fungi).

**Conclusion:** the "available" class is precise, but filter v1 missed mocks described with a word between
"mock" and "community", unfamiliar product names, or short sample labels. Filter v2 fixes the first case:
2 of the 4 clear misses are now "available", SRR20340758 ("mock bacterial community") and SRR37578827 (its study
title reads "mock microbial communities"). This can be reproduced by running
`python step1_find_mocks.py --reclassify` and comparing the `availability` column for these two runs. Product names such as "ATCC mock samples" and short labels such as
"Mock E" still need a manual check. The hand check was done on filter v1 and its sample is kept unchanged.

## Limitations

- **Titles only.** Classification reads ENA titles, not full sample metadata or papers; step 2 addresses this.
- **Missed mocks.** Runs never described as mocks in their titles cannot be found by this search.
- **"16S" is inferred from text.** ENA has no reliable field for the target gene.
- **One annotator.** The hand check was done by one person; a second annotator would make it more robust.
- **"Available" is not yet "reusable."** Primer presence, target region and metadata quality are checked in later steps.
- **Small compute.** Steps 3–4 will run on a small subset; scaling to all runs would need an HPC cluster.

## How to run

Requires Python 3 with `requests` and `pandas`.

```bash
pip install -r requirements.txt
python step1_find_mocks.py                # full run: query ENA, then classify
python step1_find_mocks.py --reclassify   # re-apply the filter to an existing mock_runs.tsv, no ENA queries
```

`manual_check_sample.tsv` is only created if it does not exist yet, so a finished hand check is never overwritten.

Outputs are written to `data/step1/`:

| File | Content | In this repository |
| --- | --- | --- |
| `mock_studies.tsv` | One row per study with counts of available, needs-check and not-available runs; runs without a study accession grouped as "(no study accession)" | Yes |
| `manual_check_sample.tsv` | The 30-run hand-check sample | Yes |
| `search_term_counts.tsv` | Runs found per search term, including zeros | Yes |
| `download_gaps.tsv` | Searches where ENA returned fewer runs than it counted | Yes |
| `mock_runs.tsv` | All runs with flags (~13 MB) | No, regenerate by running the script |
| `raw_*.tsv` | Raw ENA results per search term | No, regenerate by running the script |

ENA content changes over time, so a new run may give slightly different numbers.

## References

- Jurburg SD, et al. (2020). The archives are half-empty: an assessment of the availability of microbial
  community sequencing data. *Communications Biology* 3, 474. https://doi.org/10.1038/s42003-020-01204-9
- Bokulich NA, et al. (2016). mockrobiota: a public resource for microbiome bioinformatics benchmarking.
  *mSystems* 1(5), e00062-16. https://doi.org/10.1128/mSystems.00062-16
- ENA Portal API documentation: https://www.ebi.ac.uk/ena/portal/api/

## Author

Kinnari Zala · [github.com/Kin-zala](https://github.com/Kin-zala)

## License

MIT, see [LICENSE](LICENSE).
