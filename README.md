# Review Summarizer Eval Kit

Reproducible eval for an AI feature that summarizes customer reviews for a local business app.

## Run it

    python3 generate_data.py   # builds data/reviews.json and data/golden_set.json (seeded)
    python3 run_eval.py        # runs baseline, Candidate A, Candidate B; writes results/results.json

Python 3 standard library only. No API keys needed.

## Files

- `generate_data.py`: synthetic review generator with sentence-level ground truth and planted traps
- `data/golden_set.json`: answer key per business (must-mention themes, safety complaints, traps)
- `outputs/candidate_a_llm_v1.json`: LLM summaries (prompt v1) with claim-level annotations
- `run_eval.py`: baseline system, keyword guardrail, automated grader, release gates
- `results/results.json`: per-system metrics, gate pass/fail, per-claim verdicts

## Plugging in a real model

Replace `outputs/candidate_a_llm_v1.json` with your model's summaries in the same format.
Claim decomposition can be done by an LLM judge; keep `human_check` for adjudicated details.

## Provenance

Dataset and Candidate A summaries were produced with Claude and self-graded. Treat numbers as
directional until re-run with the production model and an independent annotator.
