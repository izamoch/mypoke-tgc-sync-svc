import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

import httpx

import sync

# Import database and sync modules
# Import database and sync modules
from database import SessionLocal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sync_job")


def generate_report(start_time: datetime, end_time: datetime, cards_metrics: dict, prices_metrics: dict):
    duration = (end_time - start_time).total_seconds()
    report_desc = f"# Sync Report - {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    report_desc += f"**Duration:** {duration:.1f} seconds\n\n"

    report_desc += "## Phase 1: Sets and Cards\n"
    if cards_metrics:
        report_desc += f"- **New Sets Added:** {cards_metrics.get('new_sets', 0)}\n"
        report_desc += f"- **Cards Processed (New):** {cards_metrics.get('cards_processed', 0)}\n"
        report_desc += f"- **Cards Inserted:** {cards_metrics.get('new_cards', 0)}\n"
        if cards_metrics.get("errors"):
            report_desc += "- **Errors:**\n"
            for err in cards_metrics["errors"]:
                report_desc += f"  - `{err}`\n"
    else:
        report_desc += "*(Failed or Skipped)*\n"

    report_desc += "\n## Phase 2: Prices\n"
    if prices_metrics:
        report_desc += f"- **Cards Scheduled for Check:** {prices_metrics.get('scheduled_for_check', 0)} / {prices_metrics.get('total_cards', 0)}\n"
        report_desc += f"- **Cards Actually Checked:** {prices_metrics.get('checked_count', 0)}\n"
        report_desc += f"- **Prices Updated:** {prices_metrics.get('updated_count', 0)}\n"

        report_desc += "\n### Strategy Breakdown\n"
        for strat, count in prices_metrics.get("strategy_breakdown", {}).items():
            report_desc += f"- **{strat}:** {count}\n"

        report_desc += "\n### Variant Updates\n"
        for var, count in prices_metrics.get("variant_updates", {}).items():
            report_desc += f"- **{var}:** {count}\n"

        if prices_metrics.get("errors_by_type"):
            report_desc += "\n### Error Summary by Type\n"
            for etype, count in prices_metrics["errors_by_type"].items():
                report_desc += f"- **{etype}:** {count}\n"
    else:
        report_desc += "*(Failed or Skipped)*\n"

    # Save report locally
    report_filename = f"reports/sync_report_{start_time.strftime('%Y%m%d_%H%M%S')}.md"
    os.makedirs("reports", exist_ok=True)
    try:
        with open(report_filename, "w", encoding="utf-8") as f:
            f.write(report_desc)
        logger.info(f"Local report saved to {report_filename}")
    except Exception as e:
        logger.error(f"Failed to save local report: {e}")

    # Check if we should send a webhook
    webhook_url = os.getenv("REPORT_WEBHOOK_URL")
    if webhook_url:
        logger.info(f"Sending report to webhook: {webhook_url}")

        # Build HTML version for email friendliness
        html_desc = f"<h2>Sync Report - {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}</h2>"
        html_desc += f"<p><b>Duration:</b> {duration:.1f} seconds</p>"

        html_desc += "<h3>Phase 1: Sets and Cards</h3><ul>"
        if cards_metrics:
            html_desc += f"<li><b>New Sets Added:</b> {cards_metrics.get('new_sets', 0)}</li>"
            html_desc += f"<li><b>Cards Processed (New):</b> {cards_metrics.get('cards_processed', 0)}</li>"
            html_desc += f"<li><b>Cards Inserted:</b> {cards_metrics.get('new_cards', 0)}</li>"
            if cards_metrics.get("errors"):
                html_desc += "<li><b style='color:red;'>Errors:</b><ul>"
                for err in cards_metrics["errors"]:
                    html_desc += f"<li>{err}</li>"
                html_desc += "</ul></li>"
        else:
            html_desc += "<li><i>(Failed or Skipped)</i></li>"
        html_desc += "</ul>"

        html_desc += "<h3>Phase 2: Prices</h3><ul>"
        if prices_metrics:
            html_desc += f"<li><b>Cards Scheduled for Check:</b> {prices_metrics.get('scheduled_for_check', 0)} / {prices_metrics.get('total_cards', 0)}</li>"
            html_desc += f"<li><b>Cards Actually Checked:</b> {prices_metrics.get('checked_count', 0)}</li>"
            html_desc += f"<li><b>Prices Updated:</b> <span style='color:green;'>{prices_metrics.get('updated_count', 0)}</span></li>"

            html_desc += "<li><b>Strategy Breakdown:</b><ul>"
            for strat, count in prices_metrics.get("strategy_breakdown", {}).items():
                html_desc += f"<li><b>{strat}:</b> {count}</li>"
            html_desc += "</ul></li>"

            html_desc += "<li><b>Variant Updates:</b><ul>"
            for var, count in prices_metrics.get("variant_updates", {}).items():
                html_desc += f"<li><b>{var}:</b> {count}</li>"
            html_desc += "</ul></li>"

            if prices_metrics.get("errors_by_type"):
                html_desc += "<li><b style='color:red;'>Error Summary by Type:</b><ul>"
                for etype, count in prices_metrics["errors_by_type"].items():
                    html_desc += f"<li><b>{etype}:</b> {count}</li>"
                html_desc += "</ul></li>"
        else:
            html_desc += "<li><i>(Failed or Skipped)</i></li>"
        html_desc += "</ul>"

        payload = {
            "timestamp": start_time.isoformat(),
            "duration_seconds": duration,
            "report_markdown": report_desc,
            "report_html": html_desc,
            "metrics": {
                "new_sets": cards_metrics.get("new_sets", 0) if cards_metrics else 0,
                "new_cards": cards_metrics.get("new_cards", 0) if cards_metrics else 0,
                "prices_updated": prices_metrics.get("updated_count", 0) if prices_metrics else 0,
            },
        }

        try:
            # We are currently not in an async loop where generate_report is called natively
            # so we'll use a synchronous httpx client just for the webhook reporting to keep it simple.
            with httpx.Client(timeout=10.0) as client:
                res = client.post(webhook_url, json=payload)
                res.raise_for_status()
            logger.info("Webhook delivered successfully.")
        except Exception as e:
            logger.error(f"Failed to send webhook to {webhook_url}: {e}")


