"""
ExtractorMP.py
--------------
Convierte un mapa mental de Draw.io (.drawio) en un árbol de texto plano,
respetando el orden espacial (Y y luego X) en el que fueron dibujados los
nodos, tal como se arman los mapas de "árbol vertical" en Draw.io.

Uso desde la terminal:
    python ExtractorMP.py
        -> usa el archivo de entrada/salida configurados en DEFAULT_INPUT_FILE
           y DEFAULT_OUTPUT_FILE (mismo comportamiento que antes).

    python ExtractorMP.py "archivos/otro_mapa.drawio"
        -> usa ese archivo de entrada, con la salida por defecto.

    python ExtractorMP.py "archivos/otro_mapa.drawio" "salida2.txt"
        -> elige tanto entrada como salida.

Nota: si un nodo con estilo "temaP" (color #274B66) no tiene título, no se
va a incluir en el árbol (se requiere que todo nodo temaP tenga texto).
"""

import argparse
import html
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict

# ============================================================
#  CONFIGURACIÓN POR DEFECTO (mismo comportamiento que la versión anterior)
# ============================================================
DEFAULT_INPUT_FILE = "archivos/1 - Sistemas de Control.drawio"
DEFAULT_OUTPUT_FILE = "evaluador/guardados/unidad 1 TC.txt"

# Estilos de Draw.io -> prefijo que se agrega al valor limpio del nodo.
# Se evalúan en este orden y son acumulables (un nodo podría matchear más
# de uno), igual que en la versión original.
STYLE_PREFIXES = (
    ("rhombus", "subT "),                  # Subtítulo / sub-tema
    ("#274B66", "temaP "),                 # Tema principal
    ("gradientDirection=south", "temaS "), # Tema secundario
)

# Diferencia mínima en X para considerar que una rama "saltó" de columna
# y por lo tanto conviene separarla con una línea en blanco.
SALTO_X = 10

# Tags "de bloque" o de salto de línea: se reemplazan por un espacio para
# no pegar dos palabras que quedaban separadas visualmente.
HTML_TAGS_BLOQUE_RE = re.compile(r"</div>|<div[^>]*>|<br\s*/?>", re.IGNORECASE)
# Cualquier otro tag HTML (span, font, b, i, etc.): se elimina directamente,
# ya que son solo de estilo y no representan un salto de texto.
HTML_TAGS_RESTO_RE = re.compile(r"<[^>]+>")
PUNCT_RE = re.compile(r"[«»¿?,.;]")


# ============================================================
#  1) LIMPIEZA DE FLECHAS DUPLICADAS
# ============================================================
def _elemento_por_id(root, element_id):
    """Busca un elemento por su ID dentro del XML."""
    for elem in root.iter():
        if elem.attrib.get("id") == element_id:
            return elem
    return None


def _posicion_x(elemento):
    """Coordenada X de un elemento, o -inf si no la tiene."""
    if elemento is not None:
        geometry = elemento.find("mxGeometry")
        if geometry is not None and "x" in geometry.attrib:
            try:
                return float(geometry.attrib["x"])
            except ValueError:
                pass
    return float("-inf")


def eliminar_flechas_duplicadas(root):
    """
    Cuando un nodo recibe más de una flecha (residuos de ediciones previas
    del mapa), nos quedamos solo con la que viene del nodo ubicado más a la
    derecha (mayor X) y descartamos el resto. Se modifica el árbol XML
    directamente en memoria (ya no hace falta escribir un .drawio auxiliar).
    """
    entrantes = defaultdict(list)
    for elem in root.iter():
        if elem.attrib.get("edge") == "1":
            source, target = elem.attrib.get("source"), elem.attrib.get("target")
            if source and target:
                entrantes[target].append((source, elem))

    for _, flechas in entrantes.items():
        if len(flechas) <= 1:
            continue
        ordenadas = sorted(
            flechas,
            key=lambda par: _posicion_x(_elemento_por_id(root, par[0])),
            reverse=True,
        )
        for _, flecha in ordenadas[1:]:
            padre = next((p for p in root.iter() if flecha in list(p)), None)
            if padre is not None:
                padre.remove(flecha)


# ============================================================
#  2) LIMPIEZA DE TEXTO
# ============================================================
def limpiar_texto(texto):
    """Quita tags HTML (cualquiera, no solo div/br), entidades, acentos y signos."""
    texto = html.unescape(texto)  # &nbsp; &amp; etc. -> caracter real
    texto = texto.replace("\xa0", " ")  # nbsp ya decodificado -> espacio normal
    texto = HTML_TAGS_BLOQUE_RE.sub(" ", texto)
    texto = HTML_TAGS_RESTO_RE.sub("", texto)
    texto = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    texto = PUNCT_RE.sub("", texto)
    texto = re.sub(r"\s+", " ", texto)  # normaliza espacios múltiples
    return texto.lower().strip()


def _es_ruido(valor):
    """
    Nodos que no aportan información real al diagrama: vacíos o compuestos
    solo por números (típicamente marcadores/numeritos decorativos del
    mapa, sin relación con el contenido).
    """
    return not valor or bool(re.fullmatch(r"\d+", valor))


