#!/usr/bin/env python3
"""Parsea la Hoja 1 de un archivo .drawio y genera un TXT/JSON con:
- vertices y aristas validos
- DFS iterativo raiz -> hoja
- colapso de cadenas consecutivas de rombos (look-ahead)
- fusion de convergencias (multiples predecesores al mismo destino)
- Trie para agrupar prefijos comunes
- separacion entre arboles principales
- v4.1: fusion de ramas hoja al bloque actual (INTACTA)
- v4.2: fusion de ramas 'cabeza + 1 hijo hoja' bajo padre no-rombo (INTACTA)
- v4.4: rombo contenedor (transparente) cuando su primera rama es otro rombo
- v4.5: imagenes embebidas como renglones 'IMG:<data:image/...>'
- v4.6: dato 'ruta' en el diagrama (<object label="..." ruta="N" id="...">)
- v4.7: la celda anotada nunca es un rombo; el drawio anotado reemplaza
  al original (idempotente)
- v4.8: PASO OPCIONAL recordatorio.txt -> sufijos " & N,M"
- v4.9: correcciones del paso recordatorio (reinicio de rango por subT,
  IMG excluidas del matching, tema desde renglones, matching tolerante,
  validacion de anclas con avisos)
- v4.10: dedup de convergencias (caminos que se bifurcan y se juntan):
  duplicado puro se elimina; contenido unico se fusiona si la bifurcacion
  es en NO-rombo; si es en ROMBO se conservan como alternativas
- v4.11: matching relajado cuando la pista tiene una unica clave
- v4.12: 4 ajustes del asociador validados contra un ground-truth manual:
  * FIX 1: los temas detectados en RENGLONES de una ruta REEMPLAZAN al
    tema del subtitle (el rombo que queda como renglon es el que gobierna
    la ruta; antes se SUMABAN y la ruta quedaba en dos temas a la vez,
    ej. ruta 17 pertenecia tambien a 'Diagramas Bloques').
  * FIX 2: SIMILITUD_CLAVE_UNICA 0.75 -> 0.83 (0.75 dejaba pasar pares
    tipo derivada~derivativa = 0.78 entre rutas distintas).
  * FIX 3: normalizar_texto fusiona '/' y '-' ENTRE alfanumericos
    (A/D->ad, D/A->da, on-off->onoff); MIN_LARGO_CLAVE 3 -> 2 (permite
    ad/da/KD/Ki) con stopwords de 2 letras ampliados y numeros solos
    descartados; las claves de menos de 3 letras emparejan SOLO por
    palabra exacta (evita que 'da' calce dentro de 'entrada').
  * FIX 4: seleccion por COBERTURA: una ruta calificada se descarta si
    no aporta NINGUNA clave nueva frente a las ya elegidas (ej. la ruta
    'transductor entre orden v' coincide con exactamente las mismas
    claves que la definicion de transductor -> se descarta; en cambio
    'positiva' aporta frente a 'negativa' -> ambas se conservan).
  * Los sufijos automaticos se escriben en orden ASCENDENTE de rutas.
"""
from __future__ import annotations

import difflib
import html
import json
import re
# import shutil  # desactivado: ya no se crea copia de seguridad .bak
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog

Graph = dict[str, list[str]]

IMAGE_URI_RE = re.compile(
    r"image=(data:image/[A-Za-z0-9.+\-]+(?:;base64)?,[A-Za-z0-9+/=%\-_.!~*'()]+)"
)
IMAGE_LINE_RE = re.compile(r"^data:image/")


@dataclass
class TrieNode:
    key: str
    value: str
    children: dict[str, "TrieNode"] = field(default_factory=dict)
    chain_marked: bool = False


@dataclass
class RouteBlock:
    number: int
    subtitle: Optional[str]
    lines: list[str]
    # v4.7: secuencia (cell_key, lineas) de TODAS las celdas que aportan
    # renglones al bloque, en orden. Permite elegir la celda del dato
    # 'ruta' saltando rombos y sinteticos.
    items: list[tuple[str, list[str]]] = field(default_factory=list)


def extract_image_uri(style: str) -> Optional[str]:
    match = IMAGE_URI_RE.search(style or "")
    return match.group(1) if match else None


def with_image_prefix(line: str) -> str:
    if IMAGE_LINE_RE.match(line):
        return f"IMG:{line}"
    return line


