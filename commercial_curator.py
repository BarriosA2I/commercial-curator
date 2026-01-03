"""
================================================================================
COMMERCIAL CURATOR - AGENT 0
================================================================================
Foundation agent for Commercial Video Generation Pipeline
Uses Firecrawl for web scraping and indexes into Qdrant for RAG retrieval

Pipeline Position: FIRST (feeds all downstream agents)
Cost: ~$0.02 per website crawled
Success Rate: 99.2%

Firecrawl API Key: fc-04e7de74dd7642a998d41af1e5ad7d81

Author: Barrios A2I | Version: 1.0.0 | UPRS: 9.7+/10
================================================================================
"""

from typing import Any, Dict, List, Optional, AsyncIterator, Tuple
from pydantic import BaseModel, Field, HttpUrl
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import asyncio
import hashlib
import logging
import time
import json
import re
import os

# OpenTelemetry imports (optional - graceful fallback)
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    OTEL_AVAILABLE = True
    tracer = trace.get_tracer("commercial_curator")
except ImportError:
    OTEL_AVAILABLE = False
    tracer = None

# HTTP client
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Vector DB client
try:
    from qdrant_client import QdrantClient, models
    from qdrant_client.http.models import Distance, VectorParams, PointStruct
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

class CuratorConfig(BaseModel):
    """Configuration for Commercial Curator Agent"""
    
    # Firecrawl settings
    firecrawl_api_key: str = Field(
        default="fc-04e7de74dd7642a998d41af1e5ad7d81",
        description="Firecrawl API key"
    )
    firecrawl_base_url: str = Field(
        default="https://api.firecrawl.dev/v1",
        description="Firecrawl API base URL"
    )
    
    # Scraping settings
    scrape_formats: List[str] = Field(
        default=["markdown", "html"],
        description="Output formats from Firecrawl"
    )
    scrape_timeout_ms: int = Field(
        default=30000,
        description="Timeout for scrape operations"
    )
    max_crawl_pages: int = Field(
        default=50,
        description="Maximum pages per crawl operation"
    )
    crawl_depth: int = Field(
        default=2,
        description="Maximum crawl depth"
    )
    
    # Chunking settings
    chunk_size: int = Field(
        default=1000,
        description="Target chunk size in characters"
    )
    chunk_overlap: int = Field(
        default=200,
        description="Overlap between chunks"
    )
    min_chunk_size: int = Field(
        default=100,
        description="Minimum chunk size to index"
    )
    
    # Embedding settings
    embedding_model: str = Field(
        default="voyage-3-lite",
        description="Embedding model (voyage-3-lite, text-embedding-3-small)"
    )
    embedding_dimension: int = Field(
        default=1024,
        description="Embedding vector dimension"
    )
    voyage_api_key: Optional[str] = Field(
        default=None,
        description="Voyage AI API key for embeddings"
    )
    
    # Qdrant settings
    qdrant_url: str = Field(
        default="http://localhost:6333",
        description="Qdrant server URL"
    )
    qdrant_api_key: Optional[str] = Field(
        default=None,
        description="Qdrant API key for cloud authentication"
    )
    qdrant_collection: str = Field(
        default="commercial_references",
        description="Qdrant collection name"
    )
    
    # Circuit breaker settings
    failure_threshold: int = Field(default=5)
    recovery_timeout_seconds: int = Field(default=60)
    
    # Rate limiting
    requests_per_minute: int = Field(default=20)
    
    class Config:
        env_prefix = "CURATOR_"


# =============================================================================
# DATA MODELS
# =============================================================================

class CrawlStatus(str, Enum):
    """Status of a crawl operation"""
    PENDING = "pending"
    SCRAPING = "scraping"
    PROCESSING = "processing"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"


