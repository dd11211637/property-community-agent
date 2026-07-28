from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str, *, echo: bool = False) -> sessionmaker[Session]:
    engine = create_engine(database_url, echo=echo, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_factory_from_engine(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
