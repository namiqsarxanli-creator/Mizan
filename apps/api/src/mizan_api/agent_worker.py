"""The Temporal worker that executes durable agent runs (``MIZAN_DURABLE__ENABLED``).

    mizan-agent-worker

It builds the same services as the API (retrieval, graph, LLM gateway) and serves the agent's
steps as activities on the task queue. Any number of workers can run; if one dies mid-run,
another continues the run from its last finished step.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from mizan_agents.durable import AgentActivities, AgentRun
from mizan_agents.orchestrator import Orchestrator
from mizan_api.dependencies import build_legal_services
from mizan_core.config import get_settings
from mizan_core.logging import configure_logging, get_logger

log = get_logger(__name__)


async def serve() -> None:
    settings = get_settings()
    configure_logging(settings.log.level, json_format=settings.log.json_format)
    # The worker runs the agent in process; only the API talks to Temporal as a client.
    local = settings.model_copy(
        update={"durable": settings.durable.model_copy(update={"enabled": False})}
    )
    async with AsyncExitStack() as stack:
        services = await build_legal_services(local, stack)
        agent = services.agent
        if not isinstance(agent, Orchestrator):
            raise SystemExit(
                "the agent is disabled: it needs MIZAN_LLM__GROQ_API_KEY and the graph"
            )
        client = await Client.connect(
            settings.durable.address,
            namespace=settings.durable.namespace,
            data_converter=pydantic_data_converter,
        )
        worker = Worker(
            client,
            task_queue=settings.durable.task_queue,
            workflows=[AgentRun],
            activities=AgentActivities(agent).all(),
        )
        log.info("agent_worker_started", queue=settings.durable.task_queue)
        await worker.run()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
