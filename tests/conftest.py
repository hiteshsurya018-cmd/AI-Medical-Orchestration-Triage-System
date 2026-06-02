from __future__ import annotations

import csv
from pathlib import Path

import pytest

from docq_app import create_app
from docq_app.ml import set_models


class DummyModel:
    def __init__(self, label: str, confidence: float) -> None:
        self.label = label
        self.confidence = confidence

    def predict(self, values):
        return [self.label for _ in values]

    def predict_proba(self, values):
        return [[1 - self.confidence, self.confidence] for _ in values]


@pytest.fixture
def app(tmp_path: Path):
    dataset_path = tmp_path / "final.csv"
    with dataset_path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=["Cleaned_text", "Health", "Category"])
        writer.writeheader()
        writer.writerow({"Cleaned_text": "chest pain", "Health": "Cardiology", "Category": "medical"})
        writer.writerow({"Cleaned_text": "rash", "Health": "Dermatology", "Category": "medical"})

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DB_PATH": tmp_path / "docq.db",
            "DATASET_PATH": dataset_path,
            "MODEL_DIR": tmp_path / "models",
            "LOAD_MODELS_ON_STARTUP": False,
            "SEED_DEMO_USERS": True,
            "SEED_SLOTS": True,
        }
    )
    with app.app_context():
        set_models(DummyModel("medical", 0.92), DummyModel("Cardiology", 0.87))
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def extract_csrf(client, path: str = "/") -> str:
    client.get(path)
    with client.session_transaction() as session:
        return session["_csrf_token"]
