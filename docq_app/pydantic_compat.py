from __future__ import annotations

from pydantic import BaseModel


def model_dump(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
