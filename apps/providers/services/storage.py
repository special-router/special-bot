from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SnapshotArtifact:
    content_digest: str
    size: int


class ImmutableSnapshotStore(Protocol):
    def inspect(self, storage_key: str) -> SnapshotArtifact | None:
        raise NotImplementedError
