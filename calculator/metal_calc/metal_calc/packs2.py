"""Пак конвейера V2/V3: заготовка, станочные ставки, нормы, парк, реестр ставок.

Формат отдельный от rate pack v1, та же дисциплина: fail-closed валидация,
Decimal, у каждой ставки и каждого коэффициента обязательный источник.
Формулы времени живут в коде (timenorms.py); пак поставляет ТОЛЬКО данные:
режимы, вспомогательное время, коэффициенты.

schema_version 3 (схема разделения V2 методолога, 28.08.2026) добавляет две
секции поверх состава v2:

* ``process_park`` — подмножество глобального каталога операций
  (:mod:`metal_calc.process_catalog`), заведённое предприятием. Парк — данные
  предприятия, каталог — данные образа: у одного предприятия парк усечённый,
  у другого полный, а коды у всех одни и те же;
* ``rate_registry`` — явные ставки с адресом ``rate_id``. Агент ставку только
  ВЫБИРАЕТ; значение применяет движок. Ставка бывает скалярной и матричной
  (сетка «толщина × ширина» из книги методолога — реальный тариф вальцовки).

Канонические ставки v2-состава (``machine:*``, ``material:*``, ``blank:*``,
``extra:*``) выводятся из привычных секций автоматически: реестр не требует
перезаводить то, что уже заведено, а старые экраны продолжают работать.

Правило проекта: источник вида {"kind": "handbook", ...} обязан называть
справочник, издание и страницу. Выдача LLM источником не является.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from . import process_catalog
from .errors import InvalidRatePack, NotFound
from .rates import dec, _source as _money_source
from .securefs import SecureRoot
from .util import sha256_bytes, validate_revision

# Операции заготовки из записи 40 (закрытый список методолога).
BLANK_OPS = {"bandsaw", "pipe_cut", "laser", "plasma", "waterjet", "bench"}
BLANK_RATE_KINDS = {"per_cut_m", "per_cut", "per_hour"}
# Станочные операции агентов 2-3 (запись 40: токарно-фрезерные, эрозия, шлифовка).
MACHINE_OPS = {"turning", "milling", "drilling", "edm", "grinding"}
MATERIAL_STOCK = {"stocked", "purchase"}

#: Набор параметров режима зависит от операции: точению и сверлению нужна
#: скорость резания с подачей на оборот, фрезеровке — минутная подача стола, а
#: эрозии и шлифовке режимов в этом смысле нет вовсе, только съём.
#:
#: Вынесено в константу, потому что этот же набор рисует форму ввода в панели.
#: Раньше он был выписан внутри валидатора, и любая форма ввода неизбежно
#: заводила бы вторую копию правил — с гарантией разойтись.
NORM_FIELDS_BY_OP: dict[str, frozenset[str]] = {
    "turning": frozenset({"cutting_speed_m_min", "feed_mm_rev", "depth_mm", "source"}),
    # У сверления глубины за проход нет: сверло идёт насквозь с непрерывной
    # подачей, а глубина резания равна радиусу инструмента и не задаётся.
    # `depth_mm` здесь стоял и был мёртвым — валидатор его требовал,
    # справочник показывал, методолог заполняла, а на время он не влиял.
    # Поле, которое выглядит работающим, не будучи им, хуже отсутствующего.
    "drilling": frozenset({"cutting_speed_m_min", "feed_mm_rev", "source"}),
    "milling": frozenset({"feed_table_mm_min", "depth_mm", "source"}),
    "edm": frozenset({"removal_min_per_cm2", "source"}),
    "grinding": frozenset({"removal_min_per_cm2", "source"}),
}
assert set(NORM_FIELDS_BY_OP) == MACHINE_OPS, "norm fields must cover every machine op"


#: Явный rate_id: строчные буквоцифры с одним необязательным двоеточием
#: («svc:roll», «coop.galvanic»). Двоеточие делит пространство имён и живёт
#: в иде ровно один раз — «machine:turning:old» не адрес, а мусор.
RATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}(:[a-z0-9][a-z0-9._-]{0,31})?$")

#: Канонические префиксы занимает движок: эти ставки выводятся из секций v2
#: автоматически, и явная запись под тем же именем означала бы две правды об
#: одной цене.
RESERVED_RATE_PREFIXES = ("machine:", "material:", "blank:", "extra:")

#: Закрытый список единиц тарифа. Открытый превращал бы единицу в свободный
#: текст, а расчёт по «per_hour» и «час» — в два разных тарифа одной ставки.
TARIFF_UNITS = frozenset(
    {
        "per_hour",
        "per_piece",
        "per_cut_m",
        "per_cut",
        "per_pierce",
        "per_bend",
        "per_kg",
        "per_m2",
        "per_km",
    }
)

#: Осей у матричной ставки одна или две — как в книге методолога (толщина ×
#: ширина). Больше двух не рисуется ни на экране, ни в голове проверяющего.
MATRIX_MAX_AXES = 2
MATRIX_MAX_EDGES = 32

#: Имя указателя на действующую ревизию. Ведущее подчёркивание — не стиль:
#: ``util.REVISION_RE`` требует первым символом буквоцифру, поэтому служебные
#: файлы физически невозможно запросить как ревизию. Коллизия имён исключена
#: конструкцией, а не проверкой на равенство строк.
ACTIVE_POINTER = "_active.json"


@contextmanager
def _at(section: str, key: str | None = None) -> Iterator[None]:
    """Пометить ошибку валидации адресом строки, в которой она случилась.

    Правила не меняются — меняется только текст. Без адреса сообщение
    «Invalid material fields» на экране с четырьмя десятками материалов не
    говорит человеку, какую строку чинить, и он идёт спрашивать нас.
    """
    try:
        yield
    except InvalidRatePack as exc:
        where = f"{section}.{key}" if key is not None else section
        if exc.public_message.startswith(f"{where}: "):
            raise
        raise InvalidRatePack(f"{where}: {exc.public_message}") from exc


#: Плотность конструкционных металлов: от магния (1740) до осмия (22590).
#: Границы взяты с запасом. Это не вкусовое ограничение, а физика: значение
#: вне их — почти всегда опечатка в разряде, и она не заметна никак. Сталь
#: с плотностью 785 вместо 7850 делает металл в заказе дешевле в десять раз,
#: и увидят это не мы, а заказчик.
DENSITY_MIN_KG_M3 = Decimal(1500)
DENSITY_MAX_KG_M3 = Decimal(23000)


def _density(value: Any) -> Decimal:
    density = dec(value, field="density", positive=True)
    if not (DENSITY_MIN_KG_M3 <= density <= DENSITY_MAX_KG_M3):
        raise InvalidRatePack(
            f"Плотность {density} кг/м³ невозможна для металла "
            f"(ожидается от {DENSITY_MIN_KG_M3} до {DENSITY_MAX_KG_M3}). "
            f"Проверьте разряд: у стали 7850, у алюминия 2700."
        )
    return density


def _fields_mismatch(what: str, given: Any, expected: frozenset[str] | set[str]) -> str:
    """Назвать, каких полей не хватает и какие лишние — по-русски.

    «Invalid material fields» на экране методолога означает «что-то не так»:
    человек не знает, какое поле он забыл. Панель показывает это сообщение
    дословно, поэтому оно обязано само называть разницу.
    """
    got = set(given) if isinstance(given, dict) else set()
    parts: list[str] = []
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    if missing:
        parts.append("не хватает: " + ", ".join(missing))
    if extra:
        parts.append("лишние: " + ", ".join(extra))
    detail = "; ".join(parts) or "ожидается объект"
    return f"Неверный состав полей {what} — {detail}"


def _handbook_source(value: Any) -> dict[str, str]:
    """Источник коэффициента нормирования: справочник или канон клиента."""
    if not isinstance(value, dict) or set(value) != {"kind", "ref", "as_of"}:
        raise InvalidRatePack(
            _fields_mismatch("источника нормы", value, {"kind", "ref", "as_of"})
        )
    if value.get("kind") not in {"handbook", "client_canon"}:
        raise InvalidRatePack(
            "Источник нормы — handbook (справочник: издание и страница) или "
            "client_canon (утверждённый канон предприятия). Выдача модели "
            "источником не является."
        )
    return _money_source({**value, "kind": "client_canon"}) | {"kind": value["kind"]}


def _fingerprint(data: dict[str, Any]) -> str:
    """Отпечаток самих данных: без имени ревизии и без статуса.

    ``sha256`` считается от файла целиком и меняется от одного лишь нового
    имени ревизии. Для денег это неверная мера: методолог, сохранившая пак
    второй раз без правок, не изменила ни одной ставки — и живые заказы не
    должны от этого устаревать. Сравнивать стадии между собой надо по тому,
    что влияет на цифры.
    """
    payload = {key: value for key, value in data.items() if key not in {"revision", "status"}}
    return sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


@dataclass(frozen=True)
class PipelinePack:
    revision: str
    sha256: str
    fingerprint: str  # хеш данных без имени ревизии — см. _fingerprint
    status: str  # template | active
    blank_ops: dict[str, dict[str, Any]]
    materials: dict[str, dict[str, Any]]
    machines: dict[str, dict[str, Any]]
    norm_params: dict[str, dict[str, Any]]  # (op_code) -> режимы/коэффициенты
    overheads: dict[str, Any]  # t_aux_min, k_service_rest_pct, t_setup_min
    extras: dict[str, dict[str, Any]]  # packaging / logistics
    pricing: dict[str, Any]  # та же политика, что v1
    schema_version: int = 2
    #: v3: парк предприятия — подмножество каталога process_catalog.
    process_park: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: v3: объединённый реестр ставок — канонические (из секций v2) + явные.
    rate_registry: dict[str, dict[str, Any]] = field(default_factory=dict)

    def blank_op(self, code: str) -> dict[str, Any]:
        try:
            return self.blank_ops[code]
        except KeyError as exc:
            # Код и список доступного прямо в отказе. «Unknown blank
            # operation» без них означает для человека «что-то не так»: он не
            # знает ни что именно назвал агент, ни что предприятие завело.
            raise InvalidRatePack(
                f"Операция заготовки «{code}» не заведена в данных предприятия; "
                f"доступны: {', '.join(sorted(self.blank_ops)) or '—'}"
            ) from exc

    def material(self, code: str) -> dict[str, Any]:
        try:
            return self.materials[code]
        except KeyError as exc:
            raise InvalidRatePack(
                f"Материал «{code}» не заведён в данных предприятия; "
                f"доступны: {', '.join(sorted(self.materials)) or '—'}"
            ) from exc

    def machine(self, code: str) -> dict[str, Any]:
        try:
            return self.machines[code]
        except KeyError as exc:
            raise InvalidRatePack(
                f"Станок «{code}» не заведён в данных предприятия; "
                f"доступны: {', '.join(sorted(self.machines)) or '—'}"
            ) from exc

    def park_entry(self, code: str) -> dict[str, Any]:
        try:
            return self.process_park[code]
        except KeyError as exc:
            # «Нет в парке» и «нет в каталоге» — разные ответы. Здесь первый:
            # операция существует, но предприятие её не оказывает — вариант
            # с таким шагом уходит в подряд, а не в угадывание.
            raise InvalidRatePack(
                f"Операция «{code}» не заведена в парке предприятия; "
                f"в парке: {', '.join(sorted(self.process_park)) or '—'}. "
                f"Технически возможная, но чужая операция — это подряд "
                f"(execution_mode=outsource), а не своя строка."
            ) from exc

    def rate(self, rate_id: str) -> dict[str, Any]:
        try:
            return self.rate_registry[rate_id]
        except KeyError as exc:
            raise InvalidRatePack(
                f"Ставка «{rate_id}» не заведена в реестре; "
                f"доступны: {', '.join(sorted(self.rate_registry)) or '—'}"
            ) from exc

    def rate_value(self, rate_id: str, axis_values: dict[str, Any] | None = None) -> Decimal:
        """Значение ставки. Для матричной обязательны значения осей.

        Пустая ячейка сетки — честное «этого предприятие не делает» (в книге
        методолога так и написано: «нет»). Ноль вместо отказа means бесплатная
        операция в КП, поэтому пустота — всегда отказ с адресом ячейки.
        """
        entry = self.rate(rate_id)
        if entry["kind"] == "scalar":
            return entry["value"]
        given = dict(axis_values or {})
        indexes: list[int] = []
        for axis in entry["axes"]:
            name = axis["name"]
            raw = given.pop(name, None)
            if raw is None:
                raise InvalidRatePack(
                    f"Ставка «{rate_id}» матричная: нужны значения осей "
                    f"{', '.join(a['name'] for a in entry['axes'])}"
                )
            value = dec(raw, field=name, positive=True)
            indexes.append(_axis_interval(rate_id, axis, value))
        if given:
            raise InvalidRatePack(
                f"Лишние оси для ставки «{rate_id}»: {', '.join(sorted(given))}"
            )
        row = indexes[0]
        col = indexes[1] if len(indexes) > 1 else 0
        cell = entry["grid"][row][col]
        if cell is None:
            raise InvalidRatePack(
                f"Тариф «{rate_id}» не оказывается для этих параметров "
                f"(в сетке ставок здесь стоит «нет») — это подряд или отказ, "
                f"но не ноль."
            )
        return cell

    def norms_for(self, op_code: str, material_group: str) -> dict[str, Any]:
        key = f"{op_code}:{material_group}"
        try:
            return self.norm_params[key]
        except KeyError as exc:
            # Самый частый пробел в данных, и он всплывает на третьей стадии,
            # когда две уже утверждены. Отказ обязан сразу называть, что
            # именно заводить, — иначе человек ищет причину в расчёте.
            raise InvalidRatePack(
                f"Для операции «{op_code}» нет нормы времени по группе "
                f"материалов «{material_group}». Заведите её на экране "
                f"«Данные» в разделе «Режимы и нормы»."
            ) from exc


def implausible_values(pack: "PipelinePack") -> list[str]:
    """Числа, которые формально верны, но почти наверняка опечатка.

    Отказом это быть не может: у каждого предприятия свои цифры, и запретить
    дорогой сплав или дешёвую операцию значит запретить работать. Но и
    молчать нельзя — опечатка в разряде не видна вообще ничем, а уезжает
    прямо в цену заказчику.

    Поэтому здесь ровно то же разделение, что и с покрытием норм: физически
    невозможное отвергает валидатор, подозрительное показывается человеку и
    остаётся его решением.
    """
    notes: list[str] = []
    for code, material in sorted(pack.materials.items()):
        rate = material["rate_rub_per_kg"]
        if rate < Decimal(10):
            notes.append(f"материал «{code}»: {rate} ₽/кг — дешевле лома")
        elif rate > Decimal(100_000):
            notes.append(f"материал «{code}»: {rate} ₽/кг — дороже серебра")
    for code, machine in sorted(pack.machines.items()):
        rate = machine["rate_rub_per_hour"]
        if rate < Decimal(100):
            notes.append(f"станок «{code}»: {rate} ₽/час — ниже любой зарплаты")
        elif rate > Decimal(100_000):
            notes.append(f"станок «{code}»: {rate} ₽/час")
    margin = pack.pricing["margin_percent"]
    material_markup = pack.pricing.get("material_markup_percent", Decimal("0"))
    if margin == 0 and material_markup == 0:
        notes.append("наценка 0% — работа по себестоимости")
    elif margin > Decimal(500):
        notes.append(f"наценка {margin}%")
    if material_markup > Decimal(500):
        notes.append(f"наценка на материал {material_markup}%")
    return notes


def norm_coverage(pack: "PipelinePack") -> dict[str, list[str]]:
    """Какие пары «станок × группа материалов» остались без нормы времени.

    Это НЕ ошибка данных, поэтому публикацию не блокирует: предприятие может
    не обрабатывать нержавейку на эрозии, и требовать от него выдуманную
    норму — худшее из возможного. Но и молчать нельзя: без нормы третья
    стадия падает уже ПОСЛЕ того, как человек утвердил заготовку и маршрут,
    то есть цена ошибки — переделка двух согласованных стадий.

    Отдаётся человеку на экране «Данные» и агенту в справочнике, чтобы
    «это мы не считаем» было решением, а не сюрпризом в середине заказа.
    """
    groups = sorted({str(material["group"]) for material in pack.materials.values()})
    missing: dict[str, list[str]] = {}
    for op_code in sorted(pack.machines):
        gaps = [group for group in groups if f"{op_code}:{group}" not in pack.norm_params]
        if gaps:
            missing[op_code] = gaps
    return missing


class PipelinePackStore:
    """Чтение паков. Записи здесь нет и не должно появиться.

    Публикация живёт в :mod:`metal_calc.packadmin`, доступном только CLI. Так
    у MCP-сервера нет даже кода, которым можно было бы изменить деньги, — не
    только прав.
    """

    def __init__(self, root: SecureRoot) -> None:
        self.root = root

    def active_revision(self) -> str:
        """Имя действующей ревизии из указателя.

        Отсутствие указателя — это «данные предприятия ещё не заведены», а не
        «сломалось»: сообщение должно вести человека в экран ввода, а не в
        поддержку.
        """
        try:
            raw = self.root.read_bytes(ACTIVE_POINTER, limit=64 * 1024)
        except (NotFound, ValueError) as exc:
            raise InvalidRatePack(
                "Данные предприятия ещё не заведены — заполните их на экране «Данные»"
            ) from exc
        try:
            pointer = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRatePack("Active pack pointer is not valid JSON") from exc
        if not isinstance(pointer, dict):
            raise InvalidRatePack("Active pack pointer must be an object")
        return validate_revision(pointer.get("revision"))

    def _active_pointer(self) -> dict[str, Any]:
        try:
            raw = self.root.read_bytes(ACTIVE_POINTER, limit=64 * 1024)
        except (NotFound, ValueError) as exc:
            raise InvalidRatePack(
                "Данные предприятия ещё не заведены — заполните их на экране «Данные»"
            ) from exc
        try:
            pointer = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRatePack("Active pack pointer is not valid JSON") from exc
        if not isinstance(pointer, dict):
            raise InvalidRatePack("Active pack pointer must be an object")
        return pointer

    def load_active(self) -> PipelinePack:
        """Действующий пак со сверкой digest из указателя.

        Указатель закрепляет и имя ревизии, и sha256 её файла. Файл, правленный
        мимо публикации (руками, скриптом, битым диском), прежде читался
        молча — и заказ считался по содержимому, которого никто не активировал.
        Указатели старого формата (без sha256) принимаются как есть.
        """
        pointer = self._active_pointer()
        pack = self.load(validate_revision(pointer.get("revision")))
        expected = pointer.get("sha256")
        if isinstance(expected, str) and expected and pack.sha256 != expected:
            raise InvalidRatePack(
                f"Файл ревизии {pack.revision} не совпадает с активированным "
                f"(sha256 разошёлся с указателем): содержимое правили мимо "
                f"публикации. Откатитесь на честную ревизию или опубликуйте "
                f"данные заново."
            )
        return pack

    def load(self, revision: str) -> PipelinePack:
        revision = validate_revision(revision)
        try:
            raw = self.root.read_bytes(f"{revision}.json", limit=1024 * 1024)
        except (NotFound, ValueError) as exc:
            raise InvalidRatePack("Pipeline pack unavailable") from exc
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRatePack("Pipeline pack is not valid JSON") from exc
        return _validate_pack2(data, sha256_bytes(raw), revision)


def _axis_interval(rate_id: str, axis: dict[str, Any], value: Decimal) -> int:
    """Номер интервала оси для значения: границы (edge_{i-1}; edge_i].

    Полуоткрытость справа — дословно из книги методолога: «более 0 / до 1,9
    (включая)». Значение на границе принадлежит нижнему интервалу, и это
    должно совпадать с тем, как читает таблицу человек, — иначе расчёт и
    бумага разойдутся ровно на граничной толщине.
    """
    if axis["name"].endswith("_code") and value != value.to_integral_value():
        raise InvalidRatePack(
            f"Значение {axis['name']}={value} должно быть целым кодом "
            f"категории для ставки «{rate_id}»"
        )
    edges: list[Decimal] = axis["edges"]
    if value <= edges[0]:
        raise InvalidRatePack(
            f"Значение {axis['name']}={value} ниже нижней границы сетки "
            f"ставки «{rate_id}» (интервалы начинаются после {edges[0]})"
        )
    for index in range(1, len(edges)):
        if value <= edges[index]:
            return index - 1
    if axis.get("open_end"):
        return len(edges) - 1
    raise InvalidRatePack(
        f"Значение {axis['name']}={value} выше верхней границы сетки "
        f"ставки «{rate_id}» ({edges[-1]}) — такой тариф не заведён"
    )


def _validate_rate_id(value: Any) -> str:
    if not isinstance(value, str) or not RATE_ID_RE.fullmatch(value):
        raise InvalidRatePack(
            "rate_id — строчные буквоцифры/точка/дефис, не длиннее 64 символов, "
            "с одним необязательным двоеточием (например «svc:roll»)"
        )
    return value


def _validate_rate_entry(rate_id: str, entry: Any) -> dict[str, Any]:
    """Явная ставка реестра: скаляр или матрица, всегда с источником.

    Поле ``vat_included`` в явной ставке обязано быть false: политика НДС в
    этом релизе одна на пак (``pricing``), и ставка «с уже включённым НДС»
    рядом с политикой «НДС сверху» — это двойное начисление, спрятанное в
    данных. Поле существует ради формата книги методолога (у неё ставки
    гросс) — но её пак придёт вместе с адаптером, а до тех пор fail-closed.
    """
    if not isinstance(entry, dict):
        raise InvalidRatePack("Ставка должна быть объектом")
    kind = entry.get("kind")
    if kind not in {"scalar", "matrix"}:
        raise InvalidRatePack("Поле kind — scalar или matrix")
    base = {"kind", "tariff_unit", "vat_included", "source"}
    expected = base | ({"value"} if kind == "scalar" else {"axes", "grid"})
    if set(entry) != expected:
        raise InvalidRatePack(_fields_mismatch("ставки", entry, expected))
    if entry.get("tariff_unit") not in TARIFF_UNITS:
        raise InvalidRatePack(
            f"Единица тарифа «{entry.get('tariff_unit')}» не из списка: "
            + ", ".join(sorted(TARIFF_UNITS))
        )
    if entry.get("vat_included") is not False:
        raise InvalidRatePack(
            "vat_included в явной ставке обязан быть false: политика НДС "
            "задаётся паком целиком (pricing), ставка-гросс рядом с ней — "
            "скрытое двойное начисление"
        )
    normalized: dict[str, Any] = {
        "kind": kind,
        "tariff_unit": entry["tariff_unit"],
        "vat_included": False,
        "source": _money_source(entry["source"]),
    }
    if kind == "scalar":
        normalized["value"] = dec(entry["value"], field="rate value", positive=True)
        return normalized

    axes_raw = entry.get("axes")
    if not isinstance(axes_raw, list) or not 1 <= len(axes_raw) <= MATRIX_MAX_AXES:
        raise InvalidRatePack(
            f"У матричной ставки 1..{MATRIX_MAX_AXES} оси; получено не то"
        )
    axes: list[dict[str, Any]] = []
    for axis_raw in axes_raw:
        if not isinstance(axis_raw, dict) or set(axis_raw) - {"name", "unit", "edges", "open_end"}:
            raise InvalidRatePack(
                _fields_mismatch("оси матрицы", axis_raw, {"name", "unit", "edges", "open_end"})
            )
        name = axis_raw.get("name")
        if not isinstance(name, str) or not name or len(name) > 32:
            raise InvalidRatePack("У оси матрицы должно быть короткое имя")
        edges_raw = axis_raw.get("edges")
        if not isinstance(edges_raw, list) or not 2 <= len(edges_raw) <= MATRIX_MAX_EDGES:
            raise InvalidRatePack(
                f"Ось «{name}»: edges — список из 2..{MATRIX_MAX_EDGES} границ"
            )
        edges = [dec(edge, field=f"edges[{name}]") for edge in edges_raw]
        if any(b <= a for a, b in zip(edges, edges[1:])):
            raise InvalidRatePack(f"Границы оси «{name}» должны строго возрастать")
        open_end = axis_raw.get("open_end", False)
        # Строгий bool: строка "false" правдива по-питоновски, и пак с ней
        # молча открывал бы верхнюю границу сетки (находка Codex M10).
        if not isinstance(open_end, bool):
            raise InvalidRatePack(
                f"Ось «{name}»: open_end — строго true или false, не строка"
            )
        axes.append(
            {
                "name": name,
                "unit": str(axis_raw.get("unit", "")),
                "edges": edges,
                "open_end": open_end,
            }
        )

    def _intervals(axis: dict[str, Any]) -> int:
        return len(axis["edges"]) - 1 + (1 if axis["open_end"] else 0)

    rows = _intervals(axes[0])
    cols = _intervals(axes[1]) if len(axes) > 1 else 1
    grid_raw = entry.get("grid")
    if not isinstance(grid_raw, list) or len(grid_raw) != rows:
        raise InvalidRatePack(
            f"Сетка ставки «{rate_id}»: ожидается {rows} строк по числу "
            f"интервалов оси «{axes[0]['name']}»"
        )
    grid: list[list[Decimal | None]] = []
    for row_index, row_raw in enumerate(grid_raw):
        if not isinstance(row_raw, list) or len(row_raw) != cols:
            raise InvalidRatePack(
                f"Сетка ставки «{rate_id}», строка {row_index + 1}: ожидается "
                f"{cols} ячеек"
            )
        row: list[Decimal | None] = []
        for col_index, cell in enumerate(row_raw):
            if cell is None:
                row.append(None)
                continue
            row.append(
                dec(cell, field=f"grid[{row_index + 1}][{col_index + 1}]", positive=True)
            )
        grid.append(row)
    if not any(cell is not None for row in grid for cell in row):
        raise InvalidRatePack(f"Сетка ставки «{rate_id}» пуста целиком — это не ставка")
    normalized["axes"] = axes
    normalized["grid"] = grid
    return normalized


def _canonical_rates(
    blank_ops: dict[str, dict[str, Any]],
    materials: dict[str, dict[str, Any]],
    machines: dict[str, dict[str, Any]],
    extras: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Канонические ставки из привычных секций: одна правда, два адреса.

    Значения НЕ копируются — берутся те же Decimal-объекты, что лежат в
    секциях. Реестр — это адресация, а не вторая база ставок.
    """
    blank_units = {"per_cut_m": "per_cut_m", "per_cut": "per_cut", "per_hour": "per_hour"}
    registry: dict[str, dict[str, Any]] = {}
    for code, op in blank_ops.items():
        registry[f"blank:{code}"] = {
            "kind": "scalar",
            "tariff_unit": blank_units[op["rate_kind"]],
            "vat_included": False,
            "value": op["rate_rub"],
            "source": op["rate_source"],
        }
    for code, material in materials.items():
        registry[f"material:{code}"] = {
            "kind": "scalar",
            "tariff_unit": "per_kg",
            "vat_included": False,
            "value": material["rate_rub_per_kg"],
            "source": material["rate_source"],
        }
    for code, machine in machines.items():
        registry[f"machine:{code}"] = {
            "kind": "scalar",
            "tariff_unit": "per_hour",
            "vat_included": False,
            "value": machine["rate_rub_per_hour"],
            "source": machine["rate_source"],
        }
    for code, extra in extras.items():
        registry[f"extra:{code}"] = {
            "kind": "scalar",
            "tariff_unit": "per_piece" if code == "packaging" else "per_km",
            "vat_included": False,
            "value": extra["rate_rub"],
            "source": extra["rate_source"],
        }
    return registry


