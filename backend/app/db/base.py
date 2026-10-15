from sqlalchemy import Engine, Numeric, create_engine
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