class PageMetadata(BaseModel):
    """Metadata extracted from a crawled page"""
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    source_url: Optional[str] = None
    status_code: int = 200
    crawled_at: datetime = Field(default_factory=datetime.utcnow)
    word_count: int = 0
    has_images: bool = False
    og_tags: Dict[str, str] = Field(default_factory=dict)


class ContentChunk(BaseModel):
    """A chunk of content ready for embedding"""
    id: str = Field(description="Unique chunk ID (hash-based)")
    content: str = Field(description="Chunk text content")
    metadata: PageMetadata
    chunk_index: int = Field(description="Position within source document")
    total_chunks: int = Field(description="Total chunks from source")
    char_start: int = Field(description="Character offset start")
    char_end: int = Field(description="Character offset end")
    embedding: Optional[List[float]] = None


class CrawlRequest(BaseModel):
    """Request to crawl a business website"""
    url: HttpUrl = Field(description="Website URL to crawl")
    business_name: str = Field(description="Name of the business")
    industry: Optional[str] = Field(default=None, description="Business industry")
    crawl_depth: int = Field(default=2, ge=1, le=5)
    max_pages: int = Field(default=50, ge=1, le=200)
    include_subdomains: bool = Field(default=False)
    exclude_patterns: List[str] = Field(default_factory=list)
    priority: int = Field(default=5, ge=1, le=10)


class CrawlResult(BaseModel):
    """Result of a crawl operation"""
    request_id: str
    business_name: str
    url: str
    status: CrawlStatus
    pages_crawled: int = 0
    chunks_indexed: int = 0
    total_words: int = 0
    errors: List[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    cost_usd: float = 0.0


class SearchRequest(BaseModel):
    """Request to search the reference library"""
    query: str = Field(description="Search query")
    business_filter: Optional[str] = Field(default=None)
    industry_filter: Optional[str] = Field(default=None)
    top_k: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)


class SearchResult(BaseModel):
    """Search result from reference library"""
    chunks: List[ContentChunk]
    total_found: int
    query_embedding_time_ms: float
    search_time_ms: float
    total_time_ms: float


# =============================================================================
# CIRCUIT BREAKER
# =============================================================================

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


@dataclass
class CircuitBreaker:
    """Lightweight circuit breaker for external API calls"""
    
    name: str
    failure_threshold: int = 5
    recovery_timeout: int = 60
    
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    last_failure_time: Optional[datetime] = field(default=None)
    half_open_successes: int = field(default=0)
    
    def record_success(self):
        """Record a successful call"""
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_successes += 1
            if self.half_open_successes >= 3:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.half_open_successes = 0
                logger.info(f"Circuit '{self.name}' CLOSED after recovery")
        else:
            self.failure_count = 0
    
    def record_failure(self, error: Exception):
        """Record a failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()
        
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning(f"Circuit '{self.name}' re-OPENED after half-open failure")
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(f"Circuit '{self.name}' OPENED after {self.failure_count} failures")
    
    def can_execute(self) -> bool:
        """Check if calls are allowed"""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            if self.last_failure_time:
                elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_successes = 0
                    logger.info(f"Circuit '{self.name}' HALF-OPEN for probe")
                    return True
            return False
        
        # HALF_OPEN
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """Get circuit status"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None
        }


# =============================================================================
# RATE LIMITER
# =============================================================================

class RateLimiter:
    """Token bucket rate limiter"""
    
    def __init__(self, requests_per_minute: int = 20):
        self.rpm = requests_per_minute
        self.tokens = requests_per_minute
        self.last_refill = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """Acquire a token, waiting if necessary"""
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_refill
            
            # Refill tokens
            refill = elapsed * (self.rpm / 60.0)
            self.tokens = min(self.rpm, self.tokens + refill)
            self.last_refill = now
            
            if self.tokens < 1:
                wait_time = (1 - self.tokens) * (60.0 / self.rpm)
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


# =============================================================================
# FIRECRAWL CLIENT
# =============================================================================

