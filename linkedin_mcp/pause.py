import asyncio
import random


async def human_pause(min_s: float, max_s: float) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
