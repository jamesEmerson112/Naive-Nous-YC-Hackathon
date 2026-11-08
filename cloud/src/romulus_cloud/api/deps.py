"""FastAPI dependencies shared by every router. Tests override `get_session`."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobStore, build_blobstore
from romulus_cloud.settings import Settings


def get_session(request: Request) -> Iterator[Session]:
    """One session per request. The route commits; nothing commits implicitly."""
    with request.app.state.sessionmaker() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_blobstore(request: Request) -> BlobStore:
    """The blob store for package and recording bytes, rooted at `settings.blob_dir`."""
    return build_blobstore(request.app.state.settings.blob_dir)


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
BlobStoreDep = Annotated[BlobStore, Depends(get_blobstore)]
