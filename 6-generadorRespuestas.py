#!/usr/bin/env python3
from __future__ import annotations

import difflib
import html
import json
import math
import os
import re
import shutil  # v4.21: reactivado; se usa shutil.which para buscar draw.io en el PATH
import statistics
import subprocess  # v4.21: export de la Pagina 1 a jpg por linea de comandos
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
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
    return "rhombus" in lowered or "flowchart.decision" in lowered or ("gradientcolor=#" in lowered and "ellipse" in lowered) or "shape=offpageconnector" in lowered


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


def make_is_tema_lookup(styles: dict[str, str], roots: list[str]):
    raices = set(roots)

    def _is_tema_key(key: str) -> bool:
        s = styles.get(key, "").lower()
        return "hexagon" in s and "gradientcolor=#" in s

    return _is_tema_key


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


def conservar_ultimo_subt(bloque: RouteBlock, is_rhombus_key) -> None:
    pos = [i for i, (key, _l) in enumerate(bloque.items) if is_rhombus_key(key)]
    n_subt = (1 if bloque.subtitle else 0) + len(pos)
    if n_subt < 2 or not pos:
        return
    ultimo = "\n".join(bloque.items[pos[-1]][1]).strip()
    if not ultimo:
        return
    quitar = set(pos)
    bloque.subtitle = ultimo
    bloque.items = [it for i, it in enumerate(bloque.items) if i not in quitar]
    bloque.lines = [line for _key, item_lines in bloque.items for line in item_lines]


def build_route_blocks(trie_root: TrieNode, is_rhombus_key) -> list[RouteBlock]:
    blocks: list[RouteBlock] = []
    number = 1
    for root_child in trie_root.children.values():
        closed, open_items, _cont = _split_into_blocks(root_child, None, is_rhombus_key)
        tree_blocks = [(None, open_items)] + closed
        for subtitle, items in tree_blocks:
            lines = [line for _key, item_lines in items for line in item_lines]
            bloque = RouteBlock(number=number, subtitle=subtitle, lines=lines, items=items)
            conservar_ultimo_subt(bloque, is_rhombus_key)
            blocks.append(bloque)
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
    is_tema_key=None,
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
            if is_rhombus_key(key) or (is_tema_key is not None and is_tema_key(key)):
                continue        # v4.17: ni rombos (subT) ni hexagonos (temaS) reciben 'ruta'
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
    image_rutas: dict[str, str],
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

        # --- AGREGADO DE 'descripcion' Y 'ruta' A IMÁGENES ---
        image_ids = set()
        for cid, (elem, wrapped) in elem_by_id.items():
            if wrapped:
                inner = next((c for c in elem if c.tag == "mxCell"), None)
                style = inner.get("style", "") if inner is not None else ""
            else:
                style = elem.get("style", "")
            if IMAGE_URI_RE.search(style):
                image_ids.add(cid)

        children_list = list(graph_root)
        for img_id in image_ids:
            entry = elem_by_id.get(img_id)
            if not entry:
                continue
            elem, already_wrapped = entry
            
            # Obtener la ruta a la que pertenece esta imagen (si existe en el mapeo)
            ruta_img = image_rutas.get(img_id)
            
            if already_wrapped and elem.tag in ("object", "UserObject"):
                if "descripcion" not in elem.attrib:
                    elem.set("descripcion", "")
                if ruta_img:
                    elem.set("ruta", ruta_img)
            else:
                obj = ET.Element("object")
                obj.set("label", elem.get("value", ""))
                obj.set("descripcion", "")
                if ruta_img:
                    obj.set("ruta", ruta_img)
                obj.set("id", img_id)
                elem.attrib.pop("id", None)
                elem.attrib.pop("value", None)
                obj.tail = elem.tail
                elem.tail = None
                if elem in children_list:
                    idx = children_list.index(elem)
                    graph_root.remove(elem)
                    obj.append(elem)
                    graph_root.insert(idx, obj)
                    children_list[idx] = obj
            
            # Corrección: si ya estaba envuelta, 'elem' es el object; si no, 'obj' es el nuevo object
            elem_by_id[img_id] = (elem if already_wrapped else obj, True)
        # --- FIN AGREGADO ---

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
# v4.14
MAX_EXTRA_PREFIJO = 3     # clave 'red' ~ 'redes' (+2) pero no 'reducir' (+4)
PESO_IDF_MIN = 0.5        # peso de una clave presente en TODAS las rutas (max = 1.0)
PENALIZACION_LONGITUD_MAX = 0.08  # penalizacion maxima por ruta larga; mantener < MARGEN
UMBRAL_CLAVES_SIN_TEMA = 0.8      # umbral exigido cuando se ignora el tema (ultimo recurso)
EPS = 1e-9                # tolerancia para comparar puntajes con umbrales
# v4.15
UMBRAL_FUERA_ORDEN = 0.8  # fuera de orden: se asigna igual si el puntaje es >= esto
PUNTAJE_MIN_TENTATIVO = 0.0  # asignacion tentativa (~) solo si el mejor puntaje es > esto
# v4.16 (solo actuan sobre tarjetas que NO superan UMBRAL_CLAVES)
PUNTAJE_MIN_MARGEN = 0.5  # margen sobre la segunda: el mejor debe tener al menos esto...
MARGEN_GANADOR = 0.2      # ...y sacarle al menos esto a la siguiente candidata
# v4.18: rutas adicionales por cobertura contigua (solo tarjetas CON subT y 2-4 claves)
MAX_CLAVES_EXTRA = 4      # la pista debe tener entre 2 y este numero de claves
MAX_RUTAS_EXTRA = 4       # maximo de rutas de una tarjeta con este mecanismo
DF_MAX_EXTRA = 3          # una clave 'rara' aparece en a lo sumo estas rutas del diagrama
MAX_SEMILLAS = 6          # candidatas probadas como punto de partida de la cadena
MARCAR_EXTRAS = True      # ruta agregada a una asignacion firme: se escribe con '~'

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
# v4.15: un '~' tras el numero ("& 23~") marca asignacion tentativa a revisar.
# v4.20: el regex se aplica SOLO a la parte de la linea ANTES del primer '|'.
SUFIJO_RUTAS_RE = re.compile(r"(?:^|\s)&\s*([0-9]+~?(?:\s*,\s*[0-9]+~?)*)\s*$")