# ============================================================
#  3) PARSEO DEL .drawio A NODOS / CONEXIONES / POSICIONES
# ============================================================
def analizar_drawio(file_path):
    """Parsea el .drawio una sola vez y devuelve (nodos, conexiones, posiciones)."""
    tree = ET.parse(file_path)
    root = tree.getroot()

    eliminar_flechas_duplicadas(root)

    graph_model = root.find(".//mxGraphModel")
    nodos, posiciones, conexiones = {}, {}, []
    if graph_model is None:
        return nodos, conexiones, posiciones

    contenedor = graph_model.find("root")
    if contenedor is None:
        return nodos, conexiones, posiciones

    for cell in contenedor.findall("mxCell"):
        cell_id = cell.get("id")
        valor_crudo = (cell.get("value") or "").strip()
        style = cell.get("style", "")
        source, target = cell.get("source"), cell.get("target")
        geometry = cell.find("mxGeometry")

        es_imagen = "shape=image" in style

        if valor_crudo or es_imagen:
            valor = limpiar_texto(valor_crudo)
            if es_imagen:
                valor = f"<imagen> {valor}".strip()
            for marca, prefijo in STYLE_PREFIXES:
                if marca in style:
                    valor = f"{prefijo}{valor}".strip()

            nodos[cell_id] = valor
            if geometry is not None:
                x = float(geometry.get("x", 0))
                y = float(geometry.get("y", 0))
                posiciones[cell_id] = (x, y)

        if source and target:
            conexiones.append((source, target))

    return nodos, conexiones, posiciones


# ============================================================
#  4) CONSTRUCCIÓN DEL ÁRBOL DE TEXTO
# ============================================================
def _orden(nodo_id, posiciones):
    x, y = posiciones.get(nodo_id, (0, 0))
    return (y, x)


def recorrer_nodo(nodo_id, hijos, nodos, posiciones, x_previo=None):
    """
    Recorre un nodo y sus descendientes en profundidad. Los nodos "ruido"
    (vacíos o puramente numéricos) no generan línea propia, pero sus hijos
    igual se recorren con normalidad.
    """
    lineas = []
    valor = nodos.get(nodo_id, "")
    x_actual = posiciones.get(nodo_id, (0, 0))[0]

    if _es_ruido(valor):
        siguiente_x_previo = x_previo
    else:
        if x_previo is not None and abs(x_actual - x_previo) > SALTO_X:
            lineas.append("")
        if valor.startswith("subT "):
            lineas.append("")  # separación extra antes de cada subT
        lineas.append(valor)
        siguiente_x_previo = x_actual

    hijos_ordenados = sorted(hijos.get(nodo_id, []), key=lambda n: _orden(n, posiciones))
    for hijo_id in hijos_ordenados:
        lineas.extend(recorrer_nodo(hijo_id, hijos, nodos, posiciones, siguiente_x_previo))

    return lineas


def construir_arbol(nodos, conexiones, posiciones):
    hijos = defaultdict(list)
    tienen_padre = set()

    for source, target in conexiones:
        hijos[source].append(target)
        tienen_padre.add(target)

    raices = sorted(set(nodos) - tienen_padre, key=lambda n: _orden(n, posiciones))

    lineas = []
    for raiz in raices:
        lineas.extend(recorrer_nodo(raiz, hijos, nodos, posiciones))
        lineas.append("")  # separador entre ramas principales

    return lineas


# ============================================================
#  5) PROLIJIDAD FINAL Y GUARDADO
# ============================================================
def colapsar_lineas_en_blanco(lineas):
    """Evita líneas en blanco consecutivas y recorta blancos al inicio/final."""
    resultado = []
    anterior_en_blanco = True  # arranca en True para descartar blancos iniciales
    for linea in lineas:
        en_blanco = linea.strip() == ""
        if en_blanco and anterior_en_blanco:
            continue
        resultado.append(linea)
        anterior_en_blanco = en_blanco

    while resultado and resultado[-1].strip() == "":
        resultado.pop()

    return resultado


def guardar_txt(lineas, output_path):
    carpeta = os.path.dirname(output_path)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as archivo:
        archivo.write("\n".join(lineas))


# ============================================================
#  6) PUNTO DE ENTRADA (terminal, sin interfaz gráfica)
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Convierte un mapa mental de Draw.io (.drawio) en un árbol de texto plano."
    )
    parser.add_argument(
        "entrada", nargs="?", default=DEFAULT_INPUT_FILE,
        help=f"Archivo .drawio de entrada (por defecto: {DEFAULT_INPUT_FILE})",
    )
    parser.add_argument(
        "salida", nargs="?", default=DEFAULT_OUTPUT_FILE,
        help=f"Archivo .txt de salida (por defecto: {DEFAULT_OUTPUT_FILE})",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.entrada):
        print(f"No se encontró el archivo de entrada: {args.entrada}")
        sys.exit(1)

    nodos, conexiones, posiciones = analizar_drawio(args.entrada)
    lineas = construir_arbol(nodos, conexiones, posiciones)
    lineas = colapsar_lineas_en_blanco(lineas)
    guardar_txt(lineas, args.salida)

    print(f"Archivo generado: {args.salida}")


if __name__ == "__main__":
    main()