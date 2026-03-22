class ToolRegistry:

    def __init__(self):
        self.tools = {}

    def register(self,name):

        def decorator(func):

            self.tools[name] = func

            return func

        return decorator

    async def execute(self,name,**kwargs):

        if name not in self.tools:
            raise ValueError(f"Tool {name} not found")

        return await self.tools[name](**kwargs)


registry = ToolRegistry()


@registry.register("ping")

async def tool_ping():

    return {"message":"pong from aureon"}