def normalizar_texto(texto: str) -> str:
    """Minusculas, sin acentos, solo alfanumericos, espacios colapsados.
    v4.12: fusiona '/' y '-' entre alfanumericos ANTES de limpiar
    (A/D -> ad, D/A -> da, on-off -> onoff, t1/t2 -> t1t2)."""
    s = unicodedata.normalize("NFD", (texto or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"(?<=[0-9a-z])[/\-](?=[0-9a-z])", "", s)
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=None)
def raiz(palabra: str) -> str:
    """v4.14 FIX 5: raiz ligera para unificar plural/singular (senales~senal,
    sensores~sensor, condiciones~condicion). Solo recorta si quedan >= 4
    letras. Se aplica igual a claves y a palabras de ruta, asi que la
    comparacion es simetrica."""
    for suf in ("ciones", "cion", "es", "s"):
        if palabra.endswith(suf) and len(palabra) - len(suf) >= 4:
            return palabra[:-len(suf)]
    return palabra


def normalizar_para_matching(texto: str) -> str:
    """normalizar_texto + raiz por palabra (solo para el asociador)."""
    return " ".join(raiz(p) for p in normalizar_texto(texto).split())


@lru_cache(maxsize=None)
def similares(a: str, b: str, umbral: float) -> bool:
    """v4.14 (velocidad): ratio >= umbral con cache y prefiltros baratos.
    real_quick_ratio y quick_ratio son cotas superiores de ratio, asi que el
    resultado es identico al de SequenceMatcher(None, a, b).ratio() >= umbral."""
    sm = difflib.SequenceMatcher(None, a, b)
    if sm.real_quick_ratio() < umbral or sm.quick_ratio() < umbral:
        return False
    return sm.ratio() >= umbral


def extraer_claves(texto: str) -> list[str]:
    """Claves: minusculas, sin acentos, sin duplicados. v4.12: descarta
    numeros solos ('14', '2') y mantiene tokens de 2 letras (ad, da, kd).
    v4.14: cada clave se reduce a su raiz (plural/singular)."""
    claves: list[str] = []
    for palabra in normalizar_texto(texto).split():
        if palabra.isdigit():
            continue
        if len(palabra) < MIN_LARGO_CLAVE or palabra in STOPWORDS:
            continue
        r = raiz(palabra)
        if r not in claves:
            claves.append(r)
    return claves


@dataclass
class RutaIndexada:
    """v4.14: palabras de una ruta ya normalizadas y con raiz (sin renglones IMG)."""
    numero: int
    palabras: frozenset[str]
    n_tokens: int


def indexar_ruta(bloque: RouteBlock) -> RutaIndexada:
    renglones = [ln for ln in bloque.lines
                 if not IMAGE_LINE_RE.match(ln) and not ln.startswith("IMG:")]
    tokens = normalizar_para_matching("\n".join(renglones)).split()
    return RutaIndexada(bloque.number, frozenset(tokens), len(tokens))


def _palabras_que_calzan(clave: str, palabras, por_largo: dict, umbral: float):
    if clave in palabras:                            # palabra exacta
        yield clave
    largo = len(clave)
    if largo >= 3:
        for extra in range(1, MAX_EXTRA_PREFIJO + 1):
            for palabra in por_largo.get(largo + extra, ()):
                if palabra.startswith(clave):
                    yield palabra
    if umbral < SIMILITUD_CLAVE:      # modo relajado: guardia de longitud mas amplia
        limite_len = largo // 3 + 2
    else:
        limite_len = largo // 4 + 1
    for l in range(max(1, largo - limite_len), largo + limite_len + 1):
        for palabra in por_largo.get(l, ()):
            if similares(clave, palabra, umbral):
                yield palabra


@lru_cache(maxsize=None)
def _tokens_tema(tema: str) -> frozenset[str]:
    return frozenset(raiz(w) for w in tema.split() if w not in STOPWORDS)


def temas_compatibles(tema_a: Optional[str], tema_b: Optional[str],
                      contencion: bool = False) -> bool:
    if not tema_a or not tema_b:
        return False
    if tema_a == tema_b:
        return True
    if similares(tema_a, tema_b, TOPIC_SIMILARIDAD):
        return True
    if not contencion:
        return False
    ta, tb = _tokens_tema(tema_a), _tokens_tema(tema_b)
    if ta and tb:
        menor, mayor = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        return menor <= mayor
    return False


def construir_temas_rutas(route_blocks: list[RouteBlock],
                          temas_subt: list[str]) -> dict[int, frozenset[str]]:
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
                    if ts == t or similares(ts, t, TOPIC_SIMILARIDAD):
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
    linea: str                           # v4.20: renglon SIN nota (lo que queda antes del primer '|')
    primero: str
    segundo: str
    tema: Optional[str]                  # subT vigente (normalizado) al leerla
    rutas_manual: Optional[list[int]]    # sufijo ya escrito a mano (None si no trae)
    malformada: bool = False             # contiene '&' sin numeros
    reinicia: bool = False               # primera tarjeta tras un subT
    nota: str = ""                       # v4.20: apunte tras el primer '|'; se conserva, no se usa
    rutas_asignadas: Optional[list[int]] = None  # resultado (None = sin cambio)
    manual_tentativa: bool = False       # el sufijo existente lleva '~' (por revisar)
    marcadas: set[int] = field(default_factory=set)   # rutas nuevas que se escriben con '~'
    manual_firmes: list[int] = field(default_factory=list)  # rutas del sufijo existente SIN '~'


