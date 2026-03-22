import asyncio
import uuid
import platform
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from enum import Enum

import psutil
import httpx
import requests

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn


# ---------------- CONFIG ----------------

class Config:
    HOST = "0.0.0.0"
    PORT = 8010
    MAX_WORKERS = 3
    DATA_DIR = Path.home() / ".aureon"


Config.DATA_DIR.mkdir(exist_ok=True)


# ---------------- MODELS ----------------

class AgentType(str, Enum):

    MONITOR = "monitor"
    EXECUTOR = "executor"
    ANALYZER = "analyzer"
    FILE_MANAGER = "file_manager"


class AgentStatus(str, Enum):

    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


class AgentConfig(BaseModel):

    name: str
    agent_type: AgentType


class TaskRequest(BaseModel):

    agent_id: str
    task_type: str
    payload: Dict[str, Any] = {}


# ---------------- DEVICE INFO ----------------

def device_info():

    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version()
    }


# ---------------- AGENT ----------------

class Agent:

    def __init__(self, name, agent_type):

        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.type = agent_type
        self.status = AgentStatus.IDLE
        self.created = datetime.now()


# ---------------- ORCHESTRATOR ----------------

class Orchestrator:

    def __init__(self):

        self.agents: Dict[str, Agent] = {}
        self.task_queue = asyncio.Queue()


    async def worker(self):

        while True:

            task = await self.task_queue.get()

            await self.execute_task(task)

            self.task_queue.task_done()


    def create_agent(self, config: AgentConfig):

        agent = Agent(config.name, config.agent_type)

        self.agents[agent.id] = agent

        return agent


    def list_agents(self):

        return list(self.agents.values())


    async def execute_task(self, task: TaskRequest):

        agent = self.agents.get(task.agent_id)

        if not agent:
            return

        agent.status = AgentStatus.RUNNING

        try:

            if agent.type == AgentType.MONITOR:

                result = monitor_system()

            elif agent.type == AgentType.FILE_MANAGER:

                result = file_manager(task)

            elif agent.type == AgentType.EXECUTOR:

                result = await fetch_url(task.payload["url"])

            elif agent.type == AgentType.ANALYZER:

                result = analyze_text(task.payload["text"])

            else:

                result = {"status": "unknown"}

            print("TASK RESULT:", result)

        except Exception as e:

            print("ERROR:", e)

        agent.status = AgentStatus.IDLE


# ---------------- FUNCTIONS ----------------

def monitor_system():

    return {

        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "timestamp": datetime.now().isoformat()

    }


def file_manager(task):

    action = task.payload.get("action")

    if action == "list":

        path = task.payload.get("path", ".")

        return [str(p) for p in Path(path).glob("*")]

    return {"status": "unknown"}


async def fetch_url(url):

    async with httpx.AsyncClient() as client:

        r = await client.get(url)

    return {

        "status": r.status_code,
        "size": len(r.text)

    }


def analyze_text(text):

    try:

        r = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3",
                "prompt": text
            }
        )

        return r.json()

    except:

        return {"ai": "ollama not running"}


# ---------------- SCHEDULER ----------------

async def scheduler():

    while True:

        for agent in orchestrator.agents.values():

            if agent.type == AgentType.MONITOR:

                await orchestrator.task_queue.put(
                    TaskRequest(
                        agent_id=agent.id,
                        task_type="monitor",
                        payload={}
                    )
                )

        await asyncio.sleep(10)


# ---------------- FASTAPI ----------------

orchestrator = Orchestrator()

app = FastAPI(title="Aureon Agent System")


@app.on_event("startup")
async def startup():

    for _ in range(Config.MAX_WORKERS):

        asyncio.create_task(orchestrator.worker())

    asyncio.create_task(scheduler())


@app.get("/")
def root():

    return {
        "status": "running",
        "device": device_info()
    }


@app.get("/agents")
def list_agents():

    return orchestrator.list_agents()


@app.post("/agents")
def create_agent(config: AgentConfig):

    agent = orchestrator.create_agent(config)

    return {
        "agent_id": agent.id,
        "name": agent.name,
        "type": agent.type
    }


@app.post("/tasks")
async def add_task(task: TaskRequest):

    await orchestrator.task_queue.put(task)

    return {"queued": True}


# ---------------- MAIN ----------------

if __name__ == "__main__":

    print("\nAUREON SERVER STARTING\n")

    uvicorn.run(
        app,
        host=Config.HOST,
        port=Config.PORT
    )
