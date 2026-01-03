"""
================================================================================
COMMERCIAL CURATOR - BACKGROUND WORKER
================================================================================
Processes long-running crawl jobs from Redis queue
Runs continuously as a Render Worker service

Author: Barrios A2I | Version: 1.0.0
================================================================================
"""

import os
import time
import logging
import asyncio
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Redis Queue
try:
    import redis
    from rq import Worker, Queue, Connection
    from rq.job import Job
    RQ_AVAILABLE = True
except ImportError:
    RQ_AVAILABLE = False
    logger.warning("RQ not available - worker will run in polling mode")

from commercial_curator import (
    CommercialCuratorAgent,
    CuratorConfig,
    CrawlRequest,
    CrawlResult,
    CrawlStatus
)


# =============================================================================
# CONFIGURATION
# =============================================================================

def get_config() -> CuratorConfig:
    """Load configuration from environment"""
    return CuratorConfig(
        firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY", ""),
        qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY"),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "commercial_references"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "voyage-3-lite"),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
        voyage_api_key=os.getenv("VOYAGE_API_KEY"),
        chunk_size=int(os.getenv("CHUNK_SIZE", "1000")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "200")),
        requests_per_minute=int(os.getenv("REQUESTS_PER_MINUTE", "20")),
        failure_threshold=int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5")),
        recovery_timeout_seconds=int(os.getenv("CIRCUIT_RECOVERY_TIMEOUT", "60"))
    )


# =============================================================================
# JOB HANDLERS
# =============================================================================

def crawl_website(
    url: str,
    business_name: str,
    industry: str = None,
    crawl_depth: int = 2,
    max_pages: int = 50
) -> dict:
    """
    RQ job handler for website crawling
    
    This function is called by the RQ worker when a job is dequeued.
    It's synchronous but internally runs the async curator.
    """
    logger.info(f"[WORKER] Processing crawl job for {business_name}")
    
    config = get_config()
    curator = CommercialCuratorAgent(config)
    
    request = CrawlRequest(
        url=url,
        business_name=business_name,
        industry=industry,
        crawl_depth=crawl_depth,
        max_pages=max_pages
    )
    
    # Run async function in sync context
    result = asyncio.run(curator.crawl_and_index(request))
    
    # Cleanup
    asyncio.run(curator.close())
    
    logger.info(
        f"[WORKER] Completed: {result.pages_crawled} pages, "
        f"{result.chunks_indexed} chunks, ${result.cost_usd:.4f}"
    )
    
    return result.model_dump()


def delete_business_data(business_name: str) -> dict:
    """RQ job handler for deleting business data"""
    logger.info(f"[WORKER] Deleting data for {business_name}")
    
    config = get_config()
    curator = CommercialCuratorAgent(config)
    
    result = asyncio.run(curator.delete_business(business_name))
    asyncio.run(curator.close())
    
    return result


# =============================================================================
# WORKER MAIN
# =============================================================================

def run_rq_worker():
    """Run RQ worker connected to Redis"""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    logger.info(f"🔧 Connecting to Redis: {redis_url}")
    
    redis_conn = redis.from_url(redis_url)
    
    # Listen on default and high-priority queues
    queues = [
        Queue("curator_high", connection=redis_conn),
        Queue("curator_default", connection=redis_conn),
        Queue("curator_low", connection=redis_conn)
    ]
    
    with Connection(redis_conn):
        worker = Worker(queues)
        logger.info("🚀 Commercial Curator Worker started")
        logger.info(f"   Queues: {[q.name for q in queues]}")
        worker.work(with_scheduler=True)


def run_polling_worker():
    """Fallback worker without Redis - polls for work"""
    logger.info("🚀 Commercial Curator Worker started (polling mode)")
    logger.warning("⚠️  No Redis available - running in limited polling mode")
    
    while True:
        # In polling mode, we just keep the worker alive
        # Actual jobs come through the FastAPI /crawl/sync endpoint
        logger.debug("Worker heartbeat...")
        time.sleep(60)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("COMMERCIAL CURATOR - BACKGROUND WORKER")
    logger.info("="*60)
    
    # Check configuration
    config = get_config()
    logger.info(f"Firecrawl: {'✅ configured' if config.firecrawl_api_key else '❌ NOT SET'}")
    logger.info(f"Qdrant: {config.qdrant_url}")
    logger.info(f"Collection: {config.qdrant_collection}")
    
    if RQ_AVAILABLE and os.getenv("REDIS_URL"):
        run_rq_worker()
    else:
        run_polling_worker()
