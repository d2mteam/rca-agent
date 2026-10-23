import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class DockerTools:
    def __init__(self, settings):
        self.settings = settings

    async def call(self, name, arguments):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "hitl.mcp_server"],
            env={**os.environ, "HITL_DATA_DIR": str(self.settings.data_dir)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=240)) as session:
                await session.initialize()
                response = await session.call_tool(name, arguments)
                if response.isError:
                    raise ValueError(" ".join(c.text for c in response.content if hasattr(c, "text")))
                if response.structuredContent is not None:
                    return response.structuredContent
                return json.loads(next(c.text for c in response.content if hasattr(c, "text")))
