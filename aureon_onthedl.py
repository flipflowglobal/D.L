import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
from enum import Enum

from dotenv import load_dotenv

load_dotenv(".env")

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

try:
    from solders.keypair import Keypair
    from solana.rpc.api import Client as SolanaClient
    _SOLANA = True
except ImportError:
    _SOLANA = False

import httpx
from web3 import Web3
from fastapi import FastAPI
from pydantic import BaseModel

# ---------------- CONFIG ----------------
DATA_DIR = Path.home() / ".aureon_onthedl"
DATA_DIR.mkdir(exist_ok=True)

SOLANA_RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
ETH_RPC = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
BSC_RPC = os.getenv("BSC_RPC", "https://bsc-dataseed.binance.org/")

# ---------------- AGENT SYSTEM ----------------
class AgentType(str, Enum):
    MONITOR = "monitor"
    FILE_MANAGER = "file_manager"
    BLOCKCHAIN = "blockchain"
    EXECUTOR = "executor"
    ANALYZER = "analyzer"

class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"

class AgentConfig(BaseModel):
    name: str
    agent_type: AgentType

class TaskRequest(BaseModel):
    agent_id: str
    task_type: str
    payload: Dict[str, Any] = {}

class Agent:
    def __init__(self, name, agent_type):
        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.type = agent_type
        self.status = AgentStatus.IDLE
        self.created = datetime.now()

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

    async def execute_task(self, task: TaskRequest):
        agent = self.agents.get(task.agent_id)
        if not agent:
            print("Agent not found:", task.agent_id)
            return

        agent.status = AgentStatus.RUNNING
        try:
            if agent.type == AgentType.MONITOR:
                result = monitor_system()
            elif agent.type == AgentType.FILE_MANAGER:
                result = file_manager(task)
            elif agent.type == AgentType.BLOCKCHAIN:
                result = await blockchain_task(task.payload)
            elif agent.type == AgentType.EXECUTOR:
                result = await fetch_url(task.payload.get("url", ""))
            elif agent.type == AgentType.ANALYZER:
                result = analyze_text(task.payload.get("text", ""))
            else:
                result = {"status": "unknown"}
            print("TASK RESULT:", result)
        except Exception as e:
            print("ERROR:", e)
        agent.status = AgentStatus.IDLE

# ---------------- SYSTEM FUNCTIONS ----------------
def monitor_system():
    if not _PSUTIL:
        return {"error": "psutil not available", "timestamp": datetime.now().isoformat()}
    return {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "timestamp": datetime.now().isoformat(),
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
        return {"status": r.status_code, "size": len(r.text)}

def analyze_text(text):
    return {"analysis": f"Text length: {len(text)}"}

# ---------------- BLOCKCHAIN MODULE ----------------
solana_client = SolanaClient(SOLANA_RPC) if _SOLANA else None
eth_client = Web3(Web3.HTTPProvider(ETH_RPC)) if ETH_RPC else None
bsc_client = Web3(Web3.HTTPProvider(BSC_RPC))

wallets: Dict[str, dict] = {"solana": {}, "ethereum": {}, "bsc": {}}

async def blockchain_task(payload: Dict[str, Any]):
    command = payload.get("command")
    chain = payload.get("chain", "").lower()

    if command == "new_wallet":
        if chain == "solana":
            if not _SOLANA:
                return {"error": "solana/solders not installed"}
            kp = Keypair()
            wallets["solana"][str(kp.pubkey())] = kp.to_bytes().hex()
            return {"address": str(kp.pubkey()), "private_key_hex": kp.to_bytes().hex()}
        elif chain in ("ethereum", "bsc"):
            w3 = eth_client if chain == "ethereum" else bsc_client
            if w3 is None:
                return {"error": f"RPC not configured for {chain}"}
            acct = w3.eth.account.create()
            wallets[chain][acct.address] = acct.key.hex()
            return {"address": acct.address, "private_key_hex": acct.key.hex()}

    elif command == "check_balance":
        address = payload.get("address")
        if chain == "solana":
            if not _SOLANA or solana_client is None:
                return {"error": "solana not available"}
            res = solana_client.get_balance(address)
            return {"address": address, "balance_lamports": res.value}
        elif chain in ("ethereum", "bsc"):
            w3 = eth_client if chain == "ethereum" else bsc_client
            if w3 is None:
                return {"error": f"RPC not configured for {chain}"}
            balance = w3.eth.get_balance(address)
            return {
                "address": address,
                "balance_wei": balance,
                "balance_eth": float(w3.from_wei(balance, "ether")),
            }

    return {"status": "unknown command or chain"}

# ---------------- FASTAPI ----------------
orchestrator = Orchestrator()


@asynccontextmanager
async def lifespan(app: FastAPI):
    for _ in range(3):
        asyncio.create_task(orchestrator.worker())
    yield


app = FastAPI(title="Aureon + OnTheDL System", lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "running"}

@app.post("/agents")
def create_agent(config: AgentConfig):
    agent = orchestrator.create_agent(config)
    return {"agent_id": agent.id, "name": agent.name, "type": agent.type}

@app.post("/tasks")
async def add_task(task: TaskRequest):
    await orchestrator.task_queue.put(task)
    return {"queued": True}

# ---------------- MAIN ----------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
