import pytest
from backend.core.config import settings

@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    monkeypatch.setattr(settings,'EMBEDDING_PROVIDER','hash')