def _validate_process_park(
    park_raw: Any,
    rate_registry: dict[str, dict[str, Any]],
    *,
    schema_version: int,
) -> dict[str, dict[str, Any]]:
    if not isinstance(park_raw, dict) or not park_raw:
        raise InvalidRatePack(
            "process_park пуст: парк предприятия — подмножество каталога "
            "операций, без него агенту не из чего строить маршрут"
        )
    park: dict[str, dict[str, Any]] = {}
    for code, entry_raw in park_raw.items():
        with _at("process_park", code if isinstance(code, str) else "?"):
            if not isinstance(code, str):
                raise InvalidRatePack("Ключ парка должен быть кодом операции")
            spec = process_catalog.get(code)
            if not spec.is_process:
                raise InvalidRatePack(
                    f"«{code}» — совместимый слот вывода, а не операция: в парк "
                    f"он не заводится; подряд оформляется профильным кодом с "
                    f"execution_mode=outsource"
                )
            if not isinstance(entry_raw, dict):
                raise InvalidRatePack("Запись парка должна быть объектом")
            allowed_fields = {"rate_id", "note"} | (
                {"method_code"} if spec.method_required else set()
            )
            if schema_version >= 4:
                allowed_fields |= {
                    "supplementary_rate_ids",
                    "requires_manual_review",
                    "in_house_axis_max",
                }
            if set(entry_raw) - allowed_fields or "rate_id" not in entry_raw:
                raise InvalidRatePack(
                    _fields_mismatch("записи парка", entry_raw, allowed_fields)
                )
            entry: dict[str, Any] = {}
            if spec.method_required:
                method = entry_raw.get("method_code")
                if method not in spec.method_codes:
                    # Ровно один метод — правило методолога дословно:
                    # альтернативы распила не суммируются и не выбираются
                    # на месте, предприятие фиксирует свой способ заранее.
                    raise InvalidRatePack(
                        f"Для «{code}» обязателен ровно один method_code из: "
                        + ", ".join(spec.method_codes)
                    )
                entry["method_code"] = method
            rate_id = entry_raw.get("rate_id")
            if rate_id is not None:
                rate_id = _validate_rate_id(rate_id)
                if rate_id not in rate_registry:
                    raise InvalidRatePack(
                        f"Ставка «{rate_id}» не найдена в реестре; доступны: "
                        + (", ".join(sorted(rate_registry)) or "—")
                    )
            entry["rate_id"] = rate_id
            supplementary_raw = entry_raw.get("supplementary_rate_ids", [])
            if not isinstance(supplementary_raw, list) or len(supplementary_raw) > 8:
                raise InvalidRatePack(
                    "supplementary_rate_ids — список до 8 дополнительных ставок"
                )
            supplementary: list[str] = []
            if supplementary_raw and spec.cost_owner != "supply":
                raise InvalidRatePack(
                    "Дополнительные ставки в schema v4 поддержаны только "
                    "для операций зоны supply"
                )
            if supplementary_raw and rate_id is None:
                raise InvalidRatePack(
                    "Дополнительные ставки требуют основную rate_id процесса"
                )
            for supplementary_rate_id_raw in supplementary_raw:
                supplementary_rate_id = _validate_rate_id(supplementary_rate_id_raw)
                if supplementary_rate_id not in rate_registry:
                    raise InvalidRatePack(
                        f"Ставка «{supplementary_rate_id}» не найдена в реестре; доступны: "
                        + (", ".join(sorted(rate_registry)) or "—")
                    )
                if supplementary_rate_id == rate_id or supplementary_rate_id in supplementary:
                    raise InvalidRatePack(
                        "Основная и дополнительные ставки процесса не должны повторяться"
                    )
                supplementary.append(supplementary_rate_id)
            if supplementary:
                entry["supplementary_rate_ids"] = supplementary
            if "requires_manual_review" in entry_raw:
                if type(entry_raw["requires_manual_review"]) is not bool:
                    raise InvalidRatePack("requires_manual_review должен быть boolean")
                entry["requires_manual_review"] = entry_raw["requires_manual_review"]
            axis_max_raw = entry_raw.get("in_house_axis_max")
            if axis_max_raw is not None:
                if rate_id is None:
                    raise InvalidRatePack("in_house_axis_max требует основную rate_id")
                rate = rate_registry[rate_id]
                rate_axes = {
                    axis["name"] for axis in rate.get("axes", [])
                }
                if (
                    rate.get("kind") != "matrix"
                    or not isinstance(axis_max_raw, dict)
                    or not axis_max_raw
                    or len(axis_max_raw) > 8
                    or set(axis_max_raw) - rate_axes
                ):
                    raise InvalidRatePack(
                        "in_house_axis_max — непустой объект максимумов осей "
                        "основной матричной ставки"
                    )
                axis_max: dict[str, str] = {}
                for axis_name, maximum_raw in axis_max_raw.items():
                    maximum = dec(maximum_raw, field=f"axis maximum {axis_name}")
                    if maximum <= 0:
                        raise InvalidRatePack("Максимум оси собственного парка должен быть > 0")
                    axis_max[axis_name] = str(maximum)
                entry["in_house_axis_max"] = axis_max
            note = entry_raw.get("note")
            if note is not None:
                if not isinstance(note, str) or len(note) > 200:
                    raise InvalidRatePack("Примечание парка — строка до 200 символов")
                entry["note"] = note
            park[code] = entry
    return park


