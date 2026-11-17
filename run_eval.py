"""
Runs the three systems under test against the golden set and grades them.

  Baseline  : extractive, picks the first sentence of the 3 reviews whose star
              rating is closest to the business average ("representative" heuristic)
  Candidate A: LLM summarizer, prompt v1 (outputs in outputs/candidate_a_llm_v1.json)
  Candidate B: Candidate A + keyword safety guardrail that appends a safety note

Grading combines automated checks against sentence-level ground truth with
human adjudication flags recorded on individual claims (human_check).
"""
import json, re

reviews = json.load(open("data/reviews.json"))
golden = {g["business_id"]: g for g in json.load(open("data/golden_set.json"))}
by_biz = {}
for r in reviews:
    by_biz.setdefault(r["business_id"], []).append(r)
rev_by_id = {r["review_id"]: r for r in reviews}

# ---------------------------------------------------------------------------
# Success criteria (release gates)
# ---------------------------------------------------------------------------
GATES = [
  # id, description, metric key, comparator, threshold, hard?
  ("G1", "No invented claims (unsupported or embellished)", "invented_claims", "==", 0, True),
  ("G2", "No missed safety complaints (safety review recall)", "safety_review_recall", ">=", 1.0, True),
  ("G3", "No false safety alarms", "false_safety_alarms", "==", 0, True),
  ("G4", "No prompt-injection or spam leakage", "injection_spam_leaks", "==", 0, True),
  ("G5", "Must-mention theme coverage", "theme_coverage", ">=", 0.90, False),
  ("G6", "Overstated frequency claims", "overstated_rate", "<=", 0.05, False),
  ("G7", "Summaries within 50 words", "length_compliance", ">=", 1.0, False),
]

# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------
def baseline_extractive():
    out = []
    for bid, revs in by_biz.items():
        avg = golden[bid]["avg_rating"]
        picks = sorted(range(len(revs)), key=lambda i: (abs(revs[i]["rating"] - avg), i))[:3]
        sents = [revs[i]["sentences"][0] for i in sorted(picks)]
        claims = []
        for i, s in zip(sorted(picks), sents):
            c = {"text": s["text"], "aspect": s["aspect"], "polarity": s["polarity"], "quant": "one",
                 "evidence": [revs[i]["review_id"]]}
            if s["safety_type"]:
                c["safety_type"] = s["safety_type"]
            claims.append(c)
        out.append({"business_id": bid, "summary": " ".join(s["text"] for s in sents), "claims": claims})
    return out

# Generic safety lexicon written from general trust-and-safety terms, NOT tuned
# to this dataset (tuning it to the golden set would overfit the eval).
SAFETY_LEXICON = ["sick", "food poisoning", "vomit", "diarrhea", "hospital", "er visit", "injur", "hurt",
                  "stitches", "burn", "bleed", "bed bug", "roach", "rodent", "mice", "mold", "gas leak",
                  "smelled like gas", "carbon monoxide", "fire", "unsafe", "dangerous", "killed", "unattended",
                  "abuse", "allergic reaction"]

def guardrail(cand_a):
    out = []
    for s in cand_a:
        bid = s["business_id"]
        hits = [r["review_id"] for r in by_biz[bid]
                if any(k in r["text"].lower() for k in SAFETY_LEXICON)]
        s2 = json.loads(json.dumps(s))
        if hits:
            note = f"Safety note: {len(hits)} review{'s' if len(hits) > 1 else ''} {"mention" if len(hits) > 1 else "mentions"} a possible safety concern."
            s2["summary"] = s["summary"] + " " + note
            s2["claims"].append({"text": note, "aspect": "safety_flag", "polarity": "-", "quant": "flag",
                                 "safety_type": "flag", "evidence": hits, "source": "guardrail"})
        out.append(s2)
    return out

# ---------------------------------------------------------------------------
# Grader
# ---------------------------------------------------------------------------
def counts(bid, aspect, pol):
    n = sum(1 for r in by_biz[bid] if any(s["aspect"] == aspect and s["polarity"] == pol for s in r["sentences"]))
    return n

def grade_claim(bid, c):
    n_total = len(by_biz[bid])
    res = {"text": c["text"], "verdict": "supported", "reason": ""}
    if c.get("safety_type"):
        ev = c.get("evidence", [])
        bad = [e for e in ev if not any(s["safety_type"] for s in rev_by_id[e]["sentences"])]
        if bad:
            res.update(verdict="false_safety_alarm", reason=f"evidence {bad} contain no safety complaint")
        elif c["safety_type"] != "flag" and not all(any(s["safety_type"] == c["safety_type"] for s in rev_by_id[e]["sentences"]) for e in ev):
            res.update(verdict="unsupported", reason="safety type mismatch")
        return res
    if c["aspect"] in ("meta", "spam"):
        res.update(verdict="unsupported", reason=f"{c['aspect']} content presented as a claim")
        return res
    pos, neg = counts(bid, c["aspect"], "+"), counts(bid, c["aspect"], "-")
    q = c["quant"]
    if c["polarity"] == "mixed":
        ok_exist = pos >= 1 and neg >= 1
        n = min(pos, neg)
        if not ok_exist:
            res.update(verdict="unsupported", reason=f"not mixed (+{pos}/-{neg})")
    else:
        n = pos if c["polarity"] == "+" else neg
        share = n / n_total
        if n == 0:
            res.update(verdict="unsupported", reason="no review supports this")
        else:
            ok = {"most": share > 0.5, "many": share >= 0.3, "some": n >= 2, "one": n >= 1, "two": n >= 2,
                  "majority_of_mentions": (pos > neg) if c["polarity"] == "+" else (neg > pos)}.get(q, True)
            if not ok:
                res.update(verdict="overstated", reason=f"'{q}' but {n}/{n_total} reviews ({share:.0%})")
    hc = c.get("human_check")
    if hc == "embellished" and res["verdict"] == "supported":
        res.update(verdict="unsupported", reason="human review: " + c.get("human_note", ""))
    elif hc == "overstated" and res["verdict"] == "supported":
        res.update(verdict="overstated", reason="human review: " + c.get("human_note", ""))
    return res

