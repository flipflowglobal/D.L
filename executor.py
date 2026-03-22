import json
from tools import registry

async def execute_task(payload):

    tool_name = payload["function_name"]

    args = payload["arguments"]

    result = await registry.execute(tool_name, **args)

    return result
