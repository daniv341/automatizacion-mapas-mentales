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
- v4.5: NUEVO - IMAGENES COMO RENGLONES:
  * Las celdas con imagen embebida (style image=data:image/...) se registran
    como nodos del grafo, asi el flujo cuadro1 -> imagen -> cuadro2 queda como
    UNA sola rama (antes la imagen rompia la conectividad y el flujo se partia
    en dos ramas distintas).
  * Cada imagen sale en TXT/JSON como un renglon propio con el prefijo 'IMG:'
    seguido del data URI EXACTO tal como viene en drawio (con o sin ';base64').
    Convencion para el script lector: todo renglon que empieza con 'IMG:' es
    una imagen; quitando 'IMG:' se obtiene el data URI usable en navegador, y
    la parte posterior a la primera coma es el base64 decodificable.
  * Detector generico: soporta cualquier formato data:image/... (png, jpeg,
    jpg, gif, svg, ...), con o sin ';base64'.
  * Si la celda-imagen ademas tiene texto (etiqueta), el renglon de la imagen
    va primero y las lineas de texto despues.
  * Imagenes sin ninguna conexion (decorativas) se descartan como huerfanas,
    igual que cualquier nodo sin aristas.
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

# Detector generico de imagenes embebidas en el style de drawio:
# image=data:image/png,BASE64 | image=data:image/jpeg;base64,BASE64 | etc.
# El payload admite el alfabeto base64 y tambien codificacion URL (%..),
# y se detiene en el ';' que separa parametros del style.
IMAGE_URI_RE = re.compile(
    r"image=(data:image/[A-Za-z0-9.+\-]+(?:;base64)?,[A-Za-z0-9+/=%\-_.!~*'()]+)"
)

# Renglon que ES una imagen (data URI al inicio de la linea).
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


def extract_image_uri(style: str) -> Optional[str]:
    """Extrae el data URI de la imagen embebida en el style de un mxCell.
    Se devuelve EXACTO tal como viene (no se anade ni quita ';base64') para
    que coincida con lo que drawio guarda y el usuario ya verifica en
    navegador."""
    match = IMAGE_URI_RE.search(style or "")
    return match.group(1) if match else None


def with_image_prefix(line: str) -> str:
    """Marca los renglones-imagen con el prefijo 'IMG:' para que un script
    lector pueda identificarlos y extraer el data URI sin ambiguedad."""
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

    for cell in graph_model.iter("mxCell"):
        cell_id = cell.get("id")
        value = cell.get("value")
        style = cell.get("style", "")

        if cell.get("edge") == "1":
            source = cell.get("source")
            target = cell.get("target")
            if source and target:
                raw_edges.append((source, target))

        if cell_id:
            text = clean_drawio_value(value)
            # v4.5: solo celdas-vertice pueden ser nodos-imagen.
            image_uri = extract_image_uri(style) if cell.get("edge") != "1" else None
            if image_uri is not None:
                # Nodo-imagen: el data URI es un renglon propio; si ademas
                # tiene etiqueta, va despues de la imagen.
                vertices[cell_id] = f"{image_uri}\n{text}" if text else image_uri
                styles[cell_id] = style
            elif text:
                vertices[cell_id] = text
                styles[cell_id] = style

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
    """De una cadena consecutiva de rombos conserva solo el ultimo.
    La raiz de la ruta nunca se descarta (proteccion historica). Marca los
    sobrevivientes de colapsos reales (cadena >= 2)."""
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
    """DFS iterativo, sin recursividad."""
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
    """target -> (synthetic_key, synthetic_value, source_ids) para indegree > 1.

    v4.5: si una fuente es una imagen (su valor es un data URI), no se mezcla
    con el texto: los textos se unen con espacios y cada imagen se agrega como
    renglon propio (linea separada por salto de linea)."""
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
    """True si 'node' es una rama de exactamente 2 nodos:
    cabeza + 1 hijo hoja, sin mas descendencia."""
    if len(node.children) != 1:
        return False
    only_child = next(iter(node.children.values()))
    return len(only_child.children) == 0


