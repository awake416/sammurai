#!/usr/bin/env python3
"""Individual email review tool for manual archive decisions.

Shows each email with full subject + preview, asks keep/archive.
Generates Gmail search queries for batch operations.
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backend.email_database import EmailDB

# Import tier definitions
from email_cleanup_suggest import TIER_1_SAFE, TIER_2_REVIEW, TIER_3_NEVER


def review_tier(db: EmailDB, tier: int) -> dict:
    """Interactive review of tier emails.

    Args:
        db: EmailDB instance
        tier: Tier number to review

    Returns:
        Dict mapping action -> list of msg_ids
    """
    if tier == 1:
        domains = TIER_1_SAFE.keys()
        tier_name = "Tier 1 (Safe to Archive)"
    elif tier == 2:
        domains = TIER_2_REVIEW.keys()
        tier_name = "Tier 2 (Review Needed)"
    elif tier == 3:
        domains = TIER_3_NEVER.keys()
        tier_name = "Tier 3 (Critical - Never Archive)"
    else:
        print(f"Invalid tier: {tier}")
        return {}

    messages = db.get_messages()
    tier_messages = [
        msg for msg in messages
        if msg["sender_jid"].split("@")[-1] in domains
    ]

    # Sort by domain, then timestamp
    tier_messages.sort(key=lambda m: (m["sender_jid"].split("@")[-1], -m.get("ts", 0)))

    decisions = {"archive": [], "keep": [], "skip": []}

    print(f"\n{'='*80}")
    print(f"{tier_name} - {len(tier_messages)} emails")
    print(f"{'='*80}\n")

    current_domain = None
    for i, msg in enumerate(tier_messages, 1):
        domain = msg["sender_jid"].split("@")[-1]
        subject = msg.get("chat_name", "(no subject)")
        sender = msg.get("sender_name", msg["sender_jid"])
        preview = msg.get("text", "")[:200]
        msg_id = msg.get("msg_id", msg.get("id"))

        # Domain header
        if domain != current_domain:
            if current_domain is not None:
                print()
            print(f"\n--- {domain} ---")
            current_domain = domain

        print(f"\n[{i}/{len(tier_messages)}]")
        print(f"From:    {sender}")
        print(f"Subject: {subject}")
        if preview.strip():
            print(f"Preview: {preview}...")
        print(f"ID:      {msg_id}")

        # Decision prompt
        while True:
            choice = input("\n[a]rchive / [k]eep / [s]kip / [q]uit? ").strip().lower()
            if choice in ["a", "archive"]:
                decisions["archive"].append(msg_id)
                print("→ Marked for archive")
                break
            elif choice in ["k", "keep"]:
                decisions["keep"].append(msg_id)
                print("→ Kept (no action)")
                break
            elif choice in ["s", "skip"]:
                decisions["skip"].append(msg_id)
                print("→ Skipped (decide later)")
                break
            elif choice in ["q", "quit"]:
                print("\nQuitting review...")
                return decisions
            else:
                print("Invalid choice. Use a/k/s/q")

    return decisions


def generate_gmail_queries(db: EmailDB, msg_ids: list[str]) -> str:
    """Generate Gmail search query for batch operations.

    Args:
        db: EmailDB instance
        msg_ids: List of Gmail message IDs

    Returns:
        Gmail search query string
    """
    if not msg_ids:
        return "(no messages)"

    # Get domains for these messages
    messages = db.get_messages()
    id_to_domain = {
        msg.get("msg_id", msg.get("id")): msg["sender_jid"].split("@")[-1]
        for msg in messages
    }

    domains = set(id_to_domain.get(mid) for mid in msg_ids if mid in id_to_domain)

    # Gmail query: from:domain1.com OR from:domain2.com
    query_parts = [f"from:@{domain}" for domain in domains if domain]
    return " OR ".join(query_parts) if query_parts else "(no valid domains)"


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Review individual emails for archive decisions"
    )
    parser.add_argument(
        "--tier",
        type=int,
        required=True,
        choices=[1, 2, 3],
        help="Tier to review (1=safe, 2=review, 3=critical)",
    )
    args = parser.parse_args()

    # Load config
    config_path = Path(__file__).parent.parent / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    email_config = config.get("email", {})

    if not email_config.get("enabled"):
        print("Email sync not enabled in config.yaml")
        sys.exit(1)

    # Initialize DB
    db_path = email_config["database"]["path"]
    db = EmailDB(db_path=db_path)

    # Run interactive review
    decisions = review_tier(db, args.tier)

    # Summary
    print(f"\n{'='*80}")
    print("REVIEW SUMMARY")
    print(f"{'='*80}")
    print(f"Archive: {len(decisions['archive'])} emails")
    print(f"Keep:    {len(decisions['keep'])} emails")
    print(f"Skip:    {len(decisions['skip'])} emails")

    if decisions["archive"]:
        print(f"\n{'='*80}")
        print("GMAIL SEARCH QUERY FOR ARCHIVAL")
        print(f"{'='*80}")
        query = generate_gmail_queries(db, decisions["archive"])
        print(f"\n{query}\n")
        print("Copy this query into Gmail search, select all, then archive.")

    db.close()


if __name__ == "__main__":
    main()
