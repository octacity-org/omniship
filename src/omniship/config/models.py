from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omniship.core.logging import Logging


class NodeConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    uses: str
    needs: list[str] = Field(default_factory=list)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    if_: dict[str, Any] | str | None = Field(default=None, alias="if")
    when: dict[str, Any] | None = Field(default=None)
    execution: Any = Field(default=None, exclude=True)
    requirements: tuple[Any, ...] = Field(default=(), exclude=True)
    # Check only: Build jobs import this task's artifacts. Like execution, it
    # shapes the generated CI files and is not written to omniship.yaml.
    forward_to_build: bool = Field(default=False, exclude=True)

    @property
    def condition(self) -> dict[str, Any] | str | None:
        return self.when or self.if_


class OmniShipConfig(BaseModel):
    version: int = 1
    logging: Logging = Field(default_factory=Logging)
    check: dict[str, NodeConfig] = Field(default_factory=dict)
    build: dict[str, NodeConfig] = Field(default_factory=dict)
    ship: dict[str, NodeConfig] = Field(default_factory=dict)