def _validate_pack2(data: Any, digest: str, requested_revision: str) -> PipelinePack:
    if not isinstance(data, dict):
        raise InvalidRatePack("Pipeline pack must be an object")
    allowed = {
        "schema_version",
        "revision",
        "status",
        "blank_ops",
        "materials",
        "machines",
        "norm_params",
        "overheads",
        "extras",
        "pricing",
    }
    schema_version = data.get("schema_version")
    if schema_version not in {2, 3, 4}:
        raise InvalidRatePack("Pipeline pack schema mismatch")
    if schema_version >= 3:
        allowed |= {"process_park", "rate_registry"}
    if set(data) != allowed:
        raise InvalidRatePack("Pipeline pack schema mismatch")
    if data.get("revision") != requested_revision:
        raise InvalidRatePack("Pipeline pack revision mismatch")
    if data.get("status") not in {"template", "active"}:
        raise InvalidRatePack("Pipeline pack status must be template or active")

    blank_ops_raw = data.get("blank_ops")
    if not isinstance(blank_ops_raw, dict) or not blank_ops_raw:
        raise InvalidRatePack("No blank operations configured")
    blank_ops: dict[str, dict[str, Any]] = {}
    for code, op in blank_ops_raw.items():
        with _at("blank_ops", code):
            if code not in BLANK_OPS or not isinstance(op, dict):
                raise InvalidRatePack(
                    f"Неизвестная операция заготовки; допустимы: "
                    f"{', '.join(sorted(BLANK_OPS))}"
                )
            if set(op) != {"rate_kind", "rate_rub", "rate_source"}:
                raise InvalidRatePack(
                    _fields_mismatch(
                        "операции заготовки", op, {"rate_kind", "rate_rub", "rate_source"}
                    )
                )
            if op["rate_kind"] not in BLANK_RATE_KINDS:
                raise InvalidRatePack("Unsupported blank rate kind")
            blank_ops[code] = {
                "rate_kind": op["rate_kind"],
                "rate_rub": dec(op["rate_rub"], field="blank rate", positive=True),
                "rate_source": _money_source(op["rate_source"]),
            }

    materials_raw = data.get("materials")
    if not isinstance(materials_raw, dict) or not materials_raw:
        raise InvalidRatePack("No materials configured")
    materials: dict[str, dict[str, Any]] = {}
    for code, mat in materials_raw.items():
        with _at("materials", code if isinstance(code, str) else "?"):
            if not isinstance(code, str) or not isinstance(mat, dict):
                raise InvalidRatePack("Invalid material")
            expected = {"grade", "group", "stock", "density_kg_m3", "rate_rub_per_kg", "rate_source"}
            if set(mat) != expected:
                raise InvalidRatePack(_fields_mismatch("материала", mat, expected))
            if mat["stock"] not in MATERIAL_STOCK:
                raise InvalidRatePack(
                    "Поле stock — stocked (оприходовано на складе) или "
                    "purchase (закупка под заказ, цена со звёздочкой)"
                )
            materials[code] = {
                "grade": str(mat["grade"]),
                "group": str(mat["group"]),
                "stock": mat["stock"],
                "density_kg_m3": _density(mat["density_kg_m3"]),
                "rate_rub_per_kg": dec(mat["rate_rub_per_kg"], field="material rate", positive=True),
                "rate_source": _money_source(mat["rate_source"]),
            }

    machines_raw = data.get("machines")
    if not isinstance(machines_raw, dict) or not machines_raw:
        raise InvalidRatePack("No machines configured")
    machines: dict[str, dict[str, Any]] = {}
    for code, machine in machines_raw.items():
        with _at("machines", code if isinstance(code, str) else "?"):
            if code not in MACHINE_OPS or not isinstance(machine, dict):
                raise InvalidRatePack(
                    f"Неизвестная станочная операция; допустимы: "
                    f"{', '.join(sorted(MACHINE_OPS))}"
                )
            if set(machine) != {"rate_rub_per_hour", "rate_source"}:
                raise InvalidRatePack(
                    _fields_mismatch("станка", machine, {"rate_rub_per_hour", "rate_source"})
                )
            machines[code] = {
                "rate_rub_per_hour": dec(
                    machine["rate_rub_per_hour"], field="machine rate", positive=True
                ),
                "rate_source": _money_source(machine["rate_source"]),
            }

    norm_raw = data.get("norm_params")
    if not isinstance(norm_raw, dict):
        raise InvalidRatePack("norm_params must be an object")
    norm_params: dict[str, dict[str, Any]] = {}
    for key, params in norm_raw.items():
        with _at("norm_params", key if isinstance(key, str) else "?"):
            if not isinstance(key, str) or ":" not in key or not isinstance(params, dict):
                raise InvalidRatePack("Invalid norm key")
            op_code = key.split(":", 1)[0]
            if op_code not in MACHINE_OPS:
                raise InvalidRatePack("Norm for unknown operation")
            # Норма для станка, которого на предприятии нет, — мёртвая запись.
            # Молча пропущенная, она хуже отсутствующей: в разделе «Нормы» её
            # видно, и человек считает операцию покрытой, а расчёт по ней не
            # пойдёт никогда, потому что станка нет в маршруте.
            if op_code not in machines:
                raise InvalidRatePack(
                    f"Норма для станка «{op_code}», которого нет в разделе "
                    f"«Станки»: сначала заведите станок или уберите норму — "
                    f"иначе запись мёртвая и выглядит работающей"
                )
            expected = NORM_FIELDS_BY_OP[op_code]
            if set(params) != expected:
                raise InvalidRatePack(_fields_mismatch("нормы", params, expected))
            normalized: dict[str, Any] = {"source": _handbook_source(params["source"])}
            for field in expected - {"source"}:
                normalized[field] = dec(params[field], field=field, positive=True)
            norm_params[key] = normalized

    overheads_raw = data.get("overheads")
    expected_overheads = {"t_aux_min", "k_service_rest_pct", "t_setup_min", "source"}
    with _at("overheads"):
        if not isinstance(overheads_raw, dict) or set(overheads_raw) != expected_overheads:
            raise InvalidRatePack(
                _fields_mismatch("накладных времени", overheads_raw, expected_overheads)
            )
        overheads = {
            "t_aux_min": dec(overheads_raw["t_aux_min"], field="t_aux_min"),
            "k_service_rest_pct": dec(
                overheads_raw["k_service_rest_pct"], field="k_service_rest_pct"
            ),
            "t_setup_min": dec(overheads_raw["t_setup_min"], field="t_setup_min"),
            "source": _handbook_source(overheads_raw["source"]),
        }
        if overheads["k_service_rest_pct"] > 100:
            raise InvalidRatePack("k_service_rest_pct out of range")

    extras_raw = data.get("extras")
    if not isinstance(extras_raw, dict):
        raise InvalidRatePack("extras must be an object")
    extras: dict[str, dict[str, Any]] = {}
    for code, extra in extras_raw.items():
        with _at("extras", code if isinstance(code, str) else "?"):
            if code not in {"packaging", "logistics"} or not isinstance(extra, dict):
                raise InvalidRatePack("Unknown extra")
            if set(extra) != {"rate_rub", "rate_source"}:
                raise InvalidRatePack("Invalid extra fields")
            extras[code] = {
                "rate_rub": dec(extra["rate_rub"], field="extra rate"),
                "rate_source": _money_source(extra["rate_source"]),
            }

    pricing_raw = data.get("pricing")
    expected_pricing = {
        "margin_basis",
        "margin_percent",
        "vat_included",
        "vat_rate_pct",
        "valid_days",
        "rounding",
    }
    if schema_version >= 4:
        expected_pricing |= {"material_markup_percent", "rates_include_vat"}
    with _at("pricing"):
        if not isinstance(pricing_raw, dict) or set(pricing_raw) != expected_pricing:
            raise InvalidRatePack(
                _fields_mismatch("политики цены", pricing_raw, expected_pricing)
            )
        if pricing_raw["margin_basis"] not in {"on_cost", "on_price"}:
            raise InvalidRatePack("Invalid margin basis")
        if pricing_raw["rounding"] not in {
            "none",
            "up_10",
            "up_100",
            "bankers",
            "half_up",
        }:
            raise InvalidRatePack("Invalid rounding policy")
        valid_days = pricing_raw["valid_days"]
        if (
            not isinstance(valid_days, int)
            or isinstance(valid_days, bool)
            or not 1 <= valid_days <= 365
        ):
            raise InvalidRatePack("Invalid quote validity")
        if type(pricing_raw["vat_included"]) is not bool:
            raise InvalidRatePack("vat_included must be a boolean")
        pricing = {
            "margin_basis": pricing_raw["margin_basis"],
            "margin_percent": dec(pricing_raw["margin_percent"], field="margin percent"),
            "vat_included": pricing_raw["vat_included"],
            "vat_rate_pct": dec(pricing_raw["vat_rate_pct"], field="VAT rate"),
            "valid_days": valid_days,
            "rounding": pricing_raw["rounding"],
        }
        if schema_version >= 4:
            pricing["material_markup_percent"] = dec(
                pricing_raw["material_markup_percent"], field="material markup percent"
            )
            if pricing["material_markup_percent"] < 0:
                raise InvalidRatePack("material markup percent must not be negative")
            if type(pricing_raw["rates_include_vat"]) is not bool:
                raise InvalidRatePack("rates_include_vat must be a boolean")
            pricing["rates_include_vat"] = pricing_raw["rates_include_vat"]
        if pricing["vat_rate_pct"] > 100:
            raise InvalidRatePack("VAT rate must be between 0 and 100")
        if not pricing["vat_included"] and pricing["vat_rate_pct"] != 0:
            raise InvalidRatePack("VAT rate must be zero when VAT is not included")
        if pricing.get("rates_include_vat") and not pricing["vat_included"]:
            raise InvalidRatePack(
                "rates_include_vat requires vat_included: gross rates need a VAT breakdown"
            )
        if pricing["margin_basis"] == "on_price" and pricing["margin_percent"] >= 100:
            raise InvalidRatePack("on_price margin must be below 100 percent")

    rate_registry = _canonical_rates(blank_ops, materials, machines, extras)
    process_park: dict[str, dict[str, Any]] = {}
    if schema_version >= 3:
        explicit_raw = data.get("rate_registry")
        if not isinstance(explicit_raw, dict):
            raise InvalidRatePack("rate_registry must be an object")
        for rate_id_raw, entry_raw in explicit_raw.items():
            with _at("rate_registry", rate_id_raw if isinstance(rate_id_raw, str) else "?"):
                rate_id = _validate_rate_id(rate_id_raw)
                if rate_id.startswith(RESERVED_RATE_PREFIXES):
                    raise InvalidRatePack(
                        "Префиксы machine:/material:/blank:/extra: занимает "
                        "движок — эти ставки выводятся из привычных секций, "
                        "явная запись под тем же именем была бы второй правдой "
                        "об одной цене"
                    )
                rate_registry[rate_id] = _validate_rate_entry(rate_id, entry_raw)
        with _at("process_park"):
            process_park = _validate_process_park(
                data.get("process_park"), rate_registry, schema_version=schema_version
            )

    return PipelinePack(
        revision=requested_revision,
        sha256=digest,
        fingerprint=_fingerprint(data),
        status=data["status"],
        blank_ops=blank_ops,
        materials=materials,
        machines=machines,
        norm_params=norm_params,
        overheads=overheads,
        extras=extras,
        pricing=pricing,
        schema_version=schema_version,
        process_park=process_park,
        rate_registry=rate_registry,
    )
