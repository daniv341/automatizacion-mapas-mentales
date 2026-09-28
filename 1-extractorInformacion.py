import os
import logging
from dataclasses import dataclass
from typing import List, Tuple
from tkinter import Tk, filedialog

import pymupdf as fitz  # PyMuPDF

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Estructura auxiliar para representar un bloque de texto
# ---------------------------------------------------------------------------

@dataclass
class Bloque:
    x0: float
    y0: float
    x1: float
    y1: float
    texto: str

    @property
    def ancho(self) -> float:
        return self.x1 - self.x0


# ---------------------------------------------------------------------------
# Detección de orden de lectura (con columnas automáticas)
# ---------------------------------------------------------------------------

def _obtener_bloques_de_texto(page: "fitz.Page") -> List[Bloque]:
    """Devuelve los bloques de texto (no imágenes) de una página."""
    crudos = page.get_text("blocks")  # (x0, y0, x1, y1, texto, block_no, block_type)
    bloques = []
    for b in crudos:
        x0, y0, x1, y1, texto = b[0], b[1], b[2], b[3], b[4]
        tipo = b[6] if len(b) > 6 else 0
        texto = texto.strip()
        if tipo == 0 and texto:
            bloques.append(Bloque(x0, y0, x1, y1, texto))
    return bloques


def _es_bloque_ancho(bloque: Bloque, ancho_pagina: float, umbral: float = 0.75) -> bool:
    """Un bloque que ocupa la mayor parte del ancho de página se trata como
    elemento de una sola columna (título, encabezado, párrafo suelto) y
    actúa como un corte entre tramos con columnas."""
    return bloque.ancho >= ancho_pagina * umbral


def _agrupar_en_columnas(bloques: List[Bloque], ancho_pagina: float,
                          gap_ratio: float = 0.04) -> List[Tuple[float, float]]:
    """Agrupa bloques 'angostos' en columnas según su borde izquierdo (x0).

    Devuelve una lista de rangos (x_min, x_max), uno por columna detectada,
    ordenados de izquierda a derecha.
    """
    if not bloques:
        return []

    xs = sorted(b.x0 for b in bloques)
    gap_min = ancho_pagina * gap_ratio

    grupos = [[xs[0]]]
    for x in xs[1:]:
        if x - grupos[-1][-1] > gap_min:
            grupos.append([x])
        else:
            grupos[-1].append(x)

    columnas = []
    for grupo in grupos:
        x_min, x_max_grupo = min(grupo), max(grupo)
        x_max = max(b.x1 for b in bloques if x_min <= b.x0 <= x_max_grupo)
        columnas.append((x_min, x_max))

    return sorted(columnas, key=lambda c: c[0])


def _indice_columna(bloque: Bloque, columnas: List[Tuple[float, float]]) -> int:
    """Índice de la columna cuyo borde izquierdo está más cerca del bloque."""
    distancias = [abs(bloque.x0 - c[0]) for c in columnas]
    return distancias.index(min(distancias))


def ordenar_bloques_pagina(page: "fitz.Page") -> List[str]:
    """Devuelve los textos de una página en orden de lectura real,
    detectando columnas automáticamente cuando existen."""
    bloques = _obtener_bloques_de_texto(page)
    if not bloques:
        return []

    ancho_pagina = page.rect.width

    anchos = sorted(
        (b for b in bloques if _es_bloque_ancho(b, ancho_pagina)),
        key=lambda b: b.y0,
    )
    angostos = [b for b in bloques if not _es_bloque_ancho(b, ancho_pagina)]
    columnas = _agrupar_en_columnas(angostos, ancho_pagina)

    resultado: List[str] = []

    def _emitir_segmento(y_desde: float, y_hasta: float) -> None:
        """Emite los bloques angostos con y0 en [y_desde, y_hasta),
        agrupados por columna (izq. a der.) y cada columna de arriba a
        abajo."""
        segmento = [b for b in angostos if y_desde <= b.y0 < y_hasta]
        if not segmento:
            return
        if columnas:
            por_columna = {i: [] for i in range(len(columnas))}
            for b in segmento:
                por_columna[_indice_columna(b, columnas)].append(b)
            for idx in sorted(por_columna):
                for b in sorted(por_columna[idx], key=lambda bl: bl.y0):
                    resultado.append(b.texto)
        else:
            for b in sorted(segmento, key=lambda bl: bl.y0):
                resultado.append(b.texto)

    y_actual = float("-inf")
    for bloque_ancho in anchos:
        _emitir_segmento(y_actual, bloque_ancho.y0)
        resultado.append(bloque_ancho.texto)
        y_actual = bloque_ancho.y0 + 0.01  # evita reprocesar el mismo bloque

    _emitir_segmento(y_actual, float("inf"))

    return resultado


