import asyncio
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
from enum import Enum

import psutil
import httpx
from solders.keypair import Keypair
from solana.rpc.api import Client as SolanaClient
from web3 import Web3
from fastapi import FastAPI
from pydantic import BaseModel

# ---------------- CONFIG ----------------
DATA_DIR = Path.home() / ".aureon_blockchain"
DATA_DIR.mkdir(exist_ok=True)

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
ETH_RPC = "https://mainnet.infura.io/v3/YOUR_INFURA_KEY"  # replace with your Infura/Alchemy key
BSC_RPC = "https://bsc-dataseed.binance.org/"

# ---------------- AGENT SETUP ----------------
class AgentType(str, Enum):
    MONITOR = "monitor"
    BLOCKCHAIN = "blockchain"

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
            if agent.type == AgentType.BLOCKCHAIN:
                result = await blockchain_task(task.payload)
            else:
                result = {"status": "unknown"}

            print("TASK RESULT:", result)
        except Exception as e:
            print("ERROR:", e)
        agent.status = AgentStatus.IDLE

# ---------------- BLOCKCHAIN FUNCTIONS ----------------
solana_client = SolanaClient(SOLANA_RPC)
eth_client = Web3(Web3.HTTPProvider(ETH_RPC))
bsc_client = Web3(Web3.HTTPProvider(BSC_RPC))

wallets = {"solana": {}, "ethereum": {}, "bsc": {}}

async def blockchain_task(payload: Dict[str, Any]):
    command = payload.get("command", "")
    chain = payload.get("chain", "").lower()

    if command == "new_wallet":
        if chain == "solana":
            keypair = Keypair()
            wallets["solana"][keypair.pubkey().to_base58()] = keypair.to_bytes()
            return {"address": keypair.pubkey().to_base58(), "private_key_hex": keypair.to_bytes().hex()}

        elif chain in ["ethereum", "bsc"]:
            w3 = eth_client if chain == "ethereum" else bsc_client
            acct = w3.eth.account.create()
            wallets[chain][acct.address] = acct.key.hex()
            return {"address": acct.address, "private_key_hex": acct.key.hex()}

    elif command == "check_balance":
        if chain == "solana":
            address = payload.get("address")
            res = solana_client.get_balance(address)
            return {"address": address, "balance": res["result"]["value"]}

        elif chain in ["ethereum", "bsc"]:
            w3 = eth_client if chain == "ethereum" else bsc_client
            address = payload.get("address")
            balance = w3.eth.get_balance(address)
            return {"address": address, "balance_wei": balance, "balance_eth": w3.from_wei(balance, "ether")}

    return {"status": "unknown command or chain"}

# ---------------- FASTAPI ----------------
orchestrator = Orchestrator()
app = FastAPI(title="Aureon Blockchain System")

@app.on_event("startup")
async def startup():
    for _ in range(2):
        asyncio.create_task(orchestrator.worker())

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
