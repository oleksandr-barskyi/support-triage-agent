import asyncio
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop import Limits, run_agent
from app.agent.model import AgentModel


class RunScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        model_factory: Callable[[], AgentModel],
        limits: Limits,
    ) -> None:
        self.session_factory = session_factory
        self.model_factory = model_factory
        self.limits = limits
        self._model: AgentModel | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def model(self) -> AgentModel:
        if self._model is None:
            self._model = self.model_factory()
        return self._model

    def start(self, run_id: int) -> None:
        task = asyncio.create_task(run_agent(self.session_factory, self.model, run_id, self.limits))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
