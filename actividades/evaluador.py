"""
evaluador.py
------------
Herramienta de estudio en modo terminal. Toma el árbol de texto generado por
ExtractorMP.py (salida.txt) y lo agrupa en "temas" según los títulos "subT",
ofreciendo distintos modos de autoevaluación.

Uso desde la terminal:
    python evaluador.py
        -> usa el archivo por defecto (DEFAULT_INPUT_FILE)

    python evaluador.py "otra_salida.txt"
        -> usa ese archivo en particular
"""

import argparse
import os
import random
import re
from typing import Dict, List, Optional, Tuple

# ============================================================
#  CONFIGURACIÓN
# ============================================================
RESERVADO_TITULO = "subT"  # marca que usa ExtractorMP.py para identificar títulos
DEFAULT_INPUT_FILE = "evaluador/guardados/unidad 2 TC.txt"
COMENTARIO_FILE = "evaluador/comentario.txt"
EVALUACION_FILE = "evaluador/evaluacion.txt"

LONGITUD_MIN_PALABRA_CLAVE = 4  # se ignoran palabras de esta longitud o menos
CANTIDAD_PALABRAS_CLAVE = 3


# ============================================================
#  1) CARGA Y PARSEO DE TEMAS
# ============================================================
def cargar_temas(nombre_archivo: str) -> Dict[str, str]:
    """
    Lee el .txt generado por ExtractorMP y agrupa el contenido bajo cada
    título "subT" encontrado. Si un título se repite, se distingue con un
    sufijo numerado (ej: "subT ejemplo (2)").
    """
    with open(nombre_archivo, "r", encoding="utf-8") as archivo:
        lineas = archivo.readlines()

    temas: Dict[str, str] = {}
    contador_titulos: Dict[str, int] = {}
    titulo_actual: Optional[str] = None
    contenido_actual: List[str] = []

    def guardar_tema_actual():
        if titulo_actual is not None:
            temas[titulo_actual] = "\n".join(contenido_actual)

    for linea in lineas:
        linea = linea.strip()
        if RESERVADO_TITULO in linea:
            guardar_tema_actual()
            if linea in temas:
                contador_titulos[linea] = contador_titulos.get(linea, 1) + 1
                titulo_actual = f"{linea} ({contador_titulos[linea]})"
            else:
                titulo_actual = linea
                contador_titulos[linea] = 1
            contenido_actual = []
        elif titulo_actual is not None:
            contenido_actual.append(linea)

    guardar_tema_actual()
    return temas


def detectar_bloques(texto: str) -> List[str]:
    """Divide un tema en bloques (separados por línea en blanco) e ignora los que solo tienen imágenes."""
    bloques = re.split(r"\n{2,}", texto)
    return [b.strip() for b in bloques if b.strip() and "<imagen>" not in b]


# ============================================================
#  2) COMPARADOR: SUBRAYAR PALABRAS COINCIDENTES
# ============================================================
def subrayar_palabras(texto: str, palabras: set) -> str:
    """Envuelve entre <> cada palabra coincidente, como palabra completa."""
    for palabra in palabras:
        texto = re.sub(rf"\b{re.escape(palabra)}\b", f"<{palabra}>", texto)
    return texto


def comparar_y_guardar(comentario: str, contenido: str, tema: str) -> None:
    """
    Compara las palabras del comentario del usuario contra el contenido real
    del tema, subraya las coincidencias y guarda ambos textos en disco
    (COMENTARIO_FILE / EVALUACION_FILE) para que el usuario pueda revisarlos.
    """
    texto_comentario = f"Tema: {tema}\n\n{comentario}"
    texto_contenido = f"Tema: {tema}\n{contenido}"

    coincidencias = set(texto_comentario.split()) & set(texto_contenido.split())

    comentario_subrayado = subrayar_palabras(texto_comentario, coincidencias)
    contenido_subrayado = subrayar_palabras(texto_contenido, coincidencias)

    carpeta = os.path.dirname(COMENTARIO_FILE)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    with open(COMENTARIO_FILE, "w", encoding="utf-8") as archivo:
        archivo.write(comentario_subrayado)
    with open(EVALUACION_FILE, "w", encoding="utf-8") as archivo:
        archivo.write(contenido_subrayado)


