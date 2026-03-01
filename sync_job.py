import asyncio
import logging
import sys
import argparse
from datetime import datetime
import json
import os

# Import database and sync modules
from database import SessionLocal
import sync
import models

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
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
            for err in cards_metrics['errors']:
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
            for etype, count in prices_metrics['errors_by_type'].items():
                report_desc += f"- **{etype}:** {count}\n"
    else:
        report_desc += "*(Failed or Skipped)*\n"
        
    filename = f"reports/sync_report_{start_time.strftime('%Y%m%d_%H%M%S')}.md"
    os.makedirs("reports", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(report_desc)
    logger.info(f"Sync report generated: {filename}")

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
