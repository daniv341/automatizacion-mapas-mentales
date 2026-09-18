#!/usr/bin/env python3
"""Parsea la Hoja 1 de un archivo .drawio y genera un TXT/JSON con:
- vertices y aristas validos
- DFS iterativo raiz -> hoja
- colapso de cadenas consecutivas de rombos (look-ahead)
- fusion de convergencias (multiples predecesores al mismo destino)
- Trie para agrupar prefijos comunes
- separacion entre arboles principales
- v4.1: fusion de ramas hoja al bloque actual
- v4.2: fusion de ramas 'cabeza + 1 hijo hoja' bajo padre no-rombo
- v4.4: rombo contenedor (transparente) cuando su primera rama es otro rombo
- v4.5: imagenes embebidas como renglones 'IMG:<data:image/...>'
- v4.6: dato 'ruta' en el diagrama (mecanismo nativo de drawio:
  <object label="..." ruta="N" id="...">)
- v4.7: NUEVO - LA CELDA ANOTADA NUNCA ES UN ROMBO:
  * Cada bloque conoce la secuencia de celdas (items) que producen sus
    renglones. El dato 'ruta' se coloca en la PRIMERA celda real NO-rombo
    de esa secuencia (se saltan rombos y nodos sinteticos de convergencia).
  * Asi, en el tronco 'DEFINICIONES -> SISTEMAS -> ...' el dato va en
    SISTEMAS (no en el rombo DEFINICIONES), y en bloques cuyo primer
    renglon es un rombo (p.ej. 'MALLA ABIERTA') el dato va al cuadro
    siguiente ('sistemas control').
  * El drawio anotado REEMPLAZA al original (misma ruta). Antes de anotar
    se eliminan los datos 'ruta' previos de la Hoja 1 (proceso idempotente).
"""
from __future__ import annotations

import html
import json
import re
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
    """Descompone el sub-arbol de 'node' en bloques lineales.

    v4.7: los bloques se llevan como ITEMS (cell_key, lineas) en orden, para
    que la anotacion 'ruta' pueda saltar rombos y sinteticos.

    Devuelve (bloques_cerrados, items_abiertos, items_continuacion):
    - bloques_cerrados: [(subtitulo, items), ...]
    - items_abiertos: tronco del sub-arbol (con el texto propio del nodo,
      salvo que sea rombo transparente).
    - items_continuacion: items de la primera rama (sin el texto del nodo).
    """
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
    """Para uso externo (otro script importa este). Devuelve un diccionario:
    {
        <numero de ruta>: {
            "tema": <texto del subT o None>,
            "ids": {"renglon1": "<id celda>", "renglon2": "<id celda>", ...},
        },
        ...
    }
    """
    vertices, styles, edges, graph, _n, _i, _o = parse_first_page(input_path)
    roots = find_roots(vertices, edges)
    leaves = find_leaves(graph)
    original_paths, chain_marks = find_paths_iterative(graph, roots, set(leaves), styles)
    convergence_map = build_convergence_map(vertices, edges)
    transformed_paths, convergence_values, _meta = apply_convergences(original_paths, convergence_map)
    trie_values = {**convergence_values, **vertices}
    trie = build_trie(transformed_paths, trie_values, vertices, chain_marks)
    is_rhombus_key = make_is_rhombus_lookup(styles)
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
    """v4.7: celda -> numero de ruta. La celda elegida es la PRIMERA de la
    secuencia de items del bloque que sea real (no sintetica) y NO rombo.
    Devuelve (anotaciones, bloques_sin_celda, conflictos)."""
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
    """v4.7: escribe el .drawio con las celdas anotadas envueltas en
    <object label="..." ruta="N" id="...">. Si output_path == input_path,
    el original se reemplaza. Limpia primero cualquier dato 'ruta' previo
    en la Hoja 1 (idempotencia)."""
    tree = ET.parse(input_path)
    diagrams = tree.getroot().findall("./diagram")
    if not diagrams:
        return 0, list(annotations)
    model = diagrams[0].find("./mxGraphModel")
    graph_root = model.find("./root") if model is not None else None
    if graph_root is None:
        return 0, list(annotations)

    # Limpiar datos 'ruta' previos en la Hoja 1.
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

    # Carpeta de salida del TXT y del JSON:
    # - Si OUTPUT_DIR esta definida, se usa esa (se crea si no existe).
    # - Si no, la carpeta del script (comportamiento original).
    output_dir = OUTPUT_DIR if OUTPUT_DIR is not None else Path(__file__).parent.resolve()
    # ESTA ES LA LINEA QUE FALTABA EN TU INTENTO: crear la carpeta.
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
    trie = build_trie(transformed_paths, trie_values, vertices, chain_marks)

    is_rhombus_key = make_is_rhombus_lookup(styles)
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


if __name__ == "__main__":
    main()