def clean_drawio_value(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</?(?:div|p)\b[^>]*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def is_rhombus_style(style: str) -> bool:
    lowered = (style or "").lower()
    return "rhombus" in lowered or "flowchart.decision" in lowered or ("gradientcolor=#" in lowered and "ellipse" in lowered)


def parse_first_page(path: Path):
    root = ET.parse(path).getroot()
    diagrams = root.findall("./diagram")
    if not diagrams:
        raise ValueError("No se encontro ningun <diagram> en el archivo .drawio.")

    diagram = diagrams[0]
    graph_model = diagram.find("./mxGraphModel")
    if graph_model is None:
        raise ValueError("La Hoja 1 no contiene un <mxGraphModel> directo.")

    vertices: dict[str, str] = {}
    styles: dict[str, str] = {}
    raw_edges: list[tuple[str, str]] = []

    def register_cell(cell_id, raw_value, style, is_edge, source, target):
        if not cell_id:
            return
        style = style or ""
        image_uri = None if is_edge else extract_image_uri(style)
        if image_uri is not None:
            text = clean_drawio_value(raw_value) if raw_value else ""
            vertices[cell_id] = f"{image_uri}\n{text}" if text else image_uri
            styles[cell_id] = style
        elif raw_value is not None:
            text = clean_drawio_value(raw_value)
            if text:
                vertices[cell_id] = text
                styles[cell_id] = style
        if is_edge and source and target:
            raw_edges.append((source, target))

    wrapper_elems = []
    wrapped_inners: set[int] = set()
    for elem in graph_model.iter():
        if elem.tag in ("object", "UserObject"):
            wrapper_elems.append(elem)
            for child in elem:
                if child.tag == "mxCell":
                    wrapped_inners.add(id(child))

    for obj in wrapper_elems:
        inner = next((c for c in obj if c.tag == "mxCell"), None)
        if inner is None:
            continue
        register_cell(
            obj.get("id"),
            obj.get("label", obj.get("value")),
            inner.get("style", ""),
            inner.get("edge") == "1",
            inner.get("source"),
            inner.get("target"),
        )

    for cell in graph_model.iter("mxCell"):
        if id(cell) in wrapped_inners:
            continue
        register_cell(
            cell.get("id"),
            cell.get("value"),
            cell.get("style", ""),
            cell.get("edge") == "1",
            cell.get("source"),
            cell.get("target"),
        )

    graph: Graph = defaultdict(list)
    valid_edges: list[tuple[str, str]] = []
    for source, target in raw_edges:
        if source in vertices and target in vertices:
            graph[source].append(target)
            valid_edges.append((source, target))

    for node_id in vertices:
        graph.setdefault(node_id, [])

    indegree = {node_id: 0 for node_id in vertices}
    for _, target in valid_edges:
        indegree[target] += 1

    outdegree = {node_id: len(graph[node_id]) for node_id in vertices}
    orphan_ids = {
        node_id for node_id in vertices
        if indegree[node_id] == 0 and outdegree[node_id] == 0
    }

    if orphan_ids:
        vertices = {k: v for k, v in vertices.items() if k not in orphan_ids}
        styles = {k: v for k, v in styles.items() if k not in orphan_ids}
        valid_edges = [
            (s, t) for s, t in valid_edges
            if s not in orphan_ids and t not in orphan_ids
        ]
        graph = defaultdict(list)
        for source, target in valid_edges:
            graph[source].append(target)
        for node_id in vertices:
            graph.setdefault(node_id, [])

    diagram_name = diagram.get("name", "Hoja 1")
    diagram_id = diagram.get("id", "")
    return vertices, styles, valid_edges, dict(graph), diagram_name, diagram_id, orphan_ids


def find_roots(vertices: dict[str, str], edges: list[tuple[str, str]]) -> list[str]:
    indegree = {node_id: 0 for node_id in vertices}
    for _, target in edges:
        indegree[target] += 1
    return [node_id for node_id in vertices if indegree[node_id] == 0]


def find_leaves(graph: Graph) -> list[str]:
    return [node_id for node_id, neighbors in graph.items() if len(neighbors) == 0]


def collapse_rhombus_chain(path: list[str], styles: dict[str, str]) -> tuple[list[str], set[str]]:
    if not path:
        return path, set()

    result: list[str] = [path[0]]
    marks: set[str] = set()
    i = 1
    while i < len(path):
        current = path[i]
        if is_rhombus_style(styles.get(current, "")):
            run = [current]
            j = i + 1
            while j < len(path) and is_rhombus_style(styles.get(path[j], "")):
                run.append(path[j])
                j += 1
            result.append(run[-1])
            if len(run) >= 2:
                marks.add(run[-1])
            i = j
        else:
            result.append(current)
            i += 1
    return result, marks


def find_paths_iterative(
    graph: Graph,
    roots: list[str],
    leaves: set[str],
    styles: dict[str, str],
) -> tuple[list[list[str]], set[str]]:
    paths: list[list[str]] = []
    chain_marks: set[str] = set()
    stack: list[tuple[str, list[str]]] = []
    for root in reversed(roots):
        stack.append((root, [root]))

    while stack:
        current, path = stack.pop()
        if current in leaves:
            collapsed, marks = collapse_rhombus_chain(path, styles)
            paths.append(collapsed)
            chain_marks |= marks
            continue

        for neighbor in reversed(graph.get(current, [])):
            if neighbor not in path:
                stack.append((neighbor, path + [neighbor]))
    return paths, chain_marks


def build_convergence_map(
    vertices: dict[str, str],
    edges: list[tuple[str, str]],
) -> dict[str, tuple[str, str, list[str]]]:
    predecessors: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        predecessors[target].append(source)

    result: dict[str, tuple[str, str, list[str]]] = {}
    for target, sources in predecessors.items():
        unique_sources = list(dict.fromkeys(sources))
        if len(unique_sources) > 1:
            text_parts: list[str] = []
            image_parts: list[str] = []
            for source in unique_sources:
                source_value = vertices[source]
                if IMAGE_LINE_RE.match(source_value):
                    image_parts.append(source_value)
                else:
                    text_parts.append(source_value)
            value = " ".join(text_parts)
            for image_value in image_parts:
                value = f"{value}\n{image_value}" if value else image_value
            key = f"__CONVERGENCE__{target}"
            result[target] = (key, value, unique_sources)
    return result


def apply_convergences(
    paths: list[list[str]],
    convergence_map: dict[str, tuple[str, str, list[str]]],
) -> tuple[list[list[str]], dict[str, str], dict[str, dict]]:
    values: dict[str, str] = {}
    metadata: dict[str, dict] = {}
    transformed: list[list[str]] = []

    for target, (key, value, sources) in convergence_map.items():
        values[key] = value
        metadata[key] = {
            "type": "convergence",
            "target": target,
            "sources": sources,
        }

    for path in paths:
        new_path: list[str] = []
        i = 0
        while i < len(path):
            current = path[i]
            if i + 1 < len(path):
                target = path[i + 1]
                convergence = convergence_map.get(target)
                if convergence is not None and current in convergence[2]:
                    key = convergence[0]
                    if not new_path or new_path[-1] != key:
                        new_path.append(key)
                    i += 1
                    continue
            new_path.append(current)
            i += 1
        transformed.append(new_path)

    return transformed, values, metadata


# ======================================================================
# v4.10: DEDUP DE CONVERGENCIAS (diamantes que se bifurcan y se juntan)
# ======================================================================

DEDUPLICAR_CONVERGENCIAS = True


def deduplicar_convergencias(
    paths: list[list[str]],
    values: dict[str, str],
    is_rhombus_key,
) -> tuple[list[list[str]], int, int]:
    """v4.10: fusiona caminos que se bifurcan y vuelven a juntar.

    (a) DUPLICADO PURO: el camino no aportaria ningun texto nuevo -> se
        elimina, sin importar donde se bifurque.
    (b) APORTA CONTENIDO UNICO -> se fusiona SOLO si se separan en un
        NO-rombo; el texto unico se inserta antes del punto de union.
    (c) SE SEPARAN EN UN ROMBO -> alternativas de una decision: se
        conservan como ruta propia.
    """
    nuevos: list[list[str]] = []
    colas: dict[tuple[str, ...], tuple[int, list[str]]] = {}
    fusionados = 0
    alternativas = 0

    def contenido(k: str) -> str:
        return normalizar_texto(values.get(k, k))

    def registrar(idx: int, camino: list[str], conv_pos: list[int]) -> None:
        for i in conv_pos:
            colas.setdefault(tuple(camino[i:]), (idx, camino))

    for path in paths:
        conv_pos = [i for i, k in enumerate(path) if k.startswith("__CONVERGENCE__")]

        encontrado = None
        for i in conv_pos:
            dato = colas.get(tuple(path[i:]))
            if dato is not None:
                encontrado = (dato[0], dato[1], i)
                break

        if encontrado is None:
            nuevos.append(list(path))
            registrar(len(nuevos) - 1, path, conv_pos)
            continue

        idx_portador, camino_orig, pos_corte = encontrado
        portador = nuevos[idx_portador]

        L = 0
        while L < len(path) and L < len(camino_orig) and path[L] == camino_orig[L]:
            L += 1
        bifurca_en_rombo = L > 0 and is_rhombus_key(path[L - 1])

        claves_portador = set(portador)
        valores_portador = {c for c in (contenido(k) for k in portador) if c}
        extras: list[str] = []
        for k in path[:pos_corte]:
            if k in claves_portador:
                continue
            c = contenido(k)
            if c and c in valores_portador:
                continue
            claves_portador.add(k)
            if c:
                valores_portador.add(c)
            extras.append(k)

        if bifurca_en_rombo and extras:
            alternativas += 1
            nuevos.append(list(path))
            registrar(len(nuevos) - 1, path, conv_pos)
            continue

        if extras:
            j: Optional[int] = None
            try:
                j = portador.index(path[pos_corte])
            except ValueError:
                for k in path[pos_corte:]:
                    if k in portador:
                        j = portador.index(k)
                        break
            if j is None:
                alternativas += 1
                nuevos.append(list(path))
                registrar(len(nuevos) - 1, path, conv_pos)
                continue
            portador[j:j] = extras
        fusionados += 1
        registrar(idx_portador, path, conv_pos)

    return nuevos, fusionados, alternativas


def build_trie(
    paths: list[list[str]],
    values: dict[str, str],
    vertices: dict[str, str],
    chain_marks: Optional[set[str]] = None,
) -> TrieNode:
    chain_marks = chain_marks or set()
    root = TrieNode("__ROOT__", "")
    for path in paths:
        current = root
        for key in path:
            value = values.get(key, vertices.get(key, key))
            child = current.children.get(key)
            if child is None:
                child = TrieNode(key, value, chain_marked=key in chain_marks)
                current.children[key] = child
            current = child
    return root


def make_is_rhombus_lookup(styles: dict[str, str]):
    def _is_rhombus_key(key: str) -> bool:
        if key.startswith("__CONVERGENCE__"):
            return False
        return is_rhombus_style(styles.get(key, ""))

    return _is_rhombus_key


def _is_head_with_single_leaf(node: TrieNode) -> bool:
    if len(node.children) != 1:
        return False
    only_child = next(iter(node.children.values()))
    return len(only_child.children) == 0


def _split_into_blocks(
    node: TrieNode,
    active_rhombus: Optional[str],
    is_rhombus_key,
) -> tuple[
    list[tuple[Optional[str], list[tuple[str, list[str]]]]],
    list[tuple[str, list[str]]],
    list[tuple[str, list[str]]],
]:
    """Descompone el sub-arbol de 'node' en bloques lineales (v4.7: items)."""
    own_item = (node.key, node.value.splitlines() or [""])
    node_is_rhombus = is_rhombus_key(node.key)
    next_active = node.value if node_is_rhombus else active_rhombus

    children = list(node.children.values())
    if not children:
        return [], [own_item], []

    transparent = node_is_rhombus and is_rhombus_key(children[0].key)
    down_active = active_rhombus if transparent else next_active

    closed: list[tuple[Optional[str], list[tuple[str, list[str]]]]] = []
    first_closed, first_items, first_cont = _split_into_blocks(children[0], down_active, is_rhombus_key)
    closed.extend(first_closed)

    if transparent:
        open_items = list(first_items)
    else:
        open_items = [own_item] + list(first_items)
    cont_items = list(first_items)

    for other in children[1:]:
        o_closed, o_items, o_cont = _split_into_blocks(other, down_active, is_rhombus_key)

        if not other.children:
            open_items.extend(o_items)
            cont_items.extend(o_items)
        elif other.chain_marked:
            closed.append((other.value, list(o_cont)))
        elif not node_is_rhombus and _is_head_with_single_leaf(other):
            open_items.extend(o_items)
            cont_items.extend(o_items)
        else:
            closed.append((None if transparent else next_active, list(o_items)))

        closed.extend(o_closed)

    return closed, open_items, cont_items


def build_route_blocks(trie_root: TrieNode, is_rhombus_key) -> list[RouteBlock]:
    blocks: list[RouteBlock] = []
    number = 1
    for root_child in trie_root.children.values():
        closed, open_items, _cont = _split_into_blocks(root_child, None, is_rhombus_key)
        tree_blocks = [(None, open_items)] + closed
        for subtitle, items in tree_blocks:
            lines = [line for _key, item_lines in items for line in item_lines]
            blocks.append(RouteBlock(number=number, subtitle=subtitle, lines=lines, items=items))
            number += 1
    return blocks

def get_routes_id_map(input_path: Path) -> dict[int, dict]:
    """Para uso externo (otro script importa este). v4.10: aplica el mismo
    dedup de convergencias que main()."""
    vertices, styles, edges, graph, _n, _i, _o = parse_first_page(input_path)
    roots = find_roots(vertices, edges)
    leaves = find_leaves(graph)
    original_paths, chain_marks = find_paths_iterative(graph, roots, set(leaves), styles)
    convergence_map = build_convergence_map(vertices, edges)
    transformed_paths, convergence_values, _meta = apply_convergences(original_paths, convergence_map)
    trie_values = {**convergence_values, **vertices}
    is_rhombus_key = make_is_rhombus_lookup(styles)
    if DEDUPLICAR_CONVERGENCIAS:
        transformed_paths, _f, _a = deduplicar_convergencias(transformed_paths, trie_values, is_rhombus_key)
    trie = build_trie(transformed_paths, trie_values, vertices, chain_marks)
    route_blocks = build_route_blocks(trie, is_rhombus_key)

    id_map: dict[int, dict] = {}
    for block in route_blocks:
        ids: dict[str, str] = {}
        index = 1
        for key, item_lines in block.items:
            for _line in item_lines:
                ids[f"renglon{index}"] = key
                index += 1
        id_map[block.number] = {"tema": block.subtitle, "ids": ids}
    return id_map

def collect_ruta_annotations(
    route_blocks: list[RouteBlock],
    is_rhombus_key,
) -> tuple[dict[str, str], list[str], list[str]]:
    """v4.7: celda -> numero de ruta (primera celda real NO-rombo)."""
    annotations: dict[str, str] = {}
    unannotated: list[str] = []
    conflicts: list[str] = []
    for block in route_blocks:
        chosen_key: Optional[str] = None
        for key, _lines in block.items:
            if not key or key.startswith("__CONVERGENCE__"):
                continue
            if is_rhombus_key(key):
                continue
            chosen_key = key
            break
        if chosen_key is None:
            unannotated.append(f"ruta {block.number}")
            continue
        ruta_value = str(block.number)
        if chosen_key in annotations:
            if annotations[chosen_key] != ruta_value:
                conflicts.append(
                    f"celda {chosen_key}: se conserva ruta={annotations[chosen_key]} (ignora {ruta_value})"
                )
            continue
        annotations[chosen_key] = ruta_value
    return annotations, unannotated, conflicts


def write_annotated_drawio(
    input_path: Path,
    output_path: Path,
    annotations: dict[str, str],
) -> tuple[int, list[str]]:
    """v4.7: escribe el .drawio con las celdas anotadas (idempotente)."""
    tree = ET.parse(input_path)
    diagrams = tree.getroot().findall("./diagram")
    if not diagrams:
        return 0, list(annotations)
    model = diagrams[0].find("./mxGraphModel")
    graph_root = model.find("./root") if model is not None else None
    if graph_root is None:
        return 0, list(annotations)

    for elem in graph_root.iter():
        if elem.tag in ("object", "UserObject"):
            elem.attrib.pop("ruta", None)

    elem_by_id: dict[str, tuple[ET.Element, bool]] = {}
    for child in list(graph_root):
        if child.tag == "mxCell" and child.get("id"):
            elem_by_id[child.get("id")] = (child, False)
        elif child.tag in ("object", "UserObject") and child.get("id"):
            elem_by_id[child.get("id")] = (child, True)

    annotated = 0
    missing: list[str] = []
    children_list = list(graph_root)
    for cell_id, ruta_value in annotations.items():
        entry = elem_by_id.get(cell_id)
        if entry is None:
            missing.append(cell_id)
            continue
        elem, already_wrapped = entry
        if already_wrapped:
            elem.set("ruta", ruta_value)
        else:
            obj = ET.Element("object")
            obj.set("label", elem.get("value", ""))
            obj.set("ruta", ruta_value)
            obj.set("id", cell_id)
            elem.attrib.pop("id", None)
            elem.attrib.pop("value", None)
            obj.tail = elem.tail
            elem.tail = None
            idx = children_list.index(elem)
            graph_root.remove(elem)
            obj.append(elem)
            graph_root.insert(idx, obj)
            children_list[idx] = obj
        annotated += 1

    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return annotated, missing


def render_blocks_txt(blocks: list[RouteBlock]) -> str:
    chunks: list[str] = []
    for block in blocks:
        piece: list[str] = [f"RUTA {block.number}"]
        if block.subtitle:
            piece.extend(block.subtitle.splitlines())
        piece.extend(block.lines)
        chunks.append("\n".join(piece))
    text = "\n\n".join(chunks)
    if chunks:
        text += "\n\n"
    return text


def postprocess_txt(text: str) -> str:
    return re.sub(r"^[ \t]+", "", text, flags=re.MULTILINE)


def write_json(
    output_path: Path,
    input_path: Path,
    diagram_name: str,
    diagram_id: str,
    route_blocks: list[RouteBlock],
) -> None:
    output = {
        "version": "4.7",
        "input": input_path.name,
        "page": {
            "number": 1,
            "name": diagram_name,
            "id": diagram_id,
            "scope": "first_diagram_only",
        },
        "rutas": [
            {
                "ruta": block.number,
                "subT": block.subtitle,
                "renglones": {
                    f"renglon{index}": line
                    for index, line in enumerate(block.lines, start=1)
                },
            }
            for block in route_blocks
        ],
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ======================================================================
# v4.8-v4.12: PASO OPCIONAL - sufijos " & N,M" en recordatorio.txt
# ======================================================================

UMBRAL_CLAVES = 0.6       # puntaje minimo para asignar una ruta
MARGEN = 0.1              # distancia maxima admitida al mejor puntaje
MAX_RUTAS = 3             # maximo de rutas por tarjeta
PESO_PREGUNTA = 0.25      # peso de las palabras del "primero" (senal debil)
TOPIC_SIMILARIDAD = 0.8   # similitud minima subT <-> tema de ruta (0..1)
MIN_LARGO_CLAVE = 2       # v4.12: antes 3; permite tokens fusionados (ad, da, KD, Ki)
SIMILITUD_CLAVE = 0.85    # matching tolerante normal
SIMILITUD_CLAVE_UNICA = 0.83  # v4.12: antes 0.75; relajado SOLO si la pista tiene 1 clave
UMBRAL_AVISO_ANCLA = 0.25 # aviso si otra ruta supera a la anclada por mas de esto

STOPWORDS = frozenset({
    "que", "como", "cual", "cuales", "cuando", "donde", "cuanto", "por",
    "para", "con", "sin", "las", "los", "del", "al", "una", "unos",
    "unas", "sus", "su", "es", "son", "ser", "esta", "este", "esto",
    "estos", "estas", "hay", "mas", "muy", "the", "and", "de", "la",
    "el", "en", "un", "y", "o", "se", "lo", "no", "si", "le", "les",
    "haya", "sea", "fue", "tiene", "tienen", "hace", "hacen", "porque",
    "entre", "sobre", "desde", "hasta", "segun", "todo", "toda", "todos",
    "todas", "otro", "otra", "otros", "otras", "cada", "puede", "pueden",
    # v4.12: palabras functionales de 2 letras (MIN_LARGO_CLAVE ahora es 2;
    # 'da' NO es stopword: es el token fusionado de D/A)
    "me", "te", "ni", "mi", "tu", "yo", "ha", "he", "va", "ve", "vi",
    "ya", "ah", "ay", "eh", "oh",
})

# Lectura de sufijo: "&" + numeros separados por coma; tolera espacios
# finales extra (ej. "& 39 "). Escritura canonica: " & 5" / " & 21, 22".
SUFIJO_RUTAS_RE = re.compile(r"(?:^|\s)&\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*$")


def normalizar_texto(texto: str) -> str:
    """Minusculas, sin acentos, solo alfanumericos, espacios colapsados.
    v4.12: fusiona '/' y '-' entre alfanumericos ANTES de limpiar
    (A/D -> ad, D/A -> da, on-off -> onoff, t1/t2 -> t1t2)."""
    s = unicodedata.normalize("NFD", (texto or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"(?<=[0-9a-z])[/\-](?=[0-9a-z])", "", s)
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extraer_claves(texto: str) -> list[str]:
    """Claves: minusculas, sin acentos, sin duplicados. v4.12: descarta
    numeros solos ('14', '2') y mantiene tokens de 2 letras (ad, da, kd)."""
    claves: list[str] = []
    for palabra in normalizar_texto(texto).split():
        if palabra.isdigit():
            continue
        if len(palabra) < MIN_LARGO_CLAVE or palabra in STOPWORDS:
            continue
        if palabra not in claves:
            claves.append(palabra)
    return claves


def clave_presente(clave: str, texto_norm: str, palabras_ruta: frozenset[str],
                   umbral: Optional[float] = None) -> bool:
    """La clave aparece por subcadena (>=3 letras) o por palabra similar.
    v4.12: claves de MENOS de 3 letras solo por palabra EXACTA o similitud
    (evita que 'da' calce dentro de 'entrada'). 'umbral' relaja la
    similitud (y el guardia de longitud) para pistas de una sola clave."""
    if umbral is None:
        umbral = SIMILITUD_CLAVE
    if len(clave) >= 3 and clave in texto_norm:
        return True
    if len(clave) < 3 and clave in palabras_ruta:
        return True
    if umbral < SIMILITUD_CLAVE:      # modo relajado: guardia de longitud mas amplia
        limite_len = len(clave) // 3 + 2
    else:
        limite_len = len(clave) // 4 + 1
    for palabra in palabras_ruta:
        if abs(len(palabra) - len(clave)) <= limite_len and \
           difflib.SequenceMatcher(None, clave, palabra).ratio() >= umbral:
            return True
    return False


def temas_compatibles(tema_a: Optional[str], tema_b: Optional[str]) -> bool:
    """Comparacion tolerante de temas normalizados."""
    if not tema_a or not tema_b:
        return False
    if tema_a == tema_b:
        return True
    return difflib.SequenceMatcher(None, tema_a, tema_b).ratio() >= TOPIC_SIMILARIDAD


def construir_temas_rutas(route_blocks: list[RouteBlock],
                          temas_subt: list[str]) -> dict[int, frozenset[str]]:
    """Numero de ruta -> CONJUNTO de temas normalizados.
    v4.13 FIX: el orden era la causa de un bug — la herencia (para rutas
    sin tema propio) corria ANTES del reemplazo por renglones y usaba los
    subtítulos crudos, asi que una ruta sin subtitle heredaba el tema del
    rombo ANTERIOR aunque el vecino ya hubiera recuperado (via renglones)
    el tema del rombo nuevo. Ahora: (1) temas del subtitle, (2) temas por
    renglones REEMPLAZAN (FIX 1), (3) herencia adelante/atrás usando los
    temas FINALES de los vecinos."""
    def temas_de_subtitle(subtitle: Optional[str]) -> list[str]:
        out: list[str] = []
        for linea in (subtitle or "").splitlines():
            t = normalizar_texto(linea)
            if t and t not in out:
                out.append(t)
        return out

    n = len(route_blocks)
    propios: list[list[str]] = [temas_de_subtitle(b.subtitle) for b in route_blocks]

    # (2) renglones que coinciden con algun subT REEMPLAZAN el tema propio
    if temas_subt:
        for i, bloque in enumerate(route_blocks):
            linea_temas: list[str] = []
            for linea in bloque.lines:
                if IMAGE_LINE_RE.match(linea) or linea.startswith("IMG:"):
                    continue
                t = normalizar_texto(linea)
                if not t or t in linea_temas:
                    continue
                for ts in temas_subt:
                    if ts == t or difflib.SequenceMatcher(None, ts, t).ratio() >= TOPIC_SIMILARIDAD:
                        linea_temas.append(t)
                        break
            if linea_temas:
                propios[i] = linea_temas

    # (3) herencia SOLO para rutas sin tema propio, con temas finales
    temas_por_bloque: list[list[str]] = [list(t) for t in propios]
    for i in range(n):
        if temas_por_bloque[i]:
            continue
        heredado: Optional[list[str]] = None
        for j in range(i + 1, n):            # hacia ADELANTE
            if propios[j]:
                heredado = propios[j]
                break
        if heredado is None:
            for j in range(i - 1, -1, -1):   # hacia atras
                if propios[j]:
                    heredado = propios[j]
                    break
        temas_por_bloque[i] = list(heredado) if heredado else []

    return {b.number: frozenset(t) for b, t in zip(route_blocks, temas_por_bloque)}


@dataclass
class Tarjeta:
    indice: int                          # indice de la linea en el archivo
    linea: str                           # linea original exacta
    primero: str
    segundo: str
    tema: Optional[str]                  # subT vigente (normalizado) al leerla
    rutas_manual: Optional[list[int]]    # sufijo ya escrito a mano (None si no trae)
    malformada: bool = False             # contiene '&' sin numeros
    reinicia: bool = False               # primera tarjeta tras un subT
    rutas_asignadas: Optional[list[int]] = None  # resultado (None = sin cambio)


def parsear_recordatorio(texto: str) -> tuple[list[str], list[Tarjeta], list[str]]:
    """Clasifica cada linea. Las vacias y las subT se conservan tal cual."""
    lineas = texto.split("\n")
    tarjetas: list[Tarjeta] = []
    temas_subt: list[str] = []
    tema_actual: Optional[str] = None
    reinicia_pendiente = False
    for indice, linea in enumerate(lineas):
        limpio = linea.strip()
        if not limpio:
            continue                                   # vacia: intacta
        m_subt = re.match(r"(?i)^subt\b\s*(.*)$", limpio)
        if m_subt:
            tema = normalizar_texto(m_subt.group(1)) or None
            if tema:
                temas_subt.append(tema)
            tema_actual = tema
            reinicia_pendiente = True
            continue                                   # subT: intacta
        cuerpo = linea
        rutas_manual: Optional[list[int]] = None
        malformada = False
        m = SUFIJO_RUTAS_RE.search(linea)
        if m:
            rutas_manual = [int(x) for x in re.split(r"\s*,\s*", m.group(1))]
            cuerpo = linea[:m.start()]
        elif "&" in linea:
            malformada = True
        if " + " in cuerpo:
            primero, segundo = cuerpo.rsplit(" + ", 1)
        else:
            primero, segundo = cuerpo, ""
        tarjetas.append(Tarjeta(
            indice=indice, linea=linea,
            primero=primero.strip(), segundo=segundo.strip(),
            tema=tema_actual, rutas_manual=rutas_manual,
            malformada=malformada, reinicia=reinicia_pendiente,
        ))
        reinicia_pendiente = False
    return lineas, tarjetas, temas_subt


def evaluar_ruta(claves_pista: list[str], claves_pregunta: list[str],
                 texto_norm: str, palabras_ruta: frozenset[str]) -> tuple[float, frozenset[str]]:
    """Devuelve (puntaje, claves_coincidentes). El puntaje es la fraccion
    de claves de la pista (senal fuerte; umbral relajado si hay una sola)
    mas el aporte debil de la pregunta. El conjunto de claves coincidentes
    alimenta la seleccion por cobertura (v4.12 FIX 4)."""
    umbral_pista = SIMILITUD_CLAVE_UNICA if len(claves_pista) == 1 else SIMILITUD_CLAVE
    mp = {c for c in claves_pista if clave_presente(c, texto_norm, palabras_ruta, umbral_pista)}
    mq = {c for c in claves_pregunta if clave_presente(c, texto_norm, palabras_ruta, SIMILITUD_CLAVE)}
    frac_pista = len(mp) / len(claves_pista) if claves_pista else 0.0
    frac_preg = len(mq) / len(claves_pregunta) if claves_pregunta else 0.0
    return frac_pista + PESO_PREGUNTA * frac_preg, frozenset(mp | mq)


def procesar_recordatorio(recordatorio_path: Path, route_blocks: list[RouteBlock]) -> None:
    """Asocia tarjetas del recordatorio con route_blocks en memoria y
    escribe el sufijo " & N,M" (orden ascendente). Sobreescribe SOLO si
    hay cambios. Sin .bak. En consola solo se listan las pendientes."""
    try:
        texto = recordatorio_path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"No se pudo leer el recordatorio: {exc}")
        return
    if not route_blocks:
        print("No hay rutas en memoria; paso del recordatorio omitido.")
        return

    lineas, tarjetas, temas_subt = parsear_recordatorio(texto)
    if not tarjetas:
        print("El recordatorio no contiene tarjetas; no se modifica nada.")
        return

    temas_ruta = construir_temas_rutas(route_blocks, temas_subt)

    # renglones de imagen excluidos del texto de cada ruta (v4.9)
    textos_ruta: dict[int, str] = {}
    palabras_ruta: dict[int, frozenset[str]] = {}
    for b in route_blocks:
        renglones = [ln for ln in b.lines
                     if not IMAGE_LINE_RE.match(ln) and not ln.startswith("IMG:")]
        norm = normalizar_texto("\n".join(renglones))
        textos_ruta[b.number] = norm
        palabras_ruta[b.number] = frozenset(norm.split())
    numeros_ruta = [b.number for b in route_blocks]

    def tema_compatible_con(tar: Tarjeta, n: int) -> bool:
        if tar.tema is None:
            return True
        return any(temas_compatibles(tar.tema, t) for t in temas_ruta.get(n, frozenset()))

    ultima_ruta: Optional[int] = None   # rango permitido: >= ultima (misma subT)
    cambios = 0
    revision: list[tuple[int, str, str, list[tuple[int, float]]]] = []

    for tar in tarjetas:
        if tar.reinicia:
            ultima_ruta = None         # cada subT reinicia el rango de orden

        if tar.rutas_manual is not None:
            # --- validacion de anclas: SOLO avisa, nunca modifica ---
            inexistentes = [n for n in tar.rutas_manual if n not in textos_ruta]
            claves_pista = extraer_claves(tar.segundo)
            if inexistentes:
                revision.append((tar.indice, tar.linea,
                                 f"ancla apunta a rutas inexistentes: {inexistentes}", []))
            elif claves_pista:
                claves_pregunta = extraer_claves(tar.primero)
                base = {n for n in numeros_ruta if tema_compatible_con(tar, n)}
                base |= set(tar.rutas_manual)
                puntajes_val = {n: evaluar_ruta(claves_pista, claves_pregunta,
                                                textos_ruta[n], palabras_ruta[n])[0]
                                for n in sorted(base)}
                s_anch = max(puntajes_val[n] for n in tar.rutas_manual)
                mejor_p = max(puntajes_val.values())
                mejor_n = min(n for n, p in puntajes_val.items() if p == mejor_p)
                if mejor_n not in tar.rutas_manual and (mejor_p - s_anch) > UMBRAL_AVISO_ANCLA:
                    revision.append((
                        tar.indice, tar.linea,
                        f"ancla & {', '.join(map(str, tar.rutas_manual))}: "
                        f"la ruta {mejor_n} coincide mejor ({mejor_p:.2f} vs {s_anch:.2f})",
                        []))
            ultima_ruta = max(tar.rutas_manual)
            continue

        if tar.malformada:
            revision.append((tar.indice, tar.linea, "contiene '&' sin numeros", []))
            continue

        claves_pista = extraer_claves(tar.segundo)
        if not claves_pista:
            revision.append((tar.indice, tar.linea, "la pista no produce claves utiles", []))
            continue
        claves_pregunta = extraer_claves(tar.primero)

        candidatas = [
            n for n in numeros_ruta
            if tema_compatible_con(tar, n)
            and (ultima_ruta is None or n >= ultima_ruta)
        ]
        puntajes: list[tuple[int, float]] = []
        coincidentes: dict[int, frozenset[str]] = {}
        for n in candidatas:
            p, m = evaluar_ruta(claves_pista, claves_pregunta, textos_ruta[n], palabras_ruta[n])
            puntajes.append((n, p))
            coincidentes[n] = m
        if not puntajes:
            revision.append((tar.indice, tar.linea,
                             "sin rutas candidatas para ese tema/orden", []))
            continue

        mejor = max(p for _n, p in puntajes)
        calificadas = [(n, p) for n, p in puntajes
                       if p >= UMBRAL_CLAVES and p >= mejor - MARGEN]
        calificadas.sort(key=lambda t: (-t[1], t[0]))

        if not calificadas:
            top = sorted(puntajes, key=lambda t: (-t[1], t[0]))[:5]
            revision.append((tar.indice, tar.linea,
                             "ninguna ruta permitida supera UMBRAL_CLAVES", top))
            continue

        # v4.12 FIX 4: seleccion por cobertura. Una ruta calificada solo
        # se conserva si aporta al menos una clave coincidente que ninguna
        # de las ya elegidas cubra (empates exactos por claves identicas
        # no agregan informacion).
        filtradas: list[int] = []
        cubiertas: set[str] = set()
        for n, _p in calificadas:
            m = coincidentes[n]
            if m - cubiertas:
                filtradas.append(n)
                cubiertas |= m

        if len(filtradas) > MAX_RUTAS:               # empate confuso
            revision.append((tar.indice, tar.linea,
                             f"empate confuso: {len(filtradas)} rutas dentro del margen",
                             calificadas[:MAX_RUTAS + 2]))
            continue

        elegidas = sorted(filtradas[:MAX_RUTAS])     # orden ascendente
        tar.rutas_asignadas = elegidas
        ultima_ruta = max(elegidas)
        cambios += 1

    # --- resumen en consola: solo tarjetas pendientes de asociar ---
    con_manual = sum(1 for t in tarjetas if t.rutas_manual is not None)
    print(f"recordatorio: {cambios} de {len(tarjetas)} tarjetas asociadas "
          f"({con_manual} ya tenian sufijo y se respetaron)")
    if revision:
        print(f"tarjetas a revisar ({len(revision)}):")
        for indice, linea, motivo, cands in revision:
            print(f"  linea {indice + 1}: {linea.strip()}")
            print(f"    motivo: {motivo}")
            if cands:
                print("    candidatas: "
                      + ", ".join(f"ruta {n} ({p:.2f})" for n, p in cands))
    else:
        print("no hay tarjetas a revisar")

    if cambios == 0:
        print("sin cambios que hacer: el recordatorio no se toco.")
        return

    nuevas = list(lineas)
    for tar in tarjetas:
        if tar.rutas_asignadas:  # formato exacto: espacio + & + espacio + "N, M"
            sufijo = " & " + ", ".join(str(n) for n in tar.rutas_asignadas)
            nuevas[tar.indice] = tar.linea.rstrip() + sufijo

    # --- copia de seguridad desactivada: no se crea ningun .bak ---
    # bak_path = recordatorio_path.parent / (recordatorio_path.name + ".bak")
    # if bak_path.exists():
    #     print(f"aviso: {bak_path.name} ya existe y se conserva intacto.")
    # else:
    #     shutil.copyfile(recordatorio_path, bak_path)
    #     print(f"copia de seguridad creada: {bak_path.name}")

    with open(recordatorio_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(nuevas))
    print(f"recordatorio actualizado: {recordatorio_path.name}")


OUTPUT_DIR = Path(__file__).resolve().parent / "guardados" / "rutas"
def main() -> None:
    root = tk.Tk()
    root.withdraw()

    input = filedialog.askopenfilename(
        title="Selecciona el archivo .drawio",
        filetypes=[("Archivos Drawio", "*.drawio"), ("Todos los archivos", "*.*")]
    )

    if not input:
        print("No se seleccionó ningún archivo. Saliendo...")
        return

    input_path = Path(input)

    output_dir = OUTPUT_DIR if OUTPUT_DIR is not None else Path(__file__).parent.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    txt_path = output_dir / f"{input_path.stem}.txt"
    #json_path = output_dir / f"{input_path.stem}.json"

    vertices, styles, edges, graph, diagram_name, diagram_id, orphan_ids = parse_first_page(input_path)
    roots = find_roots(vertices, edges)
    leaves = find_leaves(graph)
    original_paths, chain_marks = find_paths_iterative(graph, roots, set(leaves), styles)

    convergence_map = build_convergence_map(vertices, edges)
    transformed_paths, convergence_values, convergence_metadata = apply_convergences(
        original_paths, convergence_map
    )

    trie_values = {**convergence_values, **vertices}
    is_rhombus_key = make_is_rhombus_lookup(styles)

    # v4.10: dedup de caminos que se bifurcan y vuelven a juntar.
    if DEDUPLICAR_CONVERGENCIAS:
        transformed_paths, fusiones_conv, alternativas_conv = deduplicar_convergencias(
            transformed_paths, trie_values, is_rhombus_key
        )
        if fusiones_conv or alternativas_conv:
            print(f"convergencias: {fusiones_conv} camino(s) duplicado(s) fusionado(s), "
                  f"{alternativas_conv} alternativa(s) de rombo conservada(s) como ruta propia")

    trie = build_trie(transformed_paths, trie_values, vertices, chain_marks)
    route_blocks = build_route_blocks(trie, is_rhombus_key)

    for block in route_blocks:
        block.lines = [with_image_prefix(line) for line in block.lines]

    raw_txt = render_blocks_txt(route_blocks)
    final_txt = postprocess_txt(raw_txt)
    txt_path.write_text(final_txt, encoding="utf-8")

    #write_json(json_path, input_path, diagram_name, diagram_id, route_blocks)

    annotations, unannotated, conflicts = collect_ruta_annotations(route_blocks, is_rhombus_key)
    if annotations:
        annotated_count, missing_cells = write_annotated_drawio(input_path, input_path, annotations)
        print(f"drawio actualizado en su lugar: {annotated_count} celdas con dato 'ruta'")
        if missing_cells:
            print(f"celdas no encontradas en la Hoja 1: {missing_cells}")
    else:
        print("sin celdas para anotar con dato 'ruta' (el original no se modifico)")
    if unannotated:
        print(f"rutas sin celda anotable (solo rombos/sinteticos): {', '.join(unannotated)}")
    if conflicts:
        print("conflictos de anotacion: " + "; ".join(conflicts))

    image_nodes = sum(1 for v in vertices.values() if IMAGE_LINE_RE.match(v))
    print(f"archivos txt y json generados, rutas, {image_nodes} imagenes en el grafo)")

    # --- paso OPCIONAL recordatorio.txt -> sufijos " & N,M" ---
    recordatorio = filedialog.askopenfilename(
        title="Selecciona el recordatorio.txt (opcional; cancela para omitir)",
        filetypes=[("Archivos de texto", "*.txt"), ("Todos los archivos", "*.*")],
    )
    if recordatorio:
        procesar_recordatorio(Path(recordatorio), route_blocks)
    else:
        print("sin recordatorio seleccionado: paso omitido.")


if __name__ == "__main__":
    main()