async def run_sync_job(force_prices: bool = False):
    """
    Main entry point for the scheduled synchronization job.
    """
    start_time = datetime.utcnow()
    logger.info(f"Starting scheduled PokeTCG Sync Job at {start_time} (UTC)")

    db = SessionLocal()
    sync.start_sync_flag()

    cards_metrics = {}
    prices_metrics = {}

    try:
        # Step 1: Sync Sets and New Cards
        logger.info("Executing Phase 1: Sets and Cards synchronization...")
        cards_metrics = await sync.sync_sets_and_cards(db)

        if sync.SHOULD_STOP:
            logger.warning("Sync stopped prematurely during Cards phase. Exiting.")
            return

        # Step 2: Sync Prices based on Temperature Strategy
        logger.info("Executing Phase 2: Price synchronization...")
        prices_metrics = await sync.sync_prices(db, force_prices=force_prices)

        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        logger.info(f"Sync Job completed successfully in {duration:.1f} seconds.")

    except Exception as e:
        logger.error(f"Fatal error during sync job execution: {e}", exc_info=True)
        end_time = datetime.utcnow()
    finally:
        generate_report(start_time, datetime.utcnow(), cards_metrics, prices_metrics)
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pokemon TCG Backend Synchronization Job")
    parser.add_argument("--force-prices", action="store_true", help="Force price sync regardless of temperature")
    args = parser.parse_args()

    logger.info("Initializing Sync Job environment...")
    asyncio.run(run_sync_job(force_prices=args.force_prices))