# ---------------------------------------------------------------------------
# Extracción de texto (en orden) e imágenes
# ---------------------------------------------------------------------------

def extraer_texto_ordenado(pdf_path: str, output_txt: str,
                            incluir_marcador_pagina: bool = True) -> None:
    """Extrae el texto del PDF respetando el orden de lectura real."""
    doc = fitz.open(pdf_path)
    try:
        with open(output_txt, "w", encoding="utf-8") as f:
            for num_pagina, page in enumerate(doc, start=1):
                textos = ordenar_bloques_pagina(page)
                if incluir_marcador_pagina:
                    f.write(f"\n--- Página {num_pagina} ---\n\n")
                f.write("\n\n".join(textos))
                f.write("\n\n")
    finally:
        doc.close()
    logger.info("Texto extraído y ordenado guardado en: %s", output_txt)


def extraer_imagenes(pdf_path: str, output_img_folder: str) -> int:
    """Extrae todas las imágenes del PDF a una carpeta. Devuelve el total."""
    os.makedirs(output_img_folder, exist_ok=True)
    doc = fitz.open(pdf_path)
    total = 0
    try:
        for num_pagina, page in enumerate(doc, start=1):
            for img_index, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]
                base_image = doc.extract_image(xref)
                img_data = base_image["image"]
                img_ext = base_image["ext"]
                nombre = f"pagina_{num_pagina}_img_{img_index}.{img_ext}"
                ruta = os.path.join(output_img_folder, nombre)
                with open(ruta, "wb") as img_file:
                    img_file.write(img_data)
                total += 1
    finally:
        doc.close()
    logger.info("%d imagen(es) guardadas en: %s", total, output_img_folder)
    return total


def extraer_texto_subrayado(pdf_path: str, output_txt_path: str) -> List[str]:
    """Extrae únicamente el texto subrayado del PDF."""
    doc = fitz.open(pdf_path)
    textos_subrayados: List[str] = []
    try:
        for page in doc:
            anotaciones = page.annots()
            if not anotaciones:
                continue
            pagina_textos = []
            for annot in anotaciones:
                if annot.type[0] == 8:  # 8 = subrayado
                    texto = page.get_textbox(annot.rect).strip()
                    if texto:
                        pagina_textos.append(texto)
            if pagina_textos:
                textos_subrayados.extend(pagina_textos)
                textos_subrayados.append("")  # separador entre páginas
    finally:
        doc.close()

    with open(output_txt_path, "w", encoding="utf-8") as f:
        for texto in textos_subrayados:
            f.write(texto + "\n")

    return textos_subrayados


# ---------------------------------------------------------------------------
# CONFIGURACIÓN — editá estas variables según lo que necesites
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    OPCION = 1  # 1 = texto (ordenado) + imágenes | 2 = solo texto subrayado

    Tk().withdraw()  # oculta la ventana principal vacía de tkinter
    txt = filedialog.askopenfilename(
        title="Selecciona el archivo",
        filetypes=[("Archivos pdf", "*.pdf"), ("Todos los archivos", "*.*")]
    )

    # Cambiá esta variable por la ruta del PDF que quieras procesar
    PDF_ARCHIVO = txt

    OUTPUT_TXT = "modificables/borrador.txt"
    OUTPUT_IMG_FOLDER = "imagenes_salida"

    if not os.path.isfile(PDF_ARCHIVO):
        logger.error("No se encontró el archivo: %s", PDF_ARCHIVO)
    elif OPCION == 1:
        extraer_texto_ordenado(PDF_ARCHIVO, OUTPUT_TXT)
        extraer_imagenes(PDF_ARCHIVO, OUTPUT_IMG_FOLDER)
    elif OPCION == 2:
        extraer_texto_subrayado(PDF_ARCHIVO, OUTPUT_TXT)
        logger.info("Texto subrayado guardado en: %s", OUTPUT_TXT)
    else:
        logger.error("Opción inválida, elegí 1 o 2.")