from ..agents.web_agent_v2 import WebAgent
from ..core.config_loader import Config
import time


def run_layer3_task(task):
    agent = WebAgent()

    try:
        agent.goto("https://layer3.xyz")
        time.sleep(3)

        agent.login_generic(Config.LAYER3_EMAIL, Config.LAYER3_PASSWORD)
        time.sleep(5)

        return {"status": "executed", "platform": "layer3"}

    except Exception as e:
        return {"status": "error", "error": str(e)}

    finally:
        agent.close()
