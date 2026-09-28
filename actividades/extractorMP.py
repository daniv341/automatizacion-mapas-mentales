#!/usr/bin/env python3
"""extractorMP.py — Genera un TXT a partir de la Hoja 1 de un .drawio.

Reutiliza la misma logica de analisis del generador de rutas (parseo de la
Hoja 1, DFS raiz -> hoja, colapso de cadenas de rombos, fusion de
convergencias, trie y bloques), pero cambia SOLO el formato del TXT:

- Sin numeracion "RUTA N".
- El tema de cada bloque (rombo activo) se imprime una sola vez, como
  "subT <tema>", cuando cambia respecto del bloque anterior.
- Las convergencias (bifurcaciones) NO se unen en un solo renglon: cada rama
  queda en su propio renglon.
- Las imagenes embebidas se reemplazan por el marcador <imagen>.
- NO modifica el .drawio: solo lo lee.
- El orden de los bloques es el que hereda del DFS (igual que el generador):
  NO se reordena por geometria.

Propio del extractor (NO existe en el generador):
- temaP: cuadros "shape=offpageconnector" -> titulo general, impreso UNA vez
  al inicio. Por eso offpageconnector NO es un rombo aqui. Como el temaP
  suele estar suelto (sin aristas), los huerfanos se rescatan antes de
  descartarlos y el temaP se recupera de ahi.
- temaS: hexagonos con "gradientcolor=#" -> encabezados "temaS <texto>".
  Dejan de ser renglones de contenido y no se duplican.
- Fix de subT perdidos: si el primer item de un bloque es un rombo real, SU
  texto manda como subT (no el subtitulo heredado del rombo anterior).
- Encabezados uniformes: todo rombo que quede enterrado como renglon (ej. el
  que abre el primer bloque) se emite como "subT ..." una sola vez; los
  repetidos se omiten comparando contra el ultimo tema impreso. Antes el
  primer bloque mostraba el titulo sin "subT" y el mismo titulo volvia a
  salir como "subT" en el bloque siguiente (duplicado del inicio).

Cosas del generador que aqui NO se hacen (a proposito):
- dedup de convergencias (v4.10), conservar_ultimo_subt (v4.16),
  recordatorio.txt, asociador y anotacion del .drawio.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog

Graph = dict[str, list[str]]

# --- configuracion de salida (editable) ---
TOPIC_PREFIX = "subT "           # prefijo del renglon de tema (rombo activo)
TEMAP_PREFIX = "temaP "          # prefijo del tema principal (offpageconnector)
TEMAS_PREFIX = "temaS "          # prefijo del tema secundario (hexagono+gradient)
IMAGE_PLACEHOLDER = "<imagen>"   # marcador donde habia una imagen
STRIP_JOIN_COMMAS = True         # quita la coma final de cada rama al
                                 # separar una bifurcacion en renglones
OUTPUT_SUFFIX = "- MP"           # sufijo del nombre del txt generado

IMAGE_URI_RE = re.compile(
    r"image=(data:image/[A-Za-z0-9.+\-]+(?:;base64)?,[A-Za-z0-9+/=%\-_.!~*'()]+)"
)
IMAGE_LINE_RE = re.compile(r"^data:image/")
CONVERGENCE_PREFIX = "__CONVERGENCE__"


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
    items: list[tuple[str, list[str]]] = field(default_factory=list)


def extract_image_uri(style: str) -> Optional[str]:
    match = IMAGE_URI_RE.search(style or "")
    return match.group(1) if match else None


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


# EXTRACTOR: offpageconnector NO va aqui (en el generador si). En el
# extractor es el temaP y se detecta con is_temap_style.
def is_rhombus_style(style: str) -> bool:
    lowered = (style or "").lower()
    return (
        "rhombus" in lowered
        or "flowchart.decision" in lowered
        or ("gradientcolor=#" in lowered and "ellipse" in lowered)
    )


def is_temap_style(style: str) -> bool:
    """EXTRACTOR: tema principal = offpageconnector."""
    return "shape=offpageconnector" in (style or "").lower()


def is_temas_style(style: str) -> bool:
    """EXTRACTOR: tema secundario = hexagono con gradientColor.
    (Los hexagonos SIN gradientcolor siguen siendo contenido normal.)"""
    s = (style or "").lower()
    return "hexagon" in s and "gradientcolor=#" in s


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

    # EXTRACTOR: antes de descartar huerfanos se rescatan sus textos y
    # estilos, porque ahi suele vivir el temaP (offpageconnector suelto).
    # Se conserva el orden del documento (dict de insercion).
    orphan_vertices: dict[str, str] = {}
    orphan_styles: dict[str, str] = {}
    if orphan_ids:
        orphan_vertices = {k: v for k, v in vertices.items() if k in orphan_ids}
        orphan_styles = {k: v for k, v in styles.items() if k in orphan_ids}
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
    # EXTRACTOR: devuelve tambien los huerfanos rescatados
    return (
        vertices,
        styles,
        valid_edges,
        dict(graph),
        diagram_name,
        diagram_id,
        orphan_ids,
        orphan_vertices,
        orphan_styles,
    )


def find_special_cells(
    vertices: dict[str, str],
    styles: dict[str, str],
    orphan_vertices: dict[str, str],
    orphan_styles: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """EXTRACTOR: detecta temaP (offpageconnector) y temaS (hexagono+gradient)
    por estilo, tanto vivos como huerfanos (el temaP suele estar suelto)."""
    temap: dict[str, str] = {}
    temas: dict[str, str] = {}
    todos_v = dict(orphan_vertices)
    todos_v.update(vertices)
    todos_s = dict(orphan_styles)
    todos_s.update(styles)
    for cid, text in todos_v.items():
        st = todos_s.get(cid, "")
        if is_temap_style(st):
            temap[cid] = text
        elif is_temas_style(st):
            temas[cid] = text
    return temap, temas


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


def _fragment(text: str) -> str:
    """Limpia una rama de bifurcacion antes de dejarla como renglon propio."""
    text = text.strip()
    if STRIP_JOIN_COMMAS:
        text = text.rstrip(",").rstrip()
    return text


def build_convergence_map(
    vertices: dict[str, str],
    edges: list[tuple[str, str]],
) -> dict[str, tuple[str, str, list[str]]]:
    """Igual que en el generador de rutas, pero el valor de la convergencia
    une las ramas con saltos de linea (no con espacios), de modo que cada
    rama queda como renglon propio."""
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
                    frag = _fragment(source_value)
                    if frag:
                        text_parts.append(frag)
            value = "\n".join(text_parts)
            for image_value in image_parts:
                value = f"{value}\n{image_value}" if value else image_value
            key = f"{CONVERGENCE_PREFIX}{target}"
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
        if key.startswith(CONVERGENCE_PREFIX):
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
    """Descompone el sub-arbol de 'node' en bloques lineales (items)."""
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
    """EXTRACTOR: orden heredado del DFS, sin reordenamientos."""
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


# ---------- formato del TXT ----------

def canonical_topic(text: str) -> str:
    return " ".join(line.strip() for line in (text or "").splitlines() if line.strip())


def _is_rhombus_item(key: str, is_rhombus_key) -> bool:
    """EXTRACTOR: el item es un rombo real (no convergencia)."""
    return bool(key) and not key.startswith(CONVERGENCE_PREFIX) and is_rhombus_key(key)


def block_topic_and_items(
    block: RouteBlock,
    is_rhombus_key,
    temas_cells: dict[str, str],
) -> tuple[list[tuple[str, str]], Optional[str], list[tuple[str, list[str]]]]:
    """EXTRACTOR: separa los encabezados que abren el bloque.

    Devuelve (temas_iniciales, topic, items_restantes):
    - temas_iniciales: [(key, texto)] de los temaS al frente del bloque.
    - topic: texto del rombo que abre el bloque (fix de subT perdidos); si no
      hay rombo al frente, el subtitulo heredado; None si no hay ninguno.
    - items_restantes: los items que quedan como contenido.
    """
    items = list(block.items)
    lead: list[tuple[str, str]] = []
    topic: Optional[str] = None

    while items:
        key, lines = items[0]
        if key in temas_cells:
            header = canonical_topic("\n".join(lines))
            if header:
                lead.append((key, header))
            items.pop(0)
            continue
        if topic is None and _is_rhombus_item(key, is_rhombus_key):
            topic = canonical_topic("\n".join(lines)) or None
            items.pop(0)
            continue
        break

    if topic is None and block.subtitle:
        topic = canonical_topic(block.subtitle)

    return lead, topic, items


def render_blocks_txt(
    blocks: list[RouteBlock],
    is_rhombus_key,
    temap_cells: dict[str, str],
    temas_cells: dict[str, str],
    textos_subt: set[str],
    textos_tema: set[str],
) -> str:
    """EXTRACTOR: temaP primero (titulo general), luego los bloques en el
    orden heredado del DFS. Todo rombo (o titulo de seccion perdido) se
    emite como "subT" una sola vez; los repetidos se omiten comparando contra
    el ultimo tema impreso."""
    chunks: list[str] = []

    # temaP: titulo de todo el diagrama, siempre al inicio
    for _cid, text in temap_cells.items():
        titulo = canonical_topic(text)
        if titulo:
            chunks.append(f"{TEMAP_PREFIX}{titulo}")

    last_topic: Optional[str] = None
    usados: set[str] = set()

    for block in blocks:
        lead, topic, items = block_topic_and_items(block, is_rhombus_key, temas_cells)

        # temaS que abren el bloque: encabezado antes del subT
        for key, header in lead:
            chunks.append(f"{TEMAS_PREFIX}{header}")
            usados.add(key)

        piece: list[str] = []
        if topic and topic != last_topic:
            piece.append(f"{TOPIC_PREFIX}{topic}")
            last_topic = topic

        for key, lines in items:
            if key in temap_cells:
                continue                     # ya impreso al inicio
            if key in temas_cells:           # temaS a mitad del bloque
                header = canonical_topic("\n".join(lines))
                if piece:
                    chunks.append("\n".join(piece))
                    piece = []
                if header:
                    chunks.append(f"{TEMAS_PREFIX}{header}")
                    usados.add(key)
                continue

            texto_item = canonical_topic("\n".join(lines))
            es_rumbo = _is_rhombus_item(key, is_rhombus_key)
            es_convergencia = key.startswith(CONVERGENCE_PREFIX)
            # un renglon cuyo texto completo es el titulo de una seccion
            # conocida es un encabezado perdido (rombo enterrado en contenido,
            # celda suelta, etc.); las convergencias no participan del temaS.
            coincide_subt = bool(texto_item) and texto_item in textos_subt
            coincide_tema = (
                not es_convergencia
                and bool(texto_item)
                and texto_item in textos_tema
                and texto_item not in textos_subt
            )

            if es_rumbo or coincide_subt or coincide_tema:
                if texto_item and texto_item == last_topic:
                    continue               # subT duplicado: se omite
                if piece:
                    chunks.append("\n".join(piece))
                    piece = []
                if not es_rumbo and coincide_tema:
                    chunks.append(f"{TEMAS_PREFIX}{texto_item}")
                elif texto_item:
                    piece.append(f"{TOPIC_PREFIX}{texto_item}")
                    last_topic = texto_item
                continue

            for line in lines:
                piece.append(IMAGE_PLACEHOLDER if IMAGE_LINE_RE.match(line) else line)
        if piece:
            chunks.append("\n".join(piece))

    # temaS huerfanos que no aparecieron en ningun bloque
    for cid, text in temas_cells.items():
        if cid in usados:
            continue
        header = canonical_topic(text)
        if header:
            chunks.append(f"{TEMAS_PREFIX}{header}")

    if not chunks:
        return ""
    return "\n\n".join(chunks) + "\n\n"


def postprocess_txt(text: str) -> str:
    return re.sub(r"^[ \t]+", "", text, flags=re.MULTILINE)

# AQUI DONDE SE GUARDA
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "guardados" / "plantillas"


def main() -> None:
    root = tk.Tk()
    root.withdraw()

    input_file = filedialog.askopenfilename(
        title="Selecciona el archivo .drawio",
        filetypes=[("Archivos Drawio", "*.drawio"), ("Todos los archivos", "*.*")],
    )
    if not input_file:
        print("No se seleccionó ningún archivo. Saliendo...")
        return

    input_path = Path(input_file)
    output_dir = OUTPUT_DIR if OUTPUT_DIR is not None else Path(__file__).parent.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # AQUI SE PONE NOMBRE AL TXT
    txt_path = output_dir / f"{input_path.stem}{OUTPUT_SUFFIX}.txt"

    # EXTRACTOR: parse_first_page devuelve tambien los huerfanos rescatados
    (
        vertices, styles, edges, graph,
        _diagram_name, _diagram_id, _orphan_ids,
        orphan_vertices, orphan_styles,
    ) = parse_first_page(input_path)

    # EXTRACTOR: temaP y temaS por estilo, vivos o huerfanos
    temap_cells, temas_cells = find_special_cells(
        vertices, styles, orphan_vertices, orphan_styles
    )

    roots = find_roots(vertices, edges)
    leaves = find_leaves(graph)
    original_paths, chain_marks = find_paths_iterative(graph, roots, set(leaves), styles)

    convergence_map = build_convergence_map(vertices, edges)
    transformed_paths, convergence_values, _meta = apply_convergences(original_paths, convergence_map)

    trie_values = {**convergence_values, **vertices}
    trie = build_trie(transformed_paths, trie_values, vertices, chain_marks)

    is_rhombus_key = make_is_rhombus_lookup(styles)
    route_blocks = build_route_blocks(trie, is_rhombus_key)

    # titulos de seccion conocidos (para rescatar encabezados que quedaron
    # como renglones sueltos y eliminar duplicados)
    textos_subt = {
        canonical_topic(v)
        for k, v in vertices.items()
        if is_rhombus_style(styles.get(k, ""))
    }
    textos_tema = {canonical_topic(v) for v in temas_cells.values()}

    final_txt = postprocess_txt(
        render_blocks_txt(
            route_blocks, is_rhombus_key,
            temap_cells, temas_cells, textos_subt, textos_tema,
        )
    )
    txt_path.write_text(final_txt, encoding="utf-8")

    image_nodes = sum(1 for v in vertices.values() if IMAGE_LINE_RE.match(v))
    print(f"txt generado: {txt_path}")
    print(f"temaP detectados: {len(temap_cells)}, temaS detectados: {len(temas_cells)}")
    print(f"{len(route_blocks)} bloques, {image_nodes} imagen(es) marcadas como {IMAGE_PLACEHOLDER}")
    print("el .drawio no fue modificado")


if __name__ == "__main__":
    main()