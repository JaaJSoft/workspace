import threading
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    slug: str
    description: str
    icon: str
    color: str
    url: str | None
    active: bool = True
    order: int = 0


class ModuleRegistry:
    def __init__(self):
        self._modules: dict[str, ModuleInfo] = {}
        self._lock = threading.Lock()

    def register(self, module: ModuleInfo):
        with self._lock:
            if module.slug in self._modules:
                raise ValueError(f"Module with slug '{module.slug}' is already registered")
            self._modules[module.slug] = module

    def get_all(self) -> list[ModuleInfo]:
        return sorted(self._modules.values(), key=lambda m: m.order)

    def get_active(self) -> list[ModuleInfo]:
        return [m for m in self.get_all() if m.active]

    def get_for_template(self) -> list[dict]:
        return [asdict(m) for m in self.get_all()]


registry = ModuleRegistry()
