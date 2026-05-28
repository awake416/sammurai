#!/usr/bin/env python3
"""Email cleanup suggestion tool - labels archive candidates for manual review.

SAFETY: Read-only + label-only operations. Never deletes or archives.
User reviews in Gmail UI before running archival script.
"""

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

import yaml

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backend.email_database import EmailDB
from src.backend.gmail_client import GmailClient

logger = logging.getLogger(__name__)

# Archive tiers (confidence levels)
TIER_1_SAFE = {
    "slack.com": "Work notifications (already in Slack)",
    "mailers.zomato.com": "Food delivery promos",
    "mailer.airtel.com": "Telecom promos",
    "mail.adobe.com": "Adobe marketing",
    "cleartrip.com": "Travel booking confirmations (old)",
}

TIER_2_REVIEW = {
    "graphik.ai": "Vendor outreach (rejected)",
    "vecton.ai": "Vendor outreach (rejected)",
    "sobha.com": "Real estate promo",
    "gonuclei.com": "Vendor outreach",
    "mail.trae.ai": "Vendor outreach",
    "primeinvestor.in": "Investment newsletter + refund pending",
    "protium.co.in": "Work domain emails",
}

TIER_3_NEVER = {
    "uidai.gov.in": "Government notices (Aadhaar)",
    "indiaai.gov.in": "Government notices (AI)",
    "bankofbaroda.bank.in": "Bank notices",
    "axisbank.com": "Bank notices",
    "icicibank.com": "Bank notices",
    "custcomm.icicibank.com": "Bank communications",
    "alerts.sbi.bank.in": "Bank alerts",
    "alerts.axisbankmail.bank.in": "Bank alerts",
    "axis.bank.in": "Bank statements",
    "houseclay.com": "Tenant lease notices",
    "vaultproptech.com": "Property management",
    "mygateapp.in": "Community notices (MyGate)",
    "godigit.com": "Insurance claims/documents",
    "bseindia.in": "Stock exchange statements",
    "nse.co.in": "Stock exchange statements",
    "mail.anthropic.com": "Claude payment receipts",
    "email.paytmmoney.com": "Investment platform statements",
}


def analyze_cleanup_candidates(db: EmailDB) -> dict:
    """Analyze emails and group by cleanup tier.

    Args:
        db: EmailDB instance

    Returns:
        Dict mapping tier -> domain -> message count
    """
    messages = db.get_messages()

    tier_stats = {
        "tier1_safe": defaultdict(int),
        "tier2_review": defaultdict(int),
        "tier3_never": defaultdict(int),
        "unknown": defaultdict(int),
    }

    for msg in messages:
        domain = msg["sender_jid"].split("@")[-1]

        if domain in TIER_1_SAFE:
            tier_stats["tier1_safe"][domain] += 1
        elif domain in TIER_2_REVIEW:
            tier_stats["tier2_review"][domain] += 1
        elif domain in TIER_3_NEVER:
            tier_stats["tier3_never"][domain] += 1
        else:
            tier_stats["unknown"][domain] += 1

    return tier_stats


