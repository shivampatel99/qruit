from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.connectors.registry import ConnectorRegistry
from app.crypto import ApprovalTokenSigner, TokenCipher
from app.pipeline.approval import ApprovalPipeline
from app.pipeline.ingestion import IngestionPipeline
from app.pipeline.interview import InterviewPipeline
from app.pipeline.screening import ScreeningPipeline
from app.services.alerts import DeliveryService
from app.services.connector_config import load_all_configs
from app.services.jobs import JobQueue
from app.services.reqruit_client import ReqruitClient, ReqruitInterviewProvider
from app.storage import Storage


@dataclass
class Container:
    settings: Settings
    storage: Storage
    cipher: TokenCipher
    signer: ApprovalTokenSigner
    registry: ConnectorRegistry
    reqruit: ReqruitClient
    interview_provider: ReqruitInterviewProvider
    delivery: DeliveryService
    ingestion: IngestionPipeline
    screening: ScreeningPipeline
    approval: ApprovalPipeline
    interview: InterviewPipeline
    jobs: JobQueue


async def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    storage = Storage(settings.database_url)
    await storage.init_schema()
    cipher = TokenCipher(settings)
    signer = ApprovalTokenSigner(settings)
    await load_all_configs(settings, storage, cipher)
    registry = ConnectorRegistry(settings, storage)
    reqruit = ReqruitClient(settings)
    interview_provider = ReqruitInterviewProvider(settings)
    delivery = DeliveryService(settings, storage, registry, signer)
    ingestion = IngestionPipeline(settings, storage, registry, reqruit, delivery)
    screening = ScreeningPipeline(settings, storage, reqruit, delivery, signer)
    interview = InterviewPipeline(settings, storage, interview_provider, delivery, signer)
    approval = ApprovalPipeline(storage, registry, delivery, screening, signer)
    jobs = JobQueue(settings)
    return Container(
        settings=settings, storage=storage, cipher=cipher, signer=signer, registry=registry,
        reqruit=reqruit, interview_provider=interview_provider, delivery=delivery,
        ingestion=ingestion, screening=screening, approval=approval, interview=interview,
        jobs=jobs,
    )


_container: Container | None = None
_container_lock = asyncio.Lock()


async def get_container() -> Container:
    """Process-wide singleton, built once. A plain module global (not
    functools.lru_cache, which can't cache a coroutine's result) guarded by
    a lock so concurrent first requests don't each build their own Container."""
    global _container
    if _container is None:
        async with _container_lock:
            if _container is None:
                _container = await build_container()
    return _container
