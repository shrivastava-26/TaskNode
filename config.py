import os
from dotenv import load_dotenv

load_dotenv()


def app_db_url():
    return os.environ["APP_DB_URL"]