def print_analysis(stats: dict, total: int) -> None:
    """Print cleanup analysis report.

    Args:
        stats: Tier statistics
        total: Total message count
    """
    print("\n" + "=" * 80)
    print("EMAIL CLEANUP ANALYSIS")
    print("=" * 80)
    print(f"\nTotal emails analyzed: {total}")

    print("\n🟢 TIER 1 - SAFE TO ARCHIVE (high confidence)")
    print("-" * 80)
    tier1_total = sum(stats["tier1_safe"].values())
    if tier1_total > 0:
        for domain, count in sorted(stats["tier1_safe"].items(), key=lambda x: x[1], reverse=True):
            reason = TIER_1_SAFE[domain]
            pct = (count / total) * 100
            print(f"  {domain:30} {count:3} emails ({pct:5.1f}%) - {reason}")
        print(f"\n  Total: {tier1_total} emails ({(tier1_total/total)*100:.1f}%)")
    else:
        print("  No Tier 1 candidates found")

    print("\n🟡 TIER 2 - REVIEW BEFORE ARCHIVE (needs confirmation)")
    print("-" * 80)
    tier2_total = sum(stats["tier2_review"].values())
    if tier2_total > 0:
        for domain, count in sorted(stats["tier2_review"].items(), key=lambda x: x[1], reverse=True):
            reason = TIER_2_REVIEW[domain]
            pct = (count / total) * 100
            print(f"  {domain:30} {count:3} emails ({pct:5.1f}%) - {reason}")
        print(f"\n  Total: {tier2_total} emails ({(tier2_total/total)*100:.1f}%)")
    else:
        print("  No Tier 2 candidates found")

    print("\n🔴 TIER 3 - NEVER ARCHIVE (critical)")
    print("-" * 80)
    tier3_total = sum(stats["tier3_never"].values())
    if tier3_total > 0:
        for domain, count in sorted(stats["tier3_never"].items(), key=lambda x: x[1], reverse=True):
            reason = TIER_3_NEVER[domain]
            pct = (count / total) * 100
            print(f"  {domain:30} {count:3} emails ({pct:5.1f}%) - {reason}")
        print(f"\n  Total: {tier3_total} emails ({(tier3_total/total)*100:.1f}%)")
    else:
        print("  No protected emails found")

    print("\n⚪ UNKNOWN DOMAINS (not classified)")
    print("-" * 80)
    unknown_total = sum(stats["unknown"].values())
    if unknown_total > 0:
        for domain, count in sorted(stats["unknown"].items(), key=lambda x: x[1], reverse=True):
            pct = (count / total) * 100
            print(f"  {domain:30} {count:3} emails ({pct:5.1f}%)")
        print(f"\n  Total: {unknown_total} emails ({(unknown_total/total)*100:.1f}%)")
    else:
        print("  All domains classified")

    print("\n" + "=" * 80)
    print("\nNEXT STEPS:")
    print("1. Review Tier 1 domains above")
    print("2. Run: python scripts/email_cleanup_suggest.py --apply-labels --tier 1")
    print("3. Check Gmail for 'sammurai/archive-candidate' label")
    print("4. Remove label from emails you want to keep")
    print("5. Run archival script (coming soon)")
    print()


def apply_labels(gmail: GmailClient, db: EmailDB, tier: int, dry_run: bool = True) -> None:
    """Apply 'sammurai/archive-candidate' label to tier emails.

    Args:
        gmail: GmailClient instance
        db: EmailDB instance
        tier: Tier number (1 or 2)
        dry_run: If True, only print what would be labeled
    """
    if tier == 1:
        domains = TIER_1_SAFE.keys()
        tier_name = "Tier 1 (Safe)"
    elif tier == 2:
        domains = TIER_2_REVIEW.keys()
        tier_name = "Tier 2 (Review)"
    else:
        logger.error(f"Invalid tier: {tier}. Must be 1 or 2.")
        return

    messages = db.get_messages()
    candidates = [msg for msg in messages if msg["sender_jid"].split("@")[-1] in domains]

    print(f"\n{tier_name} - {len(candidates)} emails to label")
    print("=" * 80)

    if dry_run:
        print("\nDRY RUN - No labels will be applied")
        print("\nEmails that would be labeled:")
        for msg in candidates[:10]:
            domain = msg["sender_jid"].split("@")[-1]
            subject = msg.get("chat_name", "(no subject)")[:60]
            print(f"  {domain:25} | {subject}")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")
        print(f"\nRun with --no-dry-run to apply labels")
    else:
        print("\nApplying labels... (NOT IMPLEMENTED YET)")
        print("Gmail API label application coming in next iteration")
        print("\nFor now, manually add 'sammurai/archive-candidate' label in Gmail")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze and suggest emails for archival (read-only + labels)"
    )
    parser.add_argument(
        "--apply-labels",
        action="store_true",
        help="Apply 'sammurai/archive-candidate' label to tier emails",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=[1, 2],
        help="Tier to apply labels to (1=safe, 2=review)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually apply labels (default is dry run)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Load config
    config_path = Path(__file__).parent.parent / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    email_config = config.get("email", {})

    if not email_config.get("enabled"):
        logger.error("Email sync not enabled in config.yaml")
        sys.exit(1)

    # Initialize DB
    db_path = email_config["database"]["path"]
    db = EmailDB(db_path=db_path)

    # Analyze
    stats = analyze_cleanup_candidates(db)
    total = len(db.get_messages())

    print_analysis(stats, total)

    # Apply labels if requested
    if args.apply_labels:
        if not args.tier:
            logger.error("--tier required when using --apply-labels")
            sys.exit(1)

        gmail = GmailClient()
        gmail.authenticate()
        apply_labels(gmail, db, args.tier, dry_run=not args.no_dry_run)

    db.close()


if __name__ == "__main__":
    main()
