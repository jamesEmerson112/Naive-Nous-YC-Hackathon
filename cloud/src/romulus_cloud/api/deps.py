"""FastAPI dependencies shared by every router. Tests override `get_session`."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from romulus_cloud.settings import Settings


def get_session(request: Request) -> Iterator[Session]:
    """One session per request. The route commits; nothing commits implicitly."""
    with request.app.state.sessionmaker() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
