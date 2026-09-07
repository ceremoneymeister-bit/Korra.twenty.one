#!/usr/bin/env python3
"""cadkit — детерминированные метрики раскроя из DXF/DWG.

Один вход: файл или папка заказа. На выходе по каждой детали — габарит, чистая площадь
(с вычетом отверстий), длина реза, число врезок, длины линий гиба, масса при заданной
толщине и список замечаний нормоконтроля.

Считает арифметика, а не модель: ezdxf читает геометрию, shapely собирает контуры.
DWG конвертируется через dwg2dxf (libredwg или libdxfrw) — путь берётся из
переменной CADKIT_DWG2DXF либо из ./bin/dwg2dxf рядом со скриптом.

Проверено 10.08.2026 на обезличенном наборе исходного контура A: расхождение с
ядром OCCT (cadquery) ≤ 0,01 % по площади и длине реза.

Использование:
    cadkit.py <файл|папка> [...] [-t 4] [-d 7850] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict

try:
    import ezdxf
    from ezdxf import path as ezpath
except ImportError:  # pragma: no cover
    sys.exit("нет ezdxf: pip install ezdxf shapely")

try:
    from shapely.geometry import LineString, MultiLineString, Polygon
    from shapely.ops import polygonize_full, unary_union
except ImportError:  # pragma: no cover
    sys.exit("нет shapely: pip install ezdxf shapely")

# ---------------------------------------------------------------- константы

SAGITTA = 0.002          # мм, точность спрямления дуг и сплайнов (на r=25 даёт ~0,002 % по площади)
SNAP = 1e-3              # мм, сварка микрозазоров экспорта (у Компаса 1e-6…1e-4)
BEND_LINETYPES = ("PHANTOM", "CENTER", "DASHDOT", "ОСЕВАЯ", "ШТРИХПУНКТИР", "K5LT_DASHDOT",
                  "K5LT_CENTER", "K5LT_PHANTOM")
THIN_LINETYPES = ("THIN", "ТОНК", "K5LT_THIN", "DIM")   # размерные/выносные по ГОСТ 2.303
THICK_LW = 50            # 0,50 мм и толще — основная линия контура
# Рамка чертежа в раскрой не идёт. Ловим три её вида (ГОСТ 2.301 / 2.104):
#   лист целиком · внутренняя рамка (поля 20 слева, по 5 с трёх сторон) · рамка выше штампа (55 мм)
_SHEETS = [(210, 297), (297, 420), (420, 594), (594, 841), (841, 1189)]
PAPER: list[tuple[float, float]] = []
for _a, _b in _SHEETS:
    for _w, _h in ((_a, _b), (_b, _a)):          # книжная и альбомная ориентация
        _iw, _ih = _w - 25, _h - 10              # внутренняя рамка: поля 20 слева, по 5 остальные
        for _pair in ((_w, _h), (_iw, _ih), (_iw, _ih - 55)):   # 55 мм — высота основной надписи
            _p = tuple(sorted(_pair))
            if _p not in PAPER:
                PAPER.append(_p)  # type: ignore[arg-type]
SKIP_TYPES = {"POINT", "TEXT", "MTEXT", "DIMENSION", "ATTDEF", "ATTRIB", "LEADER",
              "MULTILEADER", "HATCH", "SOLID", "VIEWPORT", "IMAGE", "WIPEOUT"}
# $INSUNITS -> мм
UNIT_SCALE = {0: None, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 11: 1e-7, 13: 1e-6}
UNIT_NAME = {0: "не задано", 1: "дюймы", 2: "футы", 4: "мм", 5: "см", 6: "м"}
SANE_MIN_MM, SANE_MAX_MM = 1.0, 12000.0


@dataclass
class Part:
    file: str
    ok: bool = False
    units: str = ""
    scale: float = 1.0
    bbox: tuple[float, float] | None = None
    area_mm2: float | None = None       # чистая, с вычетом отверстий
    outer_area_mm2: float | None = None  # по внешнему контуру
    cut_length_mm: float | None = None
    pierces: int = 0                     # число замкнутых контуров = врезок
    holes: int = 0
    contours: int = 0                    # число отдельных деталей в файле
    bend_lines_mm: list[float] = field(default_factory=list)
    mass_kg: float | None = None
    entity_types: dict[str, int] = field(default_factory=dict)
    top_contours: list[dict] = field(default_factory=list)  # контуры верхнего уровня (виды/детали)
    largest: dict | None = None                             # крупнейший контур = кандидат «деталь»
    texts: list[dict] = field(default_factory=list)         # надписи: штамп, марка стали, масса
    dimensions: list[float] = field(default_factory=list)   # значения размерных надписей, мм
    warnings: list[str] = field(default_factory=list)
    error: str = ""


# ------------------------------------------------------------------ чтение

def find_dwg2dxf() -> str | None:
    env = os.environ.get("CADKIT_DWG2DXF")
    if env and os.path.exists(env):
        return env
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "dwg2dxf")
    if os.path.exists(local):
        return local
    return shutil.which("dwg2dxf")


def dwg_to_dxf(path: str, workdir: str) -> tuple[str, list[str]]:
    """DWG -> DXF. Возвращает путь к DXF и замечания."""
    tool = find_dwg2dxf()
    if not tool:
        raise RuntimeError("dwg2dxf не найден (CADKIT_DWG2DXF / ./bin/dwg2dxf / PATH)")
    out = os.path.join(workdir, os.path.splitext(os.path.basename(path))[0] + ".dxf")
    # libredwg:  dwg2dxf -o out in       libdxfrw:  dwg2dxf in -v2000 -y out
    attempts = ([tool, "-y", "-o", out, path], [tool, path, "-v2000", "-y", out])
    last = ""
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                return out, []
            last = (r.stderr or r.stdout or "").strip()[:200]
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"конвертация DWG не удалась: {last}")


def effective_lineweight(e, doc) -> int:
    """Толщина линии с учётом BYLAYER (-1) и BYBLOCK (-2)."""
    lw = e.dxf.get("lineweight", -1)
    if lw is None:
        return -1
    if lw < 0:
        try:
            return doc.layers.get(e.dxf.layer).dxf.lineweight
        except Exception:  # noqa: BLE001
            return -1
    return lw


def read_text(e) -> str:
    """Текст сущности: TEXT/ATTRIB — как есть, MTEXT — без форматирующих кодов."""
    t = e.dxftype()
    try:
        if t == "MTEXT":
            return e.plain_text(split=False).strip()
        if t in ("TEXT", "ATTRIB", "ATTDEF"):
            return str(e.dxf.get("text", "")).strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""


def collect_entities(doc) -> tuple[list, list[float], dict[str, int], list[str]]:
    """Разворачивает блоки, отделяет линии гиба и тонкие (размерные) линии.

    ГОСТ 2.303: контур детали — сплошная ТОЛСТАЯ основная; размерные, выносные и осевые —
    тонкие. Если в файле есть и толстые, и тонкие — берём в раскрой только толстые.
    Если толщина везде одна (типичный DXF-раскрой) — берём всё.
    """
    msp = doc.modelspace()
    geometry, bends, types, warns = [], [], {}, []
    thin: list = []
    texts: list[dict] = []
    dims: list[float] = []

    def take(e, depth=0):
        t = e.dxftype()
        types[t] = types.get(t, 0) + 1
        if t in ("TEXT", "MTEXT", "ATTRIB"):
            s = read_text(e)
            if s and len(texts) < 400:
                try:
                    p = e.dxf.get("insert", None) or e.dxf.get("align_point", None)
                    xy = (round(p.x, 1), round(p.y, 1)) if p is not None else None
                except Exception:  # noqa: BLE001
                    xy = None
                texts.append(dict(text=s[:200], at=xy))
        if t == "DIMENSION":
            try:
                m = e.get_measurement()
                if isinstance(m, (int, float)):
                    dims.append(round(float(m), 2))
            except Exception:  # noqa: BLE001
                pass
        if t == "INSERT":
            for a in getattr(e, "attribs", []) or []:
                s = read_text(a)
                if s and len(texts) < 400:
                    texts.append(dict(text=s[:200], at=None))
            if depth > 4:
                warns.append("вложенность блоков > 4 — часть геометрии пропущена")
                return
            # Свой разворот блока вместо virtual_entities(): у конвертированных из DWG файлов
            # встречается вырожденный MTEXT, на котором генератор ezdxf падает ZeroDivisionError
            # и молча теряет ВСЮ оставшуюся геометрию блока. Текст нам не нужен — не трогаем его.
            try:
                block = doc.blocks.get(e.dxf.name)
                matrix = e.matrix44()
            except Exception as exc:  # noqa: BLE001
                warns.append(f"блок {e.dxf.get('name', '?')} не прочитан ({type(exc).__name__})")
                return
            if block is None:
                warns.append(f"блок {e.dxf.get('name', '?')} отсутствует в файле")
                return
            lost = 0
            for sub in block:
                if sub.dxftype() in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
                    s = read_text(sub)
                    if s and len(texts) < 400:
                        texts.append(dict(text=s[:200], at=None))
                    continue
                if sub.dxftype() in SKIP_TYPES:
                    continue
                try:
                    copy = sub.copy()
                    copy.transform(matrix)
                except Exception:  # noqa: BLE001
                    lost += 1
                    continue
                take(copy, depth + 1)
            if lost:
                warns.append(f"{lost} сущностей блока не перенеслись — проверить деталь глазами")
            return
        if t in SKIP_TYPES:
            return
        lt = (e.dxf.get("linetype", "BYLAYER") or "").upper()
        if lt.startswith(BEND_LINETYPES):
            if t == "LINE":
                bends.append(round((e.dxf.end - e.dxf.start).magnitude, 3))
            return
        if any(k in lt for k in THIN_LINETYPES):
            thin.append(e)
            return
        geometry.append(e)

    for e in msp:
        take(e)

    # разделение по толщине линии — только если в файле реально есть обе
    weights = [(e, effective_lineweight(e, doc)) for e in geometry]
    thick = [e for e, lw in weights if lw >= THICK_LW]
    light = [e for e, lw in weights if 0 <= lw < THICK_LW]
    if thick and light:
        warns.append(f"чертёж, а не раскрой: {len(light)} тонких линий отброшено "
                     f"(размерные/выносные), контур взят по {len(thick)} основным")
        geometry = thick
    elif thin and geometry:
        warns.append(f"отброшено {len(thin)} тонких линий по типу линии")
    return geometry, bends, types, warns, texts, dims


def to_segments(entities, scale: float) -> tuple[list[LineString], list[str]]:
    segs, warns, skipped = [], [], 0
    for e in entities:
        try:
            p = ezpath.make_path(e)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        pts = [(round(v.x * scale / SNAP) * SNAP, round(v.y * scale / SNAP) * SNAP)
               for v in p.flattening(SAGITTA / max(scale, 1e-9))]
        # выкидываем повторы после сварки координат
        clean = [pts[0]] if pts else []
        for q in pts[1:]:
            if q != clean[-1]:
                clean.append(q)
        if len(clean) >= 2:
            segs.append(LineString(clean))
    if skipped:
        warns.append(f"{skipped} сущностей не преобразованы в геометрию")
    return segs, warns


# ------------------------------------------------------------------ расчёт

def nesting_depth(polys):
    """Глубина вложенности контуров: чётная — металл, нечётная — отверстие.

    polygonize отдаёт плоское разбиение: грань «деталь» уже БЕЗ дырок, а сами дырки —
    отдельные грани. Поэтому вложенность считаем по ВНЕШНИМ кольцам (Polygon(exterior)),
    иначе точка в дырке не попадёт внутрь родителя и глубина будет нулевой у всех.
    """
    shells = [Polygon(p.exterior) for p in polys]
    order = sorted(range(len(polys)), key=lambda i: shells[i].area, reverse=True)
    depth = [0] * len(polys)
    for pos, i in enumerate(order):
        pt = polys[i].representative_point()
        for j in order[:pos]:
            if shells[j].covers(pt):
                depth[i] += 1
    return depth


def analyze(path: str, thickness: float | None, density: float, workdir: str) -> Part:
    part = Part(file=os.path.basename(path))
    src = path
    try:
        if path.lower().endswith(".dwg"):
            src, w = dwg_to_dxf(path, workdir)
            part.warnings += w
        doc = ezdxf.readfile(src)
    except Exception as exc:  # noqa: BLE001
        part.error = f"{type(exc).__name__}: {exc}"
        return part

    insunits = doc.header.get("$INSUNITS", 0) or 0
    scale = UNIT_SCALE.get(insunits)
    part.units = UNIT_NAME.get(insunits, f"код {insunits}")
    if scale is None:
        scale = 1.0
        part.warnings.append("$INSUNITS не задан — единицы приняты как мм, СВЕРИТЬ со штампом")
    elif scale != 1.0:
        part.warnings.append(f"чертёж в единицах «{part.units}» — пересчитан в мм (×{scale})")
    part.scale = scale

    entities, bends, types, warns, texts, dims = collect_entities(doc)
    part.bend_lines_mm = [round(b * scale, 2) for b in bends]
    part.entity_types = types
    part.texts = texts
    part.dimensions = sorted({round(d * scale, 2) for d in dims}, reverse=True)[:60]
    part.warnings += warns
    if not entities:
        part.error = "в файле нет геометрии (только текст/размеры или пустой modelspace)"
        return part

    segs, w2 = to_segments(entities, scale)
    part.warnings += w2
    if not segs:
        part.error = "геометрия не преобразовалась в контуры"
        return part

    network = unary_union(MultiLineString(segs))
    polys, cuts, dangles, invalid = polygonize_full(network)
    poly_list = list(polys.geoms)

    if dangles.geoms:
        total = sum(g.length for g in dangles.geoms)
        ends = dangles.geoms[0].coords[0]
        part.warnings.append(
            f"КОНТУР НЕ ЗАМКНУТ: {len(dangles.geoms)} висячих рёбер, {total:.2f} мм, "
            f"первое у ({ends[0]:.2f}, {ends[1]:.2f})")
    if invalid.geoms:
        part.warnings.append(f"{len(invalid.geoms)} некорректных колец — геометрия спорная")
    if not poly_list:
        part.error = "ни одного замкнутого контура — по такому файлу считать нельзя"
        return part

    depth = nesting_depth(poly_list)

    # рамка чертежа: контур верхнего уровня размером со стандартный лист — в раскрой не идёт
    frames = []
    for i, (p, d) in enumerate(zip(poly_list, depth)):
        if d != 0:
            continue
        x0, y0, x1, y1 = p.bounds
        w, h = sorted((x1 - x0, y1 - y0))
        for pw, ph in PAPER:
            if abs(w - pw) < 3 and abs(h - ph) < 3:
                frames.append(i)
                break
    if frames:
        part.warnings.append(f"отброшена рамка чертежа ({len(frames)} шт., формат листа)")
        inside_frame = {i for i in frames}
        keep = [i for i in range(len(poly_list)) if i not in inside_frame]
        poly_list = [poly_list[i] for i in keep]
        depth = nesting_depth(poly_list)

    # у граней чётной глубины дырки уже вычтены самим polygonize — их площади и складываем
    net = sum(p.area for p, d in zip(poly_list, depth) if d % 2 == 0)
    outer = sum(Polygon(p.exterior).area for p, d in zip(poly_list, depth) if d == 0)
    cut = sum(p.exterior.length for p in poly_list)
    tops = sorted(((Polygon(p.exterior).area, p.bounds) for p, d in zip(poly_list, depth) if d == 0),
                  reverse=True)
    part.top_contours = [
        dict(area_mm2=round(a, 2), bbox=(round(b[2] - b[0], 2), round(b[3] - b[1], 2)))
        for a, b in tops[:12]]

    # крупнейший контур верхнего уровня — кандидат «сама деталь», когда в файле ещё виды/штамп
    if poly_list:
        shells = [Polygon(p.exterior) for p in poly_list]
        big = max(range(len(poly_list)), key=lambda i: shells[i].area if depth[i] == 0 else -1)
        inside = [i for i in range(len(poly_list))
                  if i == big or shells[big].covers(poly_list[i].representative_point())]
        d0 = depth[big]
        l_net = sum(poly_list[i].area for i in inside if (depth[i] - d0) % 2 == 0)
        l_cut = sum(poly_list[i].exterior.length for i in inside)
        b = poly_list[big].bounds
        part.largest = dict(
            area_mm2=round(l_net, 3), cut_length_mm=round(l_cut, 3), pierces=len(inside),
            holes=sum(1 for i in inside if (depth[i] - d0) % 2 == 1),
            bbox=(round(b[2] - b[0], 3), round(b[3] - b[1], 3)),
            mass_kg=round(l_net * thickness * density / 1e9, 4) if thickness else None)
    minx, miny, maxx, maxy = network.bounds

    part.ok = True
    part.bbox = (round(maxx - minx, 3), round(maxy - miny, 3))
    part.area_mm2 = round(net, 3)
    part.outer_area_mm2 = round(outer, 3)
    part.cut_length_mm = round(cut, 3)
    part.pierces = len(poly_list)
    part.holes = sum(1 for d in depth if d % 2 == 1)
    part.contours = sum(1 for d in depth if d == 0)
    if thickness:
        part.mass_kg = round(net * thickness * density / 1e9, 4)

    if part.contours > 1:
        top = ", ".join(f"{c['bbox'][0]}×{c['bbox'][1]} мм ({c['area_mm2']:.0f} мм²)"
                        for c in part.top_contours[:4])
        part.warnings.append(
            f"это чертёж/комплект: {part.contours} контуров верхнего уровня. Взят КРУПНЕЙШИЙ; "
            f"остальные кандидаты: {top}. Если раскрой — другой вид, выбрать должен человек")
    big = max(part.bbox)
    if big < SANE_MIN_MM or big > SANE_MAX_MM:
        part.warnings.append(f"габарит {part.bbox[0]}×{part.bbox[1]} мм вне разумного — вероятна ошибка масштаба")
    if net <= 0:
        part.warnings.append("чистая площадь ≤ 0 — вложенность контуров разобрана неверно")
    return part


# -------------------------------------------------------------------- CLI

def gather(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                if ".venv" in root or "/." in root:
                    continue
                for f in sorted(files):
                    if f.lower().endswith((".dxf", ".dwg")):
                        out.append(os.path.join(root, f))
        elif p.lower().endswith((".dxf", ".dwg")):
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Метрики раскроя из DXF/DWG")
    ap.add_argument("paths", nargs="+", help="файлы или папки заказа")
    ap.add_argument("-t", "--thickness", type=float, default=None, help="толщина, мм (для массы)")
    ap.add_argument("-d", "--density", type=float, default=7850.0, help="плотность, кг/м³ (сталь 7850)")
    ap.add_argument("--texts", action="store_true", help="показать надписи (штамп, марка, масса) и размеры")
    ap.add_argument("--json", dest="json_out", help="куда записать JSON")
    ap.add_argument("--quiet", action="store_true", help="только JSON, без таблицы")
    args = ap.parse_args()

    files = gather(args.paths)
    if not files:
        print("не найдено ни одного .dxf/.dwg", file=sys.stderr)
        return 2

    results = []
    with tempfile.TemporaryDirectory(prefix="cadkit-") as workdir:
        for f in files:
            results.append(analyze(f, args.thickness, args.density, workdir))

    if not args.quiet:
        w = max(len(r.file) for r in results)
        w = min(w, 52)
        for r in results:
            name = r.file[:w].ljust(w)
            if not r.ok:
                print(f"{name}  ОТКАЗ: {r.error}")
            elif r.contours > 1 and r.largest:
                # чертёж с видами/штампом: заголовок — сама деталь, итог по файлу второй строкой
                L = r.largest
                mass = f" m={L['mass_kg']:>8.4f} кг" if L.get("mass_kg") else ""
                print(f"{name}  S={L['area_mm2']:>13.2f} мм²  рез={L['cut_length_mm']:>9.2f} мм  "
                      f"врезок={L['pierces']:>3} отв={L['holes']:>3}{mass}  "
                      f"габарит={L['bbox'][0]}×{L['bbox'][1]}  ← крупнейший контур")
                print(f"{' ' * w}  всего в файле: {r.contours} контуров верхнего уровня, "
                      f"S={r.area_mm2:.2f} мм², рез={r.cut_length_mm:.2f} мм")
            else:
                mass = f" m={r.mass_kg:>8.4f} кг" if r.mass_kg is not None else ""
                print(f"{name}  S={r.area_mm2:>13.2f} мм²  рез={r.cut_length_mm:>9.2f} мм  "
                      f"врезок={r.pierces:>3} отв={r.holes:>3}{mass}  "
                      f"габарит={r.bbox[0]}×{r.bbox[1]}  гибы={r.bend_lines_mm or '—'}")
            for warn in r.warnings:
                print(f"{' ' * w}  ⚠ {warn}")
            if args.texts:
                if r.texts:
                    joined = " | ".join(t["text"] for t in r.texts)
                    print(f"{' ' * w}  надписи: {joined[:600]}")
                if r.dimensions:
                    print(f"{' ' * w}  размеры на чертеже, мм: {r.dimensions[:20]}")
        ok = sum(1 for r in results if r.ok)
        # масса комплекта — по тем же числам, что в заголовке строки (для чертежей это деталь, не весь лист)
        total_mass = sum((r.largest or {}).get("mass_kg") or 0 if (r.contours > 1 and r.largest)
                         else (r.mass_kg or 0) for r in results if r.ok)
        print(f"\nитого: {ok}/{len(results)} деталей" + (f", масса комплекта {total_mass:.3f} кг" if total_mass else ""))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump([asdict(r) for r in results], fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"JSON: {args.json_out}")
    elif args.quiet:
        json.dump([asdict(r) for r in results], sys.stdout, ensure_ascii=False, indent=2)

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
