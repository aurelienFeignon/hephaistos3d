"""Export the contract offline, without reading local secrets or opening a database."""

import json

from sqlalchemy import create_engine

from hephaistos.api.app import create_app
from hephaistos.config import Settings

settings = Settings(
    _env_file=None, api_access_token="openapi-export-placeholder-not-a-real-secret", enable_fake_adapter=True
)
engine = create_engine("sqlite://")
try:
    schema = create_app(engine=engine, settings=settings).openapi()
    print(json.dumps(schema, sort_keys=True))
finally:
    engine.dispose()
