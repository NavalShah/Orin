"""Write the API models as an OpenAPI components document, for ``lib/api-types.ts``.

    python -m server.export_schemas > openapi.schemas.json
    npx openapi-typescript openapi.schemas.json -o lib/api-types.ts

Serialization mode is used throughout, so the generated types describe what the server
sends: fields with defaults are always present in a response.
"""
from __future__ import annotations

import json
import sys

from pydantic.json_schema import models_json_schema

from .schemas import ALL_MODELS


def build() -> dict:
    _, top = models_json_schema(
        [(m, "serialization") for m in ALL_MODELS],
        ref_template="#/components/schemas/{model}",
    )
    return {
        "openapi": "3.1.0",
        "info": {"title": "Orin API models", "version": "1"},
        "paths": {},
        "components": {"schemas": top.get("$defs", {})},
    }


if __name__ == "__main__":
    json.dump(build(), sys.stdout, indent=1)