# ============================================================
#  3) COMPLETAR PALABRAS (fill-in-the-blank)
# ============================================================
def ocultar_palabras_clave(
    bloque: str, cantidad: int = CANTIDAD_PALABRAS_CLAVE
) -> Optional[Tuple[str, List[str]]]:
    """
    Elige al azar palabras "clave" (más largas que LONGITUD_MIN_PALABRA_CLAVE)
    dentro de un bloque y las reemplaza por guiones bajos. Devuelve el bloque
    oculto y las palabras elegidas (en su orden original dentro del bloque),
    o None si el bloque no tiene suficientes palabras para el ejercicio.
    """
    palabras = bloque.split()
    candidatas = [p for p in palabras if len(p) > LONGITUD_MIN_PALABRA_CLAVE - 1]

    if len(candidatas) <= 1:
        return None

    elegidas = random.sample(candidatas, min(cantidad, len(candidatas)))

    bloque_oculto = bloque
    for palabra in elegidas:
        bloque_oculto = bloque_oculto.replace(palabra, "______", 1)

    orden_original = sorted(elegidas, key=palabras.index)
    return bloque_oculto, orden_original


# ============================================================
#  4) EVALUADOR INTERACTIVO
# ============================================================
class Evaluador:
    def __init__(self, temas: Dict[str, str]):
        self.temas = temas
        self.titulos = list(temas.keys())

    # ---------- utilidades comunes ----------
    def _elegir_temas(self) -> List[str]:
        print("\nTemas disponibles:")
        for i, titulo in enumerate(self.titulos, 1):
            print(f"{i}. {titulo}")
        seleccion = input("Ingrese los números de los temas separados por espacios: ")
        indices = [int(x) for x in seleccion.split() if x.isdigit() and 1 <= int(x) <= len(self.titulos)]
        return [self.titulos[i - 1] for i in indices]

    def _bloques_de_temas(self, temas_seleccionados: List[str]) -> List[Tuple[str, str]]:
        bloques = [
            (tema, bloque)
            for tema in temas_seleccionados
            for bloque in detectar_bloques(self.temas[tema])
        ]
        random.shuffle(bloques)
        return bloques

    @staticmethod
    def _pedir_comentario() -> str:
        print("Escriba su comentario y finalice con '&'")
        lineas = []
        while True:
            linea = input()
            if linea == "&":
                break
            lineas.append(linea)
        return "\n".join(lineas)

    @staticmethod
    def _desea_continuar() -> bool:
        return input("¿Desea continuar en este modo? (s/n): ").strip().lower() == "s"

    # ---------- modo 1 y 2: comentar temas ----------
    def modo_comentar(self, aleatorio: bool):
        while True:
            orden = list(enumerate(self.titulos, 1))
            if aleatorio:
                random.shuffle(orden)
                print("\nOrden aleatorio de los temas:")
                for num, titulo in orden:
                    print(f"{num}. {titulo}")
                seleccionados = orden
            else:
                print("\nTemas disponibles:")
                for num, titulo in orden:
                    print(f"{num}. {titulo}")
                seleccion = input("Ingrese los números de los temas separados por espacios: ")
                indices = [int(x) for x in seleccion.split() if x.isdigit() and 1 <= int(x) <= len(self.titulos)]
                seleccionados = [(i, self.titulos[i - 1]) for i in indices]

            total = len(seleccionados)
            for pos, (num, titulo) in enumerate(seleccionados, 1):
                print(f"\nTema {num}. {titulo} — quedan {total - pos} por ver")
                comentario = self._pedir_comentario()
                comparar_y_guardar(comentario, self.temas[titulo], titulo)

            if not self._desea_continuar():
                break

    # ---------- modo 3: identificar a qué tema pertenece un bloque ----------
    def modo_identificar_bloques(self):
        while True:
            temas_seleccionados = self._elegir_temas()
            if not temas_seleccionados:
                print("No se seleccionó ningún tema válido.")
                continue

            bloques = self._bloques_de_temas(temas_seleccionados)
            aciertos = 0

            for tema_correcto, bloque in bloques:
                while True:
                    print("-" * 60)
                    print(
                        "¿A qué subtema pertenece este bloque? "
                        "(si no sabe la respuesta o no hay mucho contexto, presione 0)"
                    )
                    for i, subtema in enumerate(temas_seleccionados, 1):
                        print(f"{i}. {subtema}")
                    print(f"\n{bloque}\n")

                    respuesta = input("Ingrese el número correspondiente: ").strip()
                    if respuesta == "0":
                        print(f"\n😐 El tema correcto era: {tema_correcto}\n")
                        break
                    if respuesta.isdigit() and 1 <= int(respuesta) <= len(temas_seleccionados):
                        elegido = temas_seleccionados[int(respuesta) - 1]
                        if elegido == tema_correcto:
                            print("\n✅ Respuesta correcta!\n")
                            aciertos += 1
                        else:
                            print(f"\n❌ Incorrecto. El tema correcto era: {tema_correcto}\n")
                        break
                    print("\nSelección no válida.\n")

            print(f"Resultado de esta ronda: {aciertos}/{len(bloques)} aciertos\n")
            if not self._desea_continuar():
                break

    # ---------- modo 4: completar palabras clave ----------
    def modo_completar_palabras(self):
        while True:
            temas_seleccionados = self._elegir_temas()
            if not temas_seleccionados:
                print("No se seleccionó ningún tema válido.")
                continue
            print(f"\nTemas seleccionados: {', '.join(temas_seleccionados)}")

            bloques = self._bloques_de_temas(temas_seleccionados)
            aciertos = exactos = evaluados = 0

            for tema_correcto, bloque in bloques:
                resultado = ocultar_palabras_clave(bloque)
                if resultado is None:
                    continue
                bloque_oculto, palabras_clave = resultado
                evaluados += 1

                while True:
                    print("\n" + "-" * 60)
                    print(f"tema: {tema_correcto}")
                    print(f"\n{bloque_oculto}\n")
                    entrada = input(
                        "Ingrese las palabras faltantes separadas por espacios "
                        "(0 = no sé, ` = pista): "
                    ).split()

                    if not entrada:
                        print("Ingrese al menos una palabra, '0' o '`'.\n")
                        continue
                    if entrada[0] == "0":
                        print(f"\n😐 Las palabras correctas eran: {' - '.join(palabras_clave)}\n")
                        break
                    if entrada[0] == "`":
                        print(f"pista: {palabras_clave[0]}\n")
                        continue

                    respuestas = {r.strip() for r in entrada}
                    correctas = set(palabras_clave)
                    faltantes = correctas - respuestas
                    sobrantes = respuestas - correctas

                    if respuestas == correctas:
                        print("✅✅✅ Respuesta EXACTA!")
                        exactos += 1
                        aciertos += 1
                    elif len(respuestas & correctas) >= 2:
                        print(
                            "✅ Respondiste bien, pero no del todo.\n"
                            f"Te faltaron: {' - '.join(faltantes) or '-'}\n"
                            f"Te sobraron: {' - '.join(sobrantes) or '-'}"
                        )
                        aciertos += 1
                    else:
                        print(
                            f"❌ Incorrecto. Las palabras correctas eran: {' - '.join(palabras_clave)}\n"
                            f"Te faltaron: {' - '.join(faltantes) or '-'}\n"
                            f"Te sobraron: {' - '.join(sobrantes) or '-'}"
                        )
                    break

            print(f"\nAciertos: {aciertos}/{evaluados} - Exactos: {exactos}/{evaluados}\n")
            if not self._desea_continuar():
                break

    # ---------- loop principal ----------
    def ejecutar(self):
        modos = {
            "1": lambda: self.modo_comentar(aleatorio=False),
            "2": lambda: self.modo_comentar(aleatorio=True),
            "3": self.modo_identificar_bloques,
            "4": self.modo_completar_palabras,
        }
        while True:
            print("\nSeleccione un modo:")
            print("1. Elegir varios temas y comentar")
            print("2. Mostrar títulos en orden aleatorio y comentar")
            print("3. Mostrar bloques y preguntar a qué tema/s pertenecen")
            print("4. Completar palabras")
            print("5. Salir")
            opcion = input("Ingrese el número de la opción: ").strip()

            if opcion == "5":
                break
            accion = modos.get(opcion)
            if accion is None:
                print("Opción no válida.")
                continue
            accion()


# ============================================================
#  5) PUNTO DE ENTRADA (terminal, sin interfaz gráfica)
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Evaluador interactivo de temas extraídos con ExtractorMP.py."
    )
    parser.add_argument(
        "archivo", nargs="?", default=DEFAULT_INPUT_FILE,
        help=f"Archivo de temas a evaluar (por defecto: {DEFAULT_INPUT_FILE})",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.archivo):
        print(f"No se encontró el archivo: {args.archivo}")
        return

    temas = cargar_temas(args.archivo)
    if not temas:
        print(f"No se encontraron temas ('{RESERVADO_TITULO}') en {args.archivo}.")
        return

    Evaluador(temas).ejecutar()


if __name__ == "__main__":
    main()
