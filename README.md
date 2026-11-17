# Review Summarizer Eval Kit

Reproducible eval for an AI feature that summarizes customer reviews for a local business app.

**Full write-up (release decision, error analysis, limitations):** https://claude.ai/artifact/2rtHbNrf37uhRD3u3C8yYw

## Release Decision: No Ship!

Seven release gates were set before any scoring. Four are hard gates: no invented claims, no missed safety complaints, no false safety alarms, and no prompt-injection leakage.

- **Candidate A (LLM, prompt v1):** covered 19 of 19 must-mention themes, but surfaced only 5 of 10 safety complaints, made 1 invented claim, and overstated frequency in 7 of 42 claims.
- **Candidate B (A + keyword safety guardrail):** raised safety recall to 6 of 10, but produced 2 false safety alarms by flagging "nobody got sick" reviews.

**Path to ship:** a dedicated per-review safety classifier feeding a separate, cited "Reported safety concerns" section, with frequency words computed from counts rather than written by the model.

## About the data

Reviews are synthetic and seeded, so every sentence carries a known label for aspect, polarity and safety type. Candidate A summaries were written by Claude in a chat session following prompt v1, in one pass before any grading ran. See Provenance below for limits on these numbers.

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
