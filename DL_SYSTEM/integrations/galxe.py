from ..agents.web_agent_v2 import WebAgent
from ..core.config_loader import Config
import time


def run_galxe_task(task):
    agent = WebAgent()

    try:
        agent.goto("https://galxe.com")
        time.sleep(3)

        agent.login_generic(Config.GALXE_EMAIL, Config.GALXE_PASSWORD)
        time.sleep(5)

        agent.goto("https://galxe.com/quests")
        time.sleep(5)

        return {"status": "executed", "platform": "galxe"}

    except Exception as e:
        return {"status": "error", "error": str(e)}

    finally:
        agent.close()
