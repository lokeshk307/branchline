import asyncio
from collections import defaultdict


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, workspace_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.rooms[workspace_id].add(queue)
        return queue

    def unsubscribe(self, workspace_id: str, queue: asyncio.Queue) -> None:
        self.rooms[workspace_id].discard(queue)

    def publish(self, workspace_id: str, message: dict) -> None:
        for queue in list(self.rooms.get(workspace_id, ())):
            queue.put_nowait(message)


hub = Hub()
