# Daily collection → 09:00 briefing

Sources: Saramin and JobKorea public listings. No email is sent.

1. The workflow runs at **08:10 Asia/Seoul**, on relevant source pushes, or by manual dispatch. GitHub scheduled runs can be delayed.
2. Parsers and filters are tested first. Request spacing is at least 1.1 seconds. Access challenges are recorded and not bypassed.
3. A separate `briefing-data` branch contains `status.json`, `latest.json`, `latest.md`, and `candidates.csv`. No personal profile or credentials are stored.
4. The existing 09:00 task reads `status.json` first, checks the Korean date of `generated_at` and source errors, then reads job evidence from `latest.json`. Yesterday's result must not be described as today's collection.
5. Search cards are candidates only. The briefing verifies deadline, mandatory experience and tools, actual Seoul work address, and portfolio/task similarity. Unverified/partially matching jobs are separated. Known expired, non-Seoul, or mandatory-experience-only jobs are excluded.
6. Verified public transit routes (walking and transfers included) are needed for distance sorting. Missing times and scores are never invented.

## Local run

```sh
python -m pip install -r requirements.txt
python -m unittest -v test_briefing_pipeline.py
python -u briefing_pipeline.py --pages 2 --detail-limit 12
```

Exit code 2 means partial or failed collection; an honest status report is still written.

## Fork activation

GitHub may disable Actions in a new fork until its owner enables them in the Actions tab. Do not describe scheduled execution as tested until a run and result commit are observed.

## Limits

- A JavaScript-only/changed JobKorea layout is marked as a parsing error, never a verified zero.
- Downloading detail text supplies evidence, not automatic verification of all qualifications.
- Up to 12 candidate details per source are checked; checks stop on the site's first detail access error.
- Structured expiration dates are checked when tied to the job. Other deadlines require review.
- Job similarity and transit routing are downstream briefing steps. There are no transit provider or LLM credentials in this repository.
