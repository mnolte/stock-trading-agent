from sqlalchemy import Engine, Numeric, create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_config


class Base(DeclarativeBase):
    pass


Money = Numeric(19, 8)


def get_db_url() -> str:
    cfg = get_config()
    path = cfg.env.sa_db_path
    return f"sqlite:///{path}"


def get_engine() -> Engine:
    return create_engine(get_db_url(), connect_args={"check_same_thread": False})


def get_session_maker() -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def check_schema_matches_models() -> None:
    """Refuse to start when the database schema doesn't match the models.

    Migrations are immutable once merged (AGENTS.md rule 11): a drifted
    database is moved aside and rebuilt, never patched by hand.
    """
    inspector = inspect(get_engine())
    present = set(inspector.get_table_names())
    problems: list[str] = []
    for name, table in Base.metadata.tables.items():
        if name not in present:
            problems.append(f"missing table {name}")
            continue
        columns = {c["name"] for c in inspector.get_columns(name)}
        expected = {c.name for c in table.columns}
        problems += sorted(f"{name}.{c} missing" for c in expected - columns)
        problems += sorted(f"{name}.{c} unexpected" for c in columns - expected)
    if problems:
        raise RuntimeError(
            "Database schema does not match the models: "
            + "; ".join(problems)
            + ". The database may need rebuilding — move "
            + get_config().env.sa_db_path
            + " aside and restart so migrations recreate it."
        )