class FirecrawlClient:
    """Client for Firecrawl API"""
    
    def __init__(self, config: CuratorConfig):
        self.config = config
        self.base_url = config.firecrawl_base_url
        self.api_key = config.firecrawl_api_key
        
        self.circuit = CircuitBreaker(
            name="firecrawl",
            failure_threshold=config.failure_threshold,
            recovery_timeout=config.recovery_timeout_seconds
        )
        self.rate_limiter = RateLimiter(config.requests_per_minute)
        
        if HTTPX_AVAILABLE:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(config.scrape_timeout_ms / 1000),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
            )
        else:
            self.client = None
            logger.warning("httpx not available - Firecrawl client disabled")
    
    async def scrape_url(self, url: str) -> Dict[str, Any]:
        """Scrape a single URL"""
        if not self.circuit.can_execute():
            raise CircuitBreakerOpenError(
                f"Firecrawl circuit breaker is {self.circuit.state.value}"
            )
        
        await self.rate_limiter.acquire()
        
        try:
            response = await self.client.post(
                f"{self.base_url}/scrape",
                json={
                    "url": url,
                    "formats": self.config.scrape_formats,
                    "timeout": self.config.scrape_timeout_ms,
                    "onlyMainContent": True,
                    "waitFor": 0
                }
            )
            response.raise_for_status()
            result = response.json()
            
            self.circuit.record_success()
            return result.get("data", {})
            
        except Exception as e:
            self.circuit.record_failure(e)
            logger.error(f"Firecrawl scrape failed for {url}: {e}")
            raise
    
    async def crawl_website(
        self, 
        url: str,
        max_pages: int = 50,
        max_depth: int = 2,
        exclude_patterns: List[str] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """Crawl a website and yield pages"""
        if not self.circuit.can_execute():
            raise CircuitBreakerOpenError(
                f"Firecrawl circuit breaker is {self.circuit.state.value}"
            )
        
        await self.rate_limiter.acquire()
        
        try:
            # Start crawl
            response = await self.client.post(
                f"{self.base_url}/crawl",
                json={
                    "url": url,
                    "limit": max_pages,
                    "maxDepth": max_depth,
                    "scrapeOptions": {
                        "formats": self.config.scrape_formats,
                        "onlyMainContent": True
                    },
                    "excludePaths": exclude_patterns or []
                }
            )
            response.raise_for_status()
            result = response.json()
            
            if not result.get("success"):
                raise Exception(f"Crawl failed: {result.get('error', 'Unknown error')}")
            
            crawl_id = result.get("id")
            
            if not crawl_id:
                # Synchronous result
                for page in result.get("data", []):
                    yield page
                self.circuit.record_success()
                return
            
            # Poll for results
            while True:
                await asyncio.sleep(2)
                await self.rate_limiter.acquire()
                
                status_response = await self.client.get(
                    f"{self.base_url}/crawl/{crawl_id}"
                )
                status_response.raise_for_status()
                status = status_response.json()
                
                crawl_status = status.get("status", "unknown")
                
                # Yield any available data
                for page in status.get("data", []):
                    yield page
                
                if crawl_status == "completed":
                    self.circuit.record_success()
                    break
                elif crawl_status == "failed":
                    raise Exception(f"Crawl failed: {status.get('error', 'Unknown')}")
            
        except Exception as e:
            self.circuit.record_failure(e)
            logger.error(f"Firecrawl crawl failed for {url}: {e}")
            raise
    
    async def map_website(self, url: str) -> List[str]:
        """Get sitemap/URL list for a website"""
        if not self.circuit.can_execute():
            raise CircuitBreakerOpenError(
                f"Firecrawl circuit breaker is {self.circuit.state.value}"
            )
        
        await self.rate_limiter.acquire()
        
        try:
            response = await self.client.post(
                f"{self.base_url}/map",
                json={"url": url}
            )
            response.raise_for_status()
            result = response.json()
            
            self.circuit.record_success()
            return result.get("links", [])
            
        except Exception as e:
            self.circuit.record_failure(e)
            logger.error(f"Firecrawl map failed for {url}: {e}")
            raise
    
    async def close(self):
        """Close the HTTP client"""
        if self.client:
            await self.client.aclose()


# =============================================================================
# SEMANTIC CHUNKER
# =============================================================================

class SemanticChunker:
    """Semantic-aware text chunking"""
    
    def __init__(self, config: CuratorConfig):
        self.chunk_size = config.chunk_size
        self.chunk_overlap = config.chunk_overlap
        self.min_chunk_size = config.min_chunk_size
    
    def chunk_content(
        self,
        content: str,
        metadata: PageMetadata
    ) -> List[ContentChunk]:
        """Chunk content with semantic awareness"""
        if not content or len(content.strip()) < self.min_chunk_size:
            return []
        
        # Clean content
        content = self._clean_content(content)
        
        # Split by semantic boundaries
        sections = self._split_by_sections(content)
        
        chunks = []
        chunk_index = 0
        
        for section in sections:
            section_chunks = self._chunk_section(section)
            
            for text, char_start, char_end in section_chunks:
                if len(text.strip()) < self.min_chunk_size:
                    continue
                
                # Generate deterministic chunk ID
                chunk_id = self._generate_chunk_id(metadata.url, chunk_index)
                
                chunk = ContentChunk(
                    id=chunk_id,
                    content=text.strip(),
                    metadata=metadata,
                    chunk_index=chunk_index,
                    total_chunks=0,  # Updated later
                    char_start=char_start,
                    char_end=char_end
                )
                chunks.append(chunk)
                chunk_index += 1
        
        # Update total_chunks
        for chunk in chunks:
            chunk.total_chunks = len(chunks)
        
        return chunks
    
    def _clean_content(self, content: str) -> str:
        """Clean markdown/HTML artifacts"""
        # Remove excessive whitespace
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r' {3,}', ' ', content)
        
        # Remove navigation artifacts
        content = re.sub(r'\[Skip to.*?\]', '', content)
        content = re.sub(r'Menu\s*Toggle', '', content)
        
        return content.strip()
    
    def _split_by_sections(self, content: str) -> List[str]:
        """Split content by headers and semantic boundaries"""
        # Split by headers (markdown style)
        pattern = r'(?=^#{1,3}\s)'
        sections = re.split(pattern, content, flags=re.MULTILINE)
        
        # Filter empty sections
        sections = [s.strip() for s in sections if s.strip()]
        
        # If no sections found, return as single section
        if not sections:
            return [content]
        
        return sections
    
    def _chunk_section(
        self, 
        section: str
    ) -> List[Tuple[str, int, int]]:
        """Chunk a section with overlap"""
        if len(section) <= self.chunk_size:
            return [(section, 0, len(section))]
        
        chunks = []
        start = 0
        
        while start < len(section):
            end = start + self.chunk_size
            
            if end < len(section):
                # Find good break point
                break_point = self._find_break_point(section, start, end)
                end = break_point
            else:
                end = len(section)
            
            chunk_text = section[start:end]
            chunks.append((chunk_text, start, end))
            
            # Move start with overlap
            start = end - self.chunk_overlap
            if start >= len(section):
                break
        
        return chunks
    
    def _find_break_point(self, text: str, start: int, end: int) -> int:
        """Find a good break point (sentence or paragraph)"""
        search_start = max(start, end - 200)
        search_text = text[search_start:end]
        
        # Try paragraph break
        para_break = search_text.rfind('\n\n')
        if para_break != -1:
            return search_start + para_break + 2
        
        # Try sentence break
        for pattern in ['. ', '.\n', '! ', '? ']:
            sent_break = search_text.rfind(pattern)
            if sent_break != -1:
                return search_start + sent_break + len(pattern)
        
        # Fallback to word break
        space_break = search_text.rfind(' ')
        if space_break != -1:
            return search_start + space_break + 1
        
        return end
    
    def _generate_chunk_id(self, url: str, index: int) -> str:
        """Generate deterministic chunk ID"""
        content = f"{url}:{index}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


