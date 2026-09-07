from __future__ import annotations


class MetalCalcError(Exception):
    """Expected failure safe to expose to the tool caller."""

    code = "MetalCalcError"

    def __init__(self, message: str = "Operation failed") -> None:
        super().__init__(message)
        self.public_message = message


class InvalidIdentifier(MetalCalcError):
    code = "InvalidIdentifier"


class PathEscape(MetalCalcError):
    code = "PathEscape"


class UnsupportedFormat(MetalCalcError):
    code = "UnsupportedFormat"


class FileTooLarge(MetalCalcError):
    code = "FileTooLarge"


class NotFound(MetalCalcError):
    code = "NotFound"


class Conflict(MetalCalcError):
    code = "Conflict"


class InvalidState(MetalCalcError):
    code = "InvalidState"


class InvalidRatePack(MetalCalcError):
    code = "InvalidRatePack"


class PackChanged(MetalCalcError):
    """Стадии заказа посчитаны по разным ревизиям данных предприятия.

    Отдельный класс, а не Conflict: это не «кто-то опередил тебя записью», а
    «цифры под заказом сменились» — и лечится оно не повтором операции, а
    пересчётом стадии. Стабильный код нужен, чтобы панель и раннер отличали
    этот случай по машине, а не по тексту сообщения.
    """

    code = "PackChanged"


class GeometryFailed(MetalCalcError):
    code = "GeometryFailed"


class OrderScopeDenied(MetalCalcError):
    """An autonomous session is not authorized for the requested order."""

    code = "OrderScopeDenied"
