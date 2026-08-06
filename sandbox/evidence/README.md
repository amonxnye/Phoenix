# The evidence store — real papers, retrieved once, checkable forever

Every file here is a real biomedical paper retrieved from [Europe PMC](https://europepmc.org)
by `gov/literature.py`, and every one carries where it came from and when:

```json
"source": "Europe PMC", "source_url": "https://europepmc.org/article/MED/42260217",
"license": "cc by", "retrieved_at": "2026-08-06T…Z"
```

That is Article VI.2 as a file format: external knowledge may enter the organization,
but never without its source attached.

## Why the text is stored at all

The oracle (`literature.supports`) has to check that a quoted sentence is really in the
paper and really asserts the claim. It can only do that against text it holds. Storing
it also makes the oracle **deterministic and offline**: the same claim scores the same
way a year from now, in CI, with no API key and no network — `gov/verify_literature.py`
runs with `urlopen` replaced by something that raises, and still passes.

## What is stored, and what is not

- **Open-access records only** keep their abstract text. Each carries the licence
  Europe PMC reports (`cc by`, `cc by-nc`, `cc by-nc-nd`), plus title, authors,
  journal, year, DOI, PMID and the article URL — the attribution those licences ask for.
- **Everything else is metadata only**, with `"abstract": null`. A claim cited to such
  a paper is refused with `no-verifiable-text` rather than scored on text nobody has.
  Refusing to guess is the honest behaviour, and it is also the safe one.
- Nothing here is full text, and nothing is a substitute for reading the paper. These
  are retrieval records for an evidence checker, and the `source_url` on every one
  points back at the publisher.

## Refreshing or extending it

```bash
python3 gov/literature.py --ingest "AMPK inhibits NLRP3" --limit 3   # search and store
python3 gov/literature.py --resolve 42260217                          # one PMID
python3 gov/literature.py --aliases EGFR                              # UniProt synonyms
python3 gov/literature.py --list                                      # what is held
```

Retrieval is a **human's ingest step and never an agent's cycle** — a researcher agent
reads this store and cannot fetch. Adding a paper is how you widen what the
organization can reason about; it is not something the organization does to itself.