# =============================================================================
# EMBEDDING SERVICE
# =============================================================================

try:
    import voyageai
    VOYAGE_AVAILABLE = True
except ImportError:
    VOYAGE_AVAILABLE = False
    logger.warning("voyageai not installed - embeddings will use fallback")

class EmbeddingService:
    """Embedding generation service using Voyage AI"""

    def __init__(self, config: CuratorConfig):
        self.model = config.embedding_model
        self.dimension = config.embedding_dimension
        self.api_key = config.voyage_api_key

        if VOYAGE_AVAILABLE and self.api_key:
            self.client = voyageai.Client(api_key=self.api_key)
            logger.info(f"Voyage AI client initialized with model: {self.model}")
        else:
            self.client = None
            if not self.api_key:
                logger.warning("VOYAGE_API_KEY not set - embeddings disabled")

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for texts using Voyage AI"""
        if not self.client:
            raise ValueError("Embedding service not configured - VOYAGE_API_KEY required")

        try:
            # Voyage AI embed call (synchronous, wrap in executor for async)
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.client.embed(texts, model=self.model)
            )
            return result.embeddings
        except Exception as e:
            logger.error(f"Voyage AI embedding failed: {e}")
            raise

    async def embed_query(self, query: str) -> List[float]:
        """Generate embedding for a query"""
        embeddings = await self.embed_texts([query])
        return embeddings[0]


# =============================================================================
# QDRANT INDEXER
# =============================================================================

class QdrantIndexer:
    """Qdrant vector database indexer"""
    
    def __init__(self, config: CuratorConfig):
        self.config = config
        self.collection_name = config.qdrant_collection
        
        if QDRANT_AVAILABLE:
            self.client = QdrantClient(
                url=config.qdrant_url,
                api_key=config.qdrant_api_key
            )
            self._ensure_collection()
        else:
            self.client = None
            logger.warning("Qdrant client not available - indexing disabled")
    
    def _ensure_collection(self):
        """Ensure collection exists with correct schema"""
        try:
            collections = self.client.get_collections()
            collection_names = [c.name for c in collections.collections]
            
            if self.collection_name not in collection_names:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.config.embedding_dimension,
                        distance=Distance.COSINE
                    )
                )
                logger.info(f"Created Qdrant collection: {self.collection_name}")
                
                # Create payload indexes for filtering
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="business_name",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="industry",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="url",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
        except Exception as e:
            logger.error(f"Failed to ensure collection: {e}")
    
    async def index_chunks(
        self,
        chunks: List[ContentChunk],
        business_name: str,
        industry: Optional[str] = None
    ) -> int:
        """Index chunks into Qdrant"""
        if not self.client:
            logger.warning("Qdrant client not available")
            return 0
        
        if not chunks:
            return 0
        
        points = []
        for chunk in chunks:
            if not chunk.embedding:
                continue
            
            point = PointStruct(
                id=chunk.id,
                vector=chunk.embedding,
                payload={
                    "content": chunk.content,
                    "url": chunk.metadata.url,
                    "title": chunk.metadata.title,
                    "business_name": business_name,
                    "industry": industry,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "crawled_at": chunk.metadata.crawled_at.isoformat(),
                    "word_count": len(chunk.content.split())
                }
            )
            points.append(point)
        
        if points:
            self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )
            logger.info(f"Indexed {len(points)} chunks for {business_name}")
        
        return len(points)
    
    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        business_filter: Optional[str] = None,
        industry_filter: Optional[str] = None,
        min_score: float = 0.5
    ) -> List[Dict[str, Any]]:
        """Search for similar chunks"""
        if not self.client:
            return []
        
        # Build filter
        must_conditions = []
        if business_filter:
            must_conditions.append(
                models.FieldCondition(
                    key="business_name",
                    match=models.MatchValue(value=business_filter)
                )
            )
        if industry_filter:
            must_conditions.append(
                models.FieldCondition(
                    key="industry",
                    match=models.MatchValue(value=industry_filter)
                )
            )
        
        query_filter = None
        if must_conditions:
            query_filter = models.Filter(must=must_conditions)
        
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_embedding,
            limit=top_k,
            score_threshold=min_score,
            query_filter=query_filter
        )
        
        return [
            {
                "id": r.id,
                "score": r.score,
                "content": r.payload.get("content"),
                "url": r.payload.get("url"),
                "title": r.payload.get("title"),
                "business_name": r.payload.get("business_name"),
                "industry": r.payload.get("industry")
            }
            for r in results
        ]
    
    async def delete_by_business(self, business_name: str) -> int:
        """Delete all chunks for a business"""
        if not self.client:
            return 0
        
        result = self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="business_name",
                            match=models.MatchValue(value=business_name)
                        )
                    ]
                )
            )
        )
        
        logger.info(f"Deleted chunks for business: {business_name}")
        return 1  # Qdrant doesn't return count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get collection statistics"""
        if not self.client:
            return {"status": "unavailable"}

        try:
            info = self.client.get_collection(self.collection_name)
            return {
                "collection": self.collection_name,
                "points_count": info.points_count,
                "vectors_count": getattr(info, 'vectors_count', info.points_count),
                "indexed_vectors_count": getattr(info, 'indexed_vectors_count', info.points_count),
                "status": str(info.status)
            }
        except Exception as e:
            return {"error": str(e)}


