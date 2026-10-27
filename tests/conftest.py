import os
import subprocess
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from tests.helpers import TEST_DATABASE_URL, VEHICLES, new_client


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    _recreate_database(TEST_DATABASE_URL)
    _apply_migrations(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL, pool_size=20, pool_pre_ping=True)
    SessionLocal.configure(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE reservations, vehicles RESTART IDENTITY"))


@pytest.fixture
def client(engine: Engine) -> TestClient:
    return new_client()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def vehicle_id(client: TestClient) -> int:
    response = client.post(
        VEHICLES, json={"plate_number": "12-345-67", "model": "Corolla"}
    )
    return response.json()["id"]


def _recreate_database(database_url: str) -> None:
    url = make_url(database_url)
    name = url.database
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


def _apply_migrations(database_url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
    )
