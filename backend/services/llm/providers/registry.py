from urllib.parse import urlparse

from .base import BaseProvider
from .base import ServiceType

# (service_type, provider_name) → concrete class.
# Provider families register themselves by importing this module and calling
# :func:`register`.
_REGISTRY: dict[tuple[ServiceType, str], type[BaseProvider]] = {}


def register(service_type: ServiceType, provider_name: str, cls: type[BaseProvider]) -> None:
    _REGISTRY[(service_type, provider_name)] = cls


def resolve(service_type: ServiceType, provider_name: str) -> type[BaseProvider]:
    try:
        return _REGISTRY[(service_type, provider_name)]
    except KeyError as e:
        raise LookupError(
            f"No provider registered for service={service_type.value!r}, provider={provider_name!r}"
        ) from e


def infer_provider_name(base_url: str) -> str:
    """Infer provider family from the base URL host.

    Defaults to ``"mimo"`` (legacy Xiaomi MiMo) when the host is unrecognised;
    providers can be selected explicitly via ``SETTINGS.<svc>_provider`` to
    override host inference.
    """
    host = (urlparse(base_url).hostname or "").lower()
    if host.endswith("minimaxi.com") or host.endswith("minimax.io"):
        return "minimax"
    return "mimo"