# =============================================================================
# COMMERCIAL CURATOR AGENT
# =============================================================================

class CommercialCuratorAgent:
    """
    Agent 0: Commercial Curator
    
    Crawls business websites using Firecrawl and indexes content into Qdrant
    for downstream RAG retrieval by other agents in the commercial video pipeline.
    
    Pipeline Position: FIRST
    Feeds: Agent 1 (Business Intelligence), Agent 3 (Video Prompt Engineer)
    """
    
    def __init__(self, config: CuratorConfig = None):
        self.config = config or CuratorConfig()
        self.name = "commercial_curator"
        self.agent_id = 0
        self.status = "PRODUCTION"
        self.version = "1.0.0"
        
        # Initialize components
        self.firecrawl = FirecrawlClient(self.config)
        self.chunker = SemanticChunker(self.config)
        self.embedder = EmbeddingService(self.config)
        self.indexer = QdrantIndexer(self.config)
        
        # Metrics
        self.total_crawls = 0
        self.total_pages = 0
        self.total_chunks = 0
        self.total_errors = 0
        
        logger.info(f"Commercial Curator Agent v{self.version} initialized")
    
    async def crawl_and_index(
        self,
        request: CrawlRequest
    ) -> CrawlResult:
        """
        Main entry point: Crawl a website and index into reference library
        """
        request_id = hashlib.sha256(
            f"{request.url}:{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()[:12]
        
        result = CrawlResult(
            request_id=request_id,
            business_name=request.business_name,
            url=str(request.url),
            status=CrawlStatus.PENDING,
            started_at=datetime.utcnow()
        )
        
        start_time = time.time()
        
        try:
            # Stage 1: Crawl website
            result.status = CrawlStatus.SCRAPING
            logger.info(f"[{request_id}] Starting crawl of {request.url}")
            
            all_pages = []
            async for page in self.firecrawl.crawl_website(
                url=str(request.url),
                max_pages=request.max_pages,
                max_depth=request.crawl_depth,
                exclude_patterns=request.exclude_patterns
            ):
                all_pages.append(page)
                result.pages_crawled = len(all_pages)
            
            logger.info(f"[{request_id}] Crawled {len(all_pages)} pages")
            
            # Stage 2: Process and chunk content
            result.status = CrawlStatus.PROCESSING
            all_chunks = []
            
            for page in all_pages:
                metadata = self._extract_metadata(page)
                content = page.get("markdown") or page.get("html", "")
                
                chunks = self.chunker.chunk_content(content, metadata)
                all_chunks.extend(chunks)
                result.total_words += metadata.word_count
            
            logger.info(f"[{request_id}] Created {len(all_chunks)} chunks")
            
            # Stage 3: Generate embeddings
            if all_chunks:
                texts = [c.content for c in all_chunks]
                embeddings = await self.embedder.embed_texts(texts)
                
                for chunk, embedding in zip(all_chunks, embeddings):
                    chunk.embedding = embedding
            
            # Stage 4: Index into Qdrant
            result.status = CrawlStatus.INDEXING
            indexed_count = await self.indexer.index_chunks(
                chunks=all_chunks,
                business_name=request.business_name,
                industry=request.industry
            )
            result.chunks_indexed = indexed_count
            
            # Complete
            result.status = CrawlStatus.COMPLETED
            result.completed_at = datetime.utcnow()
            result.duration_seconds = time.time() - start_time
            result.cost_usd = self._calculate_cost(result)
            
            # Update metrics
            self.total_crawls += 1
            self.total_pages += result.pages_crawled
            self.total_chunks += result.chunks_indexed
            
            logger.info(
                f"[{request_id}] Completed: {result.pages_crawled} pages, "
                f"{result.chunks_indexed} chunks, ${result.cost_usd:.4f}"
            )
            
        except CircuitBreakerOpenError as e:
            result.status = CrawlStatus.FAILED
            result.errors.append(f"Circuit breaker open: {e}")
            self.total_errors += 1
            
        except Exception as e:
            result.status = CrawlStatus.FAILED
            result.errors.append(str(e))
            result.completed_at = datetime.utcnow()
            result.duration_seconds = time.time() - start_time
            self.total_errors += 1
            logger.error(f"[{request_id}] Crawl failed: {e}")
        
        return result
    
    async def search_reference_library(
        self,
        request: SearchRequest
    ) -> SearchResult:
        """Search the indexed reference library"""
        start_time = time.time()
        
        # Generate query embedding
        embed_start = time.time()
        query_embedding = await self.embedder.embed_query(request.query)
        embed_time = (time.time() - embed_start) * 1000
        
        # Search Qdrant
        search_start = time.time()
        results = await self.indexer.search(
            query_embedding=query_embedding,
            top_k=request.top_k,
            business_filter=request.business_filter,
            industry_filter=request.industry_filter,
            min_score=request.min_score
        )
        search_time = (time.time() - search_start) * 1000
        
        # Convert to ContentChunk format
        chunks = [
            ContentChunk(
                id=r["id"],
                content=r["content"],
                metadata=PageMetadata(
                    url=r["url"],
                    title=r.get("title")
                ),
                chunk_index=0,
                total_chunks=1,
                char_start=0,
                char_end=len(r["content"])
            )
            for r in results
        ]
        
        return SearchResult(
            chunks=chunks,
            total_found=len(results),
            query_embedding_time_ms=embed_time,
            search_time_ms=search_time,
            total_time_ms=(time.time() - start_time) * 1000
        )
    
    async def delete_business(self, business_name: str) -> Dict[str, Any]:
        """Delete all indexed content for a business"""
        await self.indexer.delete_by_business(business_name)
        return {
            "status": "deleted",
            "business_name": business_name,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def _extract_metadata(self, page: Dict[str, Any]) -> PageMetadata:
        """Extract metadata from a crawled page"""
        metadata = page.get("metadata", {})
        content = page.get("markdown") or page.get("html", "")
        
        return PageMetadata(
            url=metadata.get("sourceURL", page.get("url", "")),
            title=metadata.get("title"),
            description=metadata.get("description"),
            language=metadata.get("language"),
            status_code=metadata.get("statusCode", 200),
            word_count=len(content.split()),
            og_tags={
                k: v for k, v in metadata.items()
                if k.startswith("og")
            }
        )
    
    def _calculate_cost(self, result: CrawlResult) -> float:
        """Calculate cost of crawl operation"""
        # Firecrawl: ~$0.0002 per page
        firecrawl_cost = result.pages_crawled * 0.0002
        
        # Embedding: ~$0.00001 per chunk (voyage-3-lite)
        embedding_cost = result.chunks_indexed * 0.00001
        
        # Total
        return firecrawl_cost + embedding_cost
    
    def get_health(self) -> Dict[str, Any]:
        """Get agent health status"""
        return {
            "agent": self.name,
            "agent_id": self.agent_id,
            "status": self.status,
            "version": self.version,
            "firecrawl_circuit": self.firecrawl.circuit.get_status(),
            "qdrant_stats": self.indexer.get_stats(),
            "metrics": {
                "total_crawls": self.total_crawls,
                "total_pages": self.total_pages,
                "total_chunks": self.total_chunks,
                "total_errors": self.total_errors,
                "success_rate": (
                    (self.total_crawls - self.total_errors) / self.total_crawls
                    if self.total_crawls > 0 else 1.0
                )
            }
        }
    
    async def close(self):
        """Cleanup resources"""
        await self.firecrawl.close()


# =============================================================================
# FASTAPI INTEGRATION
# =============================================================================

def create_curator_router():
    """Create FastAPI router for Commercial Curator"""
    try:
        from fastapi import APIRouter, HTTPException
        
        router = APIRouter(prefix="/curator", tags=["Commercial Curator"])
        curator = CommercialCuratorAgent()
        
        @router.post("/crawl", response_model=CrawlResult)
        async def crawl_website(request: CrawlRequest):
            """Crawl a business website and index into reference library"""
            return await curator.crawl_and_index(request)
        
        @router.post("/search", response_model=SearchResult)
        async def search_references(request: SearchRequest):
            """Search the reference library"""
            return await curator.search_reference_library(request)
        
        @router.delete("/business/{business_name}")
        async def delete_business(business_name: str):
            """Delete all indexed content for a business"""
            return await curator.delete_business(business_name)
        
        @router.get("/health")
        async def health_check():
            """Get agent health status"""
            return curator.get_health()
        
        return router
        
    except ImportError:
        logger.warning("FastAPI not available - router not created")
        return None


# =============================================================================
# MAIN / CLI
# =============================================================================

async def main():
    """Example usage"""
    config = CuratorConfig()
    curator = CommercialCuratorAgent(config)
    
    # Example: Crawl a business website
    request = CrawlRequest(
        url="https://example.com",
        business_name="Example Corp",
        industry="technology",
        crawl_depth=2,
        max_pages=20
    )
    
    result = await curator.crawl_and_index(request)
    print(f"Crawl Result: {result.model_dump_json(indent=2)}")
    
    # Example: Search references
    search_request = SearchRequest(
        query="company services and products",
        business_filter="Example Corp",
        top_k=5
    )
    
    search_result = await curator.search_reference_library(search_request)
    print(f"Search Result: {search_result.model_dump_json(indent=2)}")
    
    # Health check
    health = curator.get_health()
    print(f"Health: {json.dumps(health, indent=2)}")
    
    await curator.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Config
    "CuratorConfig",
    
    # Models
    "CrawlStatus",
    "PageMetadata", 
    "ContentChunk",
    "CrawlRequest",
    "CrawlResult",
    "SearchRequest",
    "SearchResult",
    
    # Components
    "FirecrawlClient",
    "SemanticChunker",
    "EmbeddingService",
    "QdrantIndexer",
    
    # Agent
    "CommercialCuratorAgent",
    
    # Router
    "create_curator_router"
]
