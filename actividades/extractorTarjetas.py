import re
import html
import xml.etree.ElementTree as ET
from tkinter import Tk, filedialog

def extraer_tarjetas(xml_path):
    """
    Lee un archivo .xml de drawio y extrae temas (rhombus) y tarjetas (shape=card)
    en el orden en que aparecen (por id), asociando cada tarjeta al último tema
    encontrado antes que ella, tal como se generan con generar_tarjetas.py.
    Devuelve una lista de bloques: [{"tema": str, "tarjetas": [(primero, segundo), ...]}]
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    pagina = "Página-2"  # nombre de la página a extraer

    diagrama = None
    for d in root.iter("diagram"):
        if d.get("name") == pagina:
            diagrama = d
            break

    if diagrama is None:
        raise ValueError(f"No se encontró la página '{pagina}' en el archivo")

    celdas = []
    for cell in diagrama.iter("mxCell"):
        if cell.get("vertex") != "1":  # solo nos interesan los cuadros, no las flechas
            continue

        estilo = cell.get("style", "")
        valor = cell.get("value", "")
        id_cell = cell.get("id")

        # Determinar tipo según la etiqueta del estilo
        if "rhombus" in estilo:
            tipo = "tema"
        elif "shape=card" in estilo:
            tipo = "tarjeta"
        else:
            continue  # no es ni tema ni tarjeta, se ignora

        celdas.append((id_cell, tipo, valor))

    # Ordenar por id numérico cuando sea posible, para respetar el orden secuencial de creación
    def clave_orden(item):
        id_cell = item[0]
        try:
            return int(id_cell)
        except (TypeError, ValueError):
            return float("inf")

    celdas.sort(key=clave_orden)

    bloques = []
    bloque_actual = None

    for id_cell, tipo, valor in celdas:
        valor = html.unescape(valor).strip()

        if tipo == "tema":
            bloque_actual = {"tema": valor, "tarjetas": []}
            bloques.append(bloque_actual)
            continue

        # tipo == "tarjeta": separar en 1ro y 2do usando los saltos <br>
        partes = re.split(r"<br\s*/?>", valor, flags=re.IGNORECASE)
        partes = [p.strip() for p in partes if p.strip() != ""]

        if len(partes) >= 2:
            primero, segundo = partes[0], partes[1]
        elif len(partes) == 1:
            primero, segundo = partes[0], ""
        else:
            continue  # tarjeta vacía, se ignora

        if bloque_actual is None:
            # Tarjeta encontrada antes de cualquier tema: se agrupa sin tema asociado
            bloque_actual = {"tema": "", "tarjetas": []}
            bloques.append(bloque_actual)

        bloque_actual["tarjetas"].append((primero, segundo))

    return bloques


def generar_recordatorio(bloques, salida):
    with open(salida, "w", encoding="utf-8") as archivo:
        for bloque in bloques:
            if bloque["tema"]:
                archivo.write(f"subT {bloque['tema']}\n")
            for primero, segundo in bloque["tarjetas"]:
                if segundo:
                    archivo.write(f"{primero} + {segundo}\n")
                else:
                    archivo.write(f"{primero}\n")
            archivo.write("\n")  # línea en blanco entre bloques

    print(f"{salida} creado con {len(bloques)} tema(s)")


# ---------------- CONFIGURACIÓN ----------------



Tk().withdraw()  # oculta la ventana principal vacía de tkinter
xml_entrada = filedialog.askopenfilename(
    title="Selecciona el archivo drawio/xml",
    filetypes=[("Drawio/XML", "*.xml *.drawio"), ("Todos los archivos", "*.*")]
)
txt_salida = "./recordatorio.txt"

bloques = extraer_tarjetas(xml_entrada)
generar_recordatorio(bloques, txt_salida)