"""Noi tap hop tat ca resolver dang hoat dong.

Dang ky tuong minh (khong dung auto-discovery/magic) de luc doc code biet ngay field
nao da co quy tac, field nao chua - phuc vu dung workflow "cung cap tieu chi tung
field" cua ban.
"""

from __future__ import annotations

from app.land_normalizer.resolvers.base import Resolver


class ResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[str, Resolver] = {}

    def register(self, resolver: Resolver) -> None:
        if resolver.field_path in self._resolvers:
            raise ValueError(f"Resolver cho '{resolver.field_path}' da duoc dang ky")
        self._resolvers[resolver.field_path] = resolver

    def get(self, field_path: str) -> Resolver | None:
        return self._resolvers.get(field_path)

    def field_paths(self) -> list[str]:
        return list(self._resolvers.keys())


def build_default_registry() -> ResolverRegistry:
    from app.land_normalizer.resolvers import chu_su_dung, don_dang_ky, giay_chung_nhan, ho_so_quet, nha, thua_dat

    registry = ResolverRegistry()
    for resolver in [
        *don_dang_ky.RESOLVERS,
        *chu_su_dung.RESOLVERS,
        *thua_dat.RESOLVERS,
        *nha.RESOLVERS,
        *giay_chung_nhan.RESOLVERS,
        *ho_so_quet.RESOLVERS,
    ]:
        registry.register(resolver)
    return registry
