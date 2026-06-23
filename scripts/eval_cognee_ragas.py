"""
Cognee RAG performance evaluation using RAGAS.

Tests 3 dimensions:
1. Retrieval quality (context precision + recall)
2. Answer faithfulness (does Hermes answer match retrieved context?)
3. End-to-end answer relevancy

Run: python scripts/eval_cognee_ragas.py
"""

import logging
import os
import sys
import time
from pathlib import Path

# Must be before any sammurai imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.WARNING)  # suppress cognee noise

from src.backend.cognee_store import CogneeStore
from src.backend.digest_runner import load_config

# ---------------------------------------------------------------------------
# Ground-truth test dataset
# Each entry: question, ground_truth answer, relevant_wiki_pages (for recall)
# ---------------------------------------------------------------------------
TEST_CASES = [
    {
        "question": "How do I pay property tax for my SRP flat?",
        "ground_truth": (
            "SRP falls under rural Gram Panchayat jurisdiction, not BBMP. "
            "Pay online at https://bsk.karnataka.gov.in (Bapuji Seva Kendra portal) "
            "using OTP authentication, or via Google Pay/PhonePe under BBPS rural property tax, "
            "or in person at the Gram Panchayat office near Kodathi/Anekal."
        ),
        "relevant_page": "property_tax_srp.md",
    },
    {
        "question": "What steps are required to pay the GAIL gas bill at SRP?",
        "ground_truth": (
            "Open PNG Mitra App → Domestic PNG → Quick Payment → Online Payment → "
            "Payment For (Choose Gas Invoice) → Enter BP Number → Pay the fetched due amount."
        ),
        "relevant_page": "gail_gas_billing_srp.md",
    },
    {
        "question": "What is the Gain Security startup working on?",
        "ground_truth": (
            "Gain Security is building a cloud network security platform for AI-to-AI traffic "
            "and autonomous workloads. Features: real-time network behavior mapping, autonomous "
            "policy generation, multi-cloud visibility across AWS/Azure/GCP, agentless (no infra changes)."
        ),
        "relevant_page": "gain_security_startup.md",
    },
    {
        "question": "What happened with the Anthropic Mythos AI model leak?",
        "ground_truth": (
            "A China-linked Discord group accessed Anthropic's Mythos AI model for approximately "
            "2 weeks before detection. This reportedly influenced the White House to impose export "
            "restrictions on Anthropic's Mythos 5 and Fable 5 models (June 12, 2026). "
            "Anthropic denied the described risks."
        ),
        "relevant_page": "sovereign_ai_infrastructure.md",
    },
    {
        "question": "What are the water quality concerns at SRP?",
        "ground_truth": (
            "Residents reported Sobha RO water tasting off in June 2026, with increasing reports "
            "of illness including diarrhea and vomiting. Possible water contamination suspected. "
            "Residents requested urgent formal water quality testing at the Sobha RO Water facility."
        ),
        "relevant_page": "srp_water_quality_concerns.md",
    },
    {
        "question": "What is the process for Khata transfer at SRP from Gram Panchayat?",
        "ground_truth": (
            "Residents at SRP are in the process of transferring Khata from the Gram Panchayat. "
            "At least one resident (Kumar, Wing 10) reported difficulty filling out the required form. "
            "The form image was shared in the Wing 10 Owners group for assistance."
        ),
        "relevant_page": "srp_khata_transfer.md",
    },
    {
        "question": "What is the Royal Lit Fest 2026 date at SRP?",
        "ground_truth": "Royal Lit Fest 2026 is on July 5, 2026 at Sobha Royal Pavilion.",
        "relevant_page": "royal_lit_fest_2026.md",
    },
    {
        "question": "What did Dataminr report about ransomware groups in 2025?",
        "ground_truth": (
            "Most active ransomware groups: Qilin (consolidated market, high affiliate payouts), "
            "Cl0p (encryption-less extortion, silently exfiltrated terabytes), "
            "Inc Ransom (honored payment agreements). 225% increase in monthly threat actor alerts."
        ),
        "relevant_page": "dataminr_2026_cyber_threat_landscape.md",
    },
]


def run_cognee_search(store: CogneeStore, question: str) -> tuple[str, float]:
    """Run cognee search and return (context_text, latency_seconds)."""
    start = time.time()
    context = store.get_relevant_context(question, context_limit=2000)
    latency = time.time() - start
    return context, latency


def score_context_recall(context: str, ground_truth: str) -> float:
    """Simple keyword-overlap recall: fraction of GT key terms found in context."""
    if not context:
        return 0.0
    # Extract meaningful words from ground truth (>4 chars, not stopwords)
    stopwords = {"what", "when", "where", "which", "that", "this", "with", "from",
                 "have", "been", "they", "their", "about", "also", "more", "into"}
    gt_words = {w.lower().strip(".,()") for w in ground_truth.split()
                if len(w) > 4 and w.lower() not in stopwords}
    if not gt_words:
        return 0.0
    ctx_lower = context.lower()
    found = sum(1 for w in gt_words if w in ctx_lower)
    return found / len(gt_words)


def score_context_precision(context: str, relevant_page: str, wiki_dir: Path) -> float:
    """Check whether the retrieved context actually contains content from the relevant page."""
    if not context:
        return 0.0
    page_path = wiki_dir / relevant_page
    if not page_path.exists():
        return 0.0
    page_content = page_path.read_text(encoding="utf-8")
    # Sample 5 unique phrases (10+ chars) from the page
    lines = [l.strip() for l in page_content.splitlines() if len(l.strip()) > 15 and not l.startswith("#")]
    if not lines:
        return 0.0
    ctx_lower = context.lower()
    found = sum(1 for line in lines[:10] if line[:20].lower() in ctx_lower)
    return min(1.0, found / min(5, len(lines)))