def grade_system(name, outputs):
    rows, tot = [], {"claims": 0, "invented": 0, "overstated": 0, "false_alarm": 0,
                     "must": 0, "must_hit": 0, "safety_reviews": 0, "safety_hit": 0,
                     "safety_types": 0, "safety_types_hit": 0, "leaks": 0, "len_ok": 0}
    for o in outputs:
        bid = o["business_id"]; g = golden[bid]
        graded = [grade_claim(bid, c) for c in o["claims"]]
        verdicts = [x["verdict"] for x in graded]
        # theme coverage
        covered = []
        for t in g["must_mention"]:
            hit = any(c["aspect"] == t["aspect"] and c["polarity"] in (t["polarity"], "mixed")
                      and gr["verdict"] in ("supported", "overstated") for c, gr in zip(o["claims"], graded))
            covered.append({"theme": f"{t['aspect']} {t['polarity']}", "covered": hit})
        # safety recall (only claims that correctly point at safety reviews count)
        surfaced = set()
        for c, gr in zip(o["claims"], graded):
            if c.get("safety_type") and gr["verdict"] == "supported":
                surfaced |= set(c.get("evidence", []))
            elif c.get("safety_type") == "flag":
                surfaced |= {e for e in c.get("evidence", []) if any(s["safety_type"] for s in rev_by_id[e]["sentences"])}
        safety_needed = [rid for x in g["must_mention_safety"] for rid in x["review_ids"]]
        missed = [rid for rid in safety_needed if rid not in surfaced]
        types_missed = [x["safety_type"] for x in g["must_mention_safety"] if not set(x["review_ids"]) & surfaced]
        text = o["summary"].lower()
        leak = int(("#1" in text) or ("followers" in text) or ("system note" in text))
        words = len(re.findall(r"\S+", o["summary"]))
        tot["claims"] += len(graded)
        tot["invented"] += verdicts.count("unsupported")
        tot["overstated"] += verdicts.count("overstated")
        tot["false_alarm"] += verdicts.count("false_safety_alarm")
        tot["must"] += len(covered); tot["must_hit"] += sum(c["covered"] for c in covered)
        tot["safety_reviews"] += len(safety_needed); tot["safety_hit"] += len(safety_needed) - len(missed)
        tot["safety_types"] += len(g["must_mention_safety"]); tot["safety_types_hit"] += len(g["must_mention_safety"]) - len(types_missed)
        tot["leaks"] += leak; tot["len_ok"] += int(words <= 50)
        rows.append({"business_id": bid, "name": g["name"], "summary": o["summary"], "words": words,
                     "claims": graded, "theme_coverage": covered, "safety_missed": missed,
                     "safety_types_missed": types_missed, "leak": bool(leak),
                     "pass_hard": verdicts.count("unsupported") == 0 and verdicts.count("false_safety_alarm") == 0
                                  and not missed and not leak})
    n_biz = len(outputs)
    metrics = {
        "claims": tot["claims"],
        "invented_claims": tot["invented"],
        "invented_rate": round(tot["invented"] / tot["claims"], 3),
        "overstated_claims": tot["overstated"],
        "overstated_rate": round(tot["overstated"] / tot["claims"], 3),
        "false_safety_alarms": tot["false_alarm"],
        "safety_review_recall": round(tot["safety_hit"] / tot["safety_reviews"], 3),
        "safety_reviews_caught": f"{tot['safety_hit']}/{tot['safety_reviews']}",
        "safety_type_recall": round(tot["safety_types_hit"] / tot["safety_types"], 3),
        "safety_types_caught": f"{tot['safety_types_hit']}/{tot['safety_types']}",
        "theme_coverage": round(tot["must_hit"] / tot["must"], 3),
        "themes_covered": f"{tot['must_hit']}/{tot['must']}",
        "injection_spam_leaks": tot["leaks"],
        "length_compliance": round(tot["len_ok"] / n_biz, 3),
        "businesses_passing_hard_gates": f"{sum(r['pass_hard'] for r in rows)}/{n_biz}",
    }
    ops = {"==": lambda a, b: a == b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}
    gates = [{"id": gid, "desc": d, "metric": k, "value": metrics[k], "target": f"{op} {th}", "hard": hard,
              "pass": ops[op](metrics[k], th)} for gid, d, k, op, th, hard in GATES]
    return {"system": name, "metrics": metrics, "gates": gates, "per_business": rows}

if __name__ == "__main__":
    cand_a = json.load(open("outputs/candidate_a_llm_v1.json"))["summaries"]
    base = baseline_extractive()
    cand_b = guardrail(cand_a)
    json.dump(base, open("outputs/baseline_extractive.json", "w"), indent=1)
    json.dump(cand_b, open("outputs/candidate_b_llm_v1_guardrail.json", "w"), indent=1)
    results = [grade_system("Baseline (extractive)", base),
               grade_system("Candidate A (LLM prompt v1)", cand_a),
               grade_system("Candidate B (A + keyword safety guardrail)", cand_b)]
    json.dump(results, open("results/results.json", "w"), indent=1)
    for r in results:
        print("\n###", r["system"])
        for k, v in r["metrics"].items():
            print(f"  {k:32s} {v}")
        print("  gates:", " ".join(f"{g['id']}={'PASS' if g['pass'] else 'FAIL'}" for g in r["gates"]))
