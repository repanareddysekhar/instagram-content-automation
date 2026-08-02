import argparse
import asyncio

from app.config import get_settings
from app.db import Database
from app.pipeline import ContentPipeline


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Tech Content Agent pipeline")
    parser.add_argument("--demo", action="store_true", help="Use the built-in demo source")
    parser.add_argument("--sync-metrics", action="store_true", help="Sync insights instead of drafting")
    args = parser.parse_args()

    settings = get_settings()
    db = Database(settings.database_file)
    db.init()
    pipeline = ContentPipeline(settings, db)
    result = (
        await pipeline.sync_metrics()
        if args.sync_metrics
        else await pipeline.run(force_demo=args.demo)
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())