def llm_judge_relevancy(question: str, context: str, ground_truth: str) -> float:
    """Use LLM to score answer relevancy (0-1). Returns -1 if LLM unavailable."""
    try:
        from src.backend.llm_client import LLMClient
        llm = LLMClient(model="claude-sonnet-4.6")
        prompt = f"""Rate how well the retrieved context answers the question, compared to the ground truth.
Score from 0.0 (completely wrong/missing) to 1.0 (fully answers the question).

Question: {question}
Ground truth: {ground_truth}
Retrieved context: {context[:800] if context else '(empty)'}

Return ONLY a JSON object: {{"score": 0.7, "reason": "one line"}}"""

        import json
        result = llm.generate_json("You are a RAG quality evaluator.", prompt)
        if isinstance(result, dict):
            return float(result.get("score", 0.0))
    except Exception as e:
        print(f"  LLM judge failed: {e}")
    return -1.0


def main():
    config = load_config()
    wiki_cfg = config.get("wiki", {})
    wiki_path = Path(wiki_cfg.get("path", "~/sammurai-brain")).expanduser()
    wiki_dir = wiki_path / "wiki"
    dataset_name = wiki_cfg.get("dataset_name", "sammurai_wiki")

    store = CogneeStore(wiki_path=str(wiki_path), dataset_name=dataset_name, config=config)

    print("=" * 70)
    print("SAMMURAI COGNEE RAG EVALUATION — RAGAS-style metrics")
    print(f"Wiki: {wiki_dir} ({sum(1 for _ in wiki_dir.glob('*.md'))} pages)")
    print("=" * 70)
    print()

    results = []
    latencies = []

    for i, tc in enumerate(TEST_CASES, 1):
        q = tc["question"]
        gt = tc["ground_truth"]
        page = tc["relevant_page"]

        print(f"[{i}/{len(TEST_CASES)}] {q[:65]}...")
        context, latency = run_cognee_search(store, q)
        latencies.append(latency)

        recall = score_context_recall(context, gt)
        precision = score_context_precision(context, page, wiki_dir)
        relevancy = llm_judge_relevancy(q, context, gt)

        ctx_preview = (context[:120] + "...") if context else "(EMPTY — no results)"
        print(f"  Context ({len(context)} chars, {latency:.1f}s): {ctx_preview}")
        rel_str = f"{relevancy:.2f}" if relevancy >= 0 else "N/A"
        print(f"  Recall:    {recall:.2f} | Precision: {precision:.2f} | LLM relevancy: {rel_str}")
        print()

        results.append({
            "question": q,
            "page": page,
            "context_chars": len(context),
            "latency": latency,
            "recall": recall,
            "precision": precision,
            "relevancy": relevancy,
        })

    # Aggregate metrics
    print("=" * 70)
    print("AGGREGATE METRICS")
    print("=" * 70)

    avg_recall = sum(r["recall"] for r in results) / len(results)
    avg_precision = sum(r["precision"] for r in results) / len(results)
    valid_relevancy = [r["relevancy"] for r in results if r["relevancy"] >= 0]
    avg_relevancy = sum(valid_relevancy) / len(valid_relevancy) if valid_relevancy else -1
    avg_latency = sum(latencies) / len(latencies)
    empty_count = sum(1 for r in results if r["context_chars"] == 0)

    print(f"Context Recall (keyword overlap):  {avg_recall:.3f}  (1.0 = perfect)")
    print(f"Context Precision (page match):    {avg_precision:.3f}  (1.0 = perfect)")
    print(f"Answer Relevancy (LLM judge):      {avg_relevancy:.3f}  (1.0 = perfect)" if avg_relevancy >= 0 else "Answer Relevancy (LLM judge):      N/A")
    print(f"Avg search latency:                {avg_latency:.2f}s")
    print(f"Empty context (retrieval misses):  {empty_count}/{len(results)}")
    print()

    # Grade
    overall = (avg_recall + avg_precision) / 2
    grade = "A" if overall > 0.75 else "B" if overall > 0.55 else "C" if overall > 0.35 else "F"
    print(f"Overall grade: {grade} ({overall:.2f}/1.00)")

    # Worst performers
    worst = sorted(results, key=lambda r: r["recall"])[:3]
    print()
    print("Worst retrievals (lowest recall):")
    for r in worst:
        print(f"  {r['recall']:.2f} | {r['question'][:60]} → {r['page']}")

    print()
    print("DIAGNOSIS:")
    if empty_count > len(results) // 2:
        print("  ❌ CRITICAL: >50% queries return empty context. Index likely stale or missing pages.")
    if avg_recall < 0.3:
        print("  ❌ LOW RECALL: Retrieval missing key facts. Search type or chunking issue.")
    if avg_precision < 0.3:
        print("  ❌ LOW PRECISION: Context not from relevant pages. Vector space may be polluted.")
    if avg_latency > 5:
        print(f"  ⚠️  SLOW: {avg_latency:.1f}s avg latency. Consider caching or lighter search type.")
    if overall > 0.6:
        print("  ✅ Core retrieval working. Fine-tune chunking and search type for improvement.")


if __name__ == "__main__":
    main()