def _split_into_blocks(
    node: TrieNode,
    active_rhombus: Optional[str],
    is_rhombus_key,
) -> tuple[list[tuple[Optional[str], list[str]]], list[str], list[str]]:
    """Descompone el sub-arbol de 'node' en bloques lineales.

    Reglas para hermanos no-primeros:
    - Hoja pura: se fusiona al bloque actual (v4.1). Tambien aplica a
      imagenes (v4.5: una imagen es un renglon mas).
    - Sobreviviente de cadena de rombos: abre bloque con su PROPIO texto
      como subtitulo (v4.1).
    - Rama de exactamente 'cabeza + 1 hijo hoja' cuyo padre NO es rombo:
      se fusiona al bloque actual (v4.2).
    - En otro caso: abre bloque nuevo con subtitulo = ultimo rombo ancestro
      (v4.4: salvo que ese rombo sea transparente).
    """
    own_lines = node.value.splitlines() or [""]
    node_is_rhombus = is_rhombus_key(node.key)
    next_active = node.value if node_is_rhombus else active_rhombus

    children = list(node.children.values())
    if not children:
        return [], own_lines, []

    transparent = node_is_rhombus and is_rhombus_key(children[0].key)

    down_active = active_rhombus if transparent else next_active

    closed: list[tuple[Optional[str], list[str]]] = []
    first_closed, first_open, _ = _split_into_blocks(children[0], down_active, is_rhombus_key)
    closed.extend(first_closed)

    if transparent:
        open_lines = list(first_open)
    else:
        open_lines = own_lines + first_open
    continuation = list(first_open)

    for other in children[1:]:
        other_closed, other_open, other_cont = _split_into_blocks(other, down_active, is_rhombus_key)

        if not other.children:
            open_lines.extend(other_open)
            continuation.extend(other_open)
        elif other.chain_marked:
            closed.append((other.value, other_cont))
        elif not node_is_rhombus and _is_head_with_single_leaf(other):
            open_lines.extend(other_open)
            continuation.extend(other_open)
        else:
            closed.append((None if transparent else next_active, other_open))

        closed.extend(other_closed)

    return closed, open_lines, continuation


def build_route_blocks(trie_root: TrieNode, is_rhombus_key) -> list[RouteBlock]:
    """Recorre cada arbol principal y genera bloques numerados de forma
    continua para todo el documento."""
    blocks: list[RouteBlock] = []
    number = 1
    for root_child in trie_root.children.values():
        closed, open_lines, _ = _split_into_blocks(root_child, None, is_rhombus_key)
        tree_blocks = [(None, open_lines)] + closed
        for subtitle, lines in tree_blocks:
            blocks.append(RouteBlock(number=number, subtitle=subtitle, lines=lines))
            number += 1
    return blocks


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
        "version": "4.5",
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
    script_dir = Path(__file__).parent.resolve()
    txt_path = script_dir / f"{input_path.stem}.txt"
    #json_path = script_dir / f"{input_path.stem}.json"

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

    # v4.5: los renglones-imagen se marcan con 'IMG:' en TXT y JSON.
    for block in route_blocks:
        block.lines = [with_image_prefix(line) for line in block.lines]

    raw_txt = render_blocks_txt(route_blocks)
    final_txt = postprocess_txt(raw_txt)
    txt_path.write_text(final_txt, encoding="utf-8")

    #write_json(json_path, input_path, diagram_name, diagram_id, route_blocks)

    image_nodes = sum(1 for v in vertices.values() if IMAGE_LINE_RE.match(v))
    print(f"archivos txt y json generados ({len(route_blocks)} rutas, {image_nodes} imagenes en el grafo)")


if __name__ == "__main__":
    main()