def parsear_recordatorio(texto: str) -> tuple[list[str], list[Tarjeta], list[str]]:
    lineas = texto.split("\n")
    tarjetas: list[Tarjeta] = []
    temas_subt: list[str] = []
    tema_actual: Optional[str] = None
    reinicia_pendiente = False
    for indice, linea in enumerate(lineas):
        limpio = linea.strip()
        if not limpio:
            continue                                   # vacia: intacta
        # v4.20: corte en el primer '|' antes de cualquier otro analisis.
        nota = ""
        renglon = limpio
        if "|" in renglon:
            renglon, nota = renglon.split("|", 1)
            renglon = renglon.strip()
            nota = nota.strip()
            if not renglon:
                continue                               # linea que empezaba con '|': se ignora
        m_subt = re.match(r"(?i)^subt\b\s*(.*)$", renglon)
        if m_subt:
            tema = normalizar_texto(m_subt.group(1)) or None
            if tema:
                temas_subt.append(tema)
            tema_actual = tema
            reinicia_pendiente = True
            continue                                   # subT: intacta
        cuerpo = renglon
        rutas_manual: Optional[list[int]] = None
        manual_tentativa = False
        manual_firmes: list[int] = []
        malformada = False
        m = SUFIJO_RUTAS_RE.search(renglon)
        if m:
            pares = re.findall(r"([0-9]+)(~?)", m.group(1))
            rutas_manual = [int(n) for n, _t in pares]
            manual_firmes = [int(n) for n, t in pares if not t]
            manual_tentativa = not manual_firmes      # todas con '~' -> por revisar
            cuerpo = renglon[:m.start()]
        elif "&" in renglon:
            malformada = True
        if " + " in cuerpo:
            primero, segundo = cuerpo.rsplit(" + ", 1)
        else:
            primero, segundo = cuerpo, ""
        tarjetas.append(Tarjeta(
            indice=indice, linea=renglon, nota=nota,
            primero=primero.strip(), segundo=segundo.strip(),
            tema=tema_actual, rutas_manual=rutas_manual,
            malformada=malformada, reinicia=reinicia_pendiente,
            manual_tentativa=manual_tentativa, manual_firmes=manual_firmes,
        ))
        reinicia_pendiente = False
    return lineas, tarjetas, temas_subt


class Puntuador:
    def __init__(self, route_blocks: list[RouteBlock]) -> None:
        self.rutas: dict[int, RutaIndexada] = {b.number: indexar_ruta(b) for b in route_blocks}
        self.n_rutas = len(self.rutas)
        self.largo_ref = max(1.0, float(statistics.median(
            max(1, r.n_tokens) for r in self.rutas.values())))
        self._idf_ref = math.log((self.n_rutas + 1) / 1.5)
        self._pesos: dict[str, float] = {}
        self._dfs: dict[str, int] = {}
        # vocabulario global (palabra -> rutas que la contienen). Para cada clave
        # se calcula UNA vez que palabras del vocabulario calzan; despues, saber
        # si una ruta la contiene es una interseccion de conjuntos (sin fuzzy).
        self._rutas_de: dict[str, set[int]] = defaultdict(set)
        for r in self.rutas.values():
            for w in r.palabras:
                self._rutas_de[w].add(r.numero)
        self._vocab = frozenset(self._rutas_de)
        por_largo: dict[int, list[str]] = defaultdict(list)
        for w in self._vocab:
            por_largo[len(w)].append(w)
        self._vocab_por_largo = {k: tuple(v) for k, v in por_largo.items()}
        self._calzan: dict[tuple[str, float], frozenset[str]] = {}

    def calzan(self, clave: str, umbral: float) -> frozenset[str]:
        llave = (clave, umbral)
        r = self._calzan.get(llave)
        if r is None:
            r = frozenset(_palabras_que_calzan(clave, self._vocab, self._vocab_por_largo, umbral))
            self._calzan[llave] = r
        return r

    def existe(self, clave: str) -> bool:
        """La clave aparece en al menos una ruta del diagrama."""
        return bool(self.calzan(clave, SIMILITUD_CLAVE))

    def presente(self, clave: str, ruta: RutaIndexada, umbral: float) -> bool:
        return not self.calzan(clave, umbral).isdisjoint(ruta.palabras)

    def df(self, clave: str) -> int:
        """En cuantas rutas del diagrama aparece la clave."""
        d = self._dfs.get(clave)
        if d is None:
            con_clave: set[int] = set()
            for w in self.calzan(clave, SIMILITUD_CLAVE):
                con_clave |= self._rutas_de[w]
            d = len(con_clave)
            self._dfs[clave] = d
        return d

    def peso(self, clave: str) -> float:
        p = self._pesos.get(clave)
        if p is None:
            df = self.df(clave)
            if df == 0:
                p = (1.0 + PESO_IDF_MIN) / 2.0
            else:
                idf = math.log((self.n_rutas + 1) / (df + 0.5))
                rel = min(1.0, max(0.0, idf / self._idf_ref))
                p = PESO_IDF_MIN + (1.0 - PESO_IDF_MIN) * rel
            self._pesos[clave] = p
        return p

    def factor_longitud(self, ruta: RutaIndexada) -> float:
        n = max(1, ruta.n_tokens)
        if n <= self.largo_ref:
            return 1.0
        return 1.0 - PENALIZACION_LONGITUD_MAX * (1.0 - self.largo_ref / n)

    def _fraccion(self, claves: list[str], presentes: set[str]) -> float:
        if not claves:
            return 0.0
        total = sum(self.peso(c) for c in claves)
        return sum(self.peso(c) for c in presentes) / total

    def evaluar(self, claves_pista: list[str], claves_pregunta: list[str],
                numero: int) -> tuple[float, frozenset[str]]:
        ruta = self.rutas[numero]
        umbral_pista = SIMILITUD_CLAVE_UNICA if len(claves_pista) == 1 else SIMILITUD_CLAVE
        mp = {c for c in claves_pista if self.presente(c, ruta, umbral_pista)}
        mq = {c for c in claves_pregunta if self.presente(c, ruta, SIMILITUD_CLAVE)}
        puntaje = (self._fraccion(claves_pista, mp)
                   + PESO_PREGUNTA * self._fraccion(claves_pregunta, mq))
        return puntaje * self.factor_longitud(ruta), frozenset(mp)


def _puntuar_rutas(puntuador: Puntuador, claves_pista: list[str],
                   claves_pregunta: list[str], rutas: list[int]
                   ) -> tuple[list[tuple[int, float]], dict[int, frozenset[str]]]:
    puntajes: list[tuple[int, float]] = []
    coincidentes: dict[int, frozenset[str]] = {}
    for n in rutas:
        p, m = puntuador.evaluar(claves_pista, claves_pregunta, n)
        puntajes.append((n, p))
        coincidentes[n] = m
    return puntajes, coincidentes


def _buscar_vecina(puntuador: Puntuador, claves_pista: list[str], claves_pregunta: list[str],
                   n0: int, mp0: frozenset[str], permitidas: set[int]) -> Optional[int]:
    if len(claves_pista) < 2 or not mp0:
        return None
    faltan = {c for c in claves_pista if c not in mp0 and puntuador.existe(c)}
    if not faltan:
        return None
    mejor: Optional[tuple[float, int]] = None
    for v in (n0 + 1, n0 - 1):                 # a igualdad de puntaje, gana la siguiente
        if v not in permitidas:
            continue
        p, mp = puntuador.evaluar(claves_pista, claves_pregunta, v)
        if faltan <= mp and (mejor is None or p > mejor[0] + EPS):
            mejor = (p, v)
    return mejor[1] if mejor else None


def _extender_contiguas(puntuador: Puntuador, claves_pista: list[str], claves_pregunta: list[str],
                        base: list[int], permitidas: set[int], max_rutas: int
                        ) -> tuple[list[int], set[str]]:
    raras = {c for c in claves_pista if puntuador.existe(c) and puntuador.df(c) <= DF_MAX_EXTRA}
    memo: dict[int, frozenset[str]] = {}

    def cubre(n: int) -> frozenset[str]:
        if n not in memo:
            memo[n] = puntuador.evaluar(claves_pista, claves_pregunta, n)[1]
        return memo[n]

    cadena = sorted(set(base))
    faltan = set(raras)
    for n in cadena:
        faltan -= cubre(n)
    while faltan and len(cadena) < max_rutas:
        mejor: Optional[tuple[tuple[float, int], int, set[str]]] = None
        for n in cadena:
            for v in (n + 1, n - 1):
                if v in cadena or v not in permitidas:
                    continue
                aporta = faltan & cubre(v)
                if not aporta:
                    continue
                orden = (sum(puntuador.peso(c) for c in aporta), v)   # mas peso; luego la siguiente
                if mejor is None or orden > mejor[0]:
                    mejor = (orden, v, aporta)
        if mejor is None:
            break
        cadena = sorted(cadena + [mejor[1]])
        faltan -= mejor[2]
    return cadena, faltan


def _mejor_ventana(puntuador: Puntuador, claves_pista: list[str], claves_pregunta: list[str],
                   semillas: list[tuple[int, float]], permitidas: set[int]) -> Optional[list[int]]:
    mejor: Optional[tuple[tuple[int, float, int], list[int]]] = None
    for n0, p0 in semillas:
        cadena, faltan = _extender_contiguas(puntuador, claves_pista, claves_pregunta,
                                             [n0], permitidas, MAX_RUTAS_EXTRA)
        if len(cadena) < 2 or faltan:
            continue
        clave = (-len(cadena), p0, -n0)
        if mejor is None or clave > mejor[0]:
            mejor = (clave, cadena)
    return mejor[1] if mejor else None


def _calificar(puntajes: list[tuple[int, float]], umbral: float) -> list[tuple[int, float]]:
    if not puntajes:
        return []
    mejor = max(p for _n, p in puntajes)
    cal = [(n, p) for n, p in puntajes if p >= umbral - EPS and p >= mejor - MARGEN]
    cal.sort(key=lambda t: (-t[1], t[0]))
    return cal


def procesar_recordatorio(recordatorio_path: Path,
                          route_blocks: list[RouteBlock]) -> Optional[dict[int, list[str]]]:
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

    # v4.14: rutas indexadas (sin renglones IMG) + pesos IDF + factor de longitud
    puntuador = Puntuador(route_blocks)
    numeros_ruta = [b.number for b in route_blocks]

    todos_temas = frozenset(t for ts in temas_ruta.values() for t in ts)
    _conocidos: dict[str, bool] = {}

    def tema_conocido(tema: str) -> bool:
        r = _conocidos.get(tema)
        if r is None:
            r = any(temas_compatibles(tema, t) for t in todos_temas)
            _conocidos[tema] = r
        return r

    def tema_compatible_con(tar: Tarjeta, n: int) -> bool:
        if tar.tema is None:
            return True
        contencion = not tema_conocido(tar.tema)   # v4.16: contencion = ultimo recurso
        return any(temas_compatibles(tar.tema, t, contencion)
                   for t in temas_ruta.get(n, frozenset()))

    ultima_ruta: Optional[int] = None   # rango permitido: >= ultima (misma subT)
    linea_ultima: Optional[int] = None  # tarjeta que fijo ultima_ruta (para diagnostico)
    cambios = 0
    revision: list[tuple[int, str, str, list[tuple[int, float]]]] = []
    avisos: list[tuple[int, str, str]] = []
    tentativas: list[tuple[int, str, str, list[tuple[int, float]]]] = []

    for tar in tarjetas:
        if tar.reinicia:
            ultima_ruta = None         # cada subT reinicia el rango de orden
            linea_ultima = None

        if tar.rutas_manual is not None and tar.manual_tentativa:
            continue        # v4.15: sufijo con '~' (por revisar): se respeta y no fija el orden

        if tar.rutas_manual is not None:
            # --- validacion de anclas: SOLO avisa, nunca modifica ---
            inexistentes = [n for n in tar.rutas_manual if n not in puntuador.rutas]
            claves_pista = extraer_claves(tar.segundo)
            if inexistentes:
                revision.append((tar.indice, tar.linea,
                                 f"ancla apunta a rutas inexistentes: {inexistentes}", []))
            elif claves_pista:
                claves_pregunta = extraer_claves(tar.primero)
                base = {n for n in numeros_ruta if tema_compatible_con(tar, n)}
                base |= set(tar.rutas_manual)
                puntajes_val = {n: puntuador.evaluar(claves_pista, claves_pregunta, n)[0]
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
            ultima_ruta = max(tar.manual_firmes)   # solo las rutas sin '~' fijan el orden
            linea_ultima = tar.indice
            continue

        if tar.malformada:
            revision.append((tar.indice, tar.linea, "contiene '&' sin numeros", []))
            continue

        claves_pista = extraer_claves(tar.segundo)
        if not claves_pista:
            revision.append((tar.indice, tar.linea, "la pista no produce claves utiles", []))
            continue
        claves_pregunta = extraer_claves(tar.primero)

        # --- etapa A: rutas compatibles con el tema Y en orden ---
        por_tema = [n for n in numeros_ruta if tema_compatible_con(tar, n)]
        set_tema = set(por_tema)
        en_orden = [n for n in por_tema if ultima_ruta is None or n >= ultima_ruta]
        puntajes, coincidentes = _puntuar_rutas(puntuador, claves_pista, claves_pregunta, en_orden)
        calificadas = _calificar(puntajes, UMBRAL_CLAVES)
        sin_tema = False
        fuera_orden = False

        # --- etapa B (v4.14 FIX 3 / v4.15): el orden es una pista, no un muro.
        # Si solo coinciden rutas ANTERIORES a la ultima asignada, la tarjeta se
        # asigna igual cuando el puntaje es considerablemente alto
        # (>= UMBRAL_FUERA_ORDEN); el orden se reancla en la nueva ruta. Si no
        # llega a ese puntaje, cae al caso 'tentativa' (~) de mas abajo.
        if not calificadas and ultima_ruta is not None:
            fuera = [n for n in por_tema if n < ultima_ruta]
            p_f, c_f = _puntuar_rutas(puntuador, claves_pista, claves_pregunta, fuera)
            cal_alto = _calificar(p_f, UMBRAL_FUERA_ORDEN)
            coincidentes.update(c_f)
            if cal_alto:
                calificadas = cal_alto
                fuera_orden = True
            puntajes = puntajes + p_f

        # --- etapa C (v4.14 FIX 4): ultimo recurso, ignorar el tema (subT mal
        # escrito o ruta sin tema). Exige UMBRAL_CLAVES_SIN_TEMA y deja aviso.
        if not calificadas and tar.tema is not None:
            resto = [n for n in numeros_ruta
                     if n not in set_tema and (ultima_ruta is None or n >= ultima_ruta)]
            p_r, c_r = _puntuar_rutas(puntuador, claves_pista, claves_pregunta, resto)
            puntajes = puntajes + p_r
            coincidentes.update(c_r)
            cal_r = _calificar(p_r, UMBRAL_CLAVES_SIN_TEMA)
            if cal_r:
                calificadas = cal_r
                sin_tema = True

        if not calificadas:
            if not puntajes:
                revision.append((tar.indice, tar.linea,
                                 "sin rutas candidatas para ese tema/orden", []))
                continue
            top = sorted(puntajes, key=lambda t: (-t[1], t[0]))
            mejor_n, mejor_p = top[0]
            if mejor_p <= PUNTAJE_MIN_TENTATIVO:
                revision.append((tar.indice, tar.linea,
                                 "sin coincidencias con ninguna ruta", []))
                continue
            # v4.18: cadena de rutas contiguas del mismo subT que, juntas, cubren TODAS las
            # claves raras de la pista (una clave por ruta o mas). Sin '~': la evidencia es
            # la cobertura completa. No fija el orden.
            if tar.tema is not None and 2 <= len(claves_pista) <= MAX_CLAVES_EXTRA:
                permit = set(en_orden)
                semillas = [(n, p) for n, p in top if n in permit and coincidentes.get(n)][:MAX_SEMILLAS]
                ventana = _mejor_ventana(puntuador, claves_pista, claves_pregunta, semillas, permit)
                if ventana is not None:
                    tar.rutas_asignadas = ventana
                    cambios += 1
                    avisos.append((tar.indice, tar.linea,
                                   f"asignada a ruta(s) {', '.join(map(str, ventana))} por cobertura "
                                   f"contigua: cada ruta aporta claves de la pista y juntas las cubren todas"))
                    continue
            # v4.16: antes de rendirse con '~', dos criterios extra sobre la mejor
            # candidata (solo si es de su tema y esta en orden; una fuera de orden
            # o de otro tema sigue siendo tentativa).
            if mejor_n in set_tema and (ultima_ruta is None or mejor_n >= ultima_ruta):
                asig_t: Optional[list[int]] = None
                nota = ""
                vecina = _buscar_vecina(puntuador, claves_pista, claves_pregunta, mejor_n,
                                        coincidentes[mejor_n], set(en_orden))
                if vecina is not None:                       # parche 3
                    asig_t = sorted([mejor_n, vecina])
                    nota = (f"cobertura complementaria: la ruta {mejor_n} ({mejor_p:.2f}) "
                            f"cubre parte de la pista y la vecina {vecina} cubre el resto")
                elif mejor_p >= PUNTAJE_MIN_MARGEN - EPS:    # parche 1
                    segunda = top[1][1] if len(top) > 1 else 0.0
                    if mejor_p - segunda >= MARGEN_GANADOR - EPS:
                        asig_t = [mejor_n]
                        nota = (f"margen sobre la segunda: ruta {mejor_n} ({mejor_p:.2f}) "
                                f"vs {segunda:.2f}")
                if asig_t is not None:
                    tar.rutas_asignadas = asig_t   # no fija el orden: no superan el umbral
                    cambios += 1
                    avisos.append((tar.indice, tar.linea,
                                   f"asignada a ruta(s) {', '.join(map(str, asig_t))} sin superar "
                                   f"UMBRAL_CLAVES; {nota}"))
                    continue
            # v4.15: no supera el umbral -> se asigna la de mayor puntaje con '~'
            # (a revisar). No fija el orden: no es una asignacion confiable.
            if ultima_ruta is not None and mejor_n < ultima_ruta:
                motivo = (f"fuera de orden (ultima asignada {ultima_ruta}) y puntaje "
                          f"{mejor_p:.2f} < UMBRAL_FUERA_ORDEN")
            else:
                motivo = f"ninguna ruta permitida supera UMBRAL_CLAVES (mejor {mejor_p:.2f})"
            tar.rutas_asignadas = [mejor_n]
            tar.marcadas = {mejor_n}
            cambios += 1
            tentativas.append((tar.indice, tar.linea, motivo, top[:5]))
            continue

        # v4.12 FIX 4: seleccion por cobertura. Una ruta calificada solo
        # se conserva si aporta al menos una clave de la PISTA que ninguna
        # de las ya elegidas cubra (v4.14: la pregunta ya no cuenta aqui).
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
        linea_ultima = tar.indice
        cambios += 1
        if fuera_orden:
            avisos.append((tar.indice, tar.linea,
                           f"asignada FUERA DE ORDEN a ruta(s) {', '.join(map(str, elegidas))} "
                           f"(puntaje {calificadas[0][1]:.2f} >= UMBRAL_FUERA_ORDEN); "
                           f"el orden se reancla en la ruta {ultima_ruta}"))
        if sin_tema:
            avisos.append((tar.indice, tar.linea,
                           f"asignada a ruta(s) {', '.join(map(str, elegidas))} ignorando el "
                           f"tema (el subT no coincidio con ninguna ruta o no habia coincidencia "
                           f"dentro de su tema)"))
        # v4.18: rutas adicionales por cobertura contigua. Solo en la via normal, en
        # tarjetas CON subT y 2-4 claves; las rutas agregadas solo salen de ese subT,
        # y no fijan el orden (ultima_ruta ya se calculo con las firmes).
        if (not fuera_orden and not sin_tema and tar.tema is not None
                and 2 <= len(claves_pista) <= MAX_CLAVES_EXTRA):
            cadena, _falt = _extender_contiguas(puntuador, claves_pista, claves_pregunta,
                                                elegidas, set(en_orden), MAX_RUTAS_EXTRA)
            extras = [n for n in cadena if n not in elegidas]
            if extras:
                tar.rutas_asignadas = cadena
                if MARCAR_EXTRAS:
                    tar.marcadas = set(extras)
                avisos.append((tar.indice, tar.linea,
                               f"ruta(s) {', '.join(map(str, extras))} agregada(s) por cobertura "
                               f"contigua: cubre(n) claves raras de la pista que la(s) ruta(s) "
                               f"{', '.join(map(str, elegidas))} no cubre(n)"))

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
    if tentativas:
        print(f"asignadas con ~ (revisar) ({len(tentativas)}):")
        for indice, linea, motivo, cands in tentativas:
            print(f"  linea {indice + 1}: {linea.strip()}")
            print(f"    motivo: {motivo}")
            print("    candidatas: "
                  + ", ".join(f"ruta {n} ({p:.2f})" for n, p in cands))
    if avisos:
        print(f"asignadas con criterio relajado, verificar ({len(avisos)}):")
        for indice, linea, motivo in avisos:
            print(f"  linea {indice + 1}: {linea.strip()}")
            print(f"    {motivo}")

    if cambios == 0:
        print("sin cambios que hacer: el recordatorio no se toco.")
        return primeras_por_ruta(tarjetas)

    nuevas = list(lineas)
    for tar in tarjetas:
        if tar.rutas_asignadas:  # formato exacto: espacio + & + espacio + "N, M"
            sufijo = " & " + ", ".join(f"{n}{'~' if n in tar.marcadas else ''}"   # '~' = por revisar
                                       for n in tar.rutas_asignadas)
            cola = f" | {tar.nota}" if tar.nota else ""   # v4.20: la nota se conserva al final
            nuevas[tar.indice] = tar.linea.rstrip() + sufijo + cola

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
    return primeras_por_ruta(tarjetas)


def primeras_por_ruta(tarjetas: list[Tarjeta]) -> dict[int, list[str]]:
    mapa: dict[int, list[str]] = {}
    for tar in tarjetas:
        if tar.rutas_asignadas:
            firmes = [n for n in tar.rutas_asignadas if n not in tar.marcadas]
        elif tar.rutas_manual is not None:
            firmes = list(tar.manual_firmes)
        else:
            firmes = []
        texto = tar.primero.strip()
        if not texto:
            continue
        for n in firmes:
            lista = mapa.setdefault(n, [])
            if texto not in lista:
                lista.append(texto)
    return mapa


def aplicar_primera_al_drawio(drawio_path: Path, primeras: dict[int, list[str]],
                              accion: str) -> tuple[int, int]:
    tree = ET.parse(drawio_path)
    diagrams = tree.getroot().findall("./diagram")
    if not diagrams:
        return 0, 0
    model = diagrams[0].find("./mxGraphModel")
    graph_root = model.find("./root") if model is not None else None
    if graph_root is None:
        return 0, 0

    eliminados = escritos = 0
    for elem in graph_root.iter():
        if elem.tag in ("object", "UserObject") and "primera" in elem.attrib:
            del elem.attrib["primera"]
            eliminados += 1

    if accion == "aplicar":
        for elem in graph_root.iter():
            if elem.tag not in ("object", "UserObject"):
                continue
            ruta = (elem.get("ruta") or "").strip()
            if not ruta.isdigit():
                continue
            textos = primeras.get(int(ruta))
            if not textos:
                continue
            items = list(elem.attrib.items())
            pos = next(i for i, (k, _v) in enumerate(items) if k == "ruta")
            items.insert(pos, ("primera", " | ".join(textos)))      # antes de 'ruta'
            elem.attrib.clear()
            elem.attrib.update(items)
            escritos += 1

    tree.write(drawio_path, encoding="utf-8", xml_declaration=True)
    return escritos, eliminados


def preguntar_accion_primera(root) -> str:
    """v4.19: ventana con 3 botones. Devuelve 'no_aplicar', 'aplicar' o 'borrar'.
    Cerrar la ventana equivale a 'no_aplicar' (no se toca nada)."""
    eleccion = {"valor": "no_aplicar"}
    ventana = tk.Toplevel(root)
    ventana.title("Dato 'primera'")
    ventana.resizable(False, False)

    def elegir(valor: str) -> None:
        eleccion["valor"] = valor
        ventana.destroy()

    tk.Label(
        ventana, justify="left", anchor="w", padx=16, pady=12,
        text=("¿Aplicar el dato 'primera' del recordatorio al drawio?\n\n"
              "  No aplicar: deja los 'primera' que ya tiene el drawio.\n"
              "  Sí aplicar: reemplaza los 'primera' por los del recordatorio.\n"
              "  Borrar todo: elimina todos los 'primera' (solo quedan los datos 'ruta')."),
    ).pack()
    marco = tk.Frame(ventana, padx=12, pady=10)
    marco.pack()
    for texto, valor in (("No aplicar", "no_aplicar"), ("Sí aplicar", "aplicar"),
                         ("Borrar todo", "borrar")):
        tk.Button(marco, text=texto, width=14,
                  command=lambda v=valor: elegir(v)).pack(side="left", padx=6)

    ventana.protocol("WM_DELETE_WINDOW", lambda: elegir("no_aplicar"))
    ventana.attributes("-topmost", True)
    ventana.lift()
    ventana.focus_force()
    ventana.grab_set()
    ventana.wait_window()
    return eleccion["valor"]


# ======================================================================
# v4.21: PASO OPCIONAL - exportar la Pagina 1 del drawio a JPG
# ======================================================================

DRAWIO_EJECUTABLES = (
    r"C:\Program Files\draw.io\draw.io.exe",
    r"C:\Program Files (x86)\draw.io\draw.io.exe",
)
DRAWIO_TIMEOUT_SEG = 120   # segundos maximos de espera del export


def encontrar_drawio() -> Optional[str]:
    """v4.21: ruta del ejecutable de draw.io, o None si no esta instalado.
    Orden: Program Files -> Program Files (x86) -> LOCALAPPDATA (instalacion
    de usuario) -> 'draw.io' en el PATH."""
    for candidato in DRAWIO_EJECUTABLES:
        if Path(candidato).is_file():
            return candidato
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidato_local = Path(local) / "Programs" / "draw.io" / "draw.io.exe"
        if candidato_local.is_file():
            return str(candidato_local)
    return shutil.which("draw.io")


def preguntar_export_jpg(root) -> bool:
    """v4.21: ventana con 2 botones (mismo estilo que preguntar_accion_primera).
    Devuelve True si el usuario quiere exportar la Pagina 1 a jpg.
    Cerrar la ventana equivale a No (no se toca nada)."""
    eleccion = {"valor": False}
    ventana = tk.Toplevel(root)
    ventana.title("Exportar mapa a JPG")
    ventana.resizable(False, False)

    def elegir(valor: bool) -> None:
        eleccion["valor"] = valor
        ventana.destroy()

    tk.Label(
        ventana, justify="left", anchor="w", padx=16, pady=12,
        text=("¿Obtener la imagen en JPG del mapa (Página 1) del drawio?\n\n"
              "  Se guardará en la misma carpeta que el archivo .drawio,\n"
              "  con el mismo nombre y extensión .jpg\n"
              "  (si ya existe un jpg con ese nombre, se sobrescribe)."),
    ).pack()
    marco = tk.Frame(ventana, padx=12, pady=10)
    marco.pack()
    tk.Button(marco, text="Sí, exportar", width=14,
              command=lambda: elegir(True)).pack(side="left", padx=6)
    tk.Button(marco, text="No", width=14,
              command=lambda: elegir(False)).pack(side="left", padx=6)

    ventana.protocol("WM_DELETE_WINDOW", lambda: elegir(False))
    ventana.attributes("-topmost", True)
    ventana.lift()
    ventana.focus_force()
    ventana.grab_set()
    ventana.wait_window()
    return eleccion["valor"]


def _ejecutar_export_drawio(ejecutable: str, salida: Path, drawio_path: Path,
                            indice_pagina: int):
    """v4.21.1: corre el comando de export con el indice de pagina dado.
    -o recibe la ruta COMPLETA del jpg (misma carpeta que el drawio, mismo
    nombre): draw.io no acepta una carpeta sola como destino."""
    comando = [
        ejecutable,
        "-x", "-f", "jpg", "-q", "100", "-p", str(indice_pagina),
        "-o", str(salida),
        str(drawio_path),
    ]
    return subprocess.run(
        comando, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=DRAWIO_TIMEOUT_SEG,
    )


def exportar_mapa_jpg(root, drawio_path: Path) -> None:
    """v4.21: pregunta al usuario y, si acepta, exporta la Pagina 1 del
    .drawio a .jpg (misma carpeta, mismo nombre) llamando a draw.io por
    linea de comandos. El resultado se reporta solo por consola.
    v4.21.1: draw.io v27.0.2+ numera las paginas desde 1 ('-p 1'); las
    versiones anteriores lo hacian desde 0 ('-p 0'). Se prueba con -p 1 y,
    si falla, se reintenta con -p 0."""
    ejecutable = encontrar_drawio()
    if ejecutable is None:
        print("exportar jpg: no se encontro draw.io instalado "
              "(ni en Program Files, ni en LOCALAPPDATA, ni en el PATH); paso omitido.")
        return
    if not preguntar_export_jpg(root):
        print("exportar jpg: no se solicito el mapa (paso omitido).")
        return
    salida = drawio_path.with_suffix(".jpg")   # misma carpeta, mismo nombre, .jpg
    print(f"exportando mapa a jpg (misma carpeta que el drawio): {salida}")
    resultado = None
    try:
        resultado = _ejecutar_export_drawio(ejecutable, salida, drawio_path, 1)   # v27.0.2+
        if resultado.returncode != 0 or not salida.is_file():
            # reintento con numeracion 0-based (draw.io anterior a v27.0.2)
            resultado = _ejecutar_export_drawio(ejecutable, salida, drawio_path, 0)
    except subprocess.TimeoutExpired:
        print(f"exportar jpg: draw.io no termino en {DRAWIO_TIMEOUT_SEG} s; se cancelo.")
        return
    except OSError as exc:
        print(f"exportar jpg: no se pudo ejecutar draw.io ({exc}).")
        return
    if resultado is not None and resultado.returncode == 0 and salida.is_file():
        print(f"mapa exportado: {salida}")
    else:
        codigo = resultado.returncode if resultado is not None else "?"
        print(f"exportar jpg: draw.io termino con codigo {codigo}; "
              f"no se genero {salida.name}.")
        detalle = ((resultado.stderr or resultado.stdout) if resultado is not None else "") or ""
        detalle = detalle.strip()
        if detalle:
            print(f"  detalle: {detalle.splitlines()[-1]}")


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

    annotations, unannotated, conflicts = collect_ruta_annotations(
        route_blocks, is_rhombus_key, make_is_tema_lookup(styles, roots))   # v4.17

    # Construir el mapeo de imagenes a su ruta correspondiente
    fuentes_conv = {key: meta["sources"] for key, meta in convergence_metadata.items()}
    image_rutas = {}
    for block in route_blocks:
        for key, _ in block.items:
            if not key:
                continue
            if key.startswith("__CONVERGENCE__"):
                candidatos = fuentes_conv.get(key, [])
            else:
                candidatos = [key]
            for cid in candidatos:
                if cid in styles and IMAGE_URI_RE.search(styles.get(cid, "")):
                    image_rutas.setdefault(cid, str(block.number))
    sin_ruta = [cid for cid, v in vertices.items()
                if IMAGE_LINE_RE.match(v) and cid not in image_rutas]
    if sin_ruta:
        print(f"imagenes sin ruta (no aparecen en ninguna ruta): {', '.join(sin_ruta)}")

    if annotations or image_rutas:
        annotated_count, missing_cells = write_annotated_drawio(input_path, input_path, annotations, image_rutas)
        print(f"drawio actualizado en su lugar: {annotated_count} celdas con dato 'ruta'")
        if image_rutas:
            print(f"y {len(image_rutas)} imagen(es) con dato 'ruta' y 'descripcion'")
        if missing_cells:
            print(f"celdas no encontradas en la Hoja 1: {missing_cells}")
    else:
        print("sin celdas para anotar con dato 'ruta' ni imagenes (el original no se modifico)")
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
        primeras = procesar_recordatorio(Path(recordatorio), route_blocks)
        if primeras is not None:        # v4.19: solo se pregunta si hubo recordatorio
            accion = preguntar_accion_primera(root)
            if accion == "no_aplicar":
                print("dato 'primera': no se aplico (el drawio conserva los que ya tenia)")
            else:
                escritos, eliminados = aplicar_primera_al_drawio(input_path, primeras, accion)
                if accion == "borrar":
                    print(f"dato 'primera': {eliminados} eliminado(s); solo quedan los datos 'ruta'")
                else:
                    print(f"dato 'primera': {escritos} celda(s) con 'primera' "
                          f"({eliminados} anterior(es) reemplazado(s))")
    else:
        print("sin recordatorio seleccionado: paso omitido.")

    # --- paso OPCIONAL: exportar la Pagina 1 del drawio a JPG (v4.21) ---
    # Solo se llega aca si se selecciono un drawio (si no, main() retorno antes),
    # asi que el modal nunca aparece sin un drawio seleccionado.
    exportar_mapa_jpg(root, input_path)


if __name__ == "__main__":
    main()