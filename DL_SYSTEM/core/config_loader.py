import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    RPC_URL = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
    PRIVATE_KEY = os.getenv("PRIVATE_KEY")

    GALXE_EMAIL = os.getenv("GALXE_EMAIL")
    GALXE_PASSWORD = os.getenv("GALXE_PASSWORD")

    LAYER3_EMAIL = os.getenv("LAYER3_EMAIL")
    LAYER3_PASSWORD = os.getenv("LAYER3_PASSWORD")
