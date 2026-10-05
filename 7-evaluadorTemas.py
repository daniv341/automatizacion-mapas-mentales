import argparse
import json
import os
import re
import sys
import importlib.util
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
try:
    from tkinter import Tk, filedialog
except ImportError:   # p. ej. servidor sin tkinter: solo se puede usar --config
    Tk = filedialog = None

# ---------------- CONFIG EVALUACIÓN DE RESPUESTAS ESCRITAS ----------------
# Fracción de claves del 'segundo' para llegar a "Casi"
# (100% = "Bien"; por debajo de UMBRAL_CASI = "Mal")
UMBRAL_CASI = 0.5
# Similitud (Dice) con el contenido del bloque que, combinada con claves
# >= UMBRAL_CASI, premia parafrasear bien y da "Bien"
SIM_ALTA = 0.7
# Cuando aparecen TODAS las claves: coincidencia mínima (Dice) para "Bien"
# si NO se usó "Mostrar pista" (por debajo => "Casi")
SIM_SIN_PISTA = 0.5
# Igual, pero si SÍ se usó "Mostrar pista": la coincidencia debe ser MAYOR a
# este valor (si no llega => "Casi")
SIM_CON_PISTA = 0.6

# ---------------- CONFIG ECONOMÍA (tienda) ----------------
# Valores provisorios. Orden de precio: pista < segunda oportunidad < escudo < comodín.
PRECIO_COMODIN = 100
PRECIO_PISTA_GRATIS = 50
PRECIO_ESCUDO_RACHA = 90
PRECIO_SEGUNDA_OPORTUNIDAD = 70
# Fracción de los puntos que se cobra al acertar el reintento de la segunda oportunidad
FACTOR_RECOMPENSA_REINTENTO = 0.5

# Palabras vacías que se ignoran al evaluar (claves, respuesta escrita y texto
# de referencia de las rutas). Solo afecta la evaluación: el texto de las rutas
# se muestra completo. Van sin acentos (mismo formato que normalizar_palabra).
STOPWORDS_ES = {
    # artículos
    "el", "la", "los", "las",
    # preposiciones
    "a", "con", "de", "del", "en", "para", "por", "sin", "so", "tras", "via",
    # conjunciones
    "y", "e", "ni", "que", "o", "u", "pero", "mas", "aunque", "sino", "si", "pues", "ya",
    # pronombres comunes
    "me", "te", "se", "nos", "os", "le", "les", "lo", "mi", "tu", "su", "mis", "tus", "sus",
    "esto", "eso", "aquello",
}

# ============================================================
# UTILIDADES DE RESALTADO (claves extraídas del campo 'segundo')
# ============================================================

def normalizar_palabra(palabra):
    """Minúsculas y sin acentos, para comparar 'ANALÓGICA' con 'analogica'."""
    forma = unicodedata.normalize('NFD', palabra.lower())
    return ''.join(c for c in forma if unicodedata.combining(c) == 0)


def extraer_claves(segundo):
    """
    Extrae palabras clave del campo 'segundo' de la tarjeta.
    Tokeniza solo letras: los números solos ('1', '2') se descartan solos.
    Devuelve claves normalizadas (minúsculas, sin acentos), sin duplicados y
    sin palabras vacías (STOPWORDS_ES).
    """
    if not segundo:
        return []
    claves = []
    for token in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+', segundo):
        normalizada = normalizar_palabra(token)
        if normalizada in STOPWORDS_ES:
            continue
        if normalizada not in claves:
            claves.append(normalizada)
    return claves


# ============================================================
# PARSEO DE RESPUESTAS.TXT (rutas del diagrama)
# ============================================================

PATRON_CABECERA_RUTA = re.compile(r'^ruta\s+(\d+)\s*$', re.IGNORECASE)


def preparar_linea_ruta(linea):
    """
    Normaliza los renglones de imagen.
    Acepta 'IMG:data:image/png,...' (sin ;base64) y también
    'IMG:data:image/png;base64,...', devolviendo siempre un data URL válido.
    """
    if linea.startswith("IMG:data:image/"):
        datos = linea[len("IMG:"):]
        if ";base64," not in datos:
            coma = datos.find(",")
            if coma != -1:
                datos = datos[:coma] + ";base64" + datos[coma:]
        return "IMG:" + datos
    return linea


def parsear_respuestas(txt):
    """
    Lee respuestas.txt y arma un diccionario {numero_ruta: {"tema": ..., "lineas": [...]}}.
    - Renglón 1 del bloque: 'ruta N' (mayúsculas o minúsculas).
    - Renglón 2: tema (solo organización interna, NUNCA se muestra en la UI).
    - Renglones siguientes: contenido (texto e imágenes intercaladas).
    Si una ruta está duplicada en el archivo, gana la primera aparición.
    """
    rutas = {}

    try:
        # utf-8-sig: tolera archivos guardados con Bloc de notas (que agregan BOM)
        with open(txt, "r", encoding="utf-8-sig") as archivo:
            lineas = archivo.readlines()
    except (FileNotFoundError, TypeError):
        return rutas

    ruta_actual = None
    tema_actual = ""
    contenido = []
    esperando_tema = False

    def cerrar_bloque():
        nonlocal ruta_actual, tema_actual, contenido, esperando_tema
        if ruta_actual is not None and contenido:
            if ruta_actual not in rutas:  # duplicada: gana la primera
                rutas[ruta_actual] = {"tema": tema_actual, "lineas": contenido}
        ruta_actual = None
        tema_actual = ""
        contenido = []
        esperando_tema = False

    for renglon in lineas:
        renglon = renglon.strip()

        m = PATRON_CABECERA_RUTA.match(renglon)
        if m:
            cerrar_bloque()
            ruta_actual = int(m.group(1))
            esperando_tema = True
            continue

        if not renglon:  # línea vacía: separador, se ignora
            continue

        if esperando_tema:
            tema_actual = renglon  # se procesa pero no se muestra
            esperando_tema = False
            continue

        contenido.append(preparar_linea_ruta(renglon))

    cerrar_bloque()
    return rutas


# ============================================================
# PARSEO DE RECORDATORIO.TXT (tarjetas)
# ============================================================

# '&' + números al final; tolera coma/punto/punto y coma sueltos al final:
# '&1,4' / '&1, 23, 24, 46,' / '&3.'
PATRON_RUTAS_TARJETA = re.compile(r'&\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*[.,;]*\s*$')
# '&' suelto al final (también tolera comas sobrantes: '&,' o '& , ')
PATRON_AMPERSAND_SUELTO = re.compile(r'&[\s,;]*$')


def parsear_recordatorio(txt):
    """
    Lee recordatorio.txt y arma una lista de tarjetas.
    - 'subT' define el tema de las tarjetas siguientes.
    - Cada tarjeta es 'primero + segundo' y opcionalmente termina con '&N,M'
      indicando las rutas asociadas.
    - NUEVO: opcionalmente, después de todo lo anterior, '| nota' agrega un
      apunte personal. Formato completo:  primero + segundo &1,4 | nota
      La línea se corta en el PRIMER '|' ANTES de cualquier otro análisis: lo de
      la izquierda sigue el flujo de siempre y lo de la derecha es la nota
      (puede contener '+', '&', 'subT' o más '|' sin problema). La nota no
      participa en claves, hash de difíciles ni resaltado del drawio.
      El texto literal '\\n' dentro de la nota se convierte en salto de línea.
    - Un '&' final SIN números se limpia de la tarjeta (no se muestra) y se
      avisa por consola que esa tarjeta no tendrá botón 'Mostrar respuesta'.
    - Un '&' en medio del texto se conserva como texto literal.
    - Rutas repetidas en la misma tarjeta (&1,1) se deduplican.
    """
    with open(txt, "r", encoding="utf-8-sig") as archivo:
        lineas = archivo.readlines()

    tarjetas = []
    tema_actual = ""
    ampersands_sueltos = []
    lineas_solo_nota = []

    for renglon in lineas:
        renglon = renglon.strip()

        if not renglon:
            continue

        # NUEVO: separar la nota ('|') ANTES de todo lo demás
        nota = ""
        if "|" in renglon:
            renglon, nota = renglon.split("|", 1)
            renglon = renglon.strip()
            nota = nota.strip().replace("\\n", "\n")
            if not renglon:
                # línea que empieza con '|': no hay tarjeta a la que asignarle la nota
                lineas_solo_nota.append(nota)
                continue

        if "subT" in renglon:
            tema_actual = renglon.replace("subT", "").strip()
            continue

        rutas_asociadas = []
        m = PATRON_RUTAS_TARJETA.search(renglon)
        if m:
            for num in m.group(1).split(","):
                num = int(num)
                if num not in rutas_asociadas:  # deduplicar (&1,1 -> [1])
                    rutas_asociadas.append(num)
            renglon = renglon[:m.start()].strip()
        elif PATRON_AMPERSAND_SUELTO.search(renglon):
            # '&' sin números: se quita para que no aparezca en la tarjeta
            renglon = PATRON_AMPERSAND_SUELTO.sub("", renglon).strip()
            ampersands_sueltos.append(renglon)

        if "+" in renglon:
            primero, segundo = renglon.split("+", 1)
            primero = primero.strip()
            segundo = segundo.strip()
        else:
            primero = renglon
            segundo = ""

        tarjetas.append({
            "tema": tema_actual,
            "primero": primero,
            "segundo": segundo,
            "rutas": rutas_asociadas,
            "claves": extraer_claves(segundo),
            "nota": nota,
        })

    if lineas_solo_nota:
        print("\nAVISO: líneas que empiezan con '|' (sin tarjeta), ignoradas:")
        for texto in lineas_solo_nota:
            print(f"  - {texto}")

    if ampersands_sueltos:
        print("\nAVISO: estas tarjetas tenían '&' sin número de ruta.")
        print("El símbolo se quitó, pero NO tendrán botón 'Mostrar respuesta':")
        for texto in ampersands_sueltos:
            print(f"  - {texto}")

    return tarjetas


def reportar_asociaciones(tarjetas, rutas):
    """Diagnóstico por consola: cuántas tarjetas tienen botón y rutas faltantes."""
    con_rutas = [t for t in tarjetas if t["rutas"]]
    print(f"\nTarjetas con botón 'Mostrar respuesta': {len(con_rutas)} de {len(tarjetas)}")
    con_nota = [t for t in tarjetas if t.get("nota")]
    print(f"Tarjetas con nota (💡): {len(con_nota)} de {len(tarjetas)}")

    faltantes = sorted({n for t in tarjetas for n in t["rutas"] if n not in rutas})
    if faltantes:
        print("AVISO: estas rutas se citan pero NO existen en respuestas.txt "
              "(serán ignoradas): " + ", ".join(map(str, faltantes)))
    else:
        print("Todas las rutas citadas existen en respuestas.txt.")


# ============================================================
# RESALTADO EN EL DIAGRAMA DRAWIO (marcador automático por celda)
# ============================================================

# MODIFICADO: ya no existe un COLOR_RESALTADO fijo que haya que cambiar a mano.
# Para cada celda se lee el fontColor y el fillColor de su style y se elige,
# de esta paleta, el color con mejor contraste contra la letra, descartando
# los que no se distinguirían del fondo del cuadro. Editá la paleta si querés
# otra gama de marcadores.
PALETA_RESALTADO = [
    "#FFFF00",  # amarillo  -> ideal para letras oscuras
    "#0000FF",  # azul      -> ideal para letras claras
    "#00FFFF",  # cian
    "#FF00FF",  # magenta
    "#FF8000",  # naranja
    "#00FF00",  # verde
]

# Contraste mínimo (WCAG) entre el marcador elegido y el fondo del cuadro
# para considerar que el marcador se distingue del cuadro.
CONTRASTE_FONDO_MIN = 1.3

PATRON_PALABRA = re.compile(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+')
# Limpieza: quita exactamente los tags <font style="background-color:...">...</font>
# que este script inserta (cualquier color, así las re-ejecuciones siguen seguras)
PATRON_LIMPIEZA = re.compile(r'<font style="background-color:[^"]*">([^<]*)</font>')

NOMBRE_MODULO_RUTAS = "6-generadorRespuestas.py"

# Índice de la página (pestaña) del .drawio que contiene las imágenes con
# 'ruta'/'descripcion'. Es la MISMA página que usa resaltar_drawio (la
# primera, índice 0). Si en algún momento cambiás el diagrama de página,
# solo hay que actualizar este número.
INDICE_PAGINA_DRAWIO = 0

# BUGFIX: además de <object>, las versiones recientes de draw.io serializan
# las celdas con atributos personalizados (id, ruta, descripcion, etc.) como
# <UserObject> en lugar de <object>. Es habitual que, dentro de un mismo
# .drawio, las celdas de TEXTO (creadas hace tiempo) queden como <object> y
# las celdas de IMAGEN (agregadas después, con una versión más nueva de
# draw.io) queden como <UserObject> -- por eso "descripcion" no se extraía de
# las imágenes: el código solo miraba <object>. Se centraliza acá para que
# todo el script reconozca ambas variantes de forma consistente.
TAGS_OBJETO_DRAWIO = ("object", "UserObject")


def normalizar_tema(texto):
    """Normaliza un tema completo (minúsculas, sin acentos, espacios colapsados)."""
    if not texto:
        return ""
    return " ".join(normalizar_palabra(texto).split())


def cargar_generador_respuestas():
    """
    Importa get_routes_id_map desde 6-generadorRespuestas.py (misma carpeta).
    """
    try:
        base = Path(__file__).resolve().parent
    except NameError:
        base = Path.cwd()

    ruta_modulo = base / NOMBRE_MODULO_RUTAS
    if not ruta_modulo.exists():
        ruta_modulo = Path(NOMBRE_MODULO_RUTAS)
    if not ruta_modulo.exists():
        print(f"AVISO: no encontré {NOMBRE_MODULO_RUTAS}; no se resaltará el drawio.")
        return None

    try:
        spec = importlib.util.spec_from_file_location("generador_respuestas", ruta_modulo)
        modulo = importlib.util.module_from_spec(spec)
        sys.modules["generador_respuestas"] = modulo  # CLAVE: fix crash de dataclasses
        spec.loader.exec_module(modulo)
        return modulo.get_routes_id_map
    except Exception as error:
        print(f"AVISO: no pude importar {NOMBRE_MODULO_RUTAS}: {error}")
        return None


def calcular_claves_por_ruta(tarjetas, mapa_rutas):
    """
    Opción A con cascada:
    - Ruta citada con '&': claves = unión de los 'segundo' de las tarjetas que la citan.
    - Ruta no citada: claves por tema. El generador deja tema=None en el primer
      bloque de cada grupo (ej. ruta 12 'Naturales'); esas rutas heredan el tema
      no-vacío más cercano (primero se busca hacia ADELANTE, luego hacia atrás),
      porque el subtítulo del grupo aparece recién en el bloque siguiente.
    """
    claves_citadas = {}   # numero_ruta -> set de claves
    claves_por_tema = {}  # tema normalizado -> set de claves

    for tarjeta in tarjetas:
        claves = set(tarjeta.get("claves", []))
        for numero in tarjeta.get("rutas", []):
            claves_citadas.setdefault(numero, set()).update(claves)
        tema_norm = normalizar_tema(tarjeta.get("tema", ""))
        if tema_norm:
            claves_por_tema.setdefault(tema_norm, set()).update(claves)

    numeros = sorted(mapa_rutas)
    temas = {n: normalizar_tema(mapa_rutas[n].get("tema")) for n in numeros}

    resultado = {}
    sin_claves = []
    for i, numero in enumerate(numeros):
        if numero in claves_citadas:
            resultado[numero] = claves_citadas[numero]
            continue

        tema = temas[numero]
        if not tema:
            # Sin tema propio (primer bloque del grupo): buscar hacia adelante
            for j in range(i + 1, len(numeros)):
                if temas[numeros[j]]:
                    tema = temas[numeros[j]]
                    break
        if not tema:
            # Último recurso: hacia atrás
            for j in range(i - 1, -1, -1):
                if temas[numeros[j]]:
                    tema = temas[numeros[j]]
                    break

        if tema and tema in claves_por_tema:
            resultado[numero] = set(claves_por_tema[tema])
        else:
            resultado[numero] = set()
            sin_claves.append(numero)

    if sin_claves:
        print("AVISO: rutas sin claves de resaltado (sin '&' y sin tema asociable): "
              + ", ".join(map(str, sin_claves)))
    return resultado


def style_tiene_html1(style):
    if not style:
        return False
    return any(p.strip() == "html=1" for p in style.split(";"))


def style_agregar_html1(style):
    if not style:
        return "html=1;"
    if style.rstrip().endswith(";"):
        return style + "html=1;"
    return style + ";html=1;"


def activar_html_en_celda(elem):
    """
    Asegura html=1 en el style de la celda tocada. En <object> el style vive
    en el mxCell interno, no en el object.
    """
    tag = elem.tag.split("}")[-1]
    if tag in TAGS_OBJETO_DRAWIO:
        style = elem.get("style")
        if style is not None and not style_tiene_html1(style):
            elem.set("style", style_agregar_html1(style))
        for hijo in elem:
            if hijo.tag.split("}")[-1] == "mxCell":
                if not style_tiene_html1(hijo.get("style")):
                    hijo.set("style", style_agregar_html1(hijo.get("style")))
                break
    else:
        style = elem.get("style")
        if not style_tiene_html1(style):
            elem.set("style", style_agregar_html1(style))


# ---------- MODIFICADO: elección automática del color de marcador ----------

def _expandir_hex(valor):
    """'#abc' -> '#aabbcc'. Devuelve None si no es un hex válido."""
    if not valor:
        return None
    v = valor.strip().lstrip('#')
    if len(v) == 3:
        v = ''.join(ch * 2 for ch in v)
    if len(v) != 6:
        return None
    try:
        int(v, 16)
    except ValueError:
        return None
    return '#' + v.lower()


def luminancia_relativa(hex_color):
    """Luminancia relativa WCAG (0..1). None si el color es inválido."""
    hex_color = _expandir_hex(hex_color)
    if hex_color is None:
        return None
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def canal(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def contraste(color1, color2):
    """Ratio de contraste WCAG (1 = idénticos, 21 = máximo). 0 si algo es inválido."""
    l1 = luminancia_relativa(color1)
    l2 = luminancia_relativa(color2)
    if l1 is None or l2 is None:
        return 0.0
    clara, oscura = max(l1, l2), min(l1, l2)
    return (clara + 0.05) / (oscura + 0.05)


def extraer_style_param(style, clave):
    """De un style drawio ('html=1;fillColor=#ff0000;...') saca el valor de un parámetro."""
    if not style:
        return None
    prefijo = clave + '='
    for parte in style.split(';'):
        parte = parte.strip()
        if parte.startswith(prefijo):
            return parte[len(prefijo):]
    return None


def style_de_celda(elem):
    """
    Style 'visual' de la celda. En <object> el style vive en el mxCell
    interno (igual que con html=1); si no hubiera, se usa el del object.
    """
    if elem.tag.split('}')[-1] in TAGS_OBJETO_DRAWIO:
        for hijo in elem:
            if hijo.tag.split('}')[-1] == 'mxCell':
                style = hijo.get('style')
                if style:
                    return style
        return elem.get('style')
    return elem.get('style')


def colores_de_celda(elem):
    """
    (color de letra, color de fondo) según el style de la celda.
    Sin fontColor explícito drawio usa negro; sin fillColor se devuelve None
    (no se puede filtrar por fondo).
    """
    style = style_de_celda(elem) or ''
    letra = _expandir_hex(extraer_style_param(style, 'fontColor')) or '#000000'
    fondo = _expandir_hex(extraer_style_param(style, 'fillColor'))
    return letra, fondo


def elegir_color_resaltado(letra, fondo=None):
    """
    Elige de PALETA_RESALTADO el color con mejor contraste contra la letra.
    Si se conoce el fondo del cuadro, se descartan los candidatos que no se
    distinguirían de él (contraste < CONTRASTE_FONDO_MIN); si todos quedan
    descartados, gana el de mejor contraste con la letra.
    """
    ordenados = sorted(PALETA_RESALTADO, key=lambda c: contraste(c, letra), reverse=True)
    if fondo:
        for c in ordenados:
            if contraste(c, fondo) >= CONTRASTE_FONDO_MIN:
                return c
    return ordenados[0]


def resaltar_texto(texto, claves, color):
    """
    Envuelve en <font style="background-color:..."> las palabras cuyo
    normalized match esté en claves, usando el color dado para esta celda.
    Devuelve (nuevo_texto, hubo_cambio).
    """
    partes = []
    ultimo = 0
    cambio = False
    for m in PATRON_PALABRA.finditer(texto):
        if normalizar_palabra(m.group(0)) in claves:
            partes.append(texto[ultimo:m.start()])
            partes.append(
                f'<font style="background-color:{color}">' + m.group(0) + "</font>"
            )
            ultimo = m.end()
            cambio = True
    if not cambio:
        return texto, False
    partes.append(texto[ultimo:])
    return "".join(partes), True


def resaltar_drawio(ruta_drawio, get_routes_id_map, tarjetas):
    """
    Resalta en el drawio las palabras coincidentes y REESCRIBE el original
    en su misma ruta. Primero limpia cualquier resaltado previo (re-ejecuciones).

    MODIFICADO: el color del marcador se elige por celda según el contraste
    con el fontColor (legibilidad) y el fillColor (que se note sobre el cuadro).

    Convergencias: cuando varias celdas apuntan a un mismo destino, el
    generador las colapsa en __CONVERGENCE__<destino> y las celdas fuente
    (donde está el texto real) no aparecen en el mapa. Aquí se resuelven:
    se visita el destino Y todas las celdas con flecha hacia él.
    """
    mapa_rutas = get_routes_id_map(Path(ruta_drawio))
    if not mapa_rutas:
        print("AVISO: get_routes_id_map no devolvió rutas; drawio sin cambios.")
        return

    claves_por_ruta = calcular_claves_por_ruta(tarjetas, mapa_rutas)

    tree = ET.parse(ruta_drawio)
    root = tree.getroot()

    # Misma página que usa el generador (la primera)
    diagrams = root.findall("./diagram")
    alcance = diagrams[0] if diagrams else root

    # id -> (elemento, atributo de texto). <object> usa 'label', <mxCell> usa 'value'.
    elementos = {}
    for elem in alcance.iter():
        tag = elem.tag.split("}")[-1]
        eid = elem.get("id")
        if not eid:
            continue
        if tag == "object":
            elementos[eid] = (elem, "label")
        elif tag == "UserObject":
            # BUGFIX: mismo caso que <object> (ver TAGS_OBJETO_DRAWIO), las
            # versiones recientes de draw.io usan <UserObject> para celdas
            # con atributos personalizados (frecuente en imágenes agregadas
            # después). Antes se ignoraban acá y no se resaltaban ni resolvían.
            elementos[eid] = (elem, "label")
        elif tag == "mxCell" and eid not in elementos:
            elementos[eid] = (elem, "value")

    # Predecesores según las aristas del XML: destino -> {fuentes}
    predecesores = {}
    for elem in alcance.iter():
        if elem.tag.split("}")[-1] == "mxCell" and elem.get("edge") == "1":
            origen, destino = elem.get("source"), elem.get("target")
            if origen and destino:
                predecesores.setdefault(destino, set()).add(origen)

    PREFIJO_CONV = "__CONVERGENCE__"
    convergencias_resueltas = 0

    def resolver_id(id_celda):
        """Clave del mapa -> celdas reales a escanear/limpiar."""
        nonlocal convergencias_resueltas
        if id_celda.startswith(PREFIJO_CONV):
            destino = id_celda[len(PREFIJO_CONV):]
            fuentes = sorted(predecesores.get(destino, ()))
            if fuentes:
                convergencias_resueltas += 1
            return [destino] + fuentes
        return [id_celda]

    # id de celda real -> claves (unión de todas las rutas que la mencionan)
    claves_por_celda = {}
    ids_en_mapa = set()
    for numero, bloque in mapa_rutas.items():
        for cruda in set(bloque.get("ids", {}).values()):
            resueltas = resolver_id(cruda)
            ids_en_mapa.update(resueltas)  # también se limpian al re-ejecutar
            for id_real in resueltas:
                claves_por_celda.setdefault(id_real, set()).update(
                    claves_por_ruta.get(numero, set())
                )

    # 1) Limpieza previa en TODAS las celdas alcanzables del mapa
    for id_celda in ids_en_mapa:
        par = elementos.get(id_celda)
        if par is None:
            continue  # id inexistente en el XML: se ignora en silencio
        elem, attr = par
        texto = elem.get(attr, "")
        if texto and PATRON_LIMPIEZA.search(texto):
            elem.set(attr, PATRON_LIMPIEZA.sub(r"\1", texto))

    # 2) Resaltado nuevo (MODIFICADO: color elegido por celda según letra y fondo)
    resaltadas = 0
    colores_usados = {}
    for id_celda, claves in claves_por_celda.items():
        if not claves:
            continue
        par = elementos.get(id_celda)
        if par is None:
            continue
        elem, attr = par
        texto = elem.get(attr, "")
        if not texto:
            continue
        letra, fondo = colores_de_celda(elem)
        color = elegir_color_resaltado(letra, fondo)
        nuevo, cambio = resaltar_texto(texto, claves, color)
        if cambio:
            elem.set(attr, nuevo)
            activar_html_en_celda(elem)  # drawio solo interpreta HTML con html=1
            resaltadas += 1
            colores_usados[color] = colores_usados.get(color, 0) + 1

    tree.write(ruta_drawio, encoding="utf-8", xml_declaration=True)

    print(f"Drawio resaltado: {resaltadas} celdas modificadas "
          f"({convergencias_resueltas} convergencias resueltas).")
    if colores_usados:
        resumen = ", ".join(f"{c} x{n}" for c, n in sorted(colores_usados.items()))
        print(f"  Colores de marcador usados: {resumen}")
    citadas = {n for t in tarjetas for n in t.get("rutas", [])}
    ausentes = sorted(citadas - set(mapa_rutas.keys()))
    if ausentes:
        print("AVISO: rutas citadas que no aparecen en el mapa del drawio: "
              + ", ".join(map(str, ausentes)))


def extraer_descripciones_drawio(ruta_drawio):
    """
    NUEVO: lee el .drawio en modo SOLO LECTURA (no lo modifica) y extrae, de
    las celdas de IMAGEN (style con 'shape=image') que tienen el atributo
    personalizado 'ruta' (el mismo que ya usa 6-generadorRespuestas.py para
    identificar rutas), el atributo personalizado 'descripcion'.

    Devuelve { numero_ruta (int): ["desc de la imagen 0", "desc de la imagen 1", ...] }
    Es decir: UNA LISTA por ruta, con una posición por cada imagen de esa
    ruta encontrada en el drawio (en el orden en que aparecen en el XML),
    análogo al 'indice' que ya usa la galería para navegar imágenes dentro
    de una misma ruta (construirIndiceImagenes/INDICE_IMAGENES en el JS). Si
    una imagen puntual no tiene descripción cargada, su posición queda como
    "" (string vacío) para no correr el índice de las demás imágenes de esa
    ruta.

    Independiente de get_routes_id_map y de resaltar_drawio: no comparte
    estado con ellos ni los modifica, así que una falla acá nunca puede
    romper el resaltado ni el resto del flujo (se atrapa cualquier excepción
    y se devuelve un diccionario vacío).

    Usa la misma página que resaltar_drawio: INDICE_PAGINA_DRAWIO.

    IMPORTANTE: el orden de las imágenes acá es el orden en que aparecen en
    el .drawio (documento XML). Para que el índice coincida con el de la
    galería (que ordena según las líneas 'IMG:' de respuestas.txt), las
    imágenes de una misma ruta deben estar en el mismo orden en ambos
    lugares. Si no coincide, avisá y lo ajustamos.
    """
    descripciones = {}
    con_ruta = 0
    con_descripcion = 0
    try:
        tree = ET.parse(ruta_drawio)
        root = tree.getroot()

        diagrams = root.findall("./diagram")
        alcance = diagrams[INDICE_PAGINA_DRAWIO] if diagrams else root

        for elem in alcance.iter():
            tag = elem.tag.split("}")[-1]
            if tag not in TAGS_OBJETO_DRAWIO:
                # 'ruta'/'descripcion' son atributos personalizados: viven
                # en <object> o <UserObject> (ver TAGS_OBJETO_DRAWIO).
                continue

            estilo = style_de_celda(elem) or ""
            if "shape=image" not in estilo:
                continue  # no es una celda de imagen: no aporta al índice de imágenes

            ruta_attr = elem.get("ruta")
            tiene_ruta = ruta_attr is not None and str(ruta_attr).strip() != ""
            if not tiene_ruta:
                continue  # imagen sin ruta asignada: no se puede ubicar en la galería

            try:
                numero = int(str(ruta_attr).strip())
            except (TypeError, ValueError):
                continue  # atributo 'ruta' no numérico: se ignora en silencio

            descripcion = elem.get("descripcion") or ""
            descripcion = descripcion.strip()

            con_ruta += 1
            if descripcion:
                con_descripcion += 1

            # Se agrega SIEMPRE (aunque venga vacía) para no desalinear el
            # índice de las demás imágenes de esta misma ruta.
            descripciones.setdefault(numero, []).append(descripcion)
    except Exception as error:
        print(f"AVISO: no pude extraer descripciones del drawio: {error}")
        return {}

    # NUEVO: diagnóstico para detectar fácilmente por qué no se extrae nada
    # (p. ej. celdas con 'ruta' pero sin 'descripcion', o viceversa).
    rutas_con_imagenes = len(descripciones)
    total_imagenes = sum(len(lista) for lista in descripciones.values())
    print(f"  Imágenes con 'ruta': {con_ruta}  |  con 'descripcion' no vacía: {con_descripcion}  "
          f"|  rutas con imágenes: {rutas_con_imagenes}  |  imágenes totales indexadas: {total_imagenes}")

    return descripciones


# ============================================================
# GENERACIÓN DEL HTML
# ============================================================

def generar_html(tarjetas, rutas, fill_color, stroke_color, modo_aleatorio, txt_respuestas, espacio_hash, descripciones_rutas=None):
    datos = {"tarjetas": tarjetas, "rutas": rutas, "descripciones_rutas": descripciones_rutas or {}}
    datos_json = json.dumps(datos, ensure_ascii=False)
    nombre_base = Path(txt_respuestas).stem

    html = HTML_TEMPLATE
    html = html.replace("__FILL_COLOR__", fill_color)
    html = html.replace("__STROKE_COLOR__", stroke_color)
    html = html.replace("__DATOS_JSON__", datos_json)
    html = html.replace("__MODO_ALEATORIO__", "true" if modo_aleatorio else "false")
    html = html.replace("__UMBRAL_CASI__", str(UMBRAL_CASI))
    html = html.replace("__PRECIO_COMODIN__", str(PRECIO_COMODIN))
    html = html.replace("__PRECIO_PISTA_GRATIS__", str(PRECIO_PISTA_GRATIS))
    html = html.replace("__PRECIO_ESCUDO_RACHA__", str(PRECIO_ESCUDO_RACHA))
    html = html.replace("__PRECIO_SEGUNDA_OPORTUNIDAD__", str(PRECIO_SEGUNDA_OPORTUNIDAD))
    html = html.replace("__FACTOR_RECOMPENSA_REINTENTO__", str(FACTOR_RECOMPENSA_REINTENTO))
    html = html.replace("__SIM_ALTA__", str(SIM_ALTA))
    html = html.replace("__SIM_SIN_PISTA__", str(SIM_SIN_PISTA))
    html = html.replace("__SIM_CON_PISTA__", str(SIM_CON_PISTA))
    html = html.replace("__STOPWORDS__", json.dumps(sorted(STOPWORDS_ES)))
    # NUEVO: identidad de este evaluador (para que difíciles/notas/razones no se
    # mezclen entre dos evaluadores con tarjetas de texto idéntico). Ver hashTarjeta().
    html = html.replace("__ESPACIO__", json.dumps(espacio_hash))

    with open(f"guardados/evaluadores/evaluador_{nombre_base}.html", "w", encoding="utf-8") as archivo:
        archivo.write(html)

    print("evaluador creado")
    print(f"  Tarjetas: {len(tarjetas)}")
    print(f"  Rutas cargadas: {len(rutas)}")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tarjetas de Estudio</title>
<style>
  :root {
    --fill-color: __FILL_COLOR__;
    --stroke-color: __STROKE_COLOR__;
    /* NUEVO: color propio de la interfaz (botones, inputs, títulos, etc.).
       Ya no depende de la paleta de colores elegida al generar el evaluador:
       --stroke-color queda reservado SOLO para el look de las tarjetas
       (.tema, .tarjeta, .primero, .segundo=, .palabra-clave...). */
    --color-boton: #4F46E5;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #f5f3fa 0%, #eef1f7 100%);
    font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
    color: #333;
  }

  .nombre-evaluador {
    text-align: center;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--color-boton);
    opacity: 0.75;
    margin-bottom: 2px;
  }

  .contenedor {
    width: 100%;
    max-width: 560px;
    padding: clamp(12px, 4vw, 24px);
    text-align: center;
  }

  h1 {
    font-size: clamp(18px, 5vw, 22px);
    font-weight: 600;
    margin-bottom: clamp(10px, 3vw, 18px);
    color: #444;
  }

  .subtitulo {
    font-size: 14px;
    color: #888;
    margin-bottom: clamp(8px, 3vw, 16px);
  }

  /* ---------- Pantalla de selección de temas ---------- */

  .lista-temas {
    text-align: left;
    background: #fff;
    border-radius: 12px;
    padding: 8px 18px;
    box-shadow: 0 6px 18px rgba(0,0,0,0.06);
    margin-bottom: 20px;
  }

  .item-tema-check {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 0;
    border-bottom: 1px solid #f1f1f1;
    font-size: 15px;
    color: #444;
  }

  .item-tema-check:last-child { border-bottom: none; }

  .item-tema-check input {
    width: 18px;
    height: 18px;
    accent-color: var(--color-boton);
    cursor: pointer;
  }

  .boton-principal {
    padding: 14px 30px;
    border-radius: 26px;
    background: var(--color-boton);
    color: #fff;
    border: none;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    box-shadow: 0 6px 14px rgba(0,0,0,0.15);
  }

  .boton-principal:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* MODIFICADO: modos especiales + zona de difíciles */
  .zona-modos {
    display: flex;
    justify-content: center;
    gap: 10px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }

  .boton-modo {
    padding: 12px 22px;
    border-radius: 24px;
    background: #ffffff;
    color: var(--color-boton);
    border: 2px solid var(--color-boton);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }

  .boton-modo:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .zona-dificiles { margin-bottom: 20px; }

  .cabecera-dificiles {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
  }

  .titulo-dificiles {
    font-size: 14px;
    font-weight: 700;
    color: #444;
  }

  .fila-dificiles-botones {
    display: flex;
    gap: 8px;
  }

  .boton-mini {
    padding: 6px 12px;
    border-radius: 14px;
    background: #fff;
    color: #666;
    border: 1.5px solid #d8d8e0;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }

  .boton-mini:hover {
    color: var(--color-boton);
    border-color: var(--color-boton);
  }

  .extra-dificil {
    color: #999;
    font-size: 12px;
  }

  .nota-dificiles {
    font-size: 12px;
    color: #999;
    margin-top: 8px;
  }

  /* ---------- Área de estudio ---------- */

  .barra-superior {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 8px;
  }

  /* NUEVO: Fila 1 de la barra superior (gamificación) */
  .fila-gamificacion {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    font-size: 13px;
    font-weight: 700;
    color: #666;
  }

  .stat-gamificacion { white-space: nowrap; }

  .boton-mute {
    background: none;
    border: none;
    font-size: 16px;
    cursor: pointer;
    padding: 0 2px;
    line-height: 1;
    font-family: inherit;
  }

  /* Fila 2 de la barra superior (navegación, ya existía como barra-superior) */
  .fila-navegacion-superior {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  #area-tarjeta.observador .fila-gamificacion { display: none !important; }

  .progreso-texto {
    font-size: 14px;
    color: #888;
  }

  .cronometro {
    font-size: 14px;
    color: #888;
    font-variant-numeric: tabular-nums;
  }

  .cronometro.congelado { color: #e0574c; font-weight: 600; }

  .barra-progreso {
    width: 100%;
    height: 8px;
    background: #e2e2ea;
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: clamp(12px, 4vw, 24px);
  }

  .barra-progreso-relleno {
    height: 100%;
    background: var(--color-boton);
    width: 0%;
    transition: width 0.35s ease;
  }

  .tema {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    margin-bottom: clamp(10px, 3vw, 18px);
    font-weight: 600;
    font-size: 15px;
    color: var(--stroke-color);
    letter-spacing: 0.3px;
  }

  .tema-rombo {
    width: 12px;
    height: 12px;
    background: var(--fill-color);
    border: 2px solid var(--stroke-color);
    transform: rotate(45deg);
    flex-shrink: 0;
  }

  .tarjeta {
    position: relative;
    background: var(--fill-color);
    border: 3px solid var(--stroke-color);
    clip-path: polygon(14% 0%, 100% 0%, 100% 100%, 0% 100%, 0% 14%);
    min-height: clamp(170px, 32vw, 220px);
    border-radius: 6px;
    padding: clamp(24px, 7vw, 40px) clamp(18px, 5.5vw, 30px);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: clamp(12px, 4vw, 20px);
    box-shadow: 0 10px 24px rgba(0,0,0,0.12);
    opacity: 0;
    transform: translateY(8px);
    animation: aparecer 0.35s ease forwards;
  }

  @keyframes aparecer {
    to { opacity: 1; transform: translateY(0); }
  }

  /* MODIFICADO: badge con la cantidad de rutas asociadas (esquina sup. derecha) */
  .badge-rutas {
    position: absolute;
    top: 10px;
    right: 14px;
    font-size: 12px;
    font-weight: 700;
    color: var(--color-boton);
    background: rgba(255,255,255,0.65);
    border: 1.5px solid var(--color-boton);
    border-radius: 12px;
    padding: 2px 10px;
    pointer-events: none;
  }

  .primero {
    font-size: clamp(16.5px, 4.4vw, 19px);
    font-weight: 700;
    color: var(--stroke-color);
    line-height: 1.4;
  }

  .zona-segundo {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: clamp(10px, 3vw, 16px);
    width: 100%;
  }

  .fila-botones-tarjeta {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  .boton-pista {
    position: relative;
    padding: clamp(8px, 2.5vw, 10px) clamp(15px, 5vw, 22px);
    border-radius: 20px;
    border: 2px solid var(--color-boton);
    background: rgba(255,255,255,0.5);
    color: var(--color-boton);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }

  .boton-respuesta {
    padding: clamp(8px, 2.5vw, 10px) clamp(15px, 5vw, 22px);
    border-radius: 20px;
    border: 2px solid var(--color-boton);
    background: var(--color-boton);
    color: #fff;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    box-shadow: 0 4px 10px rgba(0,0,0,0.15);
  }

  .boton-pista:hover:not(:disabled),
  .boton-respuesta:hover:not(:disabled) { transform: translateY(-2px); }

  /* NUEVO: botón 💡 de nota (toggle) */
  .boton-nota {
    width: clamp(34px, 9vw, 40px);
    height: clamp(34px, 9vw, 40px);
    border-radius: 50%;
    border: 2px solid var(--color-boton);
    background: rgba(255,255,255,0.5);
    font-size: 18px;
    line-height: 1;
    cursor: pointer;
    font-family: inherit;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 0;
    transition: background 0.15s ease, border-color 0.15s ease, transform 0.15s ease;
  }

  .boton-nota:hover { transform: translateY(-2px); }

  .boton-nota.activa {
    background: #FFF3C4;
    border-color: #e0c860;
    box-shadow: 0 0 0 3px rgba(240,200,80,0.35);
  }

  /* NUEVO: 💡 sin nota (invita a agregar) y marca de "nota distinta a la del txt" */
  .boton-nota { position: relative; }
  .boton-nota.vacia { border-style: dashed; opacity: 0.6; }
  .boton-nota.dif::after {
    content: '';
    position: absolute;
    top: 1px;
    right: 1px;
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: #f08a24;
    border: 2px solid #fff;
  }

  /* NUEVO: panel de nota estilo post-it, debajo de la tarjeta */
  .panel-nota {
    display: none;
    margin-top: 14px;
    text-align: left;
    background: #FFF8DC;
    border: 1px dashed #e6d38a;
    border-left: 6px solid #F2C94C;
    border-radius: 4px 10px 10px 4px;
    padding: 12px 16px 14px 16px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    animation: aparecer 0.25s ease forwards;
  }

  .panel-nota-titulo {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: #a4841c;
    margin-bottom: 6px;
  }

  .panel-nota-cabecera {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 6px;
  }

  .panel-nota-cabecera .panel-nota-titulo { margin-bottom: 0; }

  .panel-nota-estado {
    text-transform: none;
    letter-spacing: 0;
    font-weight: 600;
    font-size: 11px;
    color: #b26a00;
    margin-left: 6px;
  }

  .panel-nota-versiones { display: flex; gap: 6px; }

  .boton-mini.activo {
    background: var(--color-boton);
    color: #fff;
    border-color: var(--color-boton);
  }

  .panel-nota-texto.vacio { color: #a99a5c; font-style: italic; }

  .panel-nota-editor {
    width: 100%;
    min-height: 90px;
    resize: vertical;
    border: 1.5px solid #e6d38a;
    border-radius: 8px;
    padding: 8px 10px;
    font-family: inherit;
    font-size: 14px;
    line-height: 1.5;
    background: #fffdf2;
    color: #4d4216;
  }

  .panel-nota-editor:focus { outline: 2px solid #F2C94C; outline-offset: 1px; }

  .panel-nota-acciones {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 10px;
  }

  /* NUEVO: lista de notas en la pantalla "Elegir temas" */
  .item-nota-dif, .item-nota-otra {
    border-bottom: 1px solid #f1f1f1;
    padding: 8px 0;
    font-size: 14px;
    text-align: left;
  }

  .item-nota-dif:last-child, .item-nota-otra:last-child { border-bottom: none; }
  .item-nota-dif summary { cursor: pointer; color: #444; }

  .badge-estado {
    font-size: 11px;
    font-weight: 700;
    padding: 1px 8px;
    border-radius: 10px;
    margin-left: 8px;
    white-space: nowrap;
  }

  .badge-estado.pendiente { background: #FFF3C4; color: #8a6d00; }
  .badge-estado.conflicto { background: #ffe1dc; color: #b3372c; }

  .bloque-version {
    margin: 8px 0;
    padding: 8px 10px;
    border-radius: 8px;
    background: #FFF8DC;
    border-left: 4px solid #F2C94C;
    font-size: 13px;
    color: #4d4216;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .bloque-version.txt { background: #f3f4f8; border-left-color: #b8b8c4; color: #555; }
  .bloque-version.vacio { font-style: italic; opacity: 0.75; }

  .bloque-version .etq {
    display: block;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    color: #a4841c;
    margin-bottom: 3px;
  }

  .bloque-version.txt .etq { color: #888; }
  .acciones-nota-dif { display: flex; gap: 8px; flex-wrap: wrap; margin: 6px 0 2px; }

  #zona-notas details > summary { cursor: pointer; padding: 6px 0; font-size: 14px; color: #444; }

  .panel-nota-texto {
    font-size: 14px;
    line-height: 1.55;
    color: #4d4216;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .separador-tarjeta {
    width: 60px;
    height: 2px;
    background: var(--stroke-color);
    opacity: 0.4;
  }

  .segundo {
    font-size: 16px;
    font-weight: 400;
    color: var(--stroke-color);
    line-height: 1.5;
  }

  /* ---------- Evaluación de respuesta escrita ---------- */

  .zona-eval { display:none; margin-top: 14px; text-align:left; }

  .zona-eval textarea {
    width: 100%; min-height: 74px; resize: vertical;
    border: 2px solid var(--color-boton); border-radius: 10px;
    padding: 10px 12px; font-family: inherit; font-size: 14px; color: #333;
    background: #fff;
  }

  .zona-eval textarea:focus {
    outline: 2px solid var(--color-boton); outline-offset: 1px;
  }

  .zona-eval .fila-eval { display: flex; justify-content: center; margin-top: 8px; }

  .resultado-eval {
    display: none; min-height: 18px;
    font-size: 13px; font-weight: 600; margin-top: 10px;
    text-align: left; /* MODIFICADO: evaluación multilínea alineada a la izquierda */
  }

  .resultado-eval.bien { color: #3aa76d; }
  .resultado-eval.casi { color: #e0a030; }
  .resultado-eval.mal  { color: #e0574c; }

  .item-escrito { display: block; color: #333; font-style: italic; margin-top: 3px; }
  .item-resultado { display: block; font-weight: 700; font-size: 12px; margin-top: 3px; }
  .item-resultado.bien { color: #3aa76d; }
  .item-resultado.casi { color: #e0a030; }
  .item-resultado.mal  { color: #e0574c; }

  /* ---------- Rutas (acordeón de respuestas) ---------- */

  .rutas-contenedor {
    margin-top: 18px;
    text-align: left;
    display: none;
  }

  .ruta-acordeon {
    border: 1px solid #eee;
    border-radius: 10px;
    margin-bottom: 10px;
    overflow: hidden;
    background: #fff;
    box-shadow: 0 3px 10px rgba(0,0,0,0.05);
  }

  .ruta-acordeon summary {
    cursor: pointer;
    list-style: none;
    padding: 12px 16px;
    font-weight: 600;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #fafafa;
    color: var(--color-boton);
  }

  .ruta-acordeon summary::-webkit-details-marker { display: none; }

  .ruta-acordeon summary::after {
    content: '\\25BE';
    font-size: 12px;
    color: #999;
    transition: transform 0.2s ease;
  }

  .ruta-acordeon[open] summary::after {
    transform: rotate(180deg);
  }

  .ruta-contenido {
    padding: 10px 16px 14px 16px;
  }

  .ruta-linea {
    font-size: 14px;
    color: #444;
    padding: 4px 0;
    line-height: 1.5;
  }

  .palabra-clave {
    color: var(--stroke-color);
    font-weight: 700;
  }

  .ruta-img-thumb {
    display: block;
    max-width: 100%;
    max-height: 150px;
    object-fit: contain;
    margin: 10px auto;
    border-radius: 8px;
    border: 1px solid #e2e2ea;
    background: #fff;
    cursor: zoom-in;
  }

  /* ---------- Lightbox de imágenes ---------- */

  .lightbox {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(20, 20, 30, 0.88);
    z-index: 1000;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }

  .lightbox.abierto { display: flex; }

  .lightbox img {
    max-width: 95vw;
    max-height: 92vh;
    width: auto;
    height: auto;
    border-radius: 8px;
    background: #fff;
    box-shadow: 0 10px 40px rgba(0,0,0,0.5);
  }

  .lightbox-cerrar {
    position: absolute;
    top: 14px;
    right: 22px;
    background: transparent;
    border: none;
    color: #fff;
    font-size: 36px;
    cursor: pointer;
    line-height: 1;
  }

  .estado-marca {
    min-height: 20px;
    font-size: 13px;
    font-weight: 600;
    margin-top: 14px;
    color: #999;
  }

  .estado-marca.si { color: #3aa76d; }
  .estado-marca.no { color: #e0574c; }
  .estado-marca.saltar { color: #b8b8c4; }

  /* MODIFICADO: dos filas agrupadas por función (decisión / etiquetas), en vez
     de una sola fila con wrap. Evita que el ✓ quede solo, descentrado, cuando
     no entran los 5 botones en una línea (pantallas angostas). */
  .botonera {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    margin-top: 10px;
  }

  .fila-decision, .fila-etiquetas, .fila-galeria-acciones {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: clamp(8px, 3vw, 14px);
    flex-wrap: wrap;
  }

  .boton {
    border: none;
    cursor: pointer;
    font-size: 15px;
    font-weight: 600;
    font-family: inherit;
    transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
    box-shadow: 0 4px 10px rgba(0,0,0,0.15);
  }

  .boton:hover:not(:disabled) { transform: translateY(-2px); }
  .boton:active:not(:disabled) { transform: translateY(0px); }

  .boton:disabled {
    opacity: 0.35;
    cursor: not-allowed;
    box-shadow: none;
  }

  .boton-circular {
    width: clamp(50px, 14vw, 64px);
    height: clamp(50px, 14vw, 64px);
    border-radius: 50%;
    font-size: clamp(20px, 6vw, 26px);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .boton-no { background: #e0574c; }
  .boton-si { background: #3aa76d; }

  .boton-saltar {
    position: relative;
    padding: clamp(9px, 3vw, 12px) clamp(14px, 4.5vw, 20px);
    border-radius: 24px;
    background: #ffffff;
    color: #888;
    border: 2px solid #d8d8e0;
  }

  /* MODIFICADO: botón toggle de tarjeta difícil */
  .boton-dificil {
    padding: clamp(9px, 3vw, 12px) clamp(13px, 4vw, 18px);
    border-radius: 24px;
    background: #ffffff;
    color: #888;
    border: 2px solid #d8d8e0;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    font-family: inherit;
    transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
  }

  .boton-dificil.activa {
    background: #FFF3C4;
    color: #8a6d00;
    border-color: #e0c860;
  }

  .navegacion {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: clamp(14px, 5vw, 26px);
    gap: 10px;
  }

  .boton-nav {
    padding: 10px 16px;
    border-radius: 20px;
    background: #ffffff;
    color: #666;
    border: 2px solid #e2e2ea;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }

  .boton-finalizar {
    padding: 10px 18px;
    border-radius: 20px;
    background: #ffffff;
    color: var(--color-boton);
    border: 2px solid var(--color-boton);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }

  /* ---------- Pantalla de resumen ---------- */

  .pantalla-resumen {
    display: none;
    background: #ffffff;
    border-radius: 16px;
    padding: clamp(20px, 7vw, 40px) clamp(16px, 5.5vw, 30px);
    box-shadow: 0 10px 30px rgba(0,0,0,0.1);
  }

  .pantalla-resumen h2 {
    margin-top: 0;
    color: #444;
  }

  .nota-sesion {
    font-size: clamp(21px, 6vw, 26px);
    font-weight: 700;
    margin-bottom: 10px;
    color: #444;
  }

  .nota-sesion.aprobado { color: #3aa76d; }
  .nota-sesion.desaprobado { color: #e0574c; }
  .nota-sesion.game-over { color: #c0392b !important; }

  .fila-resumen {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: clamp(9px, 3vw, 14px) 6px;
    border-bottom: 1px solid #eee;
    font-size: 16px;
  }

  .fila-resumen:last-of-type { border-bottom: none; }

  .etiqueta-resumen { display: flex; align-items: center; gap: 10px; }

  .punto {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    display: inline-block;
  }

  .punto-si { background: #3aa76d; }
  .punto-no { background: #e0574c; }
  .punto-saltar { background: #b8b8c4; }

  .valor-resumen { font-weight: 700; font-size: 18px; color: #333; }
  .valor-resumen.chico { font-size: 15px; }

  .acciones-resumen {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: clamp(14px, 5vw, 26px);
  }

  .boton-reiniciar, .boton-secundario {
    padding: clamp(9px, 3vw, 12px) clamp(18px, 5.5vw, 26px);
    border-radius: 24px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    border: none;
  }

  .boton-reiniciar {
    background: var(--color-boton);
    color: #fff;
  }

  .boton-secundario {
    background: #ffffff;
    color: var(--color-boton);
    border: 2px solid var(--color-boton);
  }

  .listas-resumen {
    margin-top: clamp(14px, 5vw, 26px);
    text-align: left;
  }

  .lista-desplegable {
    border: 1px solid #eee;
    border-radius: 10px;
    margin-bottom: 10px;
    overflow: hidden;
  }

  .lista-desplegable summary {
    cursor: pointer;
    list-style: none;
    padding: 12px 16px;
    font-weight: 600;
    font-size: 15px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #fafafa;
    color: #444;
  }

  .lista-desplegable summary::-webkit-details-marker { display: none; }

  .lista-desplegable summary::after {
    content: '\\25BE';
    font-size: 12px;
    color: #999;
    transition: transform 0.2s ease;
  }

  .lista-desplegable[open] summary::after {
    transform: rotate(180deg);
  }

  .lista-desplegable summary .etiqueta-resumen { gap: 8px; }

  .lista-contenido {
    padding: 4px 16px 12px 16px;
  }

  .item-lista {
    padding: 10px 0;
    border-bottom: 1px solid #f1f1f1;
    font-size: 14px;
  }

  .item-lista:last-child { border-bottom: none; }

  .item-lista .item-tema {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: var(--color-boton);
    margin-bottom: 3px;
  }

  .item-lista .item-renglon {
    display: block;
    color: #555;
  }

  .item-lista .item-tiempo {
    display: block;
    color: #999;
    font-size: 12px;
    margin-top: 2px;
  }

  .lista-vacia {
    padding: 10px 0;
    font-size: 14px;
    color: #aaa;
  }

  /* NUEVO: pantalla de temas con subT desplegables y tarjetas seleccionables */
  .grupo-tema { border-bottom: 1px solid #f1f1f1; }
  .grupo-tema:last-child { border-bottom: none; }

  .grupo-tema > summary {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 0;
    cursor: pointer;
    list-style: none;
    font-size: 15px;
    font-weight: 600;
    color: #444;
  }

  .grupo-tema > summary::-webkit-details-marker { display: none; }

  .grupo-tema > summary::after {
    content: '▾';
    margin-left: auto;
    font-size: 12px;
    color: #999;
    transition: transform 0.2s ease;
  }

  .grupo-tema[open] > summary::after { transform: rotate(180deg); }

  .check-grupo {
    width: 18px;
    height: 18px;
    accent-color: var(--color-boton);
    cursor: pointer;
    flex-shrink: 0;
  }

  .cuenta-grupo { font-size: 12px; font-weight: 600; color: #999; }
  .cuerpo-grupo { padding: 0 0 8px 28px; }

  .item-tarjeta-check {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid #f6f6f6;
    font-size: 14px;
    color: #555;
    cursor: pointer;
  }

  .item-tarjeta-check input {
    width: 17px;
    height: 17px;
    margin-top: 2px;
    accent-color: var(--color-boton);
    cursor: pointer;
    flex-shrink: 0;
  }

  .tc-textos { display: flex; flex-direction: column; gap: 2px; min-width: 0; overflow-wrap: anywhere; }
  .tc-primero { font-weight: 600; color: #444; }
  .tc-segundo { color: #8a8a8a; font-size: 13px; }
  .tc-marcas { margin-left: auto; padding-left: 8px; font-size: 13px; white-space: nowrap; }

  /* NUEVO: modo observador */
  .fila-observador {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    border-top: 1px solid #f6f6f6;
    border-radius: 6px;
    padding: 9px 6px;
    font-family: inherit;
    font-size: 14px;
    color: #555;
    cursor: pointer;
  }

  .fila-observador:hover { background: #f7f7fb; }

  #btn-observador-volver { display: none; }

  #area-tarjeta.observador #btn-observador-volver { display: inline-block; }

  #area-tarjeta.observador #cronometro,
  #area-tarjeta.observador .barra-progreso,
  #area-tarjeta.observador #zona-eval,
  #area-tarjeta.observador #estado-marca,
  #area-tarjeta.observador #resultado-eval,
  #area-tarjeta.observador #btn-si,
  #area-tarjeta.observador #btn-no,
  #area-tarjeta.observador #btn-saltar,
  #area-tarjeta.observador #btn-finalizar { display: none !important; }

  /* NUEVO: resultados agrupados por subT */
  .sub-desplegable { border-top: 1px solid #f1f1f1; }
  .sub-desplegable:first-child { border-top: none; }

  .sub-desplegable > summary {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 0;
    cursor: pointer;
    list-style: none;
    font-size: 13px;
    font-weight: 700;
    color: var(--color-boton);
  }

  .sub-desplegable > summary::-webkit-details-marker { display: none; }

  .sub-desplegable > summary::after {
    content: '▾';
    margin-left: auto;
    font-size: 11px;
    color: #999;
    transition: transform 0.2s ease;
  }

  .sub-desplegable[open] > summary::after { transform: rotate(180deg); }
  .sub-desplegable .item-lista { padding-left: 8px; }

  /* NUEVO: estado "Casi" */
  .estado-marca.casi { color: #e0a030; }
  .punto-casi { background: #e0a030; }

  /* NUEVO: pausa manual del cronómetro */
  .grupo-cronometro { display: flex; align-items: center; gap: 10px; }

  .boton-pausa {
    border: 1.5px solid #d8d8e0;
    background: #fff;
    color: #777;
    border-radius: 14px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
  }

  .boton-pausa:hover { border-color: var(--color-boton); color: var(--color-boton); }

  .overlay-pausa {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 3000;
    background: rgba(255,255,255,0.93);
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 14px;
    text-align: center;
    padding: 24px;
  }

  .overlay-pausa.abierto { display: flex; }
  .titulo-pausa { font-size: 26px; font-weight: 700; color: var(--color-boton); }
  .texto-pausa { font-size: 14px; color: #777; max-width: 340px; }

  /* NUEVO: botón "Revisar" (esquina superior izquierda, pasado el corte del clip-path) */
  .boton-revisar {
    position: absolute;
    top: 10px;
    left: calc(14% + 8px);
    font-size: 12px;
    font-weight: 700;
    color: var(--color-boton);
    background: rgba(255,255,255,0.65);
    border: 1.5px solid var(--color-boton);
    border-radius: 12px;
    padding: 2px 10px;
    font-family: inherit;
    cursor: pointer;
  }

  .boton-revisar.activa { background: #ffe3c2; border-color: #e0812a; color: #a85a10; }

  /* NUEVO: "Entendida por comprensión" reemplaza al tilde */
  .boton-circular.boton-si.comprension {
    width: auto;
    height: auto;
    min-height: 44px;
    border-radius: 24px;
    padding: 12px 18px;
    font-size: 14px;
  }

  /* NUEVO: modal de "Continuar con las no respondidas" */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 2500;
    background: rgba(30,30,40,0.55);
    align-items: center;
    justify-content: center;
    padding: 20px;
  }

  .modal-overlay.abierto { display: flex; }

  .modal-caja {
    background: #fff;
    border-radius: 18px;
    padding: 24px;
    width: min(92vw, 420px);
    box-shadow: 0 16px 40px rgba(0,0,0,0.25);
    display: flex;
    flex-direction: column;
    gap: 8px;
    text-align: center;
  }

  .modal-caja h3 { font-size: 18px; color: var(--color-boton); margin-bottom: 2px; }
  .modal-caja p { font-size: 14px; color: #666; margin-bottom: 6px; }
  .modal-ayuda { font-size: 12px; color: #999; margin-bottom: 8px; }

  .modal-cancelar {
    background: none;
    border: none;
    color: #888;
    font-family: inherit;
    font-size: 14px;
    cursor: pointer;
    padding: 8px;
  }

  /* NUEVO: Gamificación — modal de selección de modo */
  .opciones-modo {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin: 10px 0;
  }

  .opcion-modo {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    border-radius: 14px;
    border: 2px solid #e2e4ec;
    background: #fafafe;
    cursor: pointer;
    font-family: inherit;
    text-align: left;
  }

  .opcion-modo.seleccionada { border-color: var(--color-boton); background: #fff3ea; }
  .opcion-modo-icono { font-size: 22px; }
  .opcion-modo-nombre { font-weight: 700; color: #333; margin-right: auto; }
  .opcion-modo-detalle { font-size: 12px; color: #888; }

  /* NUEVO: Gamificación — modal de Game Over */
  .modal-gameover.abierto { background: rgba(60,0,0,0.75); }
  .modal-caja-gameover { background: #2a1010; }
  .titulo-gameover { color: #ff5252 !important; font-size: 24px !important; }
  .modal-caja-gameover p { color: #f0b0b0 !important; }

  #area-tarjeta.observador .grupo-cronometro,
  #area-tarjeta.observador #btn-pausa { display: none !important; }

  /* NUEVO: botón y panel de razones */
  .boton-razones { position: relative; }
  .boton-razones.activa { border-color: #8B5CF6; color: #8B5CF6; }
  .boton-nota:disabled, .boton-razones:disabled { opacity: 0.45; cursor: not-allowed; }

  .badge-conteo {
    position: absolute;
    top: -7px;
    right: -7px;
    min-width: 18px;
    height: 18px;
    padding: 0 4px;
    border-radius: 9px;
    background: #8B5CF6;
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    line-height: 18px;
    text-align: center;
  }

  .chips-razones {
    display: none;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 10px;
  }

  .chip-razon {
    display: inline-block;
    font-size: 12px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 12px;
    border: 1.3px solid;
    white-space: nowrap;
  }

  .chip-razon.chico { font-size: 11px; font-weight: 600; padding: 1px 8px; margin: 3px 4px 0 0; }
  .chip-razon.vacio { border-style: dashed; opacity: 0.7; font-style: italic; font-weight: 500; }
  .item-razones { margin-top: 4px; }
  .fila-chips-mini { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0; }

  .panel-razones {
    display: none;
    margin-top: 14px;
    text-align: left;
    background: #F8F9FC;
    border: 1px solid #E2E4EC;
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.06);
  }

  .panel-razones-titulo {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: #666;
    margin-bottom: 10px;
  }

  .lista-check-razones { display: flex; flex-direction: column; gap: 9px; }

  .item-check-razon {
    display: flex;
    align-items: center;
    gap: 9px;
    font-size: 14px;
    color: #444;
    cursor: pointer;
  }

  .item-check-razon input { width: 17px; height: 17px; cursor: pointer; accent-color: var(--color-boton); }
  .dot-razon { width: 11px; height: 11px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  .panel-razones-acciones { display: flex; gap: 8px; margin-top: 12px; }

  /* CORREGIDO: antes esto ocultaba las razones en modo observador; el panel
     de razones debe verse y editarse igual que en modo estudio */

  /* NUEVO: galería de imágenes (modo observador) */
  .fila-obs-botones { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; margin-top: 4px; }

  .galeria-indicador {
    font-size: 14px;
    font-weight: 700;
    color: #666;
    margin-bottom: 12px;
  }

  .galeria-imagen-wrap { display: flex; justify-content: center; }

  .galeria-img {
    max-width: 100%;
    max-height: 55vh;
    border-radius: 10px;
    border: 2px solid var(--color-boton);
    background: #fff;
    cursor: zoom-in;
  }

  .galeria-vacia { color: #888; padding: 30px 0; }

  /* NUEVO: pantalla "Rutas Huérfanas" */
  .lista-huerfanas-scroll {
    max-height: 65vh;
    overflow-y: auto;
    padding-right: 4px;
  }

  .fila-resumen-huerfana {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }

  .ruta-huerfana-titulo {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Mismo estilo que .boton-revisar pero relative (vive dentro de un <summary>) */
  .boton-revisar-huerfana {
    position: relative;
    flex-shrink: 0;
    font-size: 11px;
    font-weight: 700;
    color: var(--color-boton);
    background: rgba(255,255,255,0.65);
    border: 1.5px solid var(--color-boton);
    border-radius: 12px;
    padding: 2px 8px;
    font-family: inherit;
    cursor: pointer;
  }

  .boton-revisar-huerfana.activa { background: #ffe3c2; border-color: #e0812a; color: #a85a10; }

  /* NUEVO: Gamificación — Tienda */
  .saldo-tienda {
    font-size: 16px;
    font-weight: 700;
    color: var(--color-boton);
    margin-bottom: 14px;
    text-align: center;
  }

  .grid-tienda {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }

  .item-tienda {
    border: 2px solid #e2e4ec;
    border-radius: 14px;
    padding: 14px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }

  .item-tienda-icono { font-size: 30px; }
  .item-tienda-nombre { font-weight: 700; color: #333; }
  .item-tienda-precio { font-size: 13px; color: #888; }
  .item-tienda-descripcion { font-size: 12px; color: #999; min-height: 32px; }

  .item-tienda-boton {
    margin-top: 6px;
    padding: 8px 14px;
    border-radius: 18px;
    border: 2px solid var(--color-boton);
    background: var(--color-boton);
    color: #fff;
    font-weight: 700;
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .item-tienda-boton:disabled {
    background: #eee;
    border-color: #ddd;
    color: #aaa;
    cursor: not-allowed;
  }

  .item-tienda-inventario { font-size: 12px; color: var(--color-boton); font-weight: 700; }

  /* NUEVO: Gamificación — Historial */
  .fila-acciones-historial {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }

  .lista-historial-scroll {
    max-height: 65vh;
    overflow-y: auto;
    padding-right: 4px;
  }

  /* NUEVO: paginación del historial */
  .historial-paginacion {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 14px;
    margin-top: 12px;
  }
  .historial-paginacion-texto { font-size: 13px; color: #666; font-weight: 600; }

  /* NUEVO: resumen histórico consolidado (sesiones ya podadas) */
  .resumen-historico {
    margin-top: 14px;
    padding: 10px 12px;
    border-radius: 10px;
    background: #f7f7fb;
    border: 1px solid #ececf4;
    font-size: 12px;
    color: #666;
    text-align: center;
  }
  .resumen-historico-titulo { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: #999; margin-bottom: 4px; }

  .item-historial {
    border-bottom: 1px solid #eee;
    padding: 10px 4px;
    font-size: 13px;
    color: #555;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .item-historial:last-child { border-bottom: none; }
  .item-historial-fecha { font-weight: 700; color: #333; }
  .item-historial-detalle { display: flex; flex-wrap: wrap; gap: 10px; color: #777; }

  /* NUEVO: chip del espacio (evaluador) en cada sesion */
  .chip-espacio {
    display: inline-block;
    margin-left: 8px;
    padding: 1px 8px;
    border-radius: 10px;
    background: #eef0f7;
    color: #555;
    font-size: 11px;
    font-weight: 600;
    vertical-align: middle;
  }
  .chip-espacio-sin { background: #f7ecec; color: #a55; }

  .aviso-juego {
    margin: 8px 0;
    padding: 9px 12px;
    border-radius: 10px;
    background: #fff8e6;
    border: 1.5px solid #ecd28a;
    color: #7a5c00;
    font-size: 13px;
    font-weight: 600;
    text-align: center;
  }
  .item-reintento { display: block; font-size: 12px; color: #a4841c; }

  .fila-acciones-historial button:disabled { opacity: 0.4; cursor: not-allowed; }

  /* NUEVO: grupos de sesiones de otros evaluadores */
  #zona-historial-otros details > summary { cursor: pointer; padding: 6px 0; font-size: 14px; color: #444; }
  .grupo-historial-otro {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    padding: 8px 4px 8px 16px;
    border-bottom: 1px solid #eee;
    font-size: 13px;
    color: #555;
    text-align: left;
  }
  .grupo-historial-otro:last-child { border-bottom: none; }
  .grupo-historial-otro .grupo-titulo { font-weight: 700; color: #333; display: block; overflow-wrap: anywhere; }
  .grupo-historial-otro .grupo-detalle { color: #777; font-size: 12px; }

  /* NUEVO: modal de revinculacion */
  /* NUEVO: chip de lista activa en "Elegir temas" */
  .chip-lista-activa {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin: 6px 0 10px;
    padding: 8px 12px;
    border-radius: 10px;
    background: #eef2ff;
    border: 1.5px solid #c7d2fe;
    color: #3742a0;
    font-size: 13px;
    font-weight: 600;
  }
  .chip-lista-activa button {
    background: none;
    border: none;
    color: #3742a0;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
    line-height: 1;
    padding: 2px 6px;
  }

  .boton-guardar-lista {
    position: absolute;
    top: 14px;
    right: 14px;
  }
  .pantalla-resumen { position: relative; }

  /* NUEVO: botón "Análisis de la sesión" — esquina opuesta al de guardar lista */
  .boton-analisis-sesion {
    position: absolute;
    top: 14px;
    left: 14px;
  }
  .analisis-fila { padding: 10px 0; border-bottom: 1px solid #eee; }
  .analisis-fila:last-child { border-bottom: none; }
  .analisis-nombre { font-weight: 700; font-size: 14px; margin-bottom: 4px; }
  .analisis-badge {
    display: inline-block;
    font-size: 12px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    margin-bottom: 6px;
  }
  .analisis-badge-rojo { background: #fdecea; color: #c0392b; }
  .analisis-badge-naranja { background: #fdf3e3; color: #b36b00; }
  .analisis-badge-verde { background: #e6f6ee; color: #3aa76d; }
  .analisis-comentarios { margin: 4px 0 0; padding-left: 18px; font-size: 13px; color: #444; }
  .analisis-comentarios li { margin-bottom: 2px; }
  /* NUEVO: Resumen Ejecutivo, al principio del modal de análisis */
  .analisis-resumen-ejecutivo {
    background: #f7f8fc;
    border: 1px solid #e2e4ec;
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 12px;
  }
  .analisis-resumen-titulo {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: #999;
    margin-bottom: 6px;
  }
  .analisis-resumen-linea { font-size: 13px; color: #333; margin: 2px 0; }

  .nota-listado { font-size: 12px; color: #888; margin-top: -4px; margin-bottom: 8px; }

  .modal-caja-ancha { width: min(92vw, 520px); text-align: left; max-height: 82vh; overflow-y: auto; }
  .sync-seccion-titulo { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: #999; margin: 14px 0 6px; text-align: center; }
  .fila-borrar-evaluador {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding: 6px 4px;
    border-bottom: 1px solid #eee;
  }
  .fila-borrar-evaluador:last-child { border-bottom: none; }
  .fila-borrar-evaluador .nombre-evaluador-borrar { font-size: 13px; color: #333; font-weight: 600; overflow-wrap: anywhere; }
  .fila-backup-zona { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; padding: 7px 4px; border-bottom: 1px solid #eee; }
  .fila-backup-zona:last-of-type { border-bottom: none; }
  .fila-backup-zona .zona-nombre { font-size: 13px; font-weight: 700; color: #333; }
  .fila-backup-zona .zona-botones { display: flex; gap: 6px; }
  .modal-caja-ancha h3, .modal-caja-ancha > p.modal-ayuda { text-align: center; }

  .fila-acciones-listas { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-bottom: 10px; }
  .boton-mini-peligro { color: #a4402f; border-color: #f0c4bc; }
  .boton-lista-fila { width: 100%; margin-bottom: 10px; }

  .fila-lista-guardada {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 4px;
    border-bottom: 1px solid #eee;
  }
  .fila-lista-guardada:last-child { border-bottom: none; }
  .lista-fila-cabecera { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .fila-lista-guardada .lista-nombre-btn {
    background: none; border: none; padding: 0; text-align: left; cursor: pointer;
    font-size: 14px; font-weight: 700; color: #333;
  }
  .fila-lista-guardada .lista-nombre-btn:hover { text-decoration: underline; }
  .fila-lista-guardada .lista-detalle { font-size: 12px; color: #777; }
  .fila-lista-guardada .lista-aviso { font-size: 12px; color: #a4402f; margin-top: 2px; }
  .fila-lista-guardada .lista-acciones { display: flex; gap: 10px; margin-left: 28px; }
  .fila-lista-guardada .lista-acciones button {
    background: none; border: none; cursor: pointer; font-size: 12px; color: #555; padding: 2px 4px;
  }

  /* NUEVO: botón 🧠 (Repaso Espaciado) con sus 3 estados */
  .lista-srs-btn {
    border: 1.5px solid #ddd;
    border-radius: 8px;
    background: #fff;
    font-size: 15px;
    line-height: 1;
    padding: 4px 7px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .lista-srs-btn.srs-activo { background: #ff8c1a; border-color: #ff8c1a; color: #fff; }
  .lista-srs-btn.srs-graduado { background: #2b6fe0; border-color: #2b6fe0; color: #fff; font-size: 11px; font-weight: 700; white-space: nowrap; padding: 4px 8px; }
  .lista-srs-estado { font-size: 11px; color: #a4841c; font-weight: 600; white-space: nowrap; }
  .lista-ojo-btn { border: none; background: none; font-size: 15px; cursor: pointer; margin-left: auto; padding: 2px 6px; flex-shrink: 0; }

  .lista-ver-tarjetas {
    max-height: 160px;
    overflow-y: auto;
    border: 1px solid #eee;
    border-radius: 8px;
    padding: 6px 8px;
    margin-top: 2px;
    margin-left: 28px;
    font-size: 12px;
    background: #fafafa;
  }
  .lista-ver-tarjeta-item { padding: 3px 0; border-bottom: 1px solid #f0f0f0; }
  .lista-ver-tarjeta-item:last-child { border-bottom: none; }
  .lista-ver-tarjeta-item.lista-ver-tarjeta-faltante { color: #999; }
  .lista-ver-tarjeta-nota { margin-top: 4px; font-size: 11px; color: #999; font-style: italic; }

  /* NUEVO: badge de sesión de Repaso Espaciado */
  .badge-srs {
    display: inline-block;
    background: #ff8c1a;
    color: #fff;
    font-size: 12px;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 999px;
    margin-bottom: 6px;
  }

  #vinc-zonas { display: flex; flex-wrap: wrap; gap: 4px 12px; justify-content: center; margin-bottom: 10px; text-align: left; }
  .vinc-zona-item { font-size: 13px; color: #444; display: flex; align-items: center; gap: 4px; cursor: pointer; }
  .vinc-resumen { font-weight: 600; color: #444 !important; }
  .vinc-aviso {
    background: #fff4f2;
    border: 1.5px solid #f0c4bc;
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 12px;
    line-height: 1.5;
    color: #a4402f;
    text-align: left;
  }

  /* NUEVO: descripción de la imagen (atributo 'descripcion' del drawio) */
  .galeria-descripcion {
    margin: 14px auto 0;
    max-width: 90%;
    text-align: center;
    font-size: 14px;
    color: #555;
    line-height: 1.4;
    font-style: italic;
  }

  .sin-tarjetas {
    color: #888;
    font-size: 15px;
  }

  /* NUEVO: Gamificación — iconos circulares (Historial / Tienda) */
  .fila-iconos-gamificacion {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }

  .boton-icono-circular {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: #fff;
    border: 1.5px solid #e2e4ec;
    font-size: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    font-family: inherit;
    padding: 0;
  }
  .boton-icono-circular:hover { border-color: var(--color-boton); }

  /* ===== NUEVO: Buscador Global (Elegir Temas / Observador / Galería) ===== */
  .zona-buscador {
    position: relative;
    text-align: left;
    margin-bottom: 16px;
  }

  .input-buscador {
    width: 100%;
    padding: 12px 16px;
    border-radius: 24px;
    border: 2px solid #E2E4EC;
    font-size: 14px;
    font-family: inherit;
    color: #333;
    outline: none;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    transition: border-color 0.15s;
  }

  .input-buscador:focus { border-color: var(--color-boton); }

  /* Galería: la barra debe quedar fija mientras se navega entre imágenes */
  .zona-buscador.sticky-buscador {
    position: sticky;
    top: 0;
    z-index: 500;
    background: #eef1f7;
    padding: 10px 0 8px 0;
    margin-bottom: 6px;
  }

  .dropdown-buscador {
    display: none;
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    right: 0;
    max-height: 300px;
    overflow-y: auto;
    background: #fff;
    border-radius: 12px;
    border: 1px solid #E2E4EC;
    box-shadow: 0 10px 30px rgba(0,0,0,0.18);
    z-index: 900;
  }

  .dropdown-buscador.abierto { display: block; }

  .item-dropdown-buscador {
    display: block;
    width: 100%;
    text-align: left;
    padding: 10px 14px;
    font-size: 13.5px;
    color: #444;
    background: none;
    border: none;
    border-bottom: 1px solid #f1f1f1;
    cursor: pointer;
    font-family: inherit;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .item-dropdown-buscador:last-child { border-bottom: none; }
  .item-dropdown-buscador:hover { background: #f7f7fb; }
  .item-dropdown-buscador strong { color: var(--color-boton); }

  .tag-tema-buscador {
    color: #999;
    font-size: 12px;
    margin-right: 4px;
  }

  .dropdown-buscador-vacio {
    padding: 14px;
    font-size: 13px;
    color: #999;
    text-align: center;
  }
</style>
</head>
<body>

<div class="contenedor">
  <div class="nombre-evaluador" id="nombre-evaluador"></div>
  <h1>Tarjetas de Estudio</h1>

  <div id="pantalla-temas">
    <p class="subtitulo">Elegí qué temas (o tarjetas) querés estudiar</p>

    <!-- NUEVO: Gamificación — accesos a Historial y Tienda -->
    <div class="fila-iconos-gamificacion">
      <button class="boton-icono-circular" id="btn-abrir-historial" title="Historial de sesiones">🕐</button>
      <button class="boton-icono-circular" id="btn-abrir-listas" title="Listas guardadas">📋</button>
      <button class="boton-icono-circular" id="btn-abrir-tienda" title="Tienda">🛒</button>
    </div>

    <!-- NUEVO: Buscador Global -->
    <div class="zona-buscador" id="zona-buscador-temas">
      <input type="text" class="input-buscador" id="input-buscador-temas" placeholder="Buscar tarjetas o rutas..." autocomplete="off">
      <div class="dropdown-buscador" id="dropdown-buscador-temas"></div>
    </div>

    <!-- NUEVO: chip de la lista guardada activa (si hay una seleccionada) -->
    <div class="chip-lista-activa" id="chip-lista-activa" style="display:none;">
      <span>📋 Usando lista: "<span id="chip-lista-nombre"></span>"</span>
      <button type="button" id="btn-chip-lista-quitar" title="Quitar lista activa">✕</button>
    </div>

    <div class="lista-temas" id="lista-temas"></div>
    <button class="boton-principal" id="btn-comenzar">Comenzar estudio</button>

    <!-- MODIFICADO: modos especiales de inicio -->
    <div class="zona-modos">
      <button class="boton-modo" id="btn-modo-completo" title="Estudia todas las tarjetas, ignora los temas marcados">Evaluación completa</button>
      <button class="boton-modo" id="btn-modo-dificiles" title="Estudia solo las tarjetas marcadas como difíciles en sesiones anteriores" disabled>Repasar difíciles (0)</button>
      <!-- NUEVO: ver/editar tarjetas sin evaluarse -->
      <button class="boton-modo" id="btn-modo-observador" title="Recorrer las tarjetas sin evaluarte: ver, editar notas, marcar difíciles">👁 Modo observador</button>
    </div>

    <!-- MODIFICADO: vista previa / gestión de las difíciles guardadas -->
    <div class="lista-temas zona-dificiles" id="zona-dificiles">
      <div class="cabecera-dificiles">
        <span class="titulo-dificiles">☆ Difíciles guardadas</span>
      </div>
      <div id="lista-dificiles"></div>
      <details id="det-dificiles-otros" style="display:none">
        <summary id="suma-dificiles-otros">De otros evaluadores</summary>
        <div id="lista-dificiles-otros"></div>
        <button class="boton-mini" id="btn-vincular-dificiles" style="margin: 6px 0 8px 16px;">Vincular a este evaluador</button>
      </details>
      <!-- MODIFICADO: sección de desmarcadas (no se borran, se pueden re-activar) -->
      <details id="det-dificiles-desm" style="display:none">
        <summary id="suma-dificiles-desm">Desmarcadas (0)</summary>
        <div id="lista-dificiles-desm"></div>
        <button class="boton-mini" id="btn-vaciar-desm" style="margin: 6px 0 8px 16px;">Vaciar desmarcadas</button>
      </details>
      <p class="nota-dificiles" id="nota-dificiles"></p>
    </div>

    <!-- NUEVO: notas editadas en el navegador (localStorage) -->
    <div class="lista-temas zona-dificiles" id="zona-notas">
      <div class="cabecera-dificiles">
        <span class="titulo-dificiles">💡 Notas editadas</span>
      </div>
      <details id="det-notas-dif" style="display:none">
        <summary id="suma-notas-dif">Distintas a las del txt</summary>
        <div id="lista-notas-dif"></div>
      </details>
      <details id="det-notas-otras" style="display:none">
        <summary id="suma-notas-otras">De otros evaluadores</summary>
        <div id="lista-notas-otras"></div>
        <button class="boton-mini" id="btn-vincular-notas" style="margin: 6px 0 8px 16px;">Vincular a este evaluador</button>
      </details>
      <p class="nota-dificiles" id="nota-notas"></p>
      <button class="boton-mini" id="btn-vaciar-notas" style="display:none;">Vaciar todas</button>
    </div>

    <!-- NUEVO: tarjetas marcadas 🚩 Revisar (persisten entre sesiones) -->
    <div class="lista-temas zona-dificiles" id="zona-revisar">
      <div class="cabecera-dificiles">
        <span class="titulo-dificiles">🚩 Para revisar guardadas</span>
        <span class="fila-dificiles-botones">
          <button class="boton-mini" id="btn-vaciar-revisar" style="display:none;">Vaciar todas</button>
        </span>
      </div>
      <div id="lista-revisar-guardadas"></div>
      <p class="nota-dificiles" id="nota-revisar"></p>
    </div>

    <!-- NUEVO: razones guardadas -->
    <div class="lista-temas zona-dificiles" id="zona-razones">
      <div class="cabecera-dificiles">
        <span class="titulo-dificiles">🏷 Razones guardadas</span>
      </div>
      <details id="det-razones-aqui" style="display:none">
        <summary id="suma-razones-aqui">De este evaluador</summary>
        <div id="lista-razones-aqui"></div>
      </details>
      <details id="det-razones-otras" style="display:none">
        <summary id="suma-razones-otras">De otros evaluadores</summary>
        <div id="lista-razones-otras"></div>
        <button class="boton-mini" id="btn-vincular-razones" style="margin: 6px 0 8px 16px;">Vincular a este evaluador</button>
      </details>
      <p class="nota-dificiles" id="nota-razones"></p>
      <button class="boton-mini" id="btn-vaciar-razones" style="display:none;">Vaciar todas</button>
    </div>
  </div>

  <!-- NUEVO: modo observador (lista de tarjetas por subT; al tocar una se abre) -->
  <div id="pantalla-observador" style="display:none;">
    <p class="subtitulo">Modo observador: tocá una tarjeta para verla (sin evaluarte)</p>

    <!-- NUEVO: Buscador Global -->
    <div class="zona-buscador" id="zona-buscador-observador">
      <input type="text" class="input-buscador" id="input-buscador-observador" placeholder="Buscar tarjetas o rutas..." autocomplete="off">
      <div class="dropdown-buscador" id="dropdown-buscador-observador"></div>
    </div>

    <div class="lista-temas" id="lista-observador"></div>
    <!-- MODIFICADO: se agrega "Ver imágenes" junto al botón de volver -->
    <div class="fila-obs-botones">
      <button class="boton-secundario" id="btn-ver-huerfanas">📄 Rutas Huérfanas</button>
      <button class="boton-secundario" id="btn-ver-imagenes">🖼 Ver imágenes</button>
      <button class="boton-secundario" id="btn-observador-salir">&larr; Volver a temas</button>
    </div>

    <!-- NUEVO: notas y difíciles de imágenes (independiente de las de tarjetas) -->
    <div class="lista-temas zona-dificiles" id="zona-imagenes">
      <div class="cabecera-dificiles">
        <span class="titulo-dificiles">🖼 Datos de imágenes guardados</span>
      </div>
      <details id="det-imagenes" style="display:none">
        <summary id="suma-imagenes">Con nota o marcadas difícil</summary>
        <div id="lista-imagenes-guardadas"></div>
        <button class="boton-mini" id="btn-vincular-imagenes" style="margin: 6px 0 8px 16px; display:none;">Vincular a este evaluador</button>
      </details>
      <p class="nota-dificiles" id="nota-imagenes"></p>
      <button class="boton-mini" id="btn-vaciar-imagenes" style="display:none;">Vaciar todas</button>
    </div>
  </div>

  <!-- NUEVO: galería de imágenes de las rutas, vista secuencial e independiente -->
  <div id="pantalla-galeria" style="display:none;">
    <!-- NUEVO: Buscador Global (fijo/sticky para poder buscar mientras se navega) -->
    <div class="zona-buscador sticky-buscador" id="zona-buscador-galeria">
      <input type="text" class="input-buscador" id="input-buscador-galeria" placeholder="Buscar por ruta o descripción..." autocomplete="off">
      <div class="dropdown-buscador" id="dropdown-buscador-galeria"></div>
    </div>

    <div class="galeria-indicador" id="galeria-indicador"></div>
    <div class="galeria-imagen-wrap">
      <img id="galeria-img" class="galeria-img" alt="Imagen de una ruta" style="display:none;">
    </div>
    <p class="galeria-vacia" id="galeria-vacia" style="display:none;">No hay imágenes en las rutas de respuestas.txt.</p>
    <!-- NUEVO: descripción de la imagen (extraída del drawio), debajo de la imagen y arriba de Difícil/Nota -->
    <p class="galeria-descripcion" id="galeria-descripcion" style="display:none;"></p>

    <div class="fila-galeria-acciones" style="margin-top:14px;">
      <button class="boton-dificil" id="btn-galeria-dificil" title="Marcar/desmarcar esta imagen como difícil">☆ Difícil</button>
      <button class="boton-nota" id="btn-galeria-nota" title="Ver/editar nota de esta imagen">💡</button>
    </div>

    <!-- NUEVO: mismo estilo post-it que la nota de tarjeta -->
    <div class="panel-nota" id="panel-galeria-nota" style="display:none;">
      <div class="panel-nota-cabecera">
        <div class="panel-nota-titulo">💡 Nota de la imagen</div>
        <div class="panel-nota-versiones" id="galeria-nota-versiones" style="display:none;">
          <button class="boton-mini" id="btn-galeria-ver-actual">Versión actual</button>
          <button class="boton-mini" id="btn-galeria-ver-anterior">Versión anterior</button>
        </div>
      </div>
      <div class="panel-nota-texto" id="galeria-nota-texto"></div>
      <textarea class="panel-nota-editor" id="galeria-nota-editor" style="display:none;" placeholder="Escribí una nota para esta imagen"></textarea>
      <div class="panel-nota-acciones">
        <button class="boton-mini" id="btn-galeria-nota-editar">✎ Editar</button>
        <button class="boton-mini" id="btn-galeria-nota-guardar" style="display:none;">Guardar</button>
        <button class="boton-mini" id="btn-galeria-nota-cancelar" style="display:none;">Cancelar</button>
        <button class="boton-mini" id="btn-galeria-nota-revertir" style="display:none;">↩ Revertir a la anterior</button>
      </div>
    </div>

    <div class="navegacion" style="margin-top:20px;">
      <button class="boton-secundario" id="btn-galeria-anterior">&larr; Anterior</button>
      <button class="boton-secundario" id="btn-galeria-volver">☰ Volver a la lista</button>
      <button class="boton-secundario" id="btn-galeria-siguiente">Siguiente &rarr;</button>
    </div>
  </div>

  <!-- NUEVO: rutas de respuestas.txt no citadas por ninguna tarjeta -->
  <div id="pantalla-huerfanas" style="display:none;">
    <p class="subtitulo" id="huerfanas-subtitulo">Rutas de respuestas.txt que no están citadas por ninguna tarjeta</p>

    <div class="zona-buscador sticky-buscador" id="zona-buscador-huerfanas">
      <input type="text" class="input-buscador" id="input-buscador-huerfanas" placeholder="Buscar por ruta o contenido..." autocomplete="off">
      <div class="dropdown-buscador" id="dropdown-buscador-huerfanas"></div>
    </div>

    <div class="lista-huerfanas-scroll" id="lista-huerfanas"></div>
    <p class="galeria-vacia" id="huerfanas-vacia" style="display:none;">No hay rutas huérfanas (todas las rutas de respuestas.txt están asociadas a tarjetas).</p>

    <div style="display:flex; justify-content:center; margin-top:20px;">
      <button class="boton-secundario" id="btn-huerfanas-volver">☰ Volver a la lista</button>
    </div>
  </div>

  <!-- NUEVO: Gamificación — Tienda -->
  <div id="pantalla-tienda" style="display:none;">
    <p class="subtitulo">Tienda</p>
    <div class="saldo-tienda" id="saldo-tienda">Puntos disponibles: 0</div>
    <div class="grid-tienda" id="grid-tienda"></div>
    <div class="navegacion" style="margin-top:20px;">
      <button class="boton-secundario" id="btn-tienda-volver">← Volver</button>
    </div>
  </div>

  <!-- NUEVO: Gamificación — Historial -->
  <div id="pantalla-historial" style="display:none;">
    <p class="subtitulo">Historial de sesiones</p>
    <div class="fila-acciones-historial">
      <button class="boton-secundario" id="btn-abrir-sync" title="Backup completo y respaldos individuales">🔄 Sincronización</button>
      <button class="boton-secundario" id="btn-historial-borrar-este" title="Quita solo las sesiones de este evaluador">Borrar historial de este evaluador</button>
      <button class="boton-secundario" id="btn-historial-borrar-global" title="Vacia las sesiones de TODOS los evaluadores">Borrar historial global</button>
    </div>
    <div class="lista-historial-scroll" id="lista-historial"></div>
    <div class="historial-paginacion" id="historial-paginacion" style="display:none;"></div>
    <!-- NUEVO: estadísticas históricas consolidadas (sesiones ya podadas) de este evaluador -->
    <div class="resumen-historico" id="resumen-historico" style="display:none;"></div>
    <p class="galeria-vacia" id="historial-vacio" style="display:none;">Aún no hay sesiones guardadas.</p>
    <!-- NUEVO: sesiones de otros evaluadores (solo lectura, agrupadas por espacio) -->
    <div class="lista-temas zona-dificiles" id="zona-historial-otros" style="display:none; margin-top:14px;">
      <details id="det-historial-otros">
        <summary id="suma-historial-otros">Sesiones de otros evaluadores</summary>
        <div id="lista-historial-otros"></div>
      </details>
    </div>
    <div class="navegacion" style="margin-top:20px;">
      <button class="boton-secundario" id="btn-historial-volver">← Volver</button>
    </div>
  </div>

  <div id="area-tarjeta" style="display:none;">
    <!-- NUEVO: badge visible durante toda una sesión de Repaso Espaciado -->
    <div class="badge-srs" id="badge-srs" style="display:none;">🧠 Repaso Espaciado</div>
    <div class="barra-superior">
      <!-- NUEVO: Fila 1 — estadísticas de gamificación -->
      <div class="fila-gamificacion" id="fila-gamificacion">
        <span class="stat-gamificacion" id="stat-puntos">⭐ 0</span>
        <span class="stat-gamificacion" id="stat-racha">🔥 0</span>
        <span class="stat-gamificacion" id="stat-vidas"></span>
        <span class="stat-gamificacion" id="stat-escudo" title="Escudos de racha"></span>
        <span class="stat-gamificacion" id="stat-segunda" title="Segundas oportunidades"></span>
        <button class="boton-mute" id="btn-mute" title="Silenciar/activar sonidos">🔊</button>
      </div>
      <!-- Fila 2 — navegación (ya existía) -->
      <div class="fila-navegacion-superior">
        <div class="progreso-texto" id="progreso-texto"></div>
        <div class="grupo-cronometro">
          <div class="cronometro" id="cronometro">⏱ 00:00</div>
          <!-- NUEVO: pausa manual (por si dejás de estudiar un rato) -->
          <button class="boton-pausa" id="btn-pausa" title="Pausar el cronómetro">⏸ Pausar</button>
        </div>
      </div>
    </div>
    <div class="barra-progreso"><div class="barra-progreso-relleno" id="barra-relleno"></div></div>

    <div class="tema" id="tema-actual">
      <span class="tema-rombo"></span>
      <span id="tema-texto"></span>
    </div>

    <div class="tarjeta" id="tarjeta">
      <!-- MODIFICADO: badge con cantidad de rutas asociadas -->
      <div class="badge-rutas" id="badge-rutas" style="display:none;"></div>
      <!-- NUEVO: marcar la tarjeta para revisar (solo en memoria, durante esta sesión) -->
      <button class="boton-revisar" id="btn-revisar" title="Marcar para revisar (solo durante esta sesión)">🚩 Revisar</button>
      <div class="primero" id="tarjeta-primero"></div>
      <div class="zona-segundo" id="zona-segundo">
        <div class="fila-botones-tarjeta">
          <button class="boton-pista" id="btn-pista">Mostrar pista<span class="badge-conteo" id="badge-pista-gratis" style="display:none;"></span></button>
          <button class="boton-respuesta" id="btn-respuesta">Mostrar respuesta</button>
          <!-- NUEVO: botón de nota (solo se ve si la tarjeta tiene nota) -->
          <button class="boton-nota" id="btn-nota" title="Ver/ocultar nota (pausa el cronómetro)">💡</button>
        </div>
        <div class="separador-tarjeta" id="separador-tarjeta" style="display:none;"></div>
        <div class="segundo" id="tarjeta-segundo" style="display:none;"></div>
      </div>
    </div>

    <!-- NUEVO: panel de nota (texto plano, se inyecta con textContent) -->
    <div class="panel-nota" id="panel-nota">
      <div class="panel-nota-cabecera">
        <div class="panel-nota-titulo">💡 Nota <span class="panel-nota-estado" id="panel-nota-estado"></span></div>
        <div class="panel-nota-versiones" id="panel-nota-versiones" style="display:none;">
          <button class="boton-mini" id="btn-ver-local">Mi versión</button>
          <button class="boton-mini" id="btn-ver-txt">Versión del txt</button>
        </div>
      </div>
      <div class="panel-nota-texto" id="panel-nota-texto"></div>
      <textarea class="panel-nota-editor" id="nota-editor" style="display:none;" placeholder="Escribí tu nota (Ctrl+Enter para guardar)"></textarea>
      <div class="panel-nota-acciones">
        <button class="boton-mini" id="btn-nota-editar">✎ Editar</button>
        <button class="boton-mini" id="btn-nota-guardar" style="display:none;">Guardar</button>
        <button class="boton-mini" id="btn-nota-cancelar" style="display:none;">Cancelar</button>
        <button class="boton-mini" id="btn-nota-borrar" style="display:none;">🗑 Borrar nota</button>
      </div>
    </div>

    <!-- NUEVO: chips de razones guardadas (solo lectura) -->
    <div class="chips-razones" id="chips-razones" style="display:none;"></div>

    <!-- NUEVO: panel de razones (checkboxes de las 6 predefinidas) -->
    <div class="panel-razones" id="panel-razones" style="display:none;">
      <div class="panel-razones-titulo">🏷 ¿Por qué la marcaste así?</div>
      <div class="lista-check-razones" id="lista-check-razones"></div>
      <div class="panel-razones-acciones">
        <button class="boton-mini" id="btn-razones-guardar">Guardar</button>
        <button class="boton-mini" id="btn-razones-cerrar">Cerrar</button>
      </div>
    </div>

    <div class="zona-eval" id="zona-eval">
      <textarea id="texto-eval" placeholder="Escribí la respuesta con tus palabras (opcional). Enter para evaluar"></textarea>
      <!-- MODIFICADO: id agregado para poder ocultar el botón tras evaluar -->
      <div class="fila-eval" id="fila-eval">
        <button class="boton-pista" id="btn-evaluar">Evaluar respuesta</button>
      </div>
    </div>

    <div class="rutas-contenedor" id="rutas-contenedor"></div>

    <!-- NUEVO: avisos de consumibles (escudo / segunda oportunidad) -->
    <div class="aviso-juego" id="aviso-juego" role="status" style="display:none;"></div>

    <div class="estado-marca" id="estado-marca"></div>

    <div class="resultado-eval" id="resultado-eval"></div>

    <div class="botonera">
      <!-- MODIFICADO: fila de decisión (cambian resultados[] / avanzan la tarjeta) -->
      <div class="fila-decision">
        <button class="boton boton-circular boton-no" id="btn-no" title="No entendido">&#10007;</button>
        <button class="boton boton-saltar" id="btn-saltar">Pasar sin marcar<span class="badge-conteo" id="badge-comodines" style="display:none;"></span></button>
        <button class="boton boton-circular boton-si" id="btn-si" title="Entendido">&#10003;</button>
      </div>
      <!-- MODIFICADO: fila de etiquetas (metadata; no afectan la calificación) -->
      <div class="fila-etiquetas">
        <button class="boton-dificil" id="btn-dificil" title="Marcar/desmarcar como difícil (se guarda entre sesiones)">☆ Difícil</button>
        <button class="boton-dificil boton-razones" id="btn-razones" title="Registrar por qué marcaste esta tarjeta así">🏷 Razones<span class="badge-conteo" id="badge-conteo-razones" style="display:none;"></span></button>
      </div>
    </div>

    <div class="navegacion">
      <button class="boton-nav" id="btn-anterior">&larr; Anterior</button>
      <button class="boton-finalizar" id="btn-finalizar">Finalizar sesión</button>
      <!-- NUEVO: solo visible en modo observador (reemplaza a Finalizar sesión) -->
      <button class="boton-finalizar" id="btn-observador-volver">☰ Volver a la lista</button>
      <button class="boton-nav" id="btn-siguiente">Siguiente &rarr;</button>
    </div>
  </div>

  <div class="pantalla-resumen" id="pantalla-resumen">
    <!-- NUEVO: guardar la selección de esta sesión como lista (solo si no venía de una lista) -->
    <button class="boton-icono-circular boton-guardar-lista" id="btn-resumen-guardar-lista" title="Guardar esta selección como lista" style="display:none;">📋</button>
    <!-- NUEVO: análisis de la sesión por subtema -->
    <button class="boton-icono-circular boton-analisis-sesion" id="btn-analisis-sesion" title="Análisis de la sesión" style="display:none;">📊</button>
    <h2>Resumen de la sesión</h2>
    <div class="nota-sesion" id="nota-sesion">🎓 Nota de la sesión: 0.0 / 10</div>
    <div class="nota-listado" id="nota-listado" style="display:none;"></div>

    <!-- NUEVO: Gamificación — puntos, racha y modo jugado -->
    <div class="listas-resumen" id="resumen-gamificacion" style="display:none;">
      <div class="fila-resumen">
        <span class="etiqueta-resumen" id="resumen-puntos">⭐ Puntos ganados: 0</span>
      </div>
      <div class="fila-resumen">
        <span class="etiqueta-resumen" id="resumen-mejor-racha">🔥 Mejor racha: 0</span>
      </div>
      <div class="fila-resumen">
        <span class="etiqueta-resumen" id="resumen-modo">🎮 Modo: Light</span>
      </div>
    </div>

    <div class="fila-resumen">
      <span class="etiqueta-resumen">⏱ Tiempo total</span>
      <span class="valor-resumen chico" id="tiempo-total">00:00</span>
    </div>
    <div class="fila-resumen">
      <span class="etiqueta-resumen">⏱ Promedio por tarjeta</span>
      <span class="valor-resumen chico" id="tiempo-promedio">00:00</span>
    </div>

    <div class="listas-resumen">
      <!-- MODIFICADO: solo 3 desplegables; tiempo, respuesta escrita y
           evaluación de cada tarjeta se muestran dentro de cada item -->
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-si"></span> <span id="etq-si">Entendidas (0)</span></span></summary>
        <div class="lista-contenido" id="lista-si"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-casi"></span> <span id="etq-casi">Casi (0)</span></span></summary>
        <div class="lista-contenido" id="lista-casi"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-no"></span> <span id="etq-no">No entendidas (0)</span></span></summary>
        <div class="lista-contenido" id="lista-no"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-saltar"></span> <span id="etq-saltar">Pasadas sin marcar (0)</span></span></summary>
        <div class="lista-contenido" id="lista-saltar"></div>
      </details>
      <!-- NUEVO: tarjetas marcadas con 🚩 Revisar durante la sesión -->
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen" id="etq-revisar">🚩 Para revisar (0)</span></summary>
        <div class="lista-contenido" id="lista-revisar"></div>
      </details>
    </div>

    <div class="acciones-resumen">
      <button class="boton-reiniciar" id="btn-continuar-pendientes" style="display:none;">Continuar con las no respondidas</button>
      <button class="boton-reiniciar" id="btn-repasar-no" style="display:none;">Repasar las que no entendí</button>
      <button class="boton-reiniciar" id="btn-reiniciar">Reiniciar esta ronda</button>
      <button class="boton-secundario" id="btn-elegir-temas">Elegir otros temas</button>
    </div>
  </div>
</div>

<!-- NUEVO: overlay de pausa manual -->
<div class="overlay-pausa" id="overlay-pausa">
  <div class="titulo-pausa">⏸ En pausa</div>
  <div class="texto-pausa">El cronómetro está detenido. Tus respuestas y tarjetas siguen como estaban.</div>
  <button class="boton-reiniciar" id="btn-reanudar">▶ Reanudar</button>
</div>

<!-- NUEVO: modal (no usa window.confirm) para continuar con las no respondidas -->
<div class="modal-overlay" id="modal-continuar">
  <div class="modal-caja" role="dialog" aria-modal="true">
    <h3>Continuar con las no respondidas</h3>
    <p id="modal-continuar-texto"></p>
    <button class="boton-reiniciar" id="btn-modal-sesion">Continuar en esta sesión</button>
    <div class="modal-ayuda">Vuelve a la primera pendiente, conservando tiempos y aciertos.</div>
    <button class="boton-secundario" id="btn-modal-ronda">Iniciar nueva ronda</button>
    <div class="modal-ayuda">Sesión nueva solo con esas tarjetas (nota y tiempos aparte).</div>
    <button class="modal-cancelar" id="btn-modal-cancelar">Cancelar</button>
  </div>
</div>

<!-- NUEVO: modal Sí/No de la Segunda oportunidad -->
<div class="modal-overlay" id="modal-segunda">
  <div class="modal-caja" role="dialog" aria-modal="true">
    <h3>🔁 Segunda oportunidad</h3>
    <p id="modal-segunda-texto"></p>
    <div class="modal-ayuda" id="modal-segunda-ayuda"></div>
    <button class="boton-reiniciar" id="btn-segunda-si">Sí, reintentar</button>
    <button class="boton-secundario" id="btn-segunda-no">No, seguir</button>
  </div>
</div>

<!-- NUEVO: modal de sincronización (backup unificado) -->
<div class="modal-overlay" id="modal-sync">
  <div class="modal-caja modal-caja-ancha" role="dialog" aria-modal="true">
    <h3>🔄 Sincronización</h3>
    <p class="modal-ayuda" id="sync-fecha-backup">📅 Sin backups realizados</p>
    <div class="vinc-aviso" id="sync-aviso-pendientes" style="display:none;">⚠ Hay cambios sin respaldar</div>

    <div class="sync-seccion-titulo">Backup completo</div>
    <div class="fila-acciones-listas">
      <button class="boton-mini" id="btn-backup-exportar">📦 Exportar backup</button>
      <button class="boton-mini" id="btn-backup-importar">📥 Importar backup</button>
      <input type="file" id="input-backup-importar" accept="application/json,.json" style="display:none;">
    </div>
    <p class="lista-detalle" id="sync-backup-info"></p>
    <button class="boton-mini" id="btn-vincular-todo" style="width:100%; margin-bottom: 6px;">🔗 Vincular todo a este evaluador</button>
    <button class="boton-mini boton-mini-peligro" id="btn-borrar-evaluador" style="width:100%; margin-bottom: 6px;">🗑 Borrar un evaluador completo</button>
    <div id="lista-borrar-evaluador" style="display:none; margin-bottom: 6px;"></div>

    <div class="sync-seccion-titulo">Backups individuales</div>
    <div class="fila-backup-zona">
      <span class="zona-nombre">⭐ Gamificación</span>
      <span class="zona-botones">
        <button class="boton-mini" id="btn-historial-exportar">Exportar</button>
        <button class="boton-mini" id="btn-historial-importar">Importar</button>
        <input type="file" id="input-importar-historial" accept="application/json,.json" style="display:none;">
      </span>
    </div>
    <div class="fila-backup-zona">
      <span class="zona-nombre">☆ Difíciles</span>
      <span class="zona-botones">
        <button class="boton-mini" id="btn-exportar-dificiles" title="Descarga las difíciles activas como JSON">Exportar</button>
        <button class="boton-mini" id="btn-importar-dificiles" title="Agrega las difíciles de un JSON (fusiona con las actuales)">Importar</button>
        <input type="file" id="input-importar" accept=".json,application/json" style="display:none">
      </span>
    </div>
    <div class="fila-backup-zona">
      <span class="zona-nombre">💡 Notas</span>
      <span class="zona-botones">
        <button class="boton-mini" id="btn-exportar-notas" title="Descarga tus notas editadas como JSON">Exportar</button>
        <button class="boton-mini" id="btn-importar-notas" title="Fusiona las notas de un JSON (gana la más reciente)">Importar</button>
        <input type="file" id="input-importar-notas" accept=".json,application/json" style="display:none">
      </span>
    </div>
    <div class="fila-backup-zona">
      <span class="zona-nombre">🏷 Razones</span>
      <span class="zona-botones">
        <button class="boton-mini" id="btn-exportar-razones">Exportar</button>
        <button class="boton-mini" id="btn-importar-razones">Importar</button>
        <input type="file" id="input-importar-razones" accept=".json,application/json" style="display:none">
      </span>
    </div>
    <div class="fila-backup-zona">
      <span class="zona-nombre">🖼 Imágenes</span>
      <span class="zona-botones">
        <button class="boton-mini" id="btn-exportar-imagenes">Exportar</button>
        <button class="boton-mini" id="btn-importar-imagenes">Importar</button>
        <input type="file" id="input-importar-imagenes" accept=".json,application/json" style="display:none">
      </span>
    </div>
    <div class="fila-backup-zona">
      <span class="zona-nombre">📋 Listas</span>
      <span class="zona-botones">
        <button class="boton-mini" id="btn-listas-exportar">Exportar</button>
        <button class="boton-mini" id="btn-listas-importar">Importar</button>
        <input type="file" id="input-listas-importar" accept="application/json" style="display:none;">
      </span>
    </div>

    <button class="modal-cancelar" id="btn-sync-cerrar">Cerrar</button>
  </div>
</div>

<!-- NUEVO: modal de listas guardadas -->
<div class="modal-overlay" id="modal-listas">
  <div class="modal-caja modal-caja-ancha" role="dialog" aria-modal="true">
    <h3>📋 Listas guardadas</h3>
    <p class="modal-ayuda">Guardá selecciones de tarjetas para reutilizarlas sin volver a marcarlas.</p>
    <div class="fila-acciones-listas">
      <button class="boton-mini boton-mini-peligro" id="btn-listas-vaciar">Vaciar todas</button>
    </div>
    <details id="det-listas-vincular" style="display:none">
      <summary id="suma-listas-vincular">Hay tarjetas de listas de otros evaluadores</summary>
      <button class="boton-mini" id="btn-listas-vincular" style="margin: 6px 0 8px 16px;">Vincular a este evaluador</button>
    </details>
    <button class="boton-secundario boton-lista-fila" id="btn-lista-ninguna">Ninguna (selección manual)</button>
    <div id="lista-listas-guardadas"></div>
    <p class="galeria-vacia" id="listas-vacio" style="display:none;">Todavía no guardaste ninguna lista.</p>
    <button class="modal-cancelar" id="btn-listas-cerrar">Cerrar</button>
  </div>
</div>

<!-- NUEVO: modal "Análisis de la sesión" (desde el resumen) -->
<div class="modal-overlay" id="modal-analisis-sesion">
  <div class="modal-caja modal-caja-ancha" role="dialog" aria-modal="true">
    <h3>📊 Análisis de la sesión</h3>
    <div id="analisis-sesion-contenido"></div>
    <button class="modal-cancelar" id="btn-analisis-sesion-cerrar">Cerrar</button>
  </div>
</div>

<!-- NUEVO: modal para nombrar una lista nueva (desde el resumen) -->
<div class="modal-overlay" id="modal-guardar-lista">
  <div class="modal-caja" role="dialog" aria-modal="true">
    <h3>📋 Guardar como lista</h3>
    <p>Nombrá esta selección de tarjetas para poder reutilizarla después.</p>
    <input type="text" class="input-buscador" id="input-guardar-lista-nombre" placeholder="Nombre de la lista" autocomplete="off">
    <div class="vinc-aviso" id="guardar-lista-error" style="display:none;"></div>
    <button class="boton-reiniciar" id="btn-guardar-lista-aceptar">Guardar</button>
    <button class="modal-cancelar" id="btn-guardar-lista-cancelar">Cancelar</button>
  </div>
</div>

<!-- NUEVO: modal de aviso de Repaso Espaciado (listas SRS vencidas) -->
<div class="modal-overlay" id="modal-srs-aviso">
  <div class="modal-caja" role="dialog" aria-modal="true">
    <h3>🧠 Repaso Espaciado</h3>
    <p>Tenés tarjetas pendientes de 🧠 Repaso Espaciado:</p>
    <ul id="srs-aviso-lista" style="text-align:left; padding-left: 22px; font-size: 13px; color: #444; margin: 0 0 8px;"></ul>
    <button class="boton-reiniciar" id="btn-srs-repasar-ahora">🧠 Repasar ahora</button>
    <button class="boton-secundario" id="btn-srs-omitir-hoy">⏭ Omitir por hoy</button>
    <button class="boton-secundario" id="btn-srs-recordar-luego">⏰ Recordar más tarde</button>
  </div>
</div>

<!-- NUEVO: modal de revinculación (recuperar datos de un evaluador renombrado) -->
<div class="modal-overlay" id="modal-vincular">
  <div class="modal-caja" role="dialog" aria-modal="true">
    <h3 id="vinc-titulo">Vincular a este evaluador</h3>
    <p id="vinc-descripcion"></p>
    <div id="vinc-zonas" style="display:none;">
      <label class="vinc-zona-item"><input type="checkbox" class="vinc-zona-chk" value="dificiles" checked> ☆ Difíciles</label>
      <label class="vinc-zona-item"><input type="checkbox" class="vinc-zona-chk" value="notas" checked> 💡 Notas</label>
      <label class="vinc-zona-item"><input type="checkbox" class="vinc-zona-chk" value="razones" checked> 🏷 Razones</label>
      <label class="vinc-zona-item"><input type="checkbox" class="vinc-zona-chk" value="imagenes" checked> 🖼 Imágenes</label>
      <label class="vinc-zona-item"><input type="checkbox" class="vinc-zona-chk" value="listas" checked> 📋 Listas</label>
      <label class="vinc-zona-item"><input type="checkbox" class="vinc-zona-chk" value="sesiones" checked> 🕐 Sesiones</label>
    </div>
    <div id="vinc-campo-espacio" style="display:none;">
      <input type="text" class="input-buscador" id="vinc-espacio" list="vinc-espacios" placeholder="Nombre anterior del recordatorio (sin .txt)" autocomplete="off">
      <datalist id="vinc-espacios"></datalist>
    </div>
    <p id="vinc-resumen" class="vinc-resumen"></p>
    <div class="vinc-aviso" id="vinc-aviso"></div>
    <div class="modal-ayuda">Para confirmar, escribí CONFIRMAR:</div>
    <input type="text" class="input-buscador" id="vinc-confirmar" placeholder="CONFIRMAR" autocomplete="off">
    <button class="boton-reiniciar" id="btn-vinc-aceptar" disabled>Vincular</button>
    <button class="modal-cancelar" id="btn-vinc-cancelar">Cancelar</button>
  </div>
</div>

<!-- NUEVO: Gamificación — modal de selección de modo, antes de iniciar sesión -->
<div class="modal-overlay" id="modal-modo">
  <div class="modal-caja" role="dialog" aria-modal="true">
    <h3>Elegí el modo de juego</h3>
    <div class="opciones-modo">
      <button type="button" class="opcion-modo" id="opcion-modo-tryhard" data-modo="tryhard">
        <span class="opcion-modo-icono">🔥</span>
        <span class="opcion-modo-nombre">Tryhard</span>
        <span class="opcion-modo-detalle">1 vida</span>
      </button>
      <button type="button" class="opcion-modo" id="opcion-modo-normal" data-modo="normal">
        <span class="opcion-modo-icono">⚔️</span>
        <span class="opcion-modo-nombre">Normal</span>
        <span class="opcion-modo-detalle">3 vidas</span>
      </button>
      <button type="button" class="opcion-modo seleccionada" id="opcion-modo-light" data-modo="light">
        <span class="opcion-modo-icono">🌟</span>
        <span class="opcion-modo-nombre">Light</span>
        <span class="opcion-modo-detalle">Sin vidas, sin puntos</span>
      </button>
    </div>
    <button class="boton-principal" id="btn-modo-confirmar">Comenzar</button>
    <button class="modal-cancelar" id="btn-modo-cancelar">Cancelar</button>
  </div>
</div>

<!-- NUEVO: Gamificación — modal de Game Over (solo Tryhard/Normal) -->
<div class="modal-overlay modal-gameover" id="modal-gameover">
  <div class="modal-caja modal-caja-gameover" role="dialog" aria-modal="true">
    <h3 class="titulo-gameover">💀 Game Over</h3>
    <p>Te quedaste sin vidas.</p>
    <button class="boton-principal" id="btn-gameover-resumen">Ver resumen</button>
  </div>
</div>

<div class="lightbox" id="lightbox">
  <button class="lightbox-cerrar" title="Cerrar">&times;</button>
  <img id="lightbox-img" src="" alt="Imagen ampliada">
</div>

<script>
  const datos = __DATOS_JSON__;
  const tarjetasCompletas = datos.tarjetas;
  const rutasDisp = datos.rutas;   // { numero: { tema: ..., lineas: [...] } }
  const modoAleatorio = __MODO_ALEATORIO__;
  const UMBRAL_CASI = __UMBRAL_CASI__;
  // NUEVO: economía de los consumibles de la tienda (se editan arriba, en el .py)
  const PRECIO_COMODIN = __PRECIO_COMODIN__;
  const PRECIO_PISTA_GRATIS = __PRECIO_PISTA_GRATIS__;
  const PRECIO_ESCUDO_RACHA = __PRECIO_ESCUDO_RACHA__;
  const PRECIO_SEGUNDA_OPORTUNIDAD = __PRECIO_SEGUNDA_OPORTUNIDAD__;
  const FACTOR_RECOMPENSA_REINTENTO = __FACTOR_RECOMPENSA_REINTENTO__;
  const SIM_ALTA = __SIM_ALTA__;
  const SIM_SIN_PISTA = __SIM_SIN_PISTA__;   // NUEVO: todas las claves, sin pista
  const SIM_CON_PISTA = __SIM_CON_PISTA__;   // NUEVO: todas las claves, con pista (debe ser MAYOR)
  const STOPWORDS = new Set(__STOPWORDS__);  // NUEVO: palabras vacías (solo para evaluar)

  // ---------- Estado de la ronda actual ----------
  let tarjetasSesion = [];
  let resultados = [];
  let tiempos = [];
  let revelado = [];
  let pistaMostrada = [];  // MODIFICADO: si se usó 'Mostrar pista' (afecta la nota)
  let respuestaMostrada = [];
  let escritos = [];       // texto escrito por tarjeta (aunque no se evalue)
  let evaluaciones = [];   // {resultado, acertadas, faltaron, clavesTotal, propClaves, sim, comunes, totalEscritas, totalReferencia} o null
  let indiceActual = 0;
  let cronometroActivo = true; // false cuando ya no queda nada por marcar
  let indiceCongelado = -1;    // índice de la tarjeta cuya vista congela el cronómetro
  let notaAbierta = false;     // NUEVO: la nota de la tarjeta actual está abierta (también congela)
  let editandoNota = false;    // NUEVO: el panel de nota está en modo edición
  let versionNota = 'local';   // NUEVO: qué versión se ve en el panel: 'local' | 'txt'
  let modoObservador = false;  // NUEVO: viendo tarjetas sin evaluarse (sin cronómetro ni calificación)
  let observadorAbiertos = new Set();  // NUEVO: subT abiertos en la lista del observador
  let pausaManual = false;     // NUEVO: cronómetro pausado a mano (botón ⏸ Pausar)
  // ---------- MODIFICADO: "Revisar" en memoria, sin persistencia en disco ----------
  // Vive solo mientras la pestaña está abierta: no lee ni escribe localStorage.
  // Al recargar o cerrar la página, revisarEnMemoria se pierde por completo (a
  // propósito). Funciona igual en sesión de estudio y en modo observador, porque
  // ambos operan sobre tarjetasSesion[indiceActual].
  let revisarEnMemoria = {};

  function esRevisar(t) { return !!revisarEnMemoria[hashTarjeta(t)]; }

  function toggleRevisarActual() {
    if (tarjetasSesion.length === 0) return;
    const t = tarjetasSesion[indiceActual];
    const h = hashTarjeta(t);
    if (revisarEnMemoria[h]) {
      delete revisarEnMemoria[h];
    } else {
      revisarEnMemoria[h] = { p: t.primero || '', s: t.segundo || '', t: Date.now() };
    }
    renderTarjeta();
  }
  let porComprension = [];     // NUEVO: tarjetas marcadas Entendida por comprensión
  let razonesAbierto = false;  // NUEVO: panel de razones abierto (pausa el cronómetro)
  let razonesPendientes = null; // NUEVO: array de códigos en edición, null si el panel está cerrado

  // NUEVO: razones predefinidas (código fijo, sin texto libre ni personalización)
  const RAZONES_DEF = [
    { codigo: 'olvido', texto: 'Olvido', color: '#9CA3AF' },
    { codigo: 'incompleto', texto: 'Incompleto', color: '#F59E0B' },
    { codigo: 'confusion', texto: 'Confusión', color: '#EF4444' },
    { codigo: 'mezcla', texto: 'Mezcla de conceptos', color: '#F97316' },
    { codigo: 'terminologia', texto: 'Terminología específica', color: '#3B82F6' },
    { codigo: 'contexto', texto: 'Contexto / redacción', color: '#8B5CF6' },
  ];
  const MAPA_RAZONES = new Map(RAZONES_DEF.map((r) => [r.codigo, r]));

  function hexToRgba(hex, alpha) {
    const v = hex.replace('#', '');
    const r = parseInt(v.substring(0, 2), 16), g = parseInt(v.substring(2, 4), 16), b = parseInt(v.substring(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }
  let tiempoInicioTarjeta = 0;
  let cronometroIntervalId = null;

  // ============================================================
  // NUEVO: Gamificación — persistencia unificada (localStorage)
  // ============================================================
  const CLAVE_GAMIFICACION = 'gamificacion_v1';

  function valoresPorDefectoGamificacion() {
    return { puntosTotales: 0, inventario: { comodines: 0, pistasGratis: 0, escudosRacha: 0, segundasOportunidades: 0 }, historial: [], resumenPorEvaluador: {} };
  }

  function cargarGamificacion() {
    try {
      const crudo = localStorage.getItem(CLAVE_GAMIFICACION);
      if (!crudo) return valoresPorDefectoGamificacion();
      const obj = JSON.parse(crudo);
      if (!obj || typeof obj !== 'object') return valoresPorDefectoGamificacion();
      // Retrocompatibilidad: si falta algún campo, se completa con el default.
      const base = valoresPorDefectoGamificacion();
      return {
        puntosTotales: typeof obj.puntosTotales === 'number' ? obj.puntosTotales : base.puntosTotales,
        inventario: {
          comodines: (obj.inventario && typeof obj.inventario.comodines === 'number') ? obj.inventario.comodines : base.inventario.comodines,
          pistasGratis: (obj.inventario && typeof obj.inventario.pistasGratis === 'number') ? obj.inventario.pistasGratis : base.inventario.pistasGratis,
          escudosRacha: (obj.inventario && typeof obj.inventario.escudosRacha === 'number') ? obj.inventario.escudosRacha : base.inventario.escudosRacha,
          segundasOportunidades: (obj.inventario && typeof obj.inventario.segundasOportunidades === 'number') ? obj.inventario.segundasOportunidades : base.inventario.segundasOportunidades,
        },
        historial: Array.isArray(obj.historial) ? obj.historial : base.historial,
        // NUEVO: estadísticas consolidadas de sesiones ya podadas, por evaluador
        resumenPorEvaluador: (obj.resumenPorEvaluador && typeof obj.resumenPorEvaluador === 'object') ? obj.resumenPorEvaluador : base.resumenPorEvaluador,
      };
    } catch (e) {
      return valoresPorDefectoGamificacion();
    }
  }

  function guardarGamificacion() {
    try {
      localStorage.setItem(CLAVE_GAMIFICACION, JSON.stringify(gamificacion));
    } catch (e) {
      // Sin localStorage disponible: solo queda en memoria de esta sesión
    }
  }

  let gamificacion = cargarGamificacion();

  // ---------- Estado de sesión (se resetea en cada iniciarSesion) ----------
  let modoJuego = 'light';        // 'tryhard' | 'normal' | 'light'
  let vidasMaximas = 0;
  let vidasActuales = 0;
  let puntosSesion = 0;
  let rachaActual = 0;
  let mejorRacha = 0;
  let multiplicadorActual = 1;
  let puntosPorTarjeta = [];              // paralelo a resultados: puntos ya sumados por cada tarjeta
  let pistaGratisUsadaPorTarjeta = [];    // paralelo a resultados
  let gameOverDisparado = false;
  let historialGuardadoEstaSesion = false;
  // NUEVO: segunda oportunidad / escudo. SOLO en memoria: se reinician en cada sesión.
  let reintentadasSesion = new Set();      // tarjetas (objeto) que ya usaron su reintento en esta sesión
  let primerIntentoFallido = new Set();    // índices que fallaron el primer intento y usaron segunda oportunidad (quedan para repaso)
  let reintentoExitoso = new Set();        // subconjunto: el reintento salió bien (recompensa reducida)
  let segundaPendiente = false;            // hay un diálogo Sí/No abierto
  let modoZombie = false;   // NUEVO: si es true, se ignora POR COMPLETO la lógica de puntos/vidas/racha
  // NUEVO: sesión de Repaso Espaciado en curso. null = sesión normal.
  // { nombres: string[], hashesPorLista: { nombreLista: Set<hash> } }
  let srsSesionActiva = null;
  let sonidosActivos = true;   // NUEVO: efímero (no se guarda en localStorage), como pide el prompt

  // ---------- MODIFICADO: estado persistente de tarjetas difíciles ----------
  // Mapa { hash: {p, s, activa} } guardado en localStorage.
  // El hash se calcula sobre el texto normalizado, así el marcado sobrevive
  // a regeneraciones del HTML y a reordenamientos del recordatorio.txt.
  // activa=false = desmarcada: sigue guardada y se puede re-activar.
  const CLAVE_DIFICILES = 'dificiles_tarjetas_v1';
  let dificiles = cargarDificiles();

  function cargarDificiles() {
    try {
      const crudo = localStorage.getItem(CLAVE_DIFICILES);
      if (!crudo) return {};
      const obj = JSON.parse(crudo);
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
        // Migración: entradas viejas sin 'activa' se toman como activas
        Object.keys(obj).forEach((h) => {
          if (obj[h] && typeof obj[h] === 'object' && typeof obj[h].activa === 'undefined') {
            obj[h].activa = true;
          }
        });
        return obj;
      }
      return {};
    } catch (e) {
      return {};
    }
  }

  // ---------- OPTIMIZACIÓN: debounce de los guardados en localStorage ----------
  // Cada cambio (marcar difícil, editar nota, guardar razón, etc.) dispara un
  // guardado; si el usuario hace varios cambios seguidos (p. ej. marcar 10
  // tarjetas), antes se serializaba el objeto completo 10 veces. Con
  // crearGuardadoDebounced() se agrupan en UNA sola escritura 200-300ms
  // después del último cambio. _guardadosPendientes guarda el flush() de cada
  // guardado debounced registrado, para poder forzarlos todos en beforeunload
  // (si el usuario cierra la pestaña antes de que dispare el timer).
  const _guardadosPendientes = [];

  function crearGuardadoDebounced(fnGuardarYa, ms) {
    let temporizador = null;
    let pendiente = false;
    const disparar = () => {
      temporizador = null;
      pendiente = false;
      fnGuardarYa();
    };
    const debounced = function () {
      pendiente = true;
      clearTimeout(temporizador);
      temporizador = setTimeout(disparar, ms);
    };
    debounced.flush = function () {
      if (!pendiente) return;
      clearTimeout(temporizador);
      disparar();
    };
    _guardadosPendientes.push(debounced);
    return debounced;
  }

  // Si el usuario cierra/recarga la pestaña con guardados pendientes (dentro
  // de la ventana del debounce), se fuerza a que se escriban ya mismo.
  window.addEventListener('beforeunload', () => {
    _guardadosPendientes.forEach((d) => d.flush());
  });

  function guardarDificilesYa() {
    try {
      localStorage.setItem(CLAVE_DIFICILES, JSON.stringify(dificiles));
    } catch (e) {
      // Sin localStorage disponible: solo quedan en memoria de esta sesión
    }
  }
  const guardarDificiles = crearGuardadoDebounced(guardarDificilesYa, 250);

  // NUEVO: Listas guardadas de tarjetas (selecciones reutilizables de "Elegir temas")
  const CLAVE_LISTAS = 'listas_guardadas_v1';
  let listas = cargarListas();
  let listaActivaNombre = null;   // null = selección manual; string = nombre de la lista activa

  function cargarListas() {
    try {
      const crudo = localStorage.getItem(CLAVE_LISTAS);
      if (!crudo) return [];
      const arr = JSON.parse(crudo);
      return Array.isArray(arr) ? arr.filter((l) => l && typeof l.nombre === 'string' && Array.isArray(l.hashes)) : [];
    } catch (e) {
      return [];
    }
  }

  function guardarListasYa() {
    try {
      localStorage.setItem(CLAVE_LISTAS, JSON.stringify(listas));
    } catch (e) {
      // Sin localStorage disponible: quedan solo en memoria de esta sesión
    }
  }
  const guardarListas = crearGuardadoDebounced(guardarListasYa, 250);

  function buscarLista(nombre) {
    const clave = nombre.toLowerCase();
    return listas.find((l) => l.nombre.toLowerCase() === clave) || null;
  }

  // ============================================================
  // NUEVO: Repaso Espaciado (SRS) — opera a nivel de LISTA completa.
  // ============================================================
  const SRS_DEFAULT = { activated: false, startDate: 0, currentLevel: 1, lastReviewDate: 0, graduated: false, snoozeUntil: 0 };
  const SRS_INTERVALOS_DIAS = { 1: 1, 2: 3, 3: 7, 4: 14, 5: 30, 6: 60 };
  const SRS_MS_DIA = 86400000;

  function calcularIntervaloDias(level) {
    return SRS_INTERVALOS_DIAS[level] || SRS_INTERVALOS_DIAS[6];
  }

  // Devuelve el objeto srs de una lista, completando con los valores por
  // defecto los campos que falten (listas guardadas antes de este cambio, o
  // importadas de forma incompleta). Muta 'lista.srs' para dejarlo completo.
  function obtenerSrsLista(lista) {
    lista.srs = Object.assign({}, SRS_DEFAULT, (lista.srs && typeof lista.srs === 'object') ? lista.srs : {});
    return lista.srs;
  }

  // Solo el vencimiento por intervalo (sin mirar snoozeUntil); usado para el
  // texto "Vencido" del modal de listas.
  function srsIntervaloVencido(srs) {
    if (!srs.activated || srs.graduated) return false;
    return Date.now() >= (srs.lastReviewDate + calcularIntervaloDias(srs.currentLevel) * SRS_MS_DIA);
  }

  // §4.2: condición completa para que una lista dispare el aviso (intervalo
  // vencido Y no silenciada Y con tarjetas).
  function srsListaParaAviso(lista) {
    const srs = obtenerSrsLista(lista);
    if (!srs.activated || srs.graduated) return false;
    if (!lista.hashes || lista.hashes.length === 0) return false;
    if (Date.now() < (srs.lastReviewDate + calcularIntervaloDias(srs.currentLevel) * SRS_MS_DIA)) return false;
    if (Date.now() < (srs.snoozeUntil || 0)) return false;
    return true;
  }

  // Texto estático (no es un cronómetro en vivo) del "Próximo repaso".
  function srsTextoProximoRepaso(srs) {
    const objetivo = srs.lastReviewDate + calcularIntervaloDias(srs.currentLevel) * SRS_MS_DIA;
    const diasRestantes = (objetivo - Date.now()) / SRS_MS_DIA;
    if (diasRestantes < 0) return 'Vencido';
    if (diasRestantes < 1) {
      const horas = Math.max(1, Math.round(diasRestantes * 24));
      return 'en ' + horas + ' hora' + (horas === 1 ? '' : 's');
    }
    const dias = Math.round(diasRestantes);
    return 'en ' + dias + ' día' + (dias === 1 ? '' : 's');
  }

  function srsClaseBoton(srs) {
    if (srs.graduated) return 'srs-graduado';
    if (srs.activated) return 'srs-activo';
    return '';
  }

  // Validación defensiva de un objeto 'srs' que llega de un import (individual
  // o de un backup completo), para que datos corruptos no rompan nada.
  function normalizarSrsImportado(srsRaw) {
    if (!srsRaw || typeof srsRaw !== 'object') return Object.assign({}, SRS_DEFAULT);
    const nivel = (typeof srsRaw.currentLevel === 'number' && srsRaw.currentLevel >= 1 && srsRaw.currentLevel <= 6) ? srsRaw.currentLevel : 1;
    return {
      activated: !!srsRaw.activated,
      startDate: typeof srsRaw.startDate === 'number' ? srsRaw.startDate : 0,
      currentLevel: nivel,
      lastReviewDate: typeof srsRaw.lastReviewDate === 'number' ? srsRaw.lastReviewDate : 0,
      graduated: !!srsRaw.graduated,
      snoozeUntil: typeof srsRaw.snoozeUntil === 'number' ? srsRaw.snoozeUntil : 0,
    };
  }


  // NUEVO: identifica a ESTE evaluador (nombre de su recordatorio.txt). Se mezcla
  // en el hash para que dos evaluadores distintos con una tarjeta de texto
  // idéntico ("primero + segundo" igual) no compartan difíciles, notas ni razones.
  // Regenerar el HTML desde el MISMO recordatorio.txt mantiene este valor igual
  // (no invalida lo ya guardado); solo cambia si el archivo cambia de nombre.
  const ESPACIO_HASH = __ESPACIO__;
  document.getElementById('nombre-evaluador').textContent = ESPACIO_HASH;

  function hashTarjetaCon(espacio, t) {
    const base = espacio + '\\u0001' + (t.primero || '') + '\\u0000' + (t.segundo || '');
    const norm = normalizarPalabra(base).replace(/\\s+/g, ' ').trim();
    // djb2 -> hash de 32 bits (suficiente y sin dependencias; se mantiene corto
    // aunque se agregue el espacio, porque solo se usa como entrada del hash)
    let h = 5381;
    for (let i = 0; i < norm.length; i++) {
      h = ((h << 5) + h + norm.charCodeAt(i)) >>> 0;
    }
    return 'h' + h.toString(36);
  }

  // NUEVO: hash de ESTE evaluador (el de siempre). hashTarjetaCon permite
  // recomputar el hash que tendria una tarjeta bajo OTRO espacio (revinculacion).
  // OPTIMIZACIÓN: hashTarjeta() se llama decenas de veces por render con el
  // MISMO objeto 't' (viene siempre del mismo array de tarjetas parseadas),
  // así que se cachea por identidad de objeto en un WeakMap: si 't' no cambia,
  // no se vuelve a normalizar ni recalcular el djb2. hashTarjetaCon() (usada
  // para revincular con OTRO espacio) no se toca: no es el camino caliente.
  const _cacheHashTarjeta = new WeakMap();
  function hashTarjeta(t) {
    const previo = _cacheHashTarjeta.get(t);
    if (previo !== undefined) return previo;
    const h = hashTarjetaCon(ESPACIO_HASH, t);
    _cacheHashTarjeta.set(t, h);
    return h;
  }

  function esDificil(t) {
    const e = dificiles[hashTarjeta(t)];
    return !!(e && e.activa);
  }

  function toggleDificilActual() {
    if (tarjetasSesion.length === 0) return;
    const t = tarjetasSesion[indiceActual];
    const h = hashTarjeta(t);
    const entrada = dificiles[h];
    if (entrada && entrada.activa) {
      entrada.activa = false;
      entrada.t = Date.now();   // NUEVO: para saber si hay cambios sin respaldar (§8)
    } else if (entrada) {
      entrada.activa = true;
      entrada.t = Date.now();   // NUEVO
      if (!modoObservador) reproducirSonido('dificil');  // NUEVO: solo al MARCAR (no al desmarcar), nunca en observador
    } else {
      dificiles[h] = { p: t.primero || '', s: t.segundo || '', activa: true, t: Date.now() };
      if (!modoObservador) reproducirSonido('dificil');  // NUEVO
    }
    guardarDificiles();
    renderTarjeta();
  }

  // ---------- NUEVO: notas locales (editables en el HTML) ----------
  // Mapa { hash: {p, s, nota, base, t} } guardado en localStorage.
  //  - hash: el mismo de las difíciles (primero + segundo normalizados).
  //  - nota: texto de MI versión ('' = la borré a propósito).
  //  - base: nota que venía del txt cuando edité (sirve para distinguir
  //          'pendiente de pasar al txt' de 'el txt cambió después').
  //  - t: fecha de última edición (al importar gana la más reciente).
  // La nota local manda sobre la del txt; el txt nunca se modifica.
  const CLAVE_NOTAS = 'notas_tarjetas_v1';
  let notasLocales = cargarNotas();

  function cargarNotas() {
    try {
      const crudo = localStorage.getItem(CLAVE_NOTAS);
      if (!crudo) return {};
      const obj = JSON.parse(crudo);
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj;
      return {};
    } catch (e) {
      return {};
    }
  }

  function guardarNotasYa() {
    try {
      localStorage.setItem(CLAVE_NOTAS, JSON.stringify(notasLocales));
    } catch (e) {
      // Sin localStorage: quedan solo en memoria de esta sesión
    }
  }
  const guardarNotas = crearGuardadoDebounced(guardarNotasYa, 250);

  function notaTxtDe(t) { return (t.nota || '').trim(); }

  function entradaNotaDe(t) { return notasLocales[hashTarjeta(t)] || null; }

  // Nota que se muestra: la local si existe, si no la del txt
  function notaEfectivaDe(t) {
    const e = entradaNotaDe(t);
    return e ? (e.nota || '') : notaTxtDe(t);
  }

  // 'igual' | 'pendiente' (edité yo, el txt sigue como estaba) | 'conflicto' (el txt cambió después)
  function estadoNotaDe(t) {
    const e = entradaNotaDe(t);
    if (!e) return 'igual';
    const txt = notaTxtDe(t);
    if ((e.nota || '') === txt) return 'igual';
    return (e.base || '') === txt ? 'pendiente' : 'conflicto';
  }

  function guardarNotaLocal(t, texto) {
    texto = (texto || '').trim();
    const h = hashTarjeta(t);
    const base = notaTxtDe(t);
    if (texto === base) {
      delete notasLocales[h];   // igual al txt: no hace falta override
    } else {
      notasLocales[h] = { p: t.primero || '', s: t.segundo || '', nota: texto, base: base, t: Date.now() };
    }
    guardarNotas();
  }

  function descartarNotaLocal(t) {
    delete notasLocales[hashTarjeta(t)];
    guardarNotas();
  }

  // "Quedarme con la mía": acepto que el txt cambió y mantengo mi versión
  function conservarMiNota(t) {
    const e = entradaNotaDe(t);
    if (!e) return;
    e.base = notaTxtDe(t);
    e.t = Date.now();
    guardarNotas();
  }

  // ---------- Elementos ----------
  const elPantallaTemas = document.getElementById('pantalla-temas');
  const elListaTemas = document.getElementById('lista-temas');
  const elAreaTarjeta = document.getElementById('area-tarjeta');
  const elPantallaResumen = document.getElementById('pantalla-resumen');
  const elPantallaObservador = document.getElementById('pantalla-observador');
  const elListaObservador = document.getElementById('lista-observador');
  // NUEVO: galería de imágenes
  const elPantallaGaleria = document.getElementById('pantalla-galeria');
  const elGaleriaIndicador = document.getElementById('galeria-indicador');
  const elGaleriaImg = document.getElementById('galeria-img');
  const elGaleriaVacia = document.getElementById('galeria-vacia');
  const elGaleriaDescripcion = document.getElementById('galeria-descripcion');
  const elBtnGaleriaDificil = document.getElementById('btn-galeria-dificil');
  const elBtnGaleriaNota = document.getElementById('btn-galeria-nota');
  const elPanelGaleriaNota = document.getElementById('panel-galeria-nota');
  const elGaleriaNotaTexto = document.getElementById('galeria-nota-texto');
  const elGaleriaNotaEditor = document.getElementById('galeria-nota-editor');
  const elGaleriaNotaVersiones = document.getElementById('galeria-nota-versiones');
  const elBtnGaleriaAnterior = document.getElementById('btn-galeria-anterior');
  const elBtnGaleriaSiguiente = document.getElementById('btn-galeria-siguiente');
  // NUEVO: Buscador Global
  const elInputBuscadorTemas = document.getElementById('input-buscador-temas');
  const elDropdownBuscadorTemas = document.getElementById('dropdown-buscador-temas');
  const elInputBuscadorObservador = document.getElementById('input-buscador-observador');
  const elDropdownBuscadorObservador = document.getElementById('dropdown-buscador-observador');
  const elInputBuscadorGaleria = document.getElementById('input-buscador-galeria');
  const elDropdownBuscadorGaleria = document.getElementById('dropdown-buscador-galeria');
  // NUEVO: Rutas Huérfanas
  const elPantallaHuerfanas = document.getElementById('pantalla-huerfanas');
  const elListaHuerfanas = document.getElementById('lista-huerfanas');
  const elHuerfanasVacia = document.getElementById('huerfanas-vacia');
  const elInputBuscadorHuerfanas = document.getElementById('input-buscador-huerfanas');
  const elDropdownBuscadorHuerfanas = document.getElementById('dropdown-buscador-huerfanas');
  // NUEVO: Gamificación — elementos DOM
  const elBtnAbrirHistorial = document.getElementById('btn-abrir-historial');
  const elBtnAbrirTienda = document.getElementById('btn-abrir-tienda');
  const elBtnAbrirListas = document.getElementById('btn-abrir-listas');
  const elPantallaTienda = document.getElementById('pantalla-tienda');
  const elPantallaHistorial = document.getElementById('pantalla-historial');
  const elSaldoTienda = document.getElementById('saldo-tienda');
  const elGridTienda = document.getElementById('grid-tienda');
  const elListaHistorial = document.getElementById('lista-historial');
  const elHistorialVacio = document.getElementById('historial-vacio');
  const elBtnHistorialExportar = document.getElementById('btn-historial-exportar');
  const elBtnHistorialBorrarEste = document.getElementById('btn-historial-borrar-este');
  const elBtnHistorialBorrarGlobal = document.getElementById('btn-historial-borrar-global');
  const elZonaHistorialOtros = document.getElementById('zona-historial-otros');
  const elSumaHistorialOtros = document.getElementById('suma-historial-otros');
  const elListaHistorialOtros = document.getElementById('lista-historial-otros');
  const elDetHistorialOtros = document.getElementById('det-historial-otros');
  const elModalModo = document.getElementById('modal-modo');
  const elModalGameOver = document.getElementById('modal-gameover');
  const elFilaGamificacion = document.getElementById('fila-gamificacion');
  const elStatPuntos = document.getElementById('stat-puntos');
  const elStatRacha = document.getElementById('stat-racha');
  const elStatVidas = document.getElementById('stat-vidas');
  const elStatEscudo = document.getElementById('stat-escudo');
  const elStatSegunda = document.getElementById('stat-segunda');
  const elAvisoJuego = document.getElementById('aviso-juego');
  const elModalSegunda = document.getElementById('modal-segunda');
  const elBtnMute = document.getElementById('btn-mute');
  const elBadgePistaGratis = document.getElementById('badge-pista-gratis');
  const elBadgeComodines = document.getElementById('badge-comodines');
  const elResumenGamificacion = document.getElementById('resumen-gamificacion');
  const elBtnPausa = document.getElementById('btn-pausa');
  const elOverlayPausa = document.getElementById('overlay-pausa');
  const elBtnReanudar = document.getElementById('btn-reanudar');
  const elBtnRevisar = document.getElementById('btn-revisar');
  const elModalContinuar = document.getElementById('modal-continuar');

  const elTemaTexto = document.getElementById('tema-texto');
  const elPrimero = document.getElementById('tarjeta-primero');
  const elSegundo = document.getElementById('tarjeta-segundo');
  const elSeparador = document.getElementById('separador-tarjeta');
  const elBtnPista = document.getElementById('btn-pista');
  const elBtnRespuesta = document.getElementById('btn-respuesta');
  const elRutasContenedor = document.getElementById('rutas-contenedor');
  const elTarjeta = document.getElementById('tarjeta');
  const elProgresoTexto = document.getElementById('progreso-texto');
  const elBarraRelleno = document.getElementById('barra-relleno');
  const elCronometro = document.getElementById('cronometro');
  const elEstadoMarca = document.getElementById('estado-marca');

  const elZonaEval = document.getElementById('zona-eval');
  const elTextoEval = document.getElementById('texto-eval');
  const elBtnEvaluar = document.getElementById('btn-evaluar');
  // NUEVO: nota
  const elBtnNota = document.getElementById('btn-nota');
  const elPanelNota = document.getElementById('panel-nota');
  const elPanelNotaTexto = document.getElementById('panel-nota-texto');
  const elPanelNotaEstado = document.getElementById('panel-nota-estado');
  const elPanelNotaVersiones = document.getElementById('panel-nota-versiones');
  const elBtnVerLocal = document.getElementById('btn-ver-local');
  const elBtnVerTxt = document.getElementById('btn-ver-txt');
  const elNotaEditor = document.getElementById('nota-editor');
  const elBtnNotaEditar = document.getElementById('btn-nota-editar');
  const elBtnNotaGuardar = document.getElementById('btn-nota-guardar');
  const elBtnNotaCancelar = document.getElementById('btn-nota-cancelar');
  const elBtnNotaBorrar = document.getElementById('btn-nota-borrar');
  const elResultadoEval = document.getElementById('resultado-eval');
  // MODIFICADO: nuevos elementos
  const elFilaEval = document.getElementById('fila-eval');
  const elBadgeRutas = document.getElementById('badge-rutas');
  const elBtnRazones = document.getElementById('btn-razones');
  const elBadgeRazones = document.getElementById('badge-conteo-razones');
  const elChipsRazones = document.getElementById('chips-razones');
  const elPanelRazones = document.getElementById('panel-razones');
  const elListaCheckRazones = document.getElementById('lista-check-razones');
  const elBtnDificil = document.getElementById('btn-dificil');

  const elBtnSi = document.getElementById('btn-si');
  const elBtnNo = document.getElementById('btn-no');
  const elBtnSaltar = document.getElementById('btn-saltar');
  const elBtnAnterior = document.getElementById('btn-anterior');
  const elBtnSiguiente = document.getElementById('btn-siguiente');
  const elBtnFinalizar = document.getElementById('btn-finalizar');

  const elLightbox = document.getElementById('lightbox');
  const elLightboxImg = document.getElementById('lightbox-img');

  // ---------- Utilidades ----------

  function mezclar(lista) {
    const copia = [...lista];
    for (let i = copia.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copia[i], copia[j]] = [copia[j], copia[i]];
    }
    return copia;
  }

  function armarRenglon(tarjeta) {
    return tarjeta.segundo ? (tarjeta.primero + ' + ' + tarjeta.segundo) : tarjeta.primero;
  }

  function formatearTiempo(ms) {
    const totalSeg = Math.floor(ms / 1000);
    const min = Math.floor(totalSeg / 60);
    const seg = totalSeg % 60;
    return String(min).padStart(2, '0') + ':' + String(seg).padStart(2, '0');
  }

  function esImagen(linea) {
    return typeof linea === 'string' && linea.indexOf('IMG:data:image/') === 0;
  }

  function normalizarPalabra(palabra) {
    return palabra.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
  }

  function rutasValidasDe(tarjeta) {
    return (tarjeta.rutas || []).filter((id) => rutasDisp.hasOwnProperty(id));
  }

  function mostrarPantalla(nombre) {
    elPantallaTemas.style.display = nombre === 'temas' ? 'block' : 'none';
    elAreaTarjeta.style.display = nombre === 'estudio' ? 'block' : 'none';
    elPantallaResumen.style.display = nombre === 'resumen' ? 'block' : 'none';
    elPantallaObservador.style.display = nombre === 'observador' ? 'block' : 'none';
    elPantallaGaleria.style.display = nombre === 'galeria' ? 'block' : 'none';  // NUEVO
    elPantallaHuerfanas.style.display = nombre === 'huerfanas' ? 'block' : 'none';  // NUEVO
    elPantallaTienda.style.display = nombre === 'tienda' ? 'block' : 'none';  // NUEVO
    elPantallaHistorial.style.display = nombre === 'historial' ? 'block' : 'none';  // NUEVO

    if (nombre === 'observador') {
      // OPTIMIZACIÓN: se deja pintar primero el cambio de pantalla (el
      // display:block de arriba) y recién en el siguiente frame se hace el
      // trabajo pesado; evita que todo el reflow caiga en el mismo frame
      // sincrónico del click (se nota sobre todo en PC).
      requestAnimationFrame(() => {
        renderImagenesPreview();   // NUEVO: refresca también al entrar por primera vez (mismo motivo que "temas")
      });
    }

    if (nombre === 'temas') {
      // OPTIMIZACIÓN: mismo motivo que arriba — se agrupan las 6 funciones
      // de render en un solo requestAnimationFrame para que no se ejecuten
      // todas sincrónicamente pegadas al cambio de pantalla.
      requestAnimationFrame(() => {
        renderDificilesPreview();  // MODIFICADO: actualizar la vista de difíciles
        renderNotasPreview();      // NUEVO: notas distintas a las del txt
        renderRevisarGuardadoPreview();  // NUEVO: también al volver por "Volver a temas" del observador
        renderRazonesPreview();    // NUEVO: corrige que no aparecieran al abrir el HTML por primera vez
        actualizarMarcasTemas();   // NUEVO: refresca ★/💡/🏷 sin resetear las casillas marcadas
        evaluarAvisoSRS();         // NUEVO: §4 — avisa si hay listas de Repaso Espaciado vencidas
      });
    }
    if (nombre !== 'estudio') {
      detenerCronometro();
    }
  }

  // ---------- Pantalla de selección de temas ----------

  function nombreTema(tarjeta) {
    return tarjeta.tema && tarjeta.tema.trim() !== '' ? tarjeta.tema : '(Sin tema)';
  }

  // NUEVO: subT en el orden de aparición en recordatorio.txt, con los índices
  // (dentro de tarjetasCompletas) de sus tarjetas, también en orden del txt.
  function agruparPorTema() {
    const mapa = new Map();
    tarjetasCompletas.forEach((t, i) => {
      const nombre = nombreTema(t);
      if (!mapa.has(nombre)) mapa.set(nombre, []);
      mapa.get(nombre).push(i);
    });
    return Array.from(mapa.entries()).map((par) => ({ nombre: par[0], indices: par[1] }));
  }

  // Primero (en negrita) y segundo (gris), para listas de tarjetas
  function crearTextosTarjeta(t) {
    const cont = document.createElement('span');
    cont.className = 'tc-textos';
    const p = document.createElement('span');
    p.className = 'tc-primero';
    p.textContent = t.primero;
    cont.appendChild(p);
    if (t.segundo) {
      const sg = document.createElement('span');
      sg.className = 'tc-segundo';
      sg.textContent = t.segundo;
      cont.appendChild(sg);
    }
    return cont;
  }

  // OPTIMIZACIÓN (lazy rendering): con construcción perezosa, el cuerpo de un
  // subT colapsado puede no existir todavía en el DOM, así que la selección
  // ya NO se puede leer de los checkboxes (algunos ni están creados). Se
  // guarda aparte, por ÍNDICE de tarjeta: por defecto todo está seleccionado
  // (como siempre al abrir el HTML), así que solo se registran las
  // EXCLUSIONES explícitas del usuario. Esto permite que "Comenzar estudio"
  // y los contadores de cada subT sean correctos aunque ese subT nunca se
  // haya abierto (y por lo tanto nunca haya construido sus checkboxes).
  const seleccionTemasExcluidos = new Set();
  function estaSeleccionado(i) { return !seleccionTemasExcluidos.has(i); }
  function fijarSeleccionado(i, val) {
    if (val) seleccionTemasExcluidos.delete(i);
    else seleccionTemasExcluidos.add(i);
  }

  // nombre del subT -> { det, lazyCtrl, checkGrupo, cuenta, indices, cuerpoEl (null hasta abrirse) }
  const gruposTemaInfo = new Map();
  // índice de tarjeta -> nombre de su subT (para ubicarla aunque su grupo
  // esté colapsado/sin construir, p. ej. desde el buscador global)
  const indiceAGrupoTema = new Map();

  // Sincroniza el check del subT (marcado / parcial) y su contador n/m a
  // partir del ESTADO (seleccionTemasExcluidos), no del DOM: así es correcto
  // incluso si el cuerpo de ese subT todavía no se construyó.
  function actualizarResumenGrupo(nombreGrupo) {
    const info = gruposTemaInfo.get(nombreGrupo);
    if (!info) return;
    const total = info.indices.length;
    const marcadas = info.indices.filter(estaSeleccionado).length;
    info.checkGrupo.checked = total > 0 && marcadas === total;
    info.checkGrupo.indeterminate = marcadas > 0 && marcadas < total;
    info.cuenta.textContent = marcadas + '/' + total;
  }

  function indicesSeleccionados() {
    const set = new Set();
    tarjetasCompletas.forEach((_, i) => { if (estaSeleccionado(i)) set.add(i); });
    return set;
  }

  function actualizarConteoSeleccion() {
    const n = indicesSeleccionados().size;
    const btn = document.getElementById('btn-comenzar');
    btn.textContent = n > 0 ? 'Comenzar estudio (' + n + ')' : 'Comenzar estudio';
    btn.disabled = n === 0;
  }

  // NUEVO: refresca solo los indicadores ★/💡/🏷 de "Elegir temas" sin
  // reconstruir la lista (eso reiniciaría las casillas ya marcadas por el usuario)
  function actualizarMarcasTemas() {
    elListaTemas.querySelectorAll('.item-tarjeta-check').forEach((fila) => {
      const c = fila.querySelector('input[data-idx]');
      const marcas = fila.querySelector('.tc-marcas');
      if (!c || !marcas) return;
      const t = tarjetasCompletas[Number(c.dataset.idx)];
      if (!t) return;
      marcas.textContent = (esDificil(t) ? '★ ' : '') + (notaEfectivaDe(t) !== '' ? '💡 ' : '') + (razonesDeTarjeta(t).length > 0 ? ' 🏷' : '');
    });
  }

  const elChipListaActiva = document.getElementById('chip-lista-activa');
  const elChipListaNombre = document.getElementById('chip-lista-nombre');

  function actualizarChipListaActiva() {
    if (listaActivaNombre) {
      elChipListaNombre.textContent = listaActivaNombre;
      elChipListaActiva.style.display = 'flex';
    } else {
      elChipListaActiva.style.display = 'none';
    }
  }

  // Marca SOLO las tarjetas cuyo hash está en 'hashes'. Actualiza el ESTADO
  // (sirve aunque haya subT colapsados sin construir) y, de paso, sincroniza
  // los checkboxes de los subT que SÍ están abiertos/construidos.
  function aplicarSeleccionPorHashes(hashes) {
    const set = new Set(hashes);
    tarjetasCompletas.forEach((t, i) => fijarSeleccionado(i, set.has(hashTarjeta(t))));
    elListaTemas.querySelectorAll('input[data-idx]').forEach((c) => {
      c.checked = estaSeleccionado(Number(c.dataset.idx));
    });
    gruposTemaInfo.forEach((_, nombreGrupo) => actualizarResumenGrupo(nombreGrupo));
    actualizarConteoSeleccion();
  }

  // Selecciona una lista guardada como selección activa de "Elegir temas"
  function seleccionarListaActiva(nombre) {
    const lista = buscarLista(nombre);
    if (!lista) return;
    listaActivaNombre = lista.nombre;
    aplicarSeleccionPorHashes(lista.hashes);
    actualizarChipListaActiva();
  }

  // "Ninguna": vuelve a la selección manual (todas marcadas, como al abrir el HTML)
  function quitarListaActiva() {
    listaActivaNombre = null;
    seleccionTemasExcluidos.clear();
    elListaTemas.querySelectorAll('input[data-idx]').forEach((c) => { c.checked = true; });
    gruposTemaInfo.forEach((_, nombreGrupo) => actualizarResumenGrupo(nombreGrupo));
    actualizarConteoSeleccion();
    actualizarChipListaActiva();
  }

  document.getElementById('btn-chip-lista-quitar').addEventListener('click', () => quitarListaActiva());

  // ---------- OPTIMIZACIÓN: construcción perezosa de <details> colapsados ----------
  // Al entrar a una pantalla se armaban TODOS los nodos DOM de TODAS sus
  // secciones, incluidas las que el usuario nunca abre (p. ej. "Desmarcadas",
  // o cada subT de "Elegir temas"). Con prepararDetallesLazy(det, construir)
  // el <summary> (título + contador) se arma de entrada como siempre, pero
  // 'construir' (el cuerpo pesado) recién corre la PRIMERA vez que ese
  // <details> se abre, y queda cacheado: cerrar/reabrir no lo reconstruye.
  // Invalidación: _detallesConstruidos.delete(det) antes de volver a abrir
  // fuerza una reconstrucción (se usa tras operaciones masivas que cambian
  // el contenido de una sección ya construida).
  const _detallesConstruidos = new WeakSet();
  function prepararDetallesLazy(det, construir) {
    const intentar = () => {
      if (_detallesConstruidos.has(det)) return;
      if (!det.open) return;
      construir();
      _detallesConstruidos.add(det);
    };
    det.addEventListener('toggle', intentar);
    if (det.open) intentar();   // ya estaba abierto al prepararlo (p. ej. estado recordado)
    return {
      // Abre (si hace falta) y garantiza que el contenido ya esté construido;
      // usado cuando hay que "saltar" directo a una fila dentro de la sección.
      abrirYConstruir() {
        if (!det.open) det.open = true;   // dispara 'toggle' -> intentar(), sincrónico
        intentar();                       // red de seguridad por si el navegador no lo disparó ya
      },
      invalidar() { _detallesConstruidos.delete(det); },
      // Invalida y, si la sección ya estaba abierta, la reconstruye YA (para
      // secciones cuyo contenido depende de datos que cambian entre
      // renders, p. ej. al reabrir la pantalla "Elegir temas"). 'construir'
      // debe ser idempotente (limpiar su contenedor antes de rearmarlo).
      refrescar() {
        _detallesConstruidos.delete(det);
        intentar();
      },
    };
  }

  // OPTIMIZACIÓN (lazy rendering): el <summary> (checkbox del grupo + nombre
  // + contador n/m) se arma siempre, pero el .cuerpo-grupo (un checkbox por
  // tarjeta del subT) recién se construye la PRIMERA vez que ese subT se
  // abre, vía prepararDetallesLazy. La selección en sí vive en
  // seleccionTemasExcluidos (independiente del DOM), así que "Comenzar
  // estudio" y los contadores son correctos aunque el subT nunca se haya
  // abierto.
  function construirListaTemas() {
    elListaTemas.innerHTML = '';
    gruposTemaInfo.clear();
    agruparPorTema().forEach((g) => {
      const det = document.createElement('details');
      det.className = 'grupo-tema';

      const sum = document.createElement('summary');
      const checkGrupo = document.createElement('input');
      checkGrupo.type = 'checkbox';
      checkGrupo.className = 'check-grupo';
      checkGrupo.checked = true;
      checkGrupo.title = 'Marcar / desmarcar todas las tarjetas de este subtema';
      checkGrupo.addEventListener('click', (e) => e.stopPropagation());
      const nombre = document.createElement('span');
      nombre.textContent = g.nombre;
      const cuenta = document.createElement('span');
      cuenta.className = 'cuenta-grupo';
      sum.appendChild(checkGrupo);
      sum.appendChild(nombre);
      sum.appendChild(cuenta);
      det.appendChild(sum);

      const info = { det: det, lazyCtrl: null, checkGrupo: checkGrupo, cuenta: cuenta, indices: g.indices, cuerpoEl: null };
      gruposTemaInfo.set(g.nombre, info);
      g.indices.forEach((i) => indiceAGrupoTema.set(i, g.nombre));

      // Marcar/desmarcar el subT marca/desmarca todas sus tarjetas (funciona
      // aunque el cuerpo todavía no esté construido: actualiza el ESTADO y,
      // si el cuerpo ya existe, también sus checkboxes).
      checkGrupo.addEventListener('change', () => {
        g.indices.forEach((i) => fijarSeleccionado(i, checkGrupo.checked));
        if (info.cuerpoEl) {
          info.cuerpoEl.querySelectorAll('input[data-idx]').forEach((c) => { c.checked = checkGrupo.checked; });
        }
        actualizarResumenGrupo(g.nombre);
        actualizarConteoSeleccion();
      });

      info.lazyCtrl = prepararDetallesLazy(det, () => {
        const cuerpo = document.createElement('div');
        cuerpo.className = 'cuerpo-grupo';
        g.indices.forEach((i) => {
          const fila = document.createElement('label');
          fila.className = 'item-tarjeta-check';
          const c = document.createElement('input');
          c.type = 'checkbox';
          c.checked = estaSeleccionado(i);
          c.dataset.idx = String(i);
          c.addEventListener('change', () => {
            fijarSeleccionado(i, c.checked);
            actualizarResumenGrupo(g.nombre);
            actualizarConteoSeleccion();
          });
          fila.appendChild(c);
          fila.appendChild(crearTextosTarjeta(tarjetasCompletas[i]));
          // NUEVO: indicadores visuales (solo informativos, no clickeables)
          const marcas = document.createElement('span');
          marcas.className = 'tc-marcas';
          marcas.textContent = (esDificil(tarjetasCompletas[i]) ? '★ ' : '') + (notaEfectivaDe(tarjetasCompletas[i]) !== '' ? '💡 ' : '') + (razonesDeTarjeta(tarjetasCompletas[i]).length > 0 ? ' 🏷' : '');
          fila.appendChild(marcas);
          cuerpo.appendChild(fila);
        });
        det.appendChild(cuerpo);
        info.cuerpoEl = cuerpo;
      });

      elListaTemas.appendChild(det);
      actualizarResumenGrupo(g.nombre);
    });
    actualizarConteoSeleccion();
  }

  // MODIFICADO: ahora se estudian las tarjetas marcadas (una por una), en el
  // orden del txt; si el evaluador se generó en modo aleatorio, se mezclan.
  document.getElementById('btn-comenzar').addEventListener('click', () => {
    const seleccion = indicesSeleccionados();
    if (seleccion.size === 0) return;

    let filtradas = tarjetasCompletas.filter((_, i) => seleccion.has(i));
    if (modoAleatorio) filtradas = mezclar(filtradas);

    abrirModalSeleccionModo(filtradas);  // MODIFICADO: gamificación
  });

  // ---------- NUEVO: modo observador ----------
  // Recorre las tarjetas en el orden del txt SIN evaluarse: sin cronómetro,
  // sin Entendí / No entendí, sin Finalizar. Sí se pueden ver pista y rutas,
  // editar notas y marcar ☆ Difícil. Reutiliza la vista de tarjeta del evaluador.

  // nombre del subT -> { lazyCtrl, cuerpoEl (null hasta abrirse) }; e índice
  // de tarjeta -> nombre de su subT, para poder ubicarla y forzar su
  // construcción aunque el grupo esté colapsado (ver salirObservador).
  const gruposObservadorInfo = new Map();
  const indiceAGrupoObservador = new Map();

  // OPTIMIZACIÓN (lazy rendering): construirListaObservador() se llama cada
  // vez que se entra o se vuelve del observador (para refrescar ★/💡), así
  // que antes se reconstruían TODOS los botones de TODAS las tarjetas de
  // TODOS los subT en cada vuelta, abiertos o no. Ahora el cuerpo de un subT
  // colapsado recién se arma la primera vez que se abre (o, si ya estaba
  // abierto —viene de 'observadorAbiertos'—, se arma de una, como antes).
  function construirListaObservador() {
    elListaObservador.innerHTML = '';
    gruposObservadorInfo.clear();
    agruparPorTema().forEach((g) => {
      const det = document.createElement('details');
      det.className = 'grupo-tema';
      det.open = observadorAbiertos.has(g.nombre);
      det.addEventListener('toggle', () => {
        if (det.open) observadorAbiertos.add(g.nombre);
        else observadorAbiertos.delete(g.nombre);
      });

      const sum = document.createElement('summary');
      const nombre = document.createElement('span');
      nombre.textContent = g.nombre;
      const cuenta = document.createElement('span');
      cuenta.className = 'cuenta-grupo';
      cuenta.textContent = '(' + g.indices.length + ')';
      sum.appendChild(nombre);
      sum.appendChild(cuenta);
      det.appendChild(sum);

      const info = { lazyCtrl: null, cuerpoEl: null };
      gruposObservadorInfo.set(g.nombre, info);
      g.indices.forEach((i) => indiceAGrupoObservador.set(i, g.nombre));

      info.lazyCtrl = prepararDetallesLazy(det, () => {
        const cuerpo = document.createElement('div');
        cuerpo.className = 'cuerpo-grupo';
        cuerpo.style.paddingLeft = '0';
        g.indices.forEach((i) => {
          const t = tarjetasCompletas[i];
          const fila = document.createElement('button');
          fila.type = 'button';
          fila.className = 'fila-observador';
          fila.dataset.idx = String(i);
          fila.appendChild(crearTextosTarjeta(t));
          const marcas = document.createElement('span');
          marcas.className = 'tc-marcas';
          marcas.textContent = (esDificil(t) ? '★' : '') + (esRevisar(t) ? ' 🚩' : '') + (notaEfectivaDe(t) !== '' ? ' 💡' : '') + (razonesDeTarjeta(t).length > 0 ? ' 🏷' : '');
          fila.appendChild(marcas);
          fila.addEventListener('click', () => abrirTarjetaObservador(i));
          cuerpo.appendChild(fila);
        });
        det.appendChild(cuerpo);
        info.cuerpoEl = cuerpo;
      });

      elListaObservador.appendChild(det);
    });
  }

  function entrarObservador() {
    if (tarjetasCompletas.length === 0) return;
    mostrarPantalla('observador');
    // OPTIMIZACIÓN: se deja pintar la pantalla (vacía) primero, y recién en
    // el siguiente frame se arma la lista — evita apilar el reflow del
    // cambio de pantalla con el de construir la lista en el mismo frame.
    requestAnimationFrame(() => {
      construirListaObservador();
    });
  }

  function abrirTarjetaObservador(idx) {
    const n = tarjetasCompletas.length;
    tarjetasSesion = [...tarjetasCompletas];   // orden del txt
    resultados = new Array(n).fill(null);
    tiempos = new Array(n).fill(0);
    revelado = new Array(n).fill(false);
    pistaMostrada = new Array(n).fill(false);
    respuestaMostrada = new Array(n).fill(false);
    escritos = new Array(n).fill('');
    evaluaciones = new Array(n).fill(null);
    porComprension = new Array(n).fill(false);
    indiceActual = idx;
    cronometroActivo = false;   // sin cronómetro
    indiceCongelado = -1;
    resetPanelesUI();
    modoObservador = true;
    elAreaTarjeta.classList.add('observador');
    mostrarPantalla('estudio');
    renderTarjeta();
    window.scrollTo(0, 0);
  }

  function salirObservador() {
    if (!confirmarDescartarBorrador()) return;
    const ultimo = indiceActual;
    resetPanelesUI();
    modoObservador = false;
    elAreaTarjeta.classList.remove('observador');
    mostrarPantalla('observador');
    // OPTIMIZACIÓN: igual que entrarObservador() — el reflow de
    // construirListaObservador() (+ la apertura/scroll a la última tarjeta
    // vista, que depende de que la lista ya esté armada) se corre en el
    // siguiente frame, no pegado al cambio de pantalla.
    requestAnimationFrame(() => {
      construirListaObservador();   // refresca ★ y 💡
      // Volver al lugar donde se estaba: forzar la construcción (lazy) de su
      // subT si hiciera falta, abrirlo y mostrar la fila.
      const nombreGrupo = indiceAGrupoObservador.get(ultimo);
      const info = nombreGrupo ? gruposObservadorInfo.get(nombreGrupo) : null;
      if (info && info.lazyCtrl) info.lazyCtrl.abrirYConstruir();
      const fila = elListaObservador.querySelector('[data-idx="' + ultimo + '"]');
      if (fila) {
        const grupo = fila.closest('details');
        if (grupo) grupo.open = true;
        if (fila.scrollIntoView) fila.scrollIntoView({ block: 'center' });
      }
    });
  }

  // ============================================================
  // NUEVO: galería de imágenes de las rutas (solo desde el modo observador)
  // ============================================================
  // Datos completamente independientes de las notas/difíciles de tarjetas:
  // propia clave de localStorage, propio hash y su propia sección de preview
  // (no se suman a "Difíciles guardadas" ni "Notas editadas").
  const CLAVE_IMAGENES = 'datos_imagenes_v1';
  let datosImagenes = cargarImagenes();
  let galeriaPos = 0;            // posición actual dentro de INDICE_IMAGENES
  let galeriaNotaAbierta = false;
  let galeriaEditando = false;
  let galeriaVerAnterior = false; // false = versión actual, true = versión anterior

  function cargarImagenes() {
    try {
      const crudo = localStorage.getItem(CLAVE_IMAGENES);
      if (!crudo) return {};
      const obj = JSON.parse(crudo);
      return (obj && typeof obj === 'object' && !Array.isArray(obj)) ? obj : {};
    } catch (e) {
      return {};
    }
  }

  function guardarImagenesStorageYa() {
    try {
      localStorage.setItem(CLAVE_IMAGENES, JSON.stringify(datosImagenes));
    } catch (e) {
      // sin localStorage: quedan solo en memoria de esta sesión
    }
  }
  const guardarImagenesStorage = crearGuardadoDebounced(guardarImagenesStorageYa, 250);

  // Hash liviano: espacio del evaluador + número de ruta + índice de la imagen
  // DENTRO de esa ruta (solo contando líneas 'IMG:'). Sin base64, sin subT.
  function hashImagenCon(espacio, ruta, indice) {
    const base = espacio + '\u0001' + ruta + '\u0000' + indice;
    let h = 5381;
    for (let i = 0; i < base.length; i++) {
      h = ((h << 5) + h + base.charCodeAt(i)) >>> 0;
    }
    return 'i' + h.toString(36);   // prefijo 'i' (imagen) para no confundir con 'h' de tarjeta
  }

  // OPTIMIZACIÓN: igual que hashTarjeta(), hashImagen() se llama muchas veces
  // por render con el mismo (ruta, índice). Como son primitivos (no un objeto
  // para usar WeakMap), se cachea en un Map normal con clave compuesta.
  // hashImagenCon() (revinculación con OTRO espacio) no se toca.
  const _cacheHashImagen = new Map();
  function hashImagen(ruta, indice) {
    const clave = ruta + '\u0000' + indice;
    const previo = _cacheHashImagen.get(clave);
    if (previo !== undefined) return previo;
    const h = hashImagenCon(ESPACIO_HASH, ruta, indice);
    _cacheHashImagen.set(clave, h);
    return h;
  }

  // Recorre rutasDisp en orden numérico de ruta y arma la lista de coordenadas
  // {ruta, indice} de TODAS las imágenes, en el mismo orden en que aparecen en
  // respuestas.txt. No guarda el src acá: eso se busca "al vuelo" al renderizar.
  function construirIndiceImagenes() {
    const nav = [];
    Object.keys(rutasDisp).map(Number).sort((a, b) => a - b).forEach((ruta) => {
      const lineas = (rutasDisp[ruta] && rutasDisp[ruta].lineas) || [];
      const cantidad = lineas.filter(esImagen).length;
      for (let i = 0; i < cantidad; i++) nav.push({ ruta: ruta, indice: i });
    });
    return nav;
  }
  const INDICE_IMAGENES = construirIndiceImagenes();   // fijo: no cambia durante la sesión

  // Busca el src de una imagen por coordenadas, filtrando solo líneas 'IMG:'
  function srcDeImagen(ruta, indice) {
    const lineas = (rutasDisp[ruta] && rutasDisp[ruta].lineas) || [];
    const imagenes = lineas.filter(esImagen);
    const linea = imagenes[indice];
    return linea ? linea.substring(4) : '';   // quitar el prefijo 'IMG:'
  }

  // NUEVO: descripción de UNA imagen puntual (ruta + posición dentro de la
  // ruta), extraída del drawio. datos.descripciones_rutas ahora es
  // { ruta: [desc_img0, desc_img1, ...] } -- una lista por ruta, análoga al
  // 'indice' que ya usa srcDeImagen/INDICE_IMAGENES para navegar imágenes
  // dentro de una misma ruta.
  function descripcionDeImagen(ruta, indice) {
    const listas = datos.descripciones_rutas || {};
    const lista = listas[ruta] || listas[String(ruta)] || [];
    return lista[indice] || '';
  }

  function entradaImagenActual() {
    if (galeriaPos < 0 || galeriaPos >= INDICE_IMAGENES.length) return null;
    const { ruta, indice } = INDICE_IMAGENES[galeriaPos];
    return datosImagenes[hashImagen(ruta, indice)] || null;
  }

  function notaActualImagen() { const e = entradaImagenActual(); return e ? (e.nota || '') : ''; }
  function notaBaseImagen() { const e = entradaImagenActual(); return e ? (e.base || '') : ''; }
  function esDificilImagen() { const e = entradaImagenActual(); return !!(e && e.dificil); }

  // Guarda la entrada de esta imagen, o la borra si queda "vacía" (sin nota y
  // sin marca de difícil), para no acumular entradas basura en localStorage.
  function guardarEntradaImagen(ruta, indice, cambios) {
    const h = hashImagen(ruta, indice);
    const actual = datosImagenes[h] || { ruta: ruta, indice: indice, nota: '', base: '', dificil: false, t: 0 };
    const nueva = Object.assign({}, actual, cambios, { ruta: ruta, indice: indice, t: Date.now() });
    if (!nueva.nota && !nueva.dificil) {
      delete datosImagenes[h];
    } else {
      datosImagenes[h] = nueva;
    }
    guardarImagenesStorage();
  }

  function toggleDificilImagenActual() {
    if (INDICE_IMAGENES.length === 0) return;
    const { ruta, indice } = INDICE_IMAGENES[galeriaPos];
    guardarEntradaImagen(ruta, indice, { dificil: !esDificilImagen() });
    renderGaleria();
  }

  // ---------- Nota de la imagen: ver / editar / revertir a la anterior ----------

  function galeriaCambiosSinGuardar() {
    if (!galeriaEditando) return false;
    return elGaleriaNotaEditor.value.trim() !== notaActualImagen();
  }

  function confirmarDescartarGaleria() {
    if (!galeriaCambiosSinGuardar()) return true;
    return confirm('Tenés cambios sin guardar en la nota de esta imagen. ¿Descartarlos?');
  }

  function resetGaleriaNotaUI() {
    galeriaNotaAbierta = false;
    galeriaEditando = false;
    galeriaVerAnterior = false;
  }

  function abrirGaleriaNota() {
    if (INDICE_IMAGENES.length === 0) return;
    galeriaNotaAbierta = true;
    galeriaVerAnterior = false;
    // sin nota y sin versión anterior: directo a escribir
    galeriaEditando = (notaActualImagen() === '' && notaBaseImagen() === '');
    if (galeriaEditando) elGaleriaNotaEditor.value = '';
    renderGaleria();
    if (galeriaEditando) elGaleriaNotaEditor.focus();
  }

  function cerrarGaleriaNota() {
    if (!confirmarDescartarGaleria()) return;
    resetGaleriaNotaUI();
    renderGaleria();
  }

  function empezarEdicionGaleriaNota() {
    galeriaEditando = true;
    galeriaVerAnterior = false;
    elGaleriaNotaEditor.value = notaActualImagen();
    renderGaleria();
    elGaleriaNotaEditor.focus();
  }

  function guardarEdicionGaleriaNota() {
    if (!galeriaEditando || INDICE_IMAGENES.length === 0) return;
    const { ruta, indice } = INDICE_IMAGENES[galeriaPos];
    const anterior = notaActualImagen();
    const nueva = elGaleriaNotaEditor.value.trim();
    // solo se actualiza 'base' (versión anterior) si el texto realmente cambió
    const cambios = (nueva === anterior) ? { nota: nueva } : { nota: nueva, base: anterior };
    guardarEntradaImagen(ruta, indice, cambios);
    galeriaEditando = false;
    galeriaVerAnterior = false;
    if (notaActualImagen() === '' && notaBaseImagen() === '') {
      resetGaleriaNotaUI();
    }
    renderGaleria();
  }

  function cancelarEdicionGaleriaNota() {
    if (!confirmarDescartarGaleria()) return;
    galeriaEditando = false;
    if (notaActualImagen() === '' && notaBaseImagen() === '') {
      resetGaleriaNotaUI();
    }
    renderGaleria();
  }

  // "Revertir": la versión anterior pasa a ser la actual (deshacer de un solo nivel)
  function revertirGaleriaNota() {
    if (INDICE_IMAGENES.length === 0) return;
    const base = notaBaseImagen();
    if (base === '' && notaActualImagen() === '') return;
    const { ruta, indice } = INDICE_IMAGENES[galeriaPos];
    guardarEntradaImagen(ruta, indice, { nota: base, base: '' });
    galeriaVerAnterior = false;
    renderGaleria();
  }

  // ---------- Render y navegación ----------

  function renderGaleria() {
    const total = INDICE_IMAGENES.length;

    if (total === 0) {
      elGaleriaIndicador.textContent = '';
      elGaleriaImg.style.display = 'none';
      elGaleriaVacia.style.display = 'block';
      elGaleriaDescripcion.style.display = 'none';
      elGaleriaDescripcion.textContent = '';
      elBtnGaleriaDificil.style.display = 'none';
      elBtnGaleriaNota.style.display = 'none';
      elPanelGaleriaNota.style.display = 'none';
      elBtnGaleriaAnterior.disabled = true;
      elBtnGaleriaSiguiente.disabled = true;
      return;
    }

    elGaleriaVacia.style.display = 'none';
    elGaleriaImg.style.display = 'inline-block';
    elBtnGaleriaDificil.style.display = 'inline-block';
    elBtnGaleriaNota.style.display = 'inline-flex';
    elBtnGaleriaAnterior.disabled = false;
    elBtnGaleriaSiguiente.disabled = false;

    const { ruta, indice } = INDICE_IMAGENES[galeriaPos];
    elGaleriaImg.src = srcDeImagen(ruta, indice);
    elGaleriaIndicador.textContent = 'Imagen ' + (galeriaPos + 1) + ' de ' + total + ' (Ruta ' + ruta + ')';

    // NUEVO: descripción de ESTA imagen puntual (atributo 'descripcion' del
    // drawio, indexada por ruta + posición dentro de la ruta, igual que
    // srcDeImagen/INDICE_IMAGENES).
    const descripcionImg = descripcionDeImagen(ruta, indice);
    if (descripcionImg) {
      elGaleriaDescripcion.textContent = descripcionImg;
      elGaleriaDescripcion.style.display = 'block';
    } else {
      elGaleriaDescripcion.textContent = '';
      elGaleriaDescripcion.style.display = 'none';
    }

    const esDif = esDificilImagen();
    elBtnGaleriaDificil.textContent = esDif ? '★ Difícil' : '☆ Difícil';
    elBtnGaleriaDificil.classList.toggle('activa', esDif);

    const actual = notaActualImagen();
    const base = notaBaseImagen();
    const hayVersionAnterior = base !== '';
    elBtnGaleriaNota.classList.toggle('vacia', actual === '' && !hayVersionAnterior && !galeriaNotaAbierta);
    elBtnGaleriaNota.classList.toggle('activa', actual !== '' || galeriaNotaAbierta);
    elPanelGaleriaNota.style.display = galeriaNotaAbierta ? 'block' : 'none';
    if (!galeriaNotaAbierta) return;

    elGaleriaNotaVersiones.style.display = (hayVersionAnterior && !galeriaEditando) ? 'flex' : 'none';
    document.getElementById('btn-galeria-ver-actual').classList.toggle('activo', !galeriaVerAnterior);
    document.getElementById('btn-galeria-ver-anterior').classList.toggle('activo', galeriaVerAnterior);

    if (galeriaEditando) {
      elGaleriaNotaTexto.style.display = 'none';
      elGaleriaNotaEditor.style.display = 'block';
      document.getElementById('btn-galeria-nota-editar').style.display = 'none';
      document.getElementById('btn-galeria-nota-guardar').style.display = 'inline-block';
      document.getElementById('btn-galeria-nota-cancelar').style.display = 'inline-block';
      document.getElementById('btn-galeria-nota-revertir').style.display = 'none';
    } else {
      elGaleriaNotaEditor.style.display = 'none';
      elGaleriaNotaTexto.style.display = 'block';
      let texto, vacio;
      if (galeriaVerAnterior) {
        texto = base !== '' ? base : '(sin versión anterior)';
        vacio = base === '';
      } else {
        texto = actual !== '' ? actual : '(sin nota)';
        vacio = actual === '';
      }
      elGaleriaNotaTexto.textContent = texto;
      elGaleriaNotaTexto.classList.toggle('vacio', vacio);
      document.getElementById('btn-galeria-nota-editar').style.display = galeriaVerAnterior ? 'none' : 'inline-block';
      document.getElementById('btn-galeria-nota-editar').textContent = actual !== '' ? '✎ Editar' : '＋ Agregar nota';
      document.getElementById('btn-galeria-nota-guardar').style.display = 'none';
      document.getElementById('btn-galeria-nota-cancelar').style.display = 'none';
      document.getElementById('btn-galeria-nota-revertir').style.display = (!galeriaVerAnterior && hayVersionAnterior) ? 'inline-block' : 'none';
    }
  }

  function navegarGaleria(delta) {
    if (!confirmarDescartarGaleria()) return;
    const total = INDICE_IMAGENES.length;
    if (total === 0) return;
    // navegación continua: de la última imagen de una ruta pasa a la primera de la siguiente
    galeriaPos = (galeriaPos + delta + total) % total;
    resetGaleriaNotaUI();
    renderGaleria();
  }

  function abrirGaleria() {
    galeriaPos = 0;
    resetGaleriaNotaUI();
    mostrarPantalla('galeria');
    // OPTIMIZACIÓN: se deja pintar el cambio de pantalla primero; recién en
    // el siguiente frame se arma/decodifica la imagen de la galería.
    requestAnimationFrame(() => {
      renderGaleria();
    });
  }

  function volverListaDesdeGaleria() {
    if (!confirmarDescartarGaleria()) return;
    resetGaleriaNotaUI();
    // MODIFICADO: no se reconstruye la lista del observador (a diferencia de
    // salirObservador), así los subT que estaban abiertos quedan como estaban.
    // renderImagenesPreview() ya se llama dentro de mostrarPantalla('observador').
    mostrarPantalla('observador');
  }

  // ============================================================
  // NUEVO: Rutas Huérfanas (rutas de respuestas.txt no citadas por ninguna
  // tarjeta). Pantalla independiente, solo accesible desde el observador.
  // ============================================================

  // Se calcula UNA sola vez: las rutas huérfanas no cambian durante la sesión
  // (mismo criterio que INDICE_IMAGENES).
  const rutasHuerfanas = (() => {
    const citadas = new Set();
    tarjetasCompletas.forEach((t) => rutasValidasDe(t).forEach((n) => citadas.add(n)));
    return Object.keys(rutasDisp)
      .map(Number)
      .filter((n) => !citadas.has(n))
      .sort((a, b) => a - b);
  })();

  // NUEVO: indicador de cantidad, en el botón que abre la pantalla y en su
  // subtítulo (rutasHuerfanas no cambia durante la sesión, así que alcanza
  // con fijar el texto una sola vez, al cargar).
  document.getElementById('btn-ver-huerfanas').textContent = '📄 Rutas Huérfanas (' + rutasHuerfanas.length + ')';
  document.getElementById('huerfanas-subtitulo').textContent =
    'Rutas de respuestas.txt que no están citadas por ninguna tarjeta (' + rutasHuerfanas.length + ')';

  // ============================================================
  // NUEVO: índices invertidos para el buscador global (§3 del pedido de
  // optimización). Filtran candidatos antes de aplicar la MISMA comprobación
  // exacta de siempre, así que no cambia ningún resultado de búsqueda.
  // ============================================================

  // Separa en palabras normalizadas de 3+ caracteres, sin palabras vacías
  // (mismo STOPWORDS que ya usa el resaltado de evaluación).
  function tokenizarParaIndice(texto) {
    return normalizarPalabra(texto || '')
      .split(/[^a-z0-9áéíóúñ]+/)
      .filter((tok) => tok.length >= 3 && !STOPWORDS.has(tok));
  }

  // items: lista de ids a indexar. extraerTexto(id) -> texto completo de ese id.
  function construirIndiceInvertido(items, extraerTexto) {
    const indice = new Map();   // token -> Set<id>
    items.forEach((id) => {
      const tokens = tokenizarParaIndice(extraerTexto(id));
      tokens.forEach((tok) => {
        if (!indice.has(tok)) indice.set(tok, new Set());
        indice.get(tok).add(id);
      });
    });
    return indice;
  }

  // Candidatos cuyo texto PODRÍA contener 'consultaNorm': para cada palabra
  // de la consulta busca, entre las CLAVES del índice, las que la contienen
  // como subcadena (cubre coincidencias parciales de palabra, igual que la
  // búsqueda de siempre) y une sus ids; intersecta entre palabras de la
  // consulta (deben aparecer todas, en cualquier lugar del texto indexado).
  // Si una palabra de la consulta es muy corta/vacía y no aporta ningún
  // token, esa palabra no filtra (nunca se pierden resultados verdaderos).
  // Devuelve null si no se pudo acotar nada: en ese caso se recorre todo,
  // igual que antes del índice (fallback seguro).
  function candidatosPorIndice(indice, consultaNorm) {
    const palabras = consultaNorm.split(/\s+/).filter(Boolean);
    if (palabras.length === 0) return null;
    let resultado = null;
    for (let i = 0; i < palabras.length; i++) {
      const palabra = palabras[i];
      let idsPalabra = null;
      for (const [token, ids] of indice) {
        if (token.indexOf(palabra) !== -1) {
          if (idsPalabra === null) idsPalabra = new Set();
          ids.forEach((id) => idsPalabra.add(id));
        }
      }
      if (idsPalabra === null) continue;   // esta palabra no acota: se ignora, no se pierde nada
      resultado = (resultado === null) ? idsPalabra : new Set(Array.from(resultado).filter((id) => idsPalabra.has(id)));
    }
    return resultado;
  }

  // ---- Índice de tarjetas: primero + segundo + texto de sus rutas ----
  const indiceTarjetas = construirIndiceInvertido(
    tarjetasCompletas.map((_, i) => i),
    (i) => {
      const t = tarjetasCompletas[i];
      return (t.primero || '') + ' ' + (t.segundo || '') + ' ' + textoRutasDeTarjeta(t);
    }
  );

  // ---- Índice de rutas huérfanas: "ruta N" + su contenido de texto ----
  const indiceHuerfanas = construirIndiceInvertido(
    rutasHuerfanas,
    (numeroRuta) => 'ruta ' + numeroRuta + ' ' + (textoHuerfanaPlano(numeroRuta) || '')
  );

  // ---- Índice de imágenes: "ruta N" + descripción, por posición en INDICE_IMAGENES ----
  const indiceImagenes = construirIndiceInvertido(
    INDICE_IMAGENES.map((_, i) => i),
    (posImg) => {
      const e = INDICE_IMAGENES[posImg];
      return 'ruta ' + e.ruta + ' ' + (descripcionDeImagen(e.ruta, e.indice) || '');
    }
  );



  // "Revisar" de rutas huérfanas: EFÍMERO en memoria (igual política que
  // revisarEnMemoria de tarjetas). Objeto paralelo e independiente: nunca se
  // mezcla con revisarEnMemoria ni aparece en "🚩 Para revisar guardadas".
  let revisarRutasEnMemoria = {};
  function esRevisarRuta(numeroRuta) { return !!revisarRutasEnMemoria[numeroRuta]; }
  function toggleRevisarHuerfana(numeroRuta) {
    if (revisarRutasEnMemoria[numeroRuta]) {
      delete revisarRutasEnMemoria[numeroRuta];
    } else {
      revisarRutasEnMemoria[numeroRuta] = { ruta: numeroRuta, t: Date.now() };
    }
  }

  // Línea de ruta SIN resaltado de palabras clave (no hay tarjeta asociada
  // de donde sacar claves): a diferencia de agregarLineaResaltada, acá el
  // texto va tal cual, como nodo de texto (nunca se interpreta como HTML).
  function agregarLineaPlana(contenedor, linea) {
    const div = document.createElement('div');
    div.className = 'ruta-linea';
    div.textContent = linea;
    contenedor.appendChild(div);
  }

  function actualizarBotonRevisarHuerfana(boton, numeroRuta) {
    const activa = esRevisarRuta(numeroRuta);
    boton.classList.toggle('activa', activa);
    boton.textContent = activa ? '🚩 Para revisar' : '🚩 Revisar';
  }

  // Arma los <details> UNA sola vez (las rutas huérfanas no cambian). Cada
  // <details> es independiente entre sí: abrir uno no cierra los demás (es
  // el comportamiento nativo de <details>, no hay nada que coordinarlos).
  function construirListaHuerfanas() {
    elListaHuerfanas.innerHTML = '';
    rutasHuerfanas.forEach((numeroRuta) => {
      const bloque = rutasDisp[numeroRuta];

      const det = document.createElement('details');
      det.className = 'ruta-acordeon';
      det.dataset.ruta = String(numeroRuta);

      const sum = document.createElement('summary');
      const fila = document.createElement('span');
      fila.className = 'fila-resumen-huerfana';

      const titulo = document.createElement('span');
      titulo.className = 'ruta-huerfana-titulo';
      titulo.textContent = 'Ruta ' + numeroRuta;
      fila.appendChild(titulo);

      const btnRevisar = document.createElement('button');
      btnRevisar.type = 'button';
      btnRevisar.className = 'boton-revisar-huerfana';
      btnRevisar.dataset.ruta = String(numeroRuta);
      actualizarBotonRevisarHuerfana(btnRevisar, numeroRuta);
      btnRevisar.addEventListener('click', (e) => {
        // No debe colapsar/expandir el <details> al marcar/desmarcar.
        e.preventDefault();
        e.stopPropagation();
        toggleRevisarHuerfana(numeroRuta);
        actualizarBotonRevisarHuerfana(btnRevisar, numeroRuta);
      });
      fila.appendChild(btnRevisar);
      sum.appendChild(fila);

      det.appendChild(sum);

      // OPTIMIZACIÓN (lazy rendering): el contenido (líneas + imágenes) de
      // cada ruta recién se arma la primera vez que esa ruta se abre; con
      // muchas rutas huérfanas (e imágenes pesadas) evita construir TODAS
      // las miniaturas de entrada, aunque el usuario nunca las abra.
      prepararDetallesLazy(det, () => {
        const contenido = document.createElement('div');
        contenido.className = 'ruta-contenido';
        (bloque.lineas || []).forEach((linea) => {
          if (esImagen(linea)) {
            const img = document.createElement('img');
            img.className = 'ruta-img-thumb';
            img.src = linea.substring(4);  // quitar el prefijo 'IMG:'
            img.alt = 'Imagen de la ruta ' + numeroRuta;
            contenido.appendChild(img);
          } else {
            agregarLineaPlana(contenido, linea);
          }
        });
        det.appendChild(contenido);
      });

      elListaHuerfanas.appendChild(det);
    });
  }
  construirListaHuerfanas();  // una sola vez: no cambia durante la sesión

  // Reutiliza el lightbox ya existente (misma función que usan las rutas de
  // las tarjetas), con su propio listener delegado sobre esta lista.
  elListaHuerfanas.addEventListener('click', (evento) => {
    if (evento.target.classList.contains('ruta-img-thumb')) {
      abrirLightbox(evento.target.src);
    }
  });

  function renderHuerfanas() {
    const hay = rutasHuerfanas.length > 0;
    elListaHuerfanas.style.display = hay ? 'block' : 'none';
    elHuerfanasVacia.style.display = hay ? 'none' : 'block';
  }

  function abrirHuerfanas() {
    mostrarPantalla('huerfanas');
    renderHuerfanas();
  }

  function volverDesdeHuerfanas() {
    // Igual que volverListaDesdeGaleria: no se reconstruye la lista del
    // observador, así los subT que estaban abiertos quedan como estaban.
    mostrarPantalla('observador');
  }

  // ---------- Buscador de Rutas Huérfanas (número de ruta + contenido) ----------

  function textoHuerfanaPlano(numeroRuta) {
    const lineas = (rutasDisp[numeroRuta] && rutasDisp[numeroRuta].lineas) || [];
    return lineas.filter((linea) => !esImagen(linea)).join(' ');
  }

  function buscarHuerfanasGlobal(consultaNorm) {
    const resultados = [];
    const candidatosH = candidatosPorIndice(indiceHuerfanas, consultaNorm);
    const listaRutas = candidatosH ? rutasHuerfanas.filter((n) => candidatosH.has(n)) : rutasHuerfanas;
    listaRutas.forEach((numeroRuta) => {
      let texto = null;
      if (normalizarPalabra('ruta ' + numeroRuta).indexOf(consultaNorm) !== -1) {
        texto = 'Ruta ' + numeroRuta;
      } else {
        const contenido = textoHuerfanaPlano(numeroRuta);
        if (contenido && normalizarPalabra(contenido).indexOf(consultaNorm) !== -1) {
          texto = contenido;
        }
      }
      if (texto !== null) resultados.push({ ruta: numeroRuta, texto: texto });
    });
    return resultados;
  }

  // Al seleccionar: abre ESE <details> puntual (sin cerrar los demás que ya
  // estuvieran abiertos) y hace scroll hasta él.
  function seleccionarResultadoHuerfana(numeroRuta) {
    const det = elListaHuerfanas.querySelector('details[data-ruta="' + numeroRuta + '"]');
    if (det) {
      det.open = true;
      if (det.scrollIntoView) det.scrollIntoView({ block: 'center' });
    }
  }

  function renderDropdownBuscadorHuerfanas(consulta) {
    const consultaNorm = normalizarPalabra((consulta || '').trim());
    elDropdownBuscadorHuerfanas.innerHTML = '';
    if (consultaNorm === '') { elDropdownBuscadorHuerfanas.classList.remove('abierto'); return; }
    const resultados = buscarHuerfanasGlobal(consultaNorm).slice(0, 30);
    if (resultados.length === 0) {
      renderDropdownVacio(elDropdownBuscadorHuerfanas);
    } else {
      resultados.forEach((r) => {
        elDropdownBuscadorHuerfanas.appendChild(
          crearItemDropdownBuscador('Ruta ' + r.ruta + ':', r.texto, consultaNorm, () => {
            cerrarDropdownBuscador(elDropdownBuscadorHuerfanas);
            elInputBuscadorHuerfanas.value = '';
            seleccionarResultadoHuerfana(r.ruta);
          })
        );
      });
    }
    elDropdownBuscadorHuerfanas.classList.add('abierto');
  }

  // ============================================================
  // NUEVO: Gamificación — sonidos, puntos, rachas, vidas, tienda, historial
  // ============================================================
  // Reglas de integración: NADA de esto se activa si modoObservador === true.
  // No toca localStorage de dificiles/notas/razones/imágenes ni el buscador.

  // ---------- Sonidos (Web Audio API: sin archivos embebidos) ----------
  let contextoAudio = null;
  function obtenerContextoAudio() {
    if (!contextoAudio) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      contextoAudio = new AC();
    }
    if (contextoAudio.state === 'suspended') contextoAudio.resume();
    return contextoAudio;
  }

  // Tono simple con envolvente (ataque/decaimiento) para no "clickear".
  function tono(ctx, freq, inicio, duracion, tipo, volumen) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = tipo || 'sine';
    osc.frequency.setValueAtTime(freq, ctx.currentTime + inicio);
    gain.gain.setValueAtTime(0, ctx.currentTime + inicio);
    gain.gain.linearRampToValueAtTime(volumen || 0.2, ctx.currentTime + inicio + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + inicio + duracion);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(ctx.currentTime + inicio);
    osc.stop(ctx.currentTime + inicio + duracion + 0.02);
  }

  function reproducirSonido(nombre) {
    if (!sonidosActivos) return;
    const ctx = obtenerContextoAudio();
    if (!ctx) return;
    try {
      if (nombre === 'bien') {
        tono(ctx, 880, 0, 0.12, 'sine', 0.2);
        tono(ctx, 1320, 0.09, 0.15, 'sine', 0.18);
      } else if (nombre === 'mal') {
        tono(ctx, 180, 0, 0.28, 'sawtooth', 0.15);
      } else if (nombre === 'dificil') {
        tono(ctx, 500, 0, 0.08, 'square', 0.12);
        tono(ctx, 500, 0.1, 0.08, 'square', 0.12);
      } else if (nombre === 'racha') {
        tono(ctx, 660, 0, 0.09, 'triangle', 0.18);
        tono(ctx, 880, 0.08, 0.09, 'triangle', 0.18);
        tono(ctx, 1320, 0.16, 0.18, 'triangle', 0.2);
      } else if (nombre === 'vida_perdida') {
        tono(ctx, 220, 0, 0.1, 'square', 0.2);
        tono(ctx, 140, 0.08, 0.18, 'square', 0.2);
      } else if (nombre === 'game_over') {
        tono(ctx, 320, 0, 0.25, 'sawtooth', 0.2);
        tono(ctx, 240, 0.2, 0.25, 'sawtooth', 0.2);
        tono(ctx, 160, 0.4, 0.45, 'sawtooth', 0.22);
      }
    } catch (e) {
      // Si el navegador bloquea audio (por política de autoplay), se ignora en silencio.
    }
  }

  function toggleMute() {
    sonidosActivos = !sonidosActivos;
    elBtnMute.textContent = sonidosActivos ? '🔊' : '🔇';
  }
  elBtnMute.addEventListener('click', () => toggleMute());

  // ---------- Puntos, rachas y vidas ----------

  // Tabla de puntos base (ver prompt). El multiplicador de racha se aplica
  // solo a los puntos base POSITIVOS (nunca a la penalización de Saltar).
  function calcularPuntosBase(resultado, opciones) {
    opciones = opciones || {};
    if (resultado === 'si') {
      if (opciones.pistaGratisUsada) return 15;
      if (opciones.conPista) return 8;
      return 15;
    }
    if (resultado === 'casi') return 3;
    if (resultado === 'no') return 0;
    if (resultado === 'saltar') return opciones.comodinUsado ? 0 : -5;
    return 0;
  }

  function puntosFinales(resultado, opciones) {
    const base = calcularPuntosBase(resultado, opciones);
    const factor = (opciones && typeof opciones.factor === 'number') ? opciones.factor : 1;   // 1 = sin cambios
    return base > 0 ? Math.round(base * multiplicadorActual * factor) : base;
  }

  function totalTarjetasSesionActual() {
    return tarjetasSesion.length || 1;
  }

  function recalcularMultiplicador() {
    const pct = rachaActual / totalTarjetasSesionActual();
    let nuevo = 1;
    if (pct >= 0.75) nuevo = 2.5;
    else if (pct >= 0.5) nuevo = 2.0;
    else if (pct >= 0.3) nuevo = 1.5;
    const subio = nuevo > multiplicadorActual;
    multiplicadorActual = nuevo;
    if (subio) reproducirSonido('racha');
  }

  function actualizarRachaTrasResultado(resultado, comodinUsado, escudoUsado) {
    // El escudo de racha solo evita que el 'no' rompa la racha (y con ella el multiplicador)
    const rompeRacha = ((resultado === 'no') && !escudoUsado) || (resultado === 'saltar' && !comodinUsado);
    if (rompeRacha) {
      rachaActual = 0;
    } else if (resultado === 'si' || resultado === 'casi') {
      rachaActual++;
      if (rachaActual > mejorRacha) mejorRacha = rachaActual;
    }
    recalcularMultiplicador();
  }

  function aplicarPerdidaVida() {
    if (modoJuego === 'light') return;
    vidasActuales = Math.max(0, vidasActuales - 1);
    reproducirSonido('vida_perdida');
    if (vidasActuales === 0) dispararGameOver();
  }

  // Hook principal: se llama UNA vez por cada tarjeta que pasa de null a un
  // resultado final (primera vez que se marca). Nunca en modo observador.
  function procesarResultadoGamificacion(indice, resultado, opciones) {
    if (modoObservador) return;

    // NUEVO: "modo zombie" — sesión retomada después de un Game Over. El
    // usuario puede seguir viendo/respondiendo tarjetas, pero puntos, racha
    // y vidas quedan CONGELADOS en lo que tenían al momento del Game Over:
    // se salta por completo la lógica de puntaje/racha/vidas (y su sonido).
    if (modoZombie) return;

    opciones = opciones || {};

    const puntos = puntosFinales(resultado, opciones);
    const anterior = puntosPorTarjeta[indice] || 0;
    const delta = puntos - anterior;
    puntosPorTarjeta[indice] = puntos;
    if (modoJuego !== 'light') puntosSesion += delta;

    // NUEVO: Escudo de racha (pasivo). Solo actúa ante un fallo ('no') y si hay racha
    // que proteger; con racha 0 NO se consume. La penitencia (vida, sonido) se cobra
    // completa igual: el escudo únicamente conserva racha y multiplicador.
    // CORREGIDO: racha, multiplicador y consumibles son solo de Normal/Tryhard; en
    // Light no se consume el escudo ni se actualiza la racha (queda siempre en 0).
    let escudoUsado = false;
    if (modoJuego !== 'light') {
      if (resultado === 'no' && rachaActual > 0 && (gamificacion.inventario.escudosRacha || 0) > 0) {
        gamificacion.inventario.escudosRacha--;
        guardarGamificacion();
        escudoUsado = true;
      }
      actualizarRachaTrasResultado(resultado, !!opciones.comodinUsado, escudoUsado);
    }

    if (resultado === 'si') {
      reproducirSonido('bien');
    } else if (resultado === 'no') {
      reproducirSonido('mal');
    } else if (resultado === 'saltar' && !opciones.comodinUsado) {
      reproducirSonido('mal');
    }

    const pierdeVida = (resultado === 'no') || (resultado === 'saltar' && !opciones.comodinUsado);
    if (pierdeVida) aplicarPerdidaVida();

    if (escudoUsado) {
      const quedan = gamificacion.inventario.escudosRacha;
      mostrarAviso(primerIntentoFallido.has(indice)
        ? '🔁 El reintento también falló. Consumidos: 🔁 Segunda oportunidad + 🛡 Escudo de racha. Tu racha continúa (penitencia cobrada una sola vez). Escudos restantes: ' + quedan
        : '🛡 Escudo de racha usado: tu racha continúa (la penitencia se cobró igual). Escudos restantes: ' + quedan);
    }

    actualizarBarraGamificacion();
  }

  // Ajuste de SOLO puntos (usado por "Entendida por comprensión": la tarjeta
  // ya había sumado puntos como 'casi'/'mal' y ahora pasa a 'si'). No se
  // retocan racha/vidas/sonido acá para no duplicar efectos ya disparados en
  // el momento de la evaluación original (limitación documentada).
  function ajustarPuntosGamificacion(indice, resultado, opciones) {
    if (modoObservador) return;
    if (modoZombie) return;   // NUEVO: sesión zombie -> puntaje congelado, ni el ajuste de "entendida" suma
    const puntos = puntosFinales(resultado, opciones);
    const anterior = puntosPorTarjeta[indice] || 0;
    const delta = puntos - anterior;
    puntosPorTarjeta[indice] = puntos;
    if (modoJuego !== 'light') puntosSesion += delta;
    actualizarBarraGamificacion();
  }

  function configurarModoJuego(modo) {
    modoJuego = modo;
    if (modo === 'tryhard') { vidasMaximas = 1; }
    else if (modo === 'normal') { vidasMaximas = 3; }
    else { vidasMaximas = 0; }
  }

  function nombreModoJuego(modo) {
    if (modo === 'srs') return '🧠 SRS';   // NUEVO
    if (modo === 'tryhard') return 'Tryhard';
    if (modo === 'normal') return 'Normal';
    return 'Light';
  }

  // Refresca puntos/racha/vidas/badges de inventario. Se llama tras cada
  // cambio relevante (marcar, comprar, iniciar sesión).
  function actualizarBarraGamificacion() {
    if (modoObservador) return;

    elStatPuntos.style.display = modoJuego === 'light' ? 'none' : 'inline';
    elStatPuntos.textContent = '⭐ ' + puntosSesion;

    // CORREGIDO: racha y estos consumibles son solo de Normal/Tryhard; en Light
    // se ocultan (igual que ya pasa con puntos y vidas), para no mostrar algo
    // que no se puede usar en ese modo.
    const esLight = modoJuego === 'light';
    elStatRacha.style.display = esLight ? 'none' : 'inline';
    elStatRacha.textContent = '🔥 ' + rachaActual + (multiplicadorActual > 1 ? (' x' + multiplicadorActual) : '');

    // NUEVO: contadores de consumibles (icono + cantidad); atenuados en 0
    const nEscudos = gamificacion.inventario.escudosRacha || 0;
    const nSegundas = gamificacion.inventario.segundasOportunidades || 0;
    elStatEscudo.style.display = esLight ? 'none' : 'inline';
    elStatEscudo.textContent = '🛡 ' + nEscudos;
    elStatEscudo.style.opacity = nEscudos > 0 ? '1' : '0.4';
    elStatSegunda.style.display = esLight ? 'none' : 'inline';
    elStatSegunda.textContent = '🔁 ' + nSegundas;
    elStatSegunda.style.opacity = nSegundas > 0 ? '1' : '0.4';

    if (modoJuego === 'light') {
      elStatVidas.style.display = 'none';
      elStatVidas.textContent = '';
    } else {
      elStatVidas.style.display = 'inline';
      let corazones = '';
      for (let i = 0; i < vidasMaximas; i++) corazones += (i < vidasActuales ? '❤️' : '💔');
      elStatVidas.textContent = corazones;
    }

    // Badges de inventario en los botones de la tarjeta (ocultos en Light: ahí no se consumen)
    if (!esLight && gamificacion.inventario.pistasGratis > 0) {
      elBadgePistaGratis.textContent = 'x' + gamificacion.inventario.pistasGratis;
      elBadgePistaGratis.style.display = 'block';
    } else {
      elBadgePistaGratis.style.display = 'none';
    }
    if (!esLight && gamificacion.inventario.comodines > 0) {
      elBadgeComodines.textContent = 'x' + gamificacion.inventario.comodines;
      elBadgeComodines.style.display = 'block';
    } else {
      elBadgeComodines.style.display = 'none';
    }
  }

  // ---------- Modal de selección de modo ----------
  let listaPendienteInicio = null;
  let modoElegidoEnModal = 'light';

  function seleccionarModoEnModal(modo) {
    modoElegidoEnModal = modo;
    document.querySelectorAll('.opcion-modo').forEach((el) => {
      el.classList.toggle('seleccionada', el.dataset.modo === modo);
    });
  }

  function abrirModalSeleccionModo(lista) {
    if (!lista || lista.length === 0) return;
    listaPendienteInicio = lista;
    seleccionarModoEnModal('light');   // Light por defecto
    elModalModo.classList.add('abierto');
  }

  function cancelarModalModo() {
    elModalModo.classList.remove('abierto');
    listaPendienteInicio = null;
  }

  function confirmarModoYComenzar() {
    const lista = listaPendienteInicio;
    elModalModo.classList.remove('abierto');
    listaPendienteInicio = null;
    if (!lista) return;
    configurarModoJuego(modoElegidoEnModal);
    iniciarSesion(lista);
  }

  document.getElementById('opcion-modo-tryhard').addEventListener('click', () => seleccionarModoEnModal('tryhard'));
  document.getElementById('opcion-modo-normal').addEventListener('click', () => seleccionarModoEnModal('normal'));
  document.getElementById('opcion-modo-light').addEventListener('click', () => seleccionarModoEnModal('light'));
  document.getElementById('btn-modo-confirmar').addEventListener('click', () => confirmarModoYComenzar());
  document.getElementById('btn-modo-cancelar').addEventListener('click', () => cancelarModalModo());

  // ---------- Game Over ----------

  function dispararGameOver() {
    if (modoJuego === 'light') return;   // nunca en Light
    if (gameOverDisparado) return;       // no disparar 2 veces
    gameOverDisparado = true;
    // Reutiliza el guard ya existente (marcar/evaluarRespuesta/navegar lo
    // chequean todos): bloquea la sesión sin tocar su lógica.
    pausaManual = true;
    reproducirSonido('game_over');
    elModalGameOver.classList.add('abierto');
  }

  document.getElementById('btn-gameover-resumen').addEventListener('click', () => {
    elModalGameOver.classList.remove('abierto');
    pausaManual = false;
    finalizarSesion();
  });

  // ---------- Historial de sesiones (guardado desde mostrarResumen) ----------


  // ============================================================
  // NUEVO: poda automática del historial (100 sesiones por evaluador)
  // ============================================================
  const LIMITE_HISTORIAL_POR_EVALUADOR = 100;

  // Suma 'sesionesPodadas' (ya extraídas del historial) al resumen consolidado
  // de 'espacio'. Si ya existía un resumen previo, se SUMAN los totales y la
  // mejor nota se actualiza solo si la nueva es superior (nunca baja).
  function consolidarResumenEvaluador(espacio, sesionesPodadas) {
    if (sesionesPodadas.length === 0) return;
    if (!gamificacion.resumenPorEvaluador) gamificacion.resumenPorEvaluador = {};
    const previo = gamificacion.resumenPorEvaluador[espacio];
    const acc = previo ? Object.assign({}, previo) : {
      sesionesPodadas: 0, mejorNota: 0, sumaNotas: 0, tiempoTotalMs: 0, puntosTotales: 0, ultimaFecha: '',
    };
    sesionesPodadas.forEach((s) => {
      const nota = Number(s.nota) || 0;
      acc.sesionesPodadas++;
      if (nota > acc.mejorNota) acc.mejorNota = nota;
      acc.sumaNotas += nota;
      acc.tiempoTotalMs += Number(s.duracion_ms) || 0;
      acc.puntosTotales += Number(s.puntos_ganados) || 0;
      if (!acc.ultimaFecha || String(s.fecha) > String(acc.ultimaFecha)) acc.ultimaFecha = s.fecha;
    });
    gamificacion.resumenPorEvaluador[espacio] = acc;
  }

  // Agrupa el historial por 'espacio'; en cada grupo que supere el límite,
  // extrae las sesiones MÁS ANTIGUAS que exceden (no las primeras N) y las
  // consolida en el resumen de ese evaluador antes de descartarlas.
  function podarHistorialPorEvaluador() {
    const porEspacio = {};
    gamificacion.historial.forEach((s) => {
      const esp = espacioDeSesion(s);
      (porEspacio[esp] = porEspacio[esp] || []).push(s);
    });
    const aQuitar = new Set();
    Object.keys(porEspacio).forEach((esp) => {
      const sesiones = porEspacio[esp];
      if (sesiones.length <= LIMITE_HISTORIAL_POR_EVALUADOR) return;
      const ordenadas = sesiones.slice().sort((a, b) => String(a.fecha).localeCompare(String(b.fecha)));
      const exceso = sesiones.length - LIMITE_HISTORIAL_POR_EVALUADOR;
      const aExtraer = ordenadas.slice(0, exceso);   // las más antiguas, justo las que exceden el límite
      consolidarResumenEvaluador(esp, aExtraer);
      aExtraer.forEach((s) => aQuitar.add(s));
    });
    if (aQuitar.size > 0) {
      gamificacion.historial = gamificacion.historial.filter((s) => !aQuitar.has(s));
    }
  }

  function guardarHistorialSesion(nota) {
    if (modoObservador) return;
    if (historialGuardadoEstaSesion) return;   // evita duplicar si mostrarResumen() se llama 2 veces
    historialGuardadoEstaSesion = true;

    const duracionMs = tiempos.reduce((a, b) => a + b, 0);
    gamificacion.historial.push({
      fecha: new Date().toISOString(),
      modo: srsSesionActiva ? 'srs' : modoJuego,   // NUEVO: distingue las sesiones de Repaso Espaciado
      nota: Math.round(nota * 10) / 10,
      duracion_ms: duracionMs,
      puntos_ganados: modoJuego === 'light' ? 0 : puntosSesion,
      espacio: ESPACIO_HASH,   // NUEVO: de que evaluador es esta sesion
      // NUEVO: hashes de las tarjetas de esta sesion (permite comparar con una lista
      // guardada, incluso retroactivamente si la lista se crea después de jugar)
      tarjetas: tarjetasSesion.map((t) => hashTarjeta(t)),
      // NUEVO: nombre de la lista activa, o de la(s) lista(s) del Repaso Espaciado
      listado: srsSesionActiva
        ? (srsSesionActiva.nombres.length === 1 ? srsSesionActiva.nombres[0] : 'SRS Múltiple')
        : (listaActivaNombre || ''),
    });
    podarHistorialPorEvaluador();   // NUEVO: justo después de agregar la entrada, antes de persistir
    if (modoJuego !== 'light') {
      gamificacion.puntosTotales += puntosSesion;
    }
    guardarGamificacion();
    actualizarBarraGamificacion();
  }

  function renderResumenGamificacion(nota) {
    if (modoObservador) { elResumenGamificacion.style.display = 'none'; return; }
    elResumenGamificacion.style.display = 'block';
    document.getElementById('resumen-puntos').textContent =
      modoJuego === 'light' ? '⭐ Puntos ganados: — (modo Light)' : ('⭐ Puntos ganados: ' + puntosSesion);
    document.getElementById('resumen-mejor-racha').textContent = '🔥 Mejor racha: ' + mejorRacha;
    document.getElementById('resumen-modo').textContent = '🎮 Modo: ' + nombreModoJuego(modoJuego);

    const elNota = document.getElementById('nota-sesion');
    if (gameOverDisparado) {
      elNota.classList.add('game-over');
      elNota.textContent += ' — 💀 Game Over';
    } else {
      elNota.classList.remove('game-over');
    }
  }

  // ---------- Tienda ----------

  const ITEMS_TIENDA = [
    { id: 'comodines', icono: '🃏', nombre: 'Comodín', precio: PRECIO_COMODIN, descripcion: 'Salta una tarjeta sin perder vida ni puntos.' },
    { id: 'pistasGratis', icono: '💡', nombre: 'Pista Gratis', precio: PRECIO_PISTA_GRATIS, descripcion: 'Revela la pista manteniendo el puntaje máximo si aciertas.' },
    { id: 'segundasOportunidades', icono: '🔁', nombre: 'Segunda oportunidad', precio: PRECIO_SEGUNDA_OPORTUNIDAD,
      descripcion: 'Si fallás una respuesta escrita, te deja reintentar esa tarjeta una vez. Si acertás cobrás el ' + Math.round(FACTOR_RECOMPENSA_REINTENTO * 100) + ' % de los puntos.' },
    { id: 'escudosRacha', icono: '🛡', nombre: 'Escudo de racha', precio: PRECIO_ESCUDO_RACHA,
      descripcion: 'Al fallar conserva tu racha y su multiplicador (la penitencia se cobra igual). No se gasta si tu racha es 0.' },
  ];

  function renderTienda() {
    elSaldoTienda.textContent = 'Puntos disponibles: ' + gamificacion.puntosTotales;
    elGridTienda.innerHTML = '';
    ITEMS_TIENDA.forEach((item) => {
      const cont = document.createElement('div');
      cont.className = 'item-tienda';

      const icono = document.createElement('div');
      icono.className = 'item-tienda-icono';
      icono.textContent = item.icono;
      cont.appendChild(icono);

      const nombre = document.createElement('div');
      nombre.className = 'item-tienda-nombre';
      nombre.textContent = item.nombre;
      cont.appendChild(nombre);

      const precio = document.createElement('div');
      precio.className = 'item-tienda-precio';
      precio.textContent = item.precio + ' pts';
      cont.appendChild(precio);

      const desc = document.createElement('div');
      desc.className = 'item-tienda-descripcion';
      desc.textContent = item.descripcion;
      cont.appendChild(desc);

      const cantidad = gamificacion.inventario[item.id] || 0;
      if (cantidad > 0) {
        const inv = document.createElement('div');
        inv.className = 'item-tienda-inventario';
        inv.textContent = 'Tenés: ' + cantidad;
        cont.appendChild(inv);
      }

      const boton = document.createElement('button');
      boton.type = 'button';
      boton.className = 'item-tienda-boton';
      boton.textContent = 'Comprar';
      boton.disabled = gamificacion.puntosTotales < item.precio;
      boton.addEventListener('click', () => comprarItem(item.id));
      cont.appendChild(boton);

      elGridTienda.appendChild(cont);
    });
  }

  function comprarItem(idItem) {
    const item = ITEMS_TIENDA.find((i) => i.id === idItem);
    if (!item) return;
    if (gamificacion.puntosTotales < item.precio) return;
    gamificacion.puntosTotales -= item.precio;
    gamificacion.inventario[idItem] = (gamificacion.inventario[idItem] || 0) + 1;
    guardarGamificacion();
    renderTienda();
    actualizarBarraGamificacion();
  }

  function abrirTienda() {
    mostrarPantalla('tienda');
    renderTienda();
  }

  elBtnAbrirTienda.addEventListener('click', () => abrirTienda());
  document.getElementById('btn-tienda-volver').addEventListener('click', () => mostrarPantalla('temas'));

  // ---------- Historial ----------


  // ============================================================
  // NUEVO: modal "Listas guardadas"
  // ============================================================
  const elModalListas = document.getElementById('modal-listas');
  const elListaListasGuardadas = document.getElementById('lista-listas-guardadas');
  const elListasVacio = document.getElementById('listas-vacio');

  function hashesVigentesActuales() {
    return new Set(tarjetasCompletas.map((t) => hashTarjeta(t)));
  }

  // Hashes que HOY no corresponden a ninguna tarjeta actual, pero que SÍ
  // corresponderían a alguna bajo el nombre viejo de un evaluador que
  // aparece en el historial (candidato a "Vincular a este evaluador", §14).
  // Existen para que la limpieza automática de abajo no los borre antes de
  // que el usuario tenga chance de migrarlos.
  function hashesRecuperablesDeOtroEspacio() {
    const recuperables = new Set();
    const candidatos = new Set();
    gamificacion.historial.forEach((s) => {
      const esp = espacioDeSesion(s);
      if (esp && esp !== ESPACIO_HASH) candidatos.add(esp);
    });
    candidatos.forEach((esp) => {
      tarjetasCompletas.forEach((t) => {
        const h = hashTarjetaCon(esp, t);
        if (h !== hashTarjeta(t)) recuperables.add(h);
      });
    });
    return recuperables;
  }

  // 12.2: al entrar al modal se limpian automáticamente los hashes que ya no
  // corresponden a ninguna tarjeta actual NI a una recuperable (ver arriba);
  // si una lista queda vacía, se borra.
  function limpiarListasContraTarjetasActuales() {
    const vigentes = hashesVigentesActuales();
    const recuperables = hashesRecuperablesDeOtroEspacio();
    let cambio = false;
    listas = listas.filter((lista) => {
      const filtrados = lista.hashes.filter((h) => vigentes.has(h) || recuperables.has(h));
      if (filtrados.length !== lista.hashes.length) {
        cambio = true;
        if (filtrados.length === 0) return false;   // lista vacía: se borra
        lista.hashes = filtrados;
        lista.modificada = Date.now();
      }
      return true;
    });
    if (cambio) guardarListas();
  }

  function formatearFechaLista(ms) {
    try { return new Date(ms).toLocaleString(); } catch (e) { return String(ms); }
  }

  // Nombres de listas con el 👁 desplegado en este momento (solo en memoria;
  // se pierde al recargar, como el resto del estado de UI del modal).
  const listasVerTarjetasExpandido = new Set();

  // 👁: arma la vista con scroll interno de las tarjetas de una lista. Las
  // listas NO guardan el texto de la tarjeta, solo el hash — si una tarjeta
  // ya no existe no hay forma de mostrar qué decía, solo que falta.
  function construirVistaTarjetasLista(lista) {
    const cont = document.createElement('div');
    cont.className = 'lista-ver-tarjetas';
    let faltantes = 0;
    lista.hashes.forEach((h) => {
      const t = tarjetasCompletas.find((t) => hashTarjeta(t) === h);
      const item = document.createElement('div');
      item.className = 'lista-ver-tarjeta-item';
      if (t) {
        item.textContent = armarRenglon(t);
      } else {
        faltantes++;
        item.classList.add('lista-ver-tarjeta-faltante');
        const s = document.createElement('s');
        s.textContent = '(tarjeta original ya no existe)';
        item.appendChild(s);
      }
      cont.appendChild(item);
    });
    if (faltantes > 0) {
      const nota = document.createElement('div');
      nota.className = 'lista-ver-tarjeta-nota';
      nota.textContent = '(' + faltantes + ' tarjeta' + (faltantes === 1 ? '' : 's') + ' originales ya no existen)';
      cont.appendChild(nota);
    }
    return cont;
  }

  // Botón 🧠: blanco (inactivo) -> naranja (activo) -> [fallo] -> blanco;
  // y azul (graduado) -> confirm -> naranja de nuevo en nivel 1.
  function alternarSrsLista(nombre) {
    const lista = buscarLista(nombre);
    if (!lista) return;
    const srs = obtenerSrsLista(lista);
    if (srs.graduated) {
      if (!window.confirm('¿Reiniciar el progreso SRS de esta lista?')) return;
      srs.currentLevel = 1;
      srs.lastReviewDate = Date.now();
      srs.graduated = false;
      srs.activated = true;
      srs.snoozeUntil = 0;
    } else if (srs.activated) {
      // Activo -> inactivo: se borra todo el historial de progreso (vuelve a los valores por defecto)
      lista.srs = Object.assign({}, SRS_DEFAULT);
    } else {
      // Inactivo -> activo
      srs.activated = true;
      srs.startDate = Date.now();
      srs.currentLevel = 1;
      srs.lastReviewDate = Date.now();
      srs.graduated = false;
      srs.snoozeUntil = 0;
    }
    lista.modificada = Date.now();
    guardarListas();
    renderListasGuardadas();
  }

  function renderListasGuardadas() {
    // NUEVO (tarea 3): la cantidad "de otros evaluadores" se mide ANTES de la
    // limpieza automática de huérfanas (§12.2 las descarta enseguida), para
    // que el botón de vincular sea visible al menos en el render donde
    // realmente aparecieron esas tarjetas ajenas.
    const nOtrasListas = contarOtrasListas();
    document.getElementById('det-listas-vincular').style.display = nOtrasListas > 0 ? 'block' : 'none';
    document.getElementById('suma-listas-vincular').textContent = 'Hay tarjetas de listas de otros evaluadores (' + nOtrasListas + ')';

    limpiarListasContraTarjetasActuales();
    elListaListasGuardadas.innerHTML = '';
    const ordenadas = listas.slice().sort((a, b) => b.creada - a.creada);   // más reciente primero

    if (ordenadas.length === 0) {
      elListasVacio.style.display = 'block';
    } else {
      elListasVacio.style.display = 'none';
    }

    ordenadas.forEach((lista) => {
      const srs = obtenerSrsLista(lista);
      const fila = document.createElement('div');
      fila.className = 'fila-lista-guardada';

      // ---- Cabecera: 🧠 + nombre + estado SRS + 👁 ----
      const cabecera = document.createElement('div');
      cabecera.className = 'lista-fila-cabecera';

      const btnSrs = document.createElement('button');
      btnSrs.type = 'button';
      btnSrs.className = 'lista-srs-btn ' + srsClaseBoton(srs);
      btnSrs.textContent = srs.graduated ? '🎓 Reiniciar SRS' : '🧠';
      btnSrs.title = srs.graduated ? 'Reiniciar el progreso de Repaso Espaciado'
        : (srs.activated ? 'Repaso Espaciado activo (tocar para desactivarlo)' : 'Activar Repaso Espaciado para esta lista');
      btnSrs.addEventListener('click', () => alternarSrsLista(lista.nombre));
      cabecera.appendChild(btnSrs);

      const btnNombre = document.createElement('button');
      btnNombre.type = 'button';
      btnNombre.className = 'lista-nombre-btn';
      btnNombre.textContent = lista.nombre + (listaActivaNombre === lista.nombre ? ' (activa)' : '');
      btnNombre.addEventListener('click', () => {
        seleccionarListaActiva(lista.nombre);
        cerrarModalListas();
      });
      cabecera.appendChild(btnNombre);

      if (srs.graduated) {
        const estado = document.createElement('span');
        estado.className = 'lista-srs-estado';
        estado.textContent = '🎓 Experto';
        cabecera.appendChild(estado);
      } else if (srs.activated) {
        const estado = document.createElement('span');
        estado.className = 'lista-srs-estado';
        estado.textContent = 'Nivel ' + srs.currentLevel + ' · Próximo: ' + srsTextoProximoRepaso(srs);
        cabecera.appendChild(estado);
      }

      // OPTIMIZACIÓN: antes, tocar el 👁 llamaba a renderListasGuardadas()
      // y reconstruía TODO el modal (todas las filas) desde cero. Ahora solo
      // crea/inserta (o quita) el div.lista-ver-tarjetas DENTRO de esta fila
      // puntual, sin tocar el resto de la lista.
      const btnOjo = document.createElement('button');
      btnOjo.type = 'button';
      btnOjo.className = 'lista-ojo-btn';
      btnOjo.title = 'Ver tarjetas de esta lista';
      btnOjo.textContent = '👁';
      btnOjo.addEventListener('click', () => {
        if (listasVerTarjetasExpandido.has(lista.nombre)) {
          listasVerTarjetasExpandido.delete(lista.nombre);
          const visor = fila.querySelector('.lista-ver-tarjetas');
          if (visor) visor.remove();
        } else {
          listasVerTarjetasExpandido.add(lista.nombre);
          fila.appendChild(construirVistaTarjetasLista(lista));
        }
      });
      cabecera.appendChild(btnOjo);

      fila.appendChild(cabecera);

      // ---- Acciones (Renombrar / Borrar), debajo del título ----
      const acciones = document.createElement('div');
      acciones.className = 'lista-acciones';
      const btnRenombrar = document.createElement('button');
      btnRenombrar.type = 'button';
      btnRenombrar.textContent = '✏️ Renombrar';
      btnRenombrar.addEventListener('click', () => renombrarLista(lista.nombre));
      const btnBorrar = document.createElement('button');
      btnBorrar.type = 'button';
      btnBorrar.textContent = '🗑️ Borrar';
      btnBorrar.addEventListener('click', () => borrarLista(lista.nombre));
      acciones.appendChild(btnRenombrar);
      acciones.appendChild(btnBorrar);
      fila.appendChild(acciones);

      // ---- Cantidad y fechas, debajo de las acciones ----
      const detalle = document.createElement('div');
      detalle.className = 'lista-detalle';
      detalle.textContent = lista.hashes.length + ' tarjeta(s) · creada: ' + formatearFechaLista(lista.creada) +
        ' · modificada: ' + formatearFechaLista(lista.modificada);
      fila.appendChild(detalle);

      // ---- 👁 Ver tarjetas (con scroll interno) ----
      if (listasVerTarjetasExpandido.has(lista.nombre)) {
        fila.appendChild(construirVistaTarjetasLista(lista));
      }

      elListaListasGuardadas.appendChild(fila);
    });
  }

  function abrirModalListas() {
    elModalListas.classList.add('abierto');
    // OPTIMIZACIÓN: se deja pintar el modal (vacío) primero; recién en el
    // siguiente frame se arma el contenido (todas las filas/listas).
    requestAnimationFrame(() => {
      renderListasGuardadas();
    });
  }
  function cerrarModalListas() {
    elModalListas.classList.remove('abierto');
  }
  elBtnAbrirListas.addEventListener('click', () => abrirModalListas());
  document.getElementById('btn-listas-cerrar').addEventListener('click', () => cerrarModalListas());
  document.getElementById('btn-lista-ninguna').addEventListener('click', () => {
    quitarListaActiva();
    cerrarModalListas();
  });

  function renombrarLista(nombreActual) {
    const lista = buscarLista(nombreActual);
    if (!lista) return;
    const nuevo = window.prompt('Nuevo nombre para la lista:', lista.nombre);
    if (nuevo === null) return;
    const limpio = nuevo.trim();
    if (!limpio) { window.alert('El nombre no puede estar vacío.'); return; }
    if (limpio.toLowerCase() !== lista.nombre.toLowerCase() && buscarLista(limpio)) {
      window.alert('Ya existe una lista llamada «' + limpio + '».');
      return;
    }
    if (listaActivaNombre === lista.nombre) listaActivaNombre = limpio;
    lista.nombre = limpio;
    lista.modificada = Date.now();
    guardarListas();
    renderListasGuardadas();
    actualizarChipListaActiva();
  }

  function borrarLista(nombre) {
    if (!window.confirm('¿Borrar la lista «' + nombre + '»? Las sesiones guardadas que la usaron se conservan (mostrarán el nombre tachado).')) return;
    listas = listas.filter((l) => l.nombre !== nombre);
    guardarListas();
    if (listaActivaNombre === nombre) quitarListaActiva();
    renderListasGuardadas();
  }

  document.getElementById('btn-listas-vaciar').addEventListener('click', () => {
    if (listas.length === 0) return;
    if (!window.confirm('¿Vaciar TODAS las listas guardadas (' + listas.length + ')? No se puede deshacer.')) return;
    listas = [];
    guardarListas();
    if (listaActivaNombre) quitarListaActiva();
    renderListasGuardadas();
  });

  // ---------- Exportar / Importar ----------
  document.getElementById('btn-listas-exportar').addEventListener('click', () => {
    const salida = { version: 1, listas: listas };
    const blob = new Blob([JSON.stringify(salida, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'listas_guardadas.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  const elInputListasImportar = document.getElementById('input-listas-importar');
  document.getElementById('btn-listas-importar').addEventListener('click', () => elInputListasImportar.click());
  elInputListasImportar.addEventListener('change', () => {
    const archivo = elInputListasImportar.files[0];
    elInputListasImportar.value = '';
    if (!archivo) return;
    const lector = new FileReader();
    lector.onload = () => {
      let datos;
      try { datos = JSON.parse(lector.result); } catch (e) { window.alert('El archivo no es un JSON válido.'); return; }
      const entrantes = (datos && Array.isArray(datos.listas)) ? datos.listas : null;
      if (!entrantes) { window.alert('El archivo no tiene el formato esperado ({ "listas": [...] }).'); return; }
      const r = fusionarListasEntrantes(entrantes);
      window.alert('Importación de listas: ' + r.nuevas + ' nueva(s), ' + r.actualizadas + ' actualizada(s), ' + r.conservadas + ' conservada(s).');
    };
    lector.readAsText(archivo);
  });

  // NUEVO: fusiona listas entrantes (individual o dentro de un backup completo).
  // Mismo nombre (case-insensitive): gana la más reciente por 'modificada'. Nombre
  // nuevo: se agrega.
  function fusionarListasEntrantes(entrantes) {
    let nuevas = 0, actualizadas = 0, conservadas = 0;
    entrantes.forEach((imp) => {
      if (!imp || typeof imp.nombre !== 'string' || !Array.isArray(imp.hashes)) return;
      const existente = buscarLista(imp.nombre);
      const impModificada = typeof imp.modificada === 'number' ? imp.modificada : 0;
      if (!existente) {
        listas.push({
          nombre: imp.nombre,
          hashes: imp.hashes.slice(),
          creada: typeof imp.creada === 'number' ? imp.creada : Date.now(),
          modificada: impModificada || Date.now(),
          srs: normalizarSrsImportado(imp.srs),   // NUEVO: el progreso de Repaso Espaciado viaja con la lista
        });
        nuevas++;
      } else if (impModificada > existente.modificada) {
        existente.nombre = imp.nombre;
        existente.hashes = imp.hashes.slice();
        existente.creada = typeof imp.creada === 'number' ? imp.creada : existente.creada;
        existente.modificada = impModificada;
        existente.srs = normalizarSrsImportado(imp.srs);   // NUEVO
        actualizadas++;
      } else {
        conservadas++;
      }
    });
    guardarListas();
    renderListasGuardadas();
    actualizarChipListaActiva();
    return { nuevas: nuevas, actualizadas: actualizadas, conservadas: conservadas };
  }

  // ---------- Guardar una lista nueva (desde el resumen) ----------
  const elBtnResumenGuardarLista = document.getElementById('btn-resumen-guardar-lista');
  const elModalGuardarLista = document.getElementById('modal-guardar-lista');
  const elInputGuardarListaNombre = document.getElementById('input-guardar-lista-nombre');
  const elGuardarListaError = document.getElementById('guardar-lista-error');

  function mismoConjuntoHashes(a, b) {
    if (a.length !== b.length) return false;
    const set = new Set(a);
    return b.every((h) => set.has(h));
  }

  function abrirModalGuardarLista() {
    elInputGuardarListaNombre.value = '';
    elGuardarListaError.style.display = 'none';
    elModalGuardarLista.classList.add('abierto');
    elInputGuardarListaNombre.focus();
  }
  function cerrarModalGuardarLista() {
    elModalGuardarLista.classList.remove('abierto');
  }
  elBtnResumenGuardarLista.addEventListener('click', () => abrirModalGuardarLista());
  document.getElementById('btn-guardar-lista-cancelar').addEventListener('click', () => cerrarModalGuardarLista());
  elInputGuardarListaNombre.addEventListener('keydown', (e) => { if (e.key === 'Enter') aceptarGuardarLista(); });

  // ---------- NUEVO: "Análisis de la sesión" (por subtema, desde el resumen) ----------
  const elBtnAnalisisSesion = document.getElementById('btn-analisis-sesion');
  const elModalAnalisisSesion = document.getElementById('modal-analisis-sesion');
  const elAnalisisSesionContenido = document.getElementById('analisis-sesion-contenido');

  // Agrupa los índices (de tarjetasSesion) de la sesión actual por subT.
  function agruparSesionPorTema() {
    const grupos = new Map();   // nombre -> indices[]
    tarjetasSesion.forEach((t, i) => {
      const nombre = nombreTema(t);
      if (!grupos.has(nombre)) grupos.set(nombre, []);
      grupos.get(nombre).push(i);
    });
    return grupos;
  }

  // Calcula indicadores + comentarios para UN subtema. Devuelve null si no
  // llega al mínimo de 3 evaluadas (no se incluye en el análisis).
  function analizarSubtemaSesion(indices) {
    const esEvaluada = (i) => resultados[i] === 'si' || resultados[i] === 'no' || resultados[i] === 'casi';
    const evaluadas = indices.filter(esEvaluada);
    if (evaluadas.length < 3) return null;

    const aciertos = evaluadas.filter((i) => resultados[i] === 'si');
    const fallos = evaluadas.filter((i) => resultados[i] === 'no' || resultados[i] === 'casi');
    const casi = evaluadas.filter((i) => resultados[i] === 'casi');
    const saltadas = indices.filter((i) => resultados[i] === 'saltar');

    const nEval = evaluadas.length;
    const pctAciertos = (aciertos.length / nEval) * 100;
    const pctFallos = (fallos.length / nEval) * 100;
    const pctCasi = (casi.length / nEval) * 100;
    const pctPista = (evaluadas.filter((i) => pistaMostrada[i]).length / nEval) * 100;
    const pctResp = (evaluadas.filter((i) => respuestaMostrada[i]).length / nEval) * 100;
    const promedioMs = (lista) => lista.length ? lista.reduce((a, i) => a + (tiempos[i] || 0), 0) / lista.length : 0;
    const tiempoProFallas = promedioMs(fallos);
    const tiempoProEvaluadas = promedioMs(evaluadas);

    const comentarios = [];   // { texto, color }

    // 🔴 Rojo
    if (pctFallos >= 70) comentarios.push({ texto: 'Revisión urgente', color: 'rojo' });
    if (aciertos.length === 0 && nEval >= 2) comentarios.push({ texto: 'No acertaste ninguna', color: 'rojo' });
    if (pctPista > 50) comentarios.push({ texto: 'Dependencia de pistas', color: 'rojo' });
    if (pctResp > 50) comentarios.push({ texto: 'Revisa el contenido antes de evaluar', color: 'rojo' });
    if (fallos.length > 0 && tiempoProFallas > 30000) comentarios.push({ texto: '¿Te trabaste en este tema?', color: 'rojo' });

    // 🟠 Naranja
    if (pctFallos >= 50 && pctFallos < 70) comentarios.push({ texto: 'En proceso', color: 'naranja' });
    if (pctPista >= 20 && pctPista <= 50) comentarios.push({ texto: 'Depende un poco de pistas', color: 'naranja' });
    if (pctCasi >= 60) comentarios.push({ texto: 'Estás cerca, repasa los detalles', color: 'naranja' });

    // 🟢 Verde
    if (pctAciertos >= 80) {
      comentarios.push({ texto: 'Dominado', color: 'verde' });
    } else if (pctAciertos >= 60) {
      comentarios.push({ texto: 'Bien encaminado', color: 'verde' });
    }
    if (tiempoProEvaluadas >= 10000 && tiempoProEvaluadas <= 20000) comentarios.push({ texto: 'Ritmo adecuado', color: 'verde' });
    if (pctPista === 0 && pctResp === 0) comentarios.push({ texto: 'Autónomo', color: 'verde' });
    if (saltadas.length === 0) comentarios.push({ texto: 'Completo', color: 'verde' });

    // Color predominante del badge: el peor color presente entre los
    // comentarios que se dispararon; si ninguno se disparó, se usa el
    // porcentaje de aciertos como respaldo.
    let color = 'naranja';
    if (comentarios.some((c) => c.color === 'rojo')) color = 'rojo';
    else if (comentarios.some((c) => c.color === 'naranja')) color = 'naranja';
    else if (comentarios.some((c) => c.color === 'verde')) color = 'verde';
    else color = pctAciertos >= 60 ? 'verde' : (pctAciertos <= 30 ? 'rojo' : 'naranja');

    return {
      pctAciertos: pctAciertos,
      nAciertos: aciertos.length,
      nEvaluadas: nEval,
      comentarios: comentarios,
      color: color,
    };
  }

  // OPTIMIZACIÓN: el cálculo y el armado del DOM del modal son relativamente
  // livianos (acotados por la cantidad de subT de la sesión, no por la
  // cantidad de tarjetas), pero igual se calculan solo al ABRIR el modal —
  // no en mostrarAnalisisSesion() (que solo decide si el botón se muestra) —
  // para no sumarle trabajo extra a mostrarResumen() si el usuario nunca
  // llega a abrirlo.
  // NUEVO: "Resumen Ejecutivo", al principio del modal. Se basa SOLO en los
  // subtemas que ya pasaron el mínimo de 3 evaluadas (el mismo array 'filas'
  // que arma el detalle). Null si hay menos de 2 subtemas (no hay nada que
  // comparar).
  function construirResumenEjecutivo(filas) {
    if (filas.length < 2) return null;

    const div = document.createElement('div');
    div.className = 'analisis-resumen-ejecutivo';
    const titulo = document.createElement('div');
    titulo.className = 'analisis-resumen-titulo';
    titulo.textContent = 'Resumen ejecutivo';
    div.appendChild(titulo);

    const agregarLinea = (texto) => {
      const p = document.createElement('p');
      p.className = 'analisis-resumen-linea';
      p.textContent = texto;
      div.appendChild(p);
    };

    const todosAlto = filas.every((f) => f.info.pctAciertos >= 80);
    const ningunoSupera50 = filas.every((f) => f.info.pctAciertos <= 50);

    if (todosAlto) {
      agregarLinea('✅ Sesión sólida — todos los subT ≥80%');
      return div;
    }
    if (ningunoSupera50) {
      agregarLinea('⚠️ Sesión para repetir — ningún subT superó el 50%');
      return div;
    }

    // Desempate: mayor % aciertos; en empate, más evaluadas; si persiste,
    // orden alfabético. Se ordena de mejor a peor y se toman las puntas.
    const ordenados = [...filas].sort((a, b) => {
      if (b.info.pctAciertos !== a.info.pctAciertos) return b.info.pctAciertos - a.info.pctAciertos;
      if (b.info.nEvaluadas !== a.info.nEvaluadas) return b.info.nEvaluadas - a.info.nEvaluadas;
      return a.nombre.localeCompare(b.nombre);
    });
    const mejor = ordenados[0];
    const peor = ordenados[ordenados.length - 1];
    // El comentario más grave que aplica al peor subT: 'comentarios' ya está
    // armado en orden de gravedad (rojo, luego naranja, luego verde), así
    // que el primero es el más grave. Si no tiene ninguno (caso raro, sin
    // comentarios aplicables), se usa un texto genérico.
    const prioridadTexto = peor.info.comentarios.length > 0
      ? peor.info.comentarios[0].texto
      : 'Repasar este subtema';

    agregarLinea('✅ Fuerte: ' + mejor.nombre + ' — ' + Math.round(mejor.info.pctAciertos) + '% aciertos');
    agregarLinea('⚠️ Débil: ' + peor.nombre + ' — ' + Math.round(peor.info.pctAciertos) + '% aciertos');
    agregarLinea('🎯 Prioridad: ' + peor.nombre + ' — ' + prioridadTexto);
    return div;
  }

  function construirAnalisisSesion() {
    elAnalisisSesionContenido.innerHTML = '';
    const grupos = agruparSesionPorTema();
    const filas = [];
    grupos.forEach((indices, nombre) => {
      const info = analizarSubtemaSesion(indices);
      if (info) filas.push({ nombre: nombre, info: info });
    });

    if (filas.length === 0) {
      elAnalisisSesionContenido.innerHTML = '<p class="galeria-vacia">No hay comentarios</p>';
      return;
    }

    filas.sort((a, b) => a.info.pctAciertos - b.info.pctAciertos);   // peor a mejor

    const frag = document.createDocumentFragment();

    const elResumenEjecutivo = construirResumenEjecutivo(filas);
    if (elResumenEjecutivo) frag.appendChild(elResumenEjecutivo);

    filas.forEach(({ nombre, info }) => {
      const div = document.createElement('div');
      div.className = 'analisis-fila';

      const titulo = document.createElement('div');
      titulo.className = 'analisis-nombre';
      titulo.textContent = nombre;
      div.appendChild(titulo);

      const badge = document.createElement('span');
      badge.className = 'analisis-badge analisis-badge-' + info.color;
      badge.textContent = Math.round(info.pctAciertos) + '% de aciertos (' + info.nAciertos + ' de ' + info.nEvaluadas + ')';
      div.appendChild(badge);

      if (info.comentarios.length > 0) {
        const ul = document.createElement('ul');
        ul.className = 'analisis-comentarios';
        info.comentarios.forEach((c) => {
          const li = document.createElement('li');
          li.textContent = c.texto;
          ul.appendChild(li);
        });
        div.appendChild(ul);
      }

      frag.appendChild(div);
    });
    elAnalisisSesionContenido.appendChild(frag);
  }

  // Llamada al final de mostrarResumen(): SOLO decide si el botón se
  // muestra (barato: un .some() sobre 'resultados'). El cálculo pesado del
  // análisis queda en construirAnalisisSesion(), disparado al abrir el modal.
  function mostrarAnalisisSesion() {
    const huboEvaluadas = resultados.some((r) => r === 'si' || r === 'no' || r === 'casi');
    elBtnAnalisisSesion.style.display = huboEvaluadas ? 'block' : 'none';
  }

  function abrirModalAnalisisSesion() {
    construirAnalisisSesion();
    elModalAnalisisSesion.classList.add('abierto');
  }
  function cerrarModalAnalisisSesion() {
    elModalAnalisisSesion.classList.remove('abierto');
  }
  elBtnAnalisisSesion.addEventListener('click', () => abrirModalAnalisisSesion());
  document.getElementById('btn-analisis-sesion-cerrar').addEventListener('click', () => cerrarModalAnalisisSesion());

  // 8.3: asocia retroactivamente las sesiones "Sin listado" cuyo conjunto de
  // tarjetas coincide 100% con la lista recién guardada (incluye, de paso, a
  // la sesión que se acaba de guardar en el historial).
  function asociarRetroactivamente(lista) {
    let n = 0;
    gamificacion.historial.forEach((s) => {
      if (s.listado) return;   // ya tenía un listado asignado: no se toca
      if (!Array.isArray(s.tarjetas)) return;   // sesiones viejas sin este dato: no se puede comparar
      if (mismoConjuntoHashes(s.tarjetas, lista.hashes)) { s.listado = lista.nombre; n++; }
    });
    if (n > 0) guardarGamificacion();
  }

  function aceptarGuardarLista() {
    const nombre = elInputGuardarListaNombre.value.trim();
    if (!nombre) { elGuardarListaError.textContent = 'El nombre no puede estar vacío.'; elGuardarListaError.style.display = 'block'; return; }
    if (buscarLista(nombre)) { elGuardarListaError.textContent = 'Ya existe una lista llamada «' + nombre + '».'; elGuardarListaError.style.display = 'block'; return; }
    const hashes = tarjetasSesion.map((t) => hashTarjeta(t));
    if (listas.some((l) => mismoConjuntoHashes(l.hashes, hashes))) {
      elGuardarListaError.textContent = 'Ya existe una lista con exactamente estas mismas tarjetas.';
      elGuardarListaError.style.display = 'block';
      return;
    }
    const ahora = Date.now();
    const lista = { nombre: nombre, hashes: hashes, creada: ahora, modificada: ahora, srs: Object.assign({}, SRS_DEFAULT) };
    listas.push(lista);
    guardarListas();
    asociarRetroactivamente(lista);
    cerrarModalGuardarLista();
    elBtnResumenGuardarLista.style.display = 'none';
    renderResumenListado();
  }
  document.getElementById('btn-guardar-lista-aceptar').addEventListener('click', () => aceptarGuardarLista());

  // ---------- Estadísticas del listado en el resumen (§11) ----------
  const elNotaListado = document.getElementById('nota-listado');

  function renderResumenListado() {
    const nombre = listaActivaNombre;
    if (!nombre || !buscarLista(nombre)) { elNotaListado.style.display = 'none'; return; }
    const sesiones = gamificacion.historial.filter((s) => esSesionLocal(s) && s.listado === nombre);
    if (sesiones.length === 0) { elNotaListado.style.display = 'none'; return; }
    const ordenadas = sesiones.slice().sort((a, b) => String(a.fecha).localeCompare(String(b.fecha)));
    const mejor = Math.max(...ordenadas.map((s) => Number(s.nota) || 0));
    let texto = '📋 Listado "' + nombre + '": mejor ' + mejor.toFixed(1);
    if (ordenadas.length > 1) {
      const anterior = Number(ordenadas[ordenadas.length - 2].nota) || 0;
      texto += ' · anterior ' + anterior.toFixed(1);
    }
    elNotaListado.textContent = texto;
    elNotaListado.style.display = 'block';
  }


  // ============================================================
  // NUEVO: Backup / Sincronización unificado
  // ============================================================

  // §7: timestamp del último backup, uno por evaluador. Se guarda como el
  // valor plano que pide el prompt (Date.now()); el snapshot de puntos e
  // inventario para §8 vive en una clave aparte (ver nota en marcarUltimoBackup).
  function claveUltimoBackup() { return 'ultimo_backup_' + ESPACIO_HASH; }
  function claveSnapshotBackup() { return 'ultimo_backup_snapshot_' + ESPACIO_HASH; }

  function obtenerUltimoBackup() {
    try {
      const v = localStorage.getItem(claveUltimoBackup());
      return v ? Number(v) : 0;
    } catch (e) {
      return 0;
    }
  }

  function obtenerSnapshotBackup() {
    try {
      const crudo = localStorage.getItem(claveSnapshotBackup());
      return crudo ? JSON.parse(crudo) : null;
    } catch (e) {
      return null;
    }
  }

  // Se llama al exportar o importar un BACKUP COMPLETO (nunca en los
  // individuales, ver §7). Además del timestamp, guarda una foto de
  // puntosTotales/inventario: es lo que permite saber si "los puntos
  // cambiaron desde el último backup" (§8) sin agregar un campo 't' a algo
  // que no lo tiene.
  function marcarUltimoBackup() {
    try {
      localStorage.setItem(claveUltimoBackup(), String(Date.now()));
      localStorage.setItem(claveSnapshotBackup(), JSON.stringify({
        puntosTotales: gamificacion.puntosTotales,
        inventario: Object.assign({}, gamificacion.inventario),
      }));
    } catch (e) {
      // sin localStorage disponible: el indicador simplemente no podrá saberlo
    }
    actualizarIndicadorSync();
  }

  // §8: hay al menos una entrada con t (o equivalente) posterior al último backup.
  function hayCambiosSinRespaldar() {
    const ultimo = obtenerUltimoBackup();
    if (Object.keys(dificiles).some((h) => (dificiles[h].t || 0) > ultimo)) return true;
    if (Object.keys(notasLocales).some((h) => (notasLocales[h].t || 0) > ultimo)) return true;
    if (Object.keys(razonesGuardadas).some((h) => (razonesGuardadas[h].t || 0) > ultimo)) return true;
    if (Object.keys(datosImagenes).some((h) => (datosImagenes[h].t || 0) > ultimo)) return true;
    if (listas.some((l) => (l.modificada || 0) > ultimo)) return true;
    if (gamificacion.historial.some((s) => {
      const f = Date.parse(s.fecha);
      return !isNaN(f) && f > ultimo;
    })) return true;
    // Puntos/inventario: comparados contra la foto tomada en el último backup
    // (comprar en la tienda, por ejemplo, no deja rastro en ninguna otra zona).
    const foto = obtenerSnapshotBackup();
    if (!foto) return ultimo > 0 ? false : (gamificacion.puntosTotales > 0);   // nunca hubo backup: solo avisa si ya hay algo que perder
    if (gamificacion.puntosTotales !== foto.puntosTotales) return true;
    const inv = gamificacion.inventario, invFoto = foto.inventario || {};
    if (Object.keys(inv).some((k) => (inv[k] || 0) !== (invFoto[k] || 0))) return true;
    return false;
  }

  function actualizarIndicadorSync() {
    const ultimo = obtenerUltimoBackup();
    document.getElementById('sync-fecha-backup').textContent = ultimo > 0
      ? '📅 Último backup: ' + formatearFechaLista(ultimo)
      : '📅 Sin backups realizados';
    document.getElementById('sync-aviso-pendientes').style.display = hayCambiosSinRespaldar() ? 'block' : 'none';
  }

  // §4: arma el objeto completo de backup, reutilizando exactamente las
  // mismas funciones que arman cada JSON individual.
  function construirBackupCompleto() {
    return {
      version: 1,
      evaluador: ESPACIO_HASH,
      timestamp: Date.now(),
      gamificacion: construirExportGamificacion(),
      dificiles: construirExportDificiles(),
      notas: construirExportNotas(),
      razones: construirExportRazones(),
      imagenes: construirExportImagenes(),
      listas: { version: 1, listas: listas },
    };
  }

  function exportarBackupCompleto() {
    const salida = construirBackupCompleto();
    const blob = new Blob([JSON.stringify(salida, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'backup_' + ESPACIO_HASH + '.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    marcarUltimoBackup();
    document.getElementById('sync-backup-info').textContent = 'Backup exportado: ' + new Date().toLocaleString() + '.';
  }

  // §6: fusiona cada sección presente reutilizando las MISMAS reglas de
  // fusión que ya usa cada JSON individual. Secciones ausentes se ignoran
  // (§10: un backup viejo sin alguna sección no rompe nada).
  function importarBackupCompleto(datos) {
    if (!datos || typeof datos !== 'object') return { error: 'El archivo no es un JSON válido.' };
    if (typeof datos.version !== 'number') return { error: 'El archivo no tiene el formato esperado (falta "version").' };
    if (datos.version !== 1) return { error: 'Versión de backup desconocida (' + datos.version + ').' };

    const ZONAS = ['gamificacion', 'dificiles', 'notas', 'razones', 'imagenes', 'listas'];
    const presentes = ZONAS.filter((z) => datos[z] !== undefined && datos[z] !== null);
    if (presentes.length === 0) return { error: 'El archivo no tiene ninguna sección reconocida.' };

    let zonasActualizadas = 0, zonasSinCambios = 0;
    const contar = (huboCambios) => { huboCambios ? zonasActualizadas++ : zonasSinCambios++; };

    if (datos.gamificacion) {
      const antes = { puntos: gamificacion.puntosTotales, inv: JSON.stringify(gamificacion.inventario), n: gamificacion.historial.length };
      fusionarGamificacionImportada(datos.gamificacion);
      contar(gamificacion.puntosTotales !== antes.puntos || JSON.stringify(gamificacion.inventario) !== antes.inv || gamificacion.historial.length !== antes.n);
    }
    if (datos.dificiles) {
      const lista = Array.isArray(datos.dificiles) ? datos.dificiles : (datos.dificiles.tarjetas || []);
      const r = fusionarDificilesLista(lista);
      contar(r.nuevas > 0);
    }
    if (datos.notas) {
      const lista = Array.isArray(datos.notas) ? datos.notas : (datos.notas.notas || []);
      const r = fusionarNotasLista(lista);
      contar(r.nuevas > 0 || r.actualizadas > 0);
    }
    if (datos.razones) {
      const lista = Array.isArray(datos.razones) ? datos.razones : (datos.razones.razones || []);
      const r = fusionarRazonesLista(lista);
      contar(r.nuevas > 0 || r.actualizadas > 0);
    }
    if (datos.imagenes) {
      const lista = Array.isArray(datos.imagenes) ? datos.imagenes : (datos.imagenes.imagenes || []);
      const r = fusionarImagenesLista(lista);
      contar(r.nuevas > 0 || r.actualizadas > 0);
    }
    if (datos.listas) {
      const entrantes = Array.isArray(datos.listas) ? datos.listas : (datos.listas.listas || []);
      const r = fusionarListasEntrantes(entrantes);
      contar(r.nuevas > 0 || r.actualizadas > 0);
    }

    marcarUltimoBackup();
    return { ok: true, zonasActualizadas: zonasActualizadas, zonasSinCambios: zonasSinCambios };
  }

  const elModalSync = document.getElementById('modal-sync');
  function abrirModalSync() {
    actualizarIndicadorSync();
    document.getElementById('sync-backup-info').textContent = '';
    elModalSync.classList.add('abierto');
  }
  document.getElementById('btn-abrir-sync').addEventListener('click', () => abrirModalSync());
  document.getElementById('btn-sync-cerrar').addEventListener('click', () => elModalSync.classList.remove('abierto'));

  document.getElementById('btn-backup-exportar').addEventListener('click', () => exportarBackupCompleto());

  const elInputBackupImportar = document.getElementById('input-backup-importar');
  document.getElementById('btn-backup-importar').addEventListener('click', () => elInputBackupImportar.click());
  elInputBackupImportar.addEventListener('change', () => {
    const archivo = elInputBackupImportar.files[0];
    elInputBackupImportar.value = '';
    if (!archivo) return;
    const lector = new FileReader();
    lector.onload = () => {
      const elInfo = document.getElementById('sync-backup-info');
      let datos;
      try {
        datos = JSON.parse(lector.result);
      } catch (e) {
        elInfo.textContent = 'El archivo no es un JSON válido.';
        return;
      }
      const r = importarBackupCompleto(datos);
      if (r.error) {
        elInfo.textContent = r.error;
        return;
      }
      elInfo.textContent = 'Backup importado: ' + r.zonasActualizadas + ' zona(s) actualizada(s), ' + r.zonasSinCambios + ' zona(s) sin cambios.';
      actualizarIndicadorSync();
    };
    lector.readAsText(archivo);
  });

  function formatearFechaHistorial(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch (e) {
      return iso;
    }
  }

  // NUEVO: espacio (evaluador) de una sesion. '' = sesion sin identificar.
  function espacioDeSesion(s) {
    return (s && typeof s.espacio === 'string') ? s.espacio : '';
  }
  function esSesionLocal(s) { return espacioDeSesion(s) === ESPACIO_HASH; }
  function etiquetaEspacio(esp) { return esp ? esp : 'Sin identificar'; }

  // NUEVO: muestra las estadísticas consolidadas (sesiones ya podadas) de ESTE
  // evaluador, debajo de las sesiones recientes paginadas. Oculto si todavía
  // no se podó nada para este evaluador.
  function renderResumenHistorico() {
    const el = document.getElementById('resumen-historico');
    const r = gamificacion.resumenPorEvaluador && gamificacion.resumenPorEvaluador[ESPACIO_HASH];
    if (!r || !r.sesionesPodadas) { el.style.display = 'none'; return; }
    const promedio = r.sesionesPodadas > 0 ? (r.sumaNotas / r.sesionesPodadas) : 0;
    el.innerHTML = '';
    const titulo = document.createElement('div');
    titulo.className = 'resumen-historico-titulo';
    titulo.textContent = 'Histórico (sesiones ya podadas)';
    el.appendChild(titulo);
    const texto = document.createElement('div');
    texto.textContent = r.sesionesPodadas + ' sesión(es) · mejor ' + r.mejorNota.toFixed(1) +
      ' · promedio ' + promedio.toFixed(1) + ' · ' + formatearTiempo(r.tiempoTotalMs) +
      ' · ⭐ ' + r.puntosTotales + ' · última: ' + (r.ultimaFecha ? formatearFechaHistorial(r.ultimaFecha) : '—');
    el.appendChild(texto);
    el.style.display = 'block';
  }

  // NUEVO: cache de nodos ya construidos por sesión (clave = 'fecha', única
  // por sesión). Las sesiones son inmutables una vez guardadas, así que
  // reutilizar el mismo nodo al volver a mostrar una página ya vista es
  // seguro: nunca hace falta reconstruirlo, solo reordenar/reinsertar.
  const nodosHistorial = new Map();   // fecha -> nodo item-historial

  function construirItemHistorial(entrada) {
    const item = document.createElement('div');
    item.className = 'item-historial';

    const fecha = document.createElement('div');
    fecha.className = 'item-historial-fecha';
    fecha.textContent = formatearFechaHistorial(entrada.fecha);
    const chip = document.createElement('span');
    chip.className = 'chip-espacio';
    chip.textContent = etiquetaEspacio(espacioDeSesion(entrada));
    fecha.appendChild(chip);
    item.appendChild(fecha);

    const detalle = document.createElement('div');
    detalle.className = 'item-historial-detalle';
    const modoTxt = document.createElement('span');
    modoTxt.textContent = '🎮 ' + nombreModoJuego(entrada.modo);
    const notaTxt = document.createElement('span');
    notaTxt.textContent = '🎓 ' + Number(entrada.nota).toFixed(1) + ' / 10';
    const duracionTxt = document.createElement('span');
    duracionTxt.textContent = '⏱ ' + formatearTiempo(entrada.duracion_ms || 0);
    const puntosTxt = document.createElement('span');
    puntosTxt.textContent = '⭐ ' + (entrada.puntos_ganados || 0);
    detalle.appendChild(modoTxt);
    detalle.appendChild(notaTxt);
    detalle.appendChild(duracionTxt);
    detalle.appendChild(puntosTxt);
    item.appendChild(detalle);
    actualizarListadoEnItem(item, entrada);   // NUEVO: arma/actualiza el "listado" (ver más abajo)
    return item;
  }

  // NUEVO: arma o refresca SOLO el fragmento "listado" (tachado si la lista ya
  // no existe, §9.2/§10) dentro de un nodo ya construido. Se llama siempre que
  // se reutiliza un nodo cacheado, porque a diferencia del resto de los datos
  // de la sesión (inmutables), si una lista se borra o renombra DESPUÉS este
  // estado puede cambiar sin que la sesión en sí haya cambiado.
  function actualizarListadoEnItem(item, entrada) {
    let listadoTxt = item.querySelector('.item-historial-listado');
    if (!entrada.listado) {
      if (listadoTxt) listadoTxt.remove();
      return;
    }
    if (!listadoTxt) {
      listadoTxt = document.createElement('span');
      listadoTxt.className = 'item-historial-listado';
      item.querySelector('.item-historial-detalle').appendChild(listadoTxt);
    }
    listadoTxt.innerHTML = '';
    if (buscarLista(entrada.listado)) {
      listadoTxt.textContent = '📋 ' + entrada.listado;
    } else {
      const s = document.createElement('s');
      s.textContent = '📋 ' + entrada.listado;
      listadoTxt.appendChild(s);
    }
  }

  function renderHistorial() {
    const todas = gamificacion.historial;
    const historial = todas.filter(esSesionLocal);   // la lista principal es solo de ESTE evaluador
    elListaHistorial.innerHTML = '';

    // NUEVO: poda del cache de nodos: las sesiones que ya no existen (podadas
    // o borradas) no deben seguir ocupando memoria.
    if (nodosHistorial.size > 0) {
      const fechasVigentes = new Set(historial.map((s) => s.fecha));
      Array.from(nodosHistorial.keys()).forEach((f) => { if (!fechasVigentes.has(f)) nodosHistorial.delete(f); });
    }

    elBtnHistorialBorrarEste.disabled = historial.length === 0;
    elBtnHistorialBorrarGlobal.disabled = todas.length === 0;
    renderHistorialOtros(todas.filter((s) => !esSesionLocal(s)));
    renderResumenHistorico();

    if (historial.length === 0) {
      elListaHistorial.style.display = 'none';
      elHistorialVacio.style.display = 'block';
      document.getElementById('historial-paginacion').style.display = 'none';   // NUEVO: oculta los controles si no hay nada que paginar
      return;
    }
    elListaHistorial.style.display = 'block';
    elHistorialVacio.style.display = 'none';

    // NUEVO: paginación — 15 por página, más reciente primero. Clampea la
    // página actual por si el total cambió (p. ej. tras borrar sesiones).
    const masRecientePrimero = historial.slice().reverse();
    const totalPaginas = Math.max(1, Math.ceil(masRecientePrimero.length / HISTORIAL_POR_PAGINA));
    if (historialPaginaActual > totalPaginas) historialPaginaActual = totalPaginas;
    if (historialPaginaActual < 1) historialPaginaActual = 1;
    const inicioPagina = (historialPaginaActual - 1) * HISTORIAL_POR_PAGINA;
    const pagina = masRecientePrimero.slice(inicioPagina, inicioPagina + HISTORIAL_POR_PAGINA);

    // NUEVO: reutiliza el nodo ya construido para esta sesión si existe
    // (p. ej. al volver a una página ya vista); solo arma nodos nuevos para
    // las entradas que todavía no se habían mostrado.
    pagina.forEach((entrada) => {
      let item = nodosHistorial.get(entrada.fecha);
      if (!item) {
        item = construirItemHistorial(entrada);
        nodosHistorial.set(entrada.fecha, item);
      } else {
        actualizarListadoEnItem(item, entrada);   // CORREGIDO: refresca lo único que puede cambiar con el tiempo
      }
      elListaHistorial.appendChild(item);
    });

    renderHistorialPaginacion(totalPaginas);
  }

  // NUEVO: controles "Anterior / Página X de Y / Siguiente". Solo re-renderiza
  // la lista (sin scroll propio ni ajeno) para no afectar la posición de vista.
  function renderHistorialPaginacion(totalPaginas) {
    const el = document.getElementById('historial-paginacion');
    el.innerHTML = '';
    el.style.display = 'flex';

    const btnAnterior = document.createElement('button');
    btnAnterior.type = 'button';
    btnAnterior.className = 'boton-mini';
    btnAnterior.textContent = 'Anterior';
    btnAnterior.disabled = historialPaginaActual <= 1;
    btnAnterior.addEventListener('click', () => { historialPaginaActual--; renderHistorial(); });

    const texto = document.createElement('span');
    texto.className = 'historial-paginacion-texto';
    texto.textContent = 'Página ' + historialPaginaActual + ' de ' + totalPaginas;

    const btnSiguiente = document.createElement('button');
    btnSiguiente.type = 'button';
    btnSiguiente.className = 'boton-mini';
    btnSiguiente.textContent = 'Siguiente';
    btnSiguiente.disabled = historialPaginaActual >= totalPaginas;
    btnSiguiente.addEventListener('click', () => { historialPaginaActual++; renderHistorial(); });

    el.appendChild(btnAnterior);
    el.appendChild(texto);
    el.appendChild(btnSiguiente);
  }

  // NUEVO: "Sesiones de otros evaluadores" (solo lectura), agrupadas por espacio
  // OPTIMIZACIÓN (lazy rendering): las filas (una por espacio de otro
  // evaluador) recién se arman cuando se abre "Sesiones de otros
  // evaluadores"; mientras tanto solo se actualiza el contador del
  // <summary>. '_otrasSesionesPendientes' guarda el último dato recibido
  // para que el constructor perezoso siempre arme con lo último.
  let _otrasSesionesPendientes = [];

  function construirHistorialOtrosLazy() {
    const otras = _otrasSesionesPendientes;
    elListaHistorialOtros.innerHTML = '';
    if (otras.length === 0) return;
    const mapa = new Map();
    otras.forEach((s) => {
      const esp = espacioDeSesion(s);
      if (!mapa.has(esp)) mapa.set(esp, []);
      mapa.get(esp).push(s);
    });
    const grupos = Array.from(mapa.entries()).map(([esp, lista]) => {
      let ultima = '';
      lista.forEach((s) => { const f = String(s.fecha || ''); if (f > ultima) ultima = f; });
      return { esp: esp, cantidad: lista.length, ultima: ultima };
    });
    grupos.sort((a, b) => (a.ultima < b.ultima ? 1 : (a.ultima > b.ultima ? -1 : 0)));

    grupos.forEach((g) => {
      const fila = document.createElement('div');
      fila.className = 'grupo-historial-otro';

      const info = document.createElement('div');
      const titulo = document.createElement('span');
      titulo.className = 'grupo-titulo';
      titulo.textContent = etiquetaEspacio(g.esp);
      const detalle = document.createElement('span');
      detalle.className = 'grupo-detalle';
      detalle.textContent = g.cantidad + ' sesión(es) · última: ' + (g.ultima ? formatearFechaHistorial(g.ultima) : '—');
      info.appendChild(titulo);
      info.appendChild(detalle);

      const btn = document.createElement('button');
      btn.className = 'boton-mini';
      btn.textContent = 'Vincular a este evaluador';
      btn.addEventListener('click', () => abrirModalVincular('sesiones', g.esp));

      fila.appendChild(info);
      fila.appendChild(btn);
      elListaHistorialOtros.appendChild(fila);
    });
  }
  const lazyHistorialOtros = prepararDetallesLazy(elDetHistorialOtros, construirHistorialOtrosLazy);

  function renderHistorialOtros(otras) {
    _otrasSesionesPendientes = otras;
    if (otras.length === 0) {
      elZonaHistorialOtros.style.display = 'none';
      elListaHistorialOtros.innerHTML = '';
      return;
    }
    elSumaHistorialOtros.textContent = 'Sesiones de otros evaluadores (' + otras.length + ')';
    elZonaHistorialOtros.style.display = 'block';
    lazyHistorialOtros.refrescar();
  }

  // MODIFICADO: se exporta el objeto COMPLETO de gamificación (puntosTotales +
  // inventario + historial) en la raíz, no solo el array de historial, para
  // poder recuperar todo al importar en otro dispositivo.
  // NUEVO: arma el objeto de gamificación (usado por el botón individual y por el backup completo)
  function construirExportGamificacion() {
    // cada sesion sale con su "espacio" ('' = sin identificar)
    return Object.assign({}, gamificacion, {
      historial: gamificacion.historial.map((s) => Object.assign({}, s, { espacio: espacioDeSesion(s) })),
    });
  }

  function exportarHistorial() {
    const salida = construirExportGamificacion();
    const blob = new Blob([JSON.stringify(salida, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'gamificacion_export.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function borrarHistorialEste() {
    const n = gamificacion.historial.filter(esSesionLocal).length;
    if (n === 0) return;
    if (!window.confirm('¿Borrar las ' + n + ' sesión(es) de ESTE evaluador («' + ESPACIO_HASH + '»)? Las sesiones de otros evaluadores, los puntos y el inventario NO se ven afectados.')) return;
    gamificacion.historial = gamificacion.historial.filter((s) => !esSesionLocal(s));
    guardarGamificacion();
    renderHistorial();
  }

  function borrarHistorialGlobal() {
    const n = gamificacion.historial.length;
    if (n === 0) return;
    if (!window.confirm('¿Borrar el historial GLOBAL (' + n + ' sesión(es) de TODOS los evaluadores, incluidas las sin identificar)? Los puntos y el inventario NO se ven afectados.')) return;
    gamificacion.historial = [];
    guardarGamificacion();
    renderHistorial();
  }

  // NUEVO: Importar historial (fusiona con lo local, ver reglas en el prompt)

  function sesionMasReciente(historial) {
    if (!historial || historial.length === 0) return null;
    let mejor = historial[0];
    for (let i = 1; i < historial.length; i++) {
      if (String(historial[i].fecha) > String(mejor.fecha)) mejor = historial[i];
    }
    return mejor;
  }

  // A5: merge sin pisar. Concatena las sesiones descartando duplicados exactos
  // (misma 'fecha'); puntosTotales = max(actual, importado); cada item de
  // inventario = max. Lo importado de otros espacios aparece en "Sesiones de
  // otros evaluadores"; lo importado con el espacio actual cuenta como local.
  function fusionarGamificacionImportada(importado) {
    const impPuntos = typeof importado.puntosTotales === 'number' ? importado.puntosTotales : 0;
    const impInv = importado.inventario || {};
    const impComodines = typeof impInv.comodines === 'number' ? impInv.comodines : 0;
    const impPistas = typeof impInv.pistasGratis === 'number' ? impInv.pistasGratis : 0;
    const impHistorial = Array.isArray(importado.historial) ? importado.historial : [];

    gamificacion.puntosTotales = Math.max(gamificacion.puntosTotales, impPuntos);
    gamificacion.inventario.comodines = Math.max(gamificacion.inventario.comodines, impComodines);
    gamificacion.inventario.pistasGratis = Math.max(gamificacion.inventario.pistasGratis, impPistas);
    gamificacion.inventario.escudosRacha = Math.max(gamificacion.inventario.escudosRacha || 0,
      typeof impInv.escudosRacha === 'number' ? impInv.escudosRacha : 0);
    gamificacion.inventario.segundasOportunidades = Math.max(gamificacion.inventario.segundasOportunidades || 0,
      typeof impInv.segundasOportunidades === 'number' ? impInv.segundasOportunidades : 0);

    const fechasLocales = new Set(gamificacion.historial.map((s) => String(s.fecha)));
    impHistorial.forEach((sesion) => {
      if (!sesion || typeof sesion !== 'object') return;
      const f = String(sesion.fecha);
      if (fechasLocales.has(f)) return;
      const copia = Object.assign({}, sesion);
      if (typeof copia.espacio !== 'string') delete copia.espacio;   // sin identificar
      gamificacion.historial.push(copia);
      fechasLocales.add(f);
    });

    // NUEVO: fusiona los resúmenes consolidados (sesiones ya podadas) por evaluador
    const impResumen = (importado.resumenPorEvaluador && typeof importado.resumenPorEvaluador === 'object') ? importado.resumenPorEvaluador : {};
    if (!gamificacion.resumenPorEvaluador) gamificacion.resumenPorEvaluador = {};
    Object.keys(impResumen).forEach((esp) => {
      const r = impResumen[esp] || {};
      const previo = gamificacion.resumenPorEvaluador[esp];
      if (!previo) {
        gamificacion.resumenPorEvaluador[esp] = {
          sesionesPodadas: Number(r.sesionesPodadas) || 0,
          mejorNota: Number(r.mejorNota) || 0,
          sumaNotas: Number(r.sumaNotas) || 0,
          tiempoTotalMs: Number(r.tiempoTotalMs) || 0,
          puntosTotales: Number(r.puntosTotales) || 0,
          ultimaFecha: typeof r.ultimaFecha === 'string' ? r.ultimaFecha : '',
        };
      } else {
        previo.sesionesPodadas += Number(r.sesionesPodadas) || 0;
        previo.mejorNota = Math.max(previo.mejorNota, Number(r.mejorNota) || 0);
        previo.sumaNotas += Number(r.sumaNotas) || 0;
        previo.tiempoTotalMs += Number(r.tiempoTotalMs) || 0;
        previo.puntosTotales += Number(r.puntosTotales) || 0;
        if (typeof r.ultimaFecha === 'string' && r.ultimaFecha > (previo.ultimaFecha || '')) previo.ultimaFecha = r.ultimaFecha;
      }
    });

    podarHistorialPorEvaluador();   // por si el historial importado supera el límite por evaluador
    guardarGamificacion();
    renderHistorial();
    actualizarBarraGamificacion();
  }

  function importarHistorial(archivo) {
    const lector = new FileReader();
    lector.onload = () => {
      let importado;
      try {
        importado = JSON.parse(lector.result);
      } catch (e) {
        window.alert('No se pudo leer el archivo: el JSON no es válido.');
        return;
      }
      if (!importado || typeof importado !== 'object') {
        window.alert('El archivo no tiene el formato esperado.');
        return;
      }
      fusionarGamificacionImportada(importado);
      window.alert('Historial importado y fusionado correctamente.');
    };
    lector.onerror = () => window.alert('No se pudo leer el archivo.');
    lector.readAsText(archivo);
  }

  document.getElementById('btn-historial-importar').addEventListener('click', () => {
    document.getElementById('input-importar-historial').click();
  });
  document.getElementById('input-importar-historial').addEventListener('change', (evento) => {
    const archivo = evento.target.files && evento.target.files[0];
    if (archivo) importarHistorial(archivo);
    evento.target.value = '';   // permite volver a elegir el mismo archivo más adelante
  });

  let historialPaginaActual = 1;   // NUEVO: página actual del historial (1-indexado)
  const HISTORIAL_POR_PAGINA = 15;

  function abrirHistorial() {
    historialPaginaActual = 1;
    mostrarPantalla('historial');
    renderHistorial();
  }

  elBtnAbrirHistorial.addEventListener('click', () => abrirHistorial());
  document.getElementById('btn-historial-volver').addEventListener('click', () => mostrarPantalla('temas'));
  elBtnHistorialExportar.addEventListener('click', () => exportarHistorial());
  elBtnHistorialBorrarEste.addEventListener('click', () => borrarHistorialEste());
  elBtnHistorialBorrarGlobal.addEventListener('click', () => borrarHistorialGlobal());

  // ============================================================
  // NUEVO: Revinculación — recuperar datos de un evaluador renombrado
  // ============================================================
  // Si se renombra el recordatorio.txt cambia ESPACIO_HASH y todo lo guardado
  // bajo el nombre viejo queda "de otro evaluador". Acá se recomputa el hash que
  // tendría cada tarjeta ACTUAL bajo el espacio viejo (hashTarjetaCon /
  // hashImagenCon) y se mueven esas entradas al hash nuevo. Es de un solo
  // sentido (sin deshacer). puntosTotales e inventario nunca se tocan.

  function tieneClave(obj, k) { return Object.prototype.hasOwnProperty.call(obj, k); }

  // Zonas con datos por tarjeta/imagen. 'explicitas' = campos donde un valor
  // vacío/false es una decisión del usuario y NO se pisa al completar.
  const ZONAS_VINCULO = {
    dificiles: {
      plural: 'difíciles', titulo: 'difíciles',
      almacen: () => dificiles, guardar: () => guardarDificiles(),
      repintar: () => renderDificilesPreview(), explicitas: ['activa'],
    },
    notas: {
      plural: 'notas', titulo: 'notas',
      almacen: () => notasLocales, guardar: () => guardarNotas(),
      repintar: () => renderNotasPreview(), explicitas: ['nota'],
    },
    razones: {
      plural: 'razones', titulo: 'razones',
      almacen: () => razonesGuardadas, guardar: () => guardarRazones(),
      repintar: () => renderRazonesPreview(), explicitas: [],
    },
    imagenes: {
      plural: 'imágenes', titulo: 'datos de imágenes',
      almacen: () => datosImagenes, guardar: () => guardarImagenesStorage(),
      repintar: () => renderImagenesPreview(), explicitas: [],
    },
  };

  // Entradas de una zona que NO corresponden a ninguna tarjeta/imagen actual
  function contarOtrasTarjetas(almacen) {
    const propios = new Set(tarjetasCompletas.map((t) => hashTarjeta(t)));
    return Object.keys(almacen).filter((h) => !propios.has(h)).length;
  }
  function contarOtrasImagenes() {
    return Object.keys(datosImagenes).filter((h) => {
      const e = datosImagenes[h] || {};
      return h !== hashImagen(e.ruta, e.indice);
    }).length;
  }
  function contarOtrasListas() {
    const vigentes = hashesVigentesActuales();
    let n = 0;
    listas.forEach((lista) => { lista.hashes.forEach((h) => { if (!vigentes.has(h)) n++; }); });
    return n;
  }
  function contarOtrasZona(zona) {
    if (zona === 'imagenes') return contarOtrasImagenes();
    if (zona === 'listas') return contarOtrasListas();
    return contarOtrasTarjetas(ZONAS_VINCULO[zona].almacen());
  }

  // Lista de {de, a}: solo tarjetas/imágenes ACTUALES cuyo hash viejo existe.
  // Las entradas que no corresponden a ninguna quedan donde están.
  function movimientosVinculo(zona, viejo) {
    const movs = [];
    const vistos = new Set();
    if (zona === 'imagenes') {
      INDICE_IMAGENES.forEach((c) => {
        const de = hashImagenCon(viejo, c.ruta, c.indice);
        const a = hashImagen(c.ruta, c.indice);
        if (de === a || vistos.has(de) || !tieneClave(datosImagenes, de)) return;
        vistos.add(de);
        movs.push({ de: de, a: a });
      });
      return movs;
    }
    const almacen = ZONAS_VINCULO[zona].almacen();
    tarjetasCompletas.forEach((t) => {
      const de = hashTarjetaCon(viejo, t);
      const a = hashTarjeta(t);
      if (de === a || vistos.has(de) || !tieneClave(almacen, de)) return;
      vistos.add(de);
      movs.push({ de: de, a: a });
    });
    return movs;
  }

  function esVacioVinculo(clave, v, explicitas) {
    if (v === undefined || v === null) return true;
    if (explicitas.indexOf(clave) !== -1) return false;
    return v === '' || v === false || (Array.isArray(v) && v.length === 0);
  }

  // Colisión: gana el existente; solo se completan campos vacíos. Las listas
  // (ej. códigos de razones) se unen sin duplicados.
  function fusionarEntradaVinculo(existente, entrante, explicitas) {
    const res = Object.assign({}, existente);
    Object.keys(entrante).forEach((k) => {
      const nuevo = entrante[k];
      if (Array.isArray(nuevo) && Array.isArray(res[k])) {
        const union = res[k].slice();
        nuevo.forEach((x) => { if (union.indexOf(x) === -1) union.push(x); });
        res[k] = union;
      } else if (esVacioVinculo(k, res[k], explicitas)) {
        res[k] = nuevo;
      }
    });
    return res;
  }

  function normalizarEspacioViejo(texto) {
    return String(texto || '').trim().replace(/[.]txt$/i, '').trim();
  }
  function mismoEspacioQueActual(viejo) {
    return viejo.toLowerCase() === ESPACIO_HASH.toLowerCase();
  }

  // Vista previa (sin modificar nada): cuánto se movería
  // §14: listas — recomputa, por cada hash de cada lista, si corresponde al hash
  // viejo de alguna tarjeta actual. Los que sí, se mueven; los que no corresponden
  // a NINGUNA tarjeta actual (ni vieja ni nueva) se descartan igual que en §12.2.
  function movimientosVinculoListas(viejo) {
    const movs = [];
    tarjetasCompletas.forEach((t) => {
      const de = hashTarjetaCon(viejo, t);
      const a = hashTarjeta(t);
      if (de === a) return;
      const usaEsteHash = listas.some((lista) => lista.hashes.indexOf(de) !== -1);
      if (usaEsteHash) movs.push({ de: de, a: a });
    });
    return movs;
  }
  function ejecutarVinculoListas(viejo) {
    const mapa = new Map(movimientosVinculoListas(viejo).map((m) => [m.de, m.a]));
    const vigentes = hashesVigentesActuales();
    let movidas = 0;
    listas = listas.filter((lista) => {
      const nuevos = [];
      let cambio = false;
      lista.hashes.forEach((h) => {
        if (mapa.has(h)) { nuevos.push(mapa.get(h)); movidas++; cambio = true; }
        else if (vigentes.has(h)) { nuevos.push(h); }
        else { cambio = true; }   // huérfano: se descarta
      });
      if (cambio) { lista.hashes = nuevos; lista.modificada = Date.now(); }
      return nuevos.length > 0;
    });
    guardarListas();
    renderListasGuardadas();
    return { movidas: movidas, sin: contarOtrasListas() };
  }

  function resumenVinculo(zona, viejo) {
    if (zona === 'sesiones') {
      const n = gamificacion.historial.filter((s) => espacioDeSesion(s) === viejo).length;
      return { cantidad: n, texto: 'Se vincularían ' + n + ' sesión(es).' };
    }
    if (zona === 'listas') {
      if (!viejo) return { cantidad: 0, texto: 'Escribí el nombre anterior del recordatorio para ver qué se movería.' };
      if (mismoEspacioQueActual(viejo)) return { cantidad: 0, texto: 'Ese es el nombre de ESTE evaluador: no hay nada que vincular.' };
      const n = movimientosVinculoListas(viejo).length;
      if (n === 0) return { cantidad: 0, texto: 'No se encontraron tarjetas de listas guardadas bajo «' + viejo + '».' };
      return { cantidad: n, texto: 'Se actualizarían ' + n + ' tarjeta(s) dentro de las listas guardadas.' };
    }
    const z = ZONAS_VINCULO[zona];
    if (!viejo) {
      return { cantidad: 0, texto: 'Escribí el nombre anterior del recordatorio para ver qué se movería.' };
    }
    if (mismoEspacioQueActual(viejo)) {
      return { cantidad: 0, texto: 'Ese es el nombre de ESTE evaluador: no hay nada que vincular.' };
    }
    const n = movimientosVinculo(zona, viejo).length;
    const resto = Math.max(0, contarOtrasZona(zona) - n);
    if (n === 0) {
      return { cantidad: 0, texto: 'No se encontró ninguna entrada de ' + z.plural + ' bajo «' + viejo + '» que corresponda a las tarjetas actuales.' };
    }
    return {
      cantidad: n,
      texto: 'Se moverían ' + n + ' ' + z.plural + '.' +
        (resto > 0 ? ' Otras ' + resto + ' entrada(s) no corresponden a ninguna tarjeta actual y NO se moverían.' : ''),
    };
  }

  // Ejecuta el movimiento y devuelve { movidas, sin }
  function ejecutarVinculo(zona, viejo) {
    if (zona === 'listas') return ejecutarVinculoListas(viejo);
    if (zona === 'sesiones') {
      let n = 0;
      gamificacion.historial.forEach((s) => {
        if (espacioDeSesion(s) === viejo) { s.espacio = ESPACIO_HASH; n++; }
      });
      guardarGamificacion();
      renderHistorial();
      return { movidas: n, sin: 0 };
    }
    const z = ZONAS_VINCULO[zona];
    const almacen = z.almacen();
    const movs = movimientosVinculo(zona, viejo);
    const tomadas = movs.map((m) => ({ a: m.a, v: almacen[m.de] }));
    movs.forEach((m) => { delete almacen[m.de]; });
    tomadas.forEach((x) => {
      almacen[x.a] = tieneClave(almacen, x.a)
        ? fusionarEntradaVinculo(almacen[x.a], x.v, z.explicitas)
        : x.v;
    });
    z.guardar();
    z.repintar();
    return { movidas: movs.length, sin: contarOtrasZona(zona) };
  }

  // ---------- "Vincular todo a este evaluador" (varias zonas a la vez) ----------
  const ICONOS_ZONA_VINCULO = { dificiles: '☆', notas: '💡', razones: '🏷', imagenes: '🖼', listas: '📋', sesiones: '🕐' };
  const ZONAS_HASH_PURGABLES = ['dificiles', 'notas', 'razones', 'imagenes'];   // las únicas donde puede quedar un residuo que valga la pena ofrecer borrar

  function zonasSeleccionadasTodo() {
    return Array.from(document.querySelectorAll('.vinc-zona-chk:checked')).map((c) => c.value);
  }

  function contarMovimientosZona(zona, viejo) {
    if (zona === 'sesiones') return gamificacion.historial.filter((s) => espacioDeSesion(s) === viejo).length;
    if (zona === 'listas') return movimientosVinculoListas(viejo).length;
    return movimientosVinculo(zona, viejo).length;
  }

  // Solo espacios que efectivamente tienen algo para mover en AL MENOS una de
  // las zonas marcadas. La única fuente de "nombres de espacios viejos" que
  // tiene la app es el historial (igual que en el flujo de una sola zona).
  function candidatosParaZonas(zonas) {
    const candidatos = new Set();
    gamificacion.historial.forEach((s) => {
      const esp = espacioDeSesion(s);
      if (esp && esp !== ESPACIO_HASH) candidatos.add(esp);
    });
    return Array.from(candidatos).filter((esp) => zonas.some((z) => contarMovimientosZona(z, esp) > 0));
  }

  function refrescarDropdownTodo() {
    elVincEspacios.innerHTML = '';
    candidatosParaZonas(zonasSeleccionadasTodo()).forEach((esp) => {
      const op = document.createElement('option');
      op.value = esp;
      elVincEspacios.appendChild(op);
    });
  }

  // Resumen compacto, ej: "Se moverían: 3 ☆, 2 💡, 0 🏷" — se listan TODAS las
  // zonas marcadas, incluidas las que dan 0.
  function resumenVinculoTodo(zonas, viejo) {
    if (zonas.length === 0) return { cantidad: 0, texto: 'Marcá al menos una zona.' };
    if (!viejo) return { cantidad: 0, texto: 'Escribí o elegí el nombre anterior del recordatorio para ver qué se movería.' };
    if (mismoEspacioQueActual(viejo)) return { cantidad: 0, texto: 'Ese es el nombre de ESTE evaluador: no hay nada que vincular.' };
    let total = 0;
    const partes = zonas.map((z) => {
      const n = contarMovimientosZona(z, viejo);
      total += n;
      return n + ' ' + ICONOS_ZONA_VINCULO[z];
    });
    return { cantidad: total, texto: 'Se moverían: ' + partes.join(', ') };
  }

  // Ejecución parcial: si una zona falla, se sigue con las demás.
  function ejecutarVinculoTodo(zonas, viejo) {
    const resultados = {};
    const errores = [];
    zonas.forEach((zona) => {
      try {
        resultados[zona] = ejecutarVinculo(zona, viejo);
      } catch (e) {
        errores.push(ICONOS_ZONA_VINCULO[zona] + ' ' + zona + ': ' + e.message);
      }
    });
    // Residuos: solo tiene sentido contarlos/ofrecer borrarlos en las zonas por
    // hash que NO se autolimpian al vincular (dificiles/notas/razones/imagenes;
    // 'listas' ya descarta sus huérfanas sola, y 'sesiones' no es borrado acá:
    // para eso ya están los botones dedicados de "Borrar historial").
    const zonasResiduo = zonas.filter((z) => ZONAS_HASH_PURGABLES.indexOf(z) !== -1 && resultados[z]);
    const residuos = zonasResiduo.reduce((acc, z) => acc + contarOtrasZona(z), 0);
    return { resultados: resultados, errores: errores, residuos: residuos, zonasResiduo: zonasResiduo };
  }

  function esHuerfanoZona(zona, h) {
    if (zona === 'imagenes') {
      const e = datosImagenes[h] || {};
      return h !== hashImagen(e.ruta, e.indice);
    }
    return !hashesVigentesActuales().has(h);
  }

  function borrarResiduosZonas(zonas) {
    let borrados = 0;
    zonas.forEach((zona) => {
      const z = ZONAS_VINCULO[zona];
      const almacen = z.almacen();
      Object.keys(almacen).forEach((h) => { if (esHuerfanoZona(zona, h)) { delete almacen[h]; borrados++; } });
      z.guardar();
      z.repintar();
    });
    return borrados;
  }

  function aceptarVinculoTodo() {
    const zonas = zonasSeleccionadasTodo();
    const viejo = espacioViejoActual();
    const { resultados, errores, residuos, zonasResiduo } = ejecutarVinculoTodo(zonas, viejo);
    cerrarModalVincular();

    const lineas = zonas.map((z) => resultados[z]
      ? ICONOS_ZONA_VINCULO[z] + ' ' + z + ': movidas ' + resultados[z].movidas
      : ICONOS_ZONA_VINCULO[z] + ' ' + z + ': ERROR');
    let msg = 'Vinculación terminada.\\n' + lineas.join('\\n');
    if (errores.length > 0) msg += '\\n\\nErrores:\\n' + errores.join('\\n');
    window.alert(msg);

    if (residuos > 0) {
      const zonasTxt = zonasResiduo.map((z) => ICONOS_ZONA_VINCULO[z]).join(' ');
      if (window.confirm('Quedaron ' + residuos + ' entrada(s) que no corresponden a ninguna tarjeta actual en las zonas vinculadas (' + zonasTxt + '). ¿Borrarlas?')) {
        const n = borrarResiduosZonas(zonasResiduo);
        window.alert('Se borraron ' + n + ' entrada(s).');
      }
    }
  }


  // ============================================================
  // NUEVO: Borrado total de los datos de UN evaluador específico
  // ============================================================

  // Nombres de "espacio" con sesiones en el historial, sin contar este
  // evaluador (no puede borrarse a sí mismo) ni las sesiones sin identificar.
  function espaciosBorrables() {
    const set = new Set();
    gamificacion.historial.forEach((s) => {
      const esp = espacioDeSesion(s);
      if (esp && esp !== ESPACIO_HASH) set.add(esp);
    });
    return Array.from(set).sort();
  }

  function renderListaBorrarEvaluador() {
    const el = document.getElementById('lista-borrar-evaluador');
    const espacios = espaciosBorrables();
    el.innerHTML = '';
    if (espacios.length === 0) {
      const p = document.createElement('p');
      p.className = 'galeria-vacia';
      p.textContent = 'No hay otros evaluadores con sesiones guardadas.';
      el.appendChild(p);
      return;
    }
    espacios.forEach((esp) => {
      const fila = document.createElement('div');
      fila.className = 'fila-borrar-evaluador';
      const nombre = document.createElement('span');
      nombre.className = 'nombre-evaluador-borrar';
      nombre.textContent = esp;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'boton-mini boton-mini-peligro';
      btn.textContent = 'Borrar';
      btn.addEventListener('click', () => borrarEvaluadorCompleto(esp));
      fila.appendChild(nombre);
      fila.appendChild(btn);
      el.appendChild(fila);
    });
  }

  document.getElementById('btn-borrar-evaluador').addEventListener('click', () => {
    const el = document.getElementById('lista-borrar-evaluador');
    if (el.style.display === 'block') { el.style.display = 'none'; return; }
    renderListaBorrarEvaluador();
    el.style.display = 'block';
  });

  // Borra TODO lo asociado a 'espacio', salvo puntosTotales e inventario
  // (son globales). Las entradas por tarjeta/imagen se identifican
  // recomputando el hash que tendría cada elemento ACTUAL bajo ese espacio
  // (misma técnica que la revinculación): una entrada de una tarjeta ya
  // editada o borrada del recordatorio no se puede identificar con certeza
  // y queda sin tocar, igual que en el resto del sistema de vinculación.
  function borrarEvaluadorCompleto(espacio) {
    if (!window.confirm('¿Borrar TODOS los datos de «' + espacio + '» (sesiones, difíciles, notas, razones, imágenes y listas)? ' +
      'No se puede deshacer. Tus puntos totales y tu inventario NO se ven afectados.')) return;

    // Sesiones
    gamificacion.historial = gamificacion.historial.filter((s) => espacioDeSesion(s) !== espacio);
    guardarGamificacion();

    // Difíciles / notas / razones: mismo hash de tarjeta en las tres zonas
    const hashesTarjetas = new Set(tarjetasCompletas.map((t) => hashTarjetaCon(espacio, t)));
    hashesTarjetas.forEach((h) => {
      delete dificiles[h];
      delete notasLocales[h];
      delete razonesGuardadas[h];
    });
    guardarDificiles();
    guardarNotas();
    guardarRazones();

    // Imágenes
    INDICE_IMAGENES.forEach((c) => {
      delete datosImagenes[hashImagenCon(espacio, c.ruta, c.indice)];
    });
    guardarImagenesStorage();

    // Listas: se quitan los hashes de ese espacio; si una lista queda vacía, se borra
    listas = listas.filter((lista) => {
      lista.hashes = lista.hashes.filter((h) => !hashesTarjetas.has(h));
      return lista.hashes.length > 0;
    });
    guardarListas();

    // Claves de backup (timestamp y snapshot) asociadas a ese espacio
    try {
      localStorage.removeItem('ultimo_backup_' + espacio);
      localStorage.removeItem('ultimo_backup_snapshot_' + espacio);
    } catch (e) {
      // sin localStorage disponible: nada que limpiar ahí
    }

    document.getElementById('lista-borrar-evaluador').style.display = 'none';
    renderHistorial();
    renderDificilesPreview();
    renderNotasPreview();
    renderRazonesPreview();
    renderImagenesPreview();
    renderListasGuardadas();
    actualizarIndicadorSync();
    window.alert('Se borraron los datos de «' + espacio + '».');
  }

  document.getElementById('btn-vincular-todo').addEventListener('click', () => {
    document.getElementById('modal-sync').classList.remove('abierto');
    abrirModalVincular('todo');
  });
  document.querySelectorAll('.vinc-zona-chk').forEach((c) => c.addEventListener('change', () => {
    refrescarDropdownTodo();
    refrescarModalVincular();
  }));

  // ---------- Modal ----------
  const elModalVincular = document.getElementById('modal-vincular');
  const elVincTitulo = document.getElementById('vinc-titulo');
  const elVincDescripcion = document.getElementById('vinc-descripcion');
  const elVincZonas = document.getElementById('vinc-zonas');
  const elVincCampoEspacio = document.getElementById('vinc-campo-espacio');
  const elVincEspacio = document.getElementById('vinc-espacio');
  const elVincEspacios = document.getElementById('vinc-espacios');
  const elVincResumen = document.getElementById('vinc-resumen');
  const elVincAviso = document.getElementById('vinc-aviso');
  const elVincConfirmar = document.getElementById('vinc-confirmar');
  const elBtnVincAceptar = document.getElementById('btn-vinc-aceptar');
  let vincZona = null;
  let vincEspacioFijo = null;   // null = el usuario lo escribe; string = viene de un grupo de sesiones

  function espacioViejoActual() {
    return vincEspacioFijo !== null ? vincEspacioFijo : normalizarEspacioViejo(elVincEspacio.value);
  }

  function refrescarModalVincular() {
    if (!vincZona) return;
    const viejo = espacioViejoActual();
    const r = vincZona === 'todo' ? resumenVinculoTodo(zonasSeleccionadasTodo(), viejo) : resumenVinculo(vincZona, viejo);
    elVincResumen.textContent = r.texto;
    const nombreViejo = viejo ? '«' + viejo + '»' : 'el nombre viejo';
    elVincAviso.textContent =
      '⚠ No se puede deshacer. Si todavía usás el evaluador con ' + nombreViejo +
      ', ESTE dejará de ver esos datos. Si otro evaluador comparte contenido con este, ' +
      'sus datos se mezclarán (si ya hay un dato para la misma tarjeta, gana el que ya está acá).';
    const coincide = elVincConfirmar.value.trim() === 'CONFIRMAR';
    elBtnVincAceptar.disabled = !(coincide && r.cantidad > 0);
  }

  function abrirModalVincular(zona, espacioFijo) {
    vincZona = zona;
    vincEspacioFijo = (typeof espacioFijo === 'string') ? espacioFijo : null;
    elVincZonas.style.display = zona === 'todo' ? 'flex' : 'none';
    if (zona === 'todo') {
      elVincTitulo.textContent = 'Vincular todo a este evaluador';
      document.querySelectorAll('.vinc-zona-chk').forEach((c) => { c.checked = true; });
    } else {
      const titulo = (zona === 'sesiones' || zona === 'listas') ? zona : ZONAS_VINCULO[zona].titulo;
      elVincTitulo.textContent = 'Vincular ' + titulo + ' a este evaluador';
    }
    if (vincEspacioFijo !== null) {
      elVincDescripcion.textContent = 'Grupo: ' + etiquetaEspacio(vincEspacioFijo) + '. Pasarán a ser de este evaluador («' + ESPACIO_HASH + '»).';
      elVincCampoEspacio.style.display = 'none';
    } else {
      elVincDescripcion.textContent = 'Indicá con qué nombre de recordatorio se guardaron estos datos. Pasarán a ser de este evaluador («' + ESPACIO_HASH + '»).';
      elVincCampoEspacio.style.display = 'block';
      elVincEspacio.value = '';
      // sugerencias: espacios de otros evaluadores que ya aparecen en el historial
      if (zona === 'todo') {
        refrescarDropdownTodo();
      } else {
        elVincEspacios.innerHTML = '';
        const vistos = new Set();
        gamificacion.historial.forEach((s) => {
          const esp = espacioDeSesion(s);
          if (!esp || esp === ESPACIO_HASH || vistos.has(esp)) return;
          vistos.add(esp);
          const op = document.createElement('option');
          op.value = esp;
          elVincEspacios.appendChild(op);
        });
      }
    }
    elVincConfirmar.value = '';
    refrescarModalVincular();
    elModalVincular.classList.add('abierto');
    (vincEspacioFijo !== null ? elVincConfirmar : elVincEspacio).focus();
  }

  function cerrarModalVincular() {
    elModalVincular.classList.remove('abierto');
    vincZona = null;
    vincEspacioFijo = null;
  }

  function aceptarVinculo() {
    if (!vincZona || elBtnVincAceptar.disabled) return;
    if (vincZona === 'todo') { aceptarVinculoTodo(); return; }
    const zona = vincZona;
    const viejo = espacioViejoActual();
    let r;
    try {
      r = ejecutarVinculo(zona, viejo);
    } catch (e) {
      cerrarModalVincular();
      window.alert('No se pudo completar la vinculación: ' + e.message);
      return;
    }
    cerrarModalVincular();
    window.alert('Vinculación terminada: movidas ' + r.movidas + ', sin corresponder ' + r.sin + '.' +
      (r.sin > 0 ? ' Las ' + r.sin + ' sin corresponder quedan sin mover (en «De otros evaluadores»).' : ''));
  }

  elVincEspacio.addEventListener('input', refrescarModalVincular);
  elVincConfirmar.addEventListener('input', refrescarModalVincular);
  elVincConfirmar.addEventListener('keydown', (e) => { if (e.key === 'Enter') aceptarVinculo(); });
  elBtnVincAceptar.addEventListener('click', () => aceptarVinculo());
  document.getElementById('btn-vinc-cancelar').addEventListener('click', () => cerrarModalVincular());

  ['dificiles', 'notas', 'razones', 'imagenes'].forEach((zona) => {
    document.getElementById('btn-vincular-' + zona).addEventListener('click', () => abrirModalVincular(zona));
  });
  document.getElementById('btn-listas-vincular').addEventListener('click', () => abrirModalVincular('listas'));

  // Estado inicial de los stats (por si se entra a 'estudio' antes de iniciar)
  actualizarBarraGamificacion();

  // ============================================================
  // NUEVO: Buscador Global (Elegir Temas / Observador / Galería)
  // ============================================================
  // Opera sobre los datos ya cargados en memoria (tarjetasCompletas,
  // rutasDisp, datos.descripciones_rutas). No modifica ningún estado de
  // evaluación, notas, difíciles, razones, cronómetro ni resaltado del
  // drawio: solo lee y, al seleccionar un resultado, reutiliza funciones
  // ya existentes (checkbox + 'change', abrirTarjetaObservador, renderGaleria).

  function debounce(fn, ms) {
    let temporizador = null;
    return function (...args) {
      clearTimeout(temporizador);
      temporizador = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  // Devuelve un fragmento con 'texto' resaltando (en <strong>) la primera
  // coincidencia de 'consultaNorm' (subcadena, ya normalizada). Compara sobre
  // el texto normalizado pero recorta sobre el texto ORIGINAL, para no perder
  // acentos/mayúsculas en lo que se muestra.
  function resaltarCoincidenciaBuscador(texto, consultaNorm) {
    const frag = document.createDocumentFragment();
    if (!texto) return frag;
    if (!consultaNorm) {
      frag.appendChild(document.createTextNode(texto));
      return frag;
    }
    const norm = normalizarPalabra(texto);
    const pos = norm.indexOf(consultaNorm);
    if (pos === -1) {
      frag.appendChild(document.createTextNode(texto));
      return frag;
    }
    const fin = pos + consultaNorm.length;
    if (pos > 0) frag.appendChild(document.createTextNode(texto.slice(0, pos)));
    const marca = document.createElement('strong');
    marca.textContent = texto.slice(pos, fin);
    frag.appendChild(marca);
    if (fin < texto.length) frag.appendChild(document.createTextNode(texto.slice(fin)));
    return frag;
  }

  // Texto (sin imágenes) de todas las rutas asociadas a una tarjeta, para
  // buscar dentro de rutasDisp[id].lineas.
  function textoRutasDeTarjeta(t) {
    return rutasValidasDe(t).map((id) => {
      const lineas = (rutasDisp[id] && rutasDisp[id].lineas) || [];
      return lineas.filter((linea) => !esImagen(linea)).join(' ');
    }).join(' ');
  }

  // Busca en primero / segundo / texto de rutas. Devuelve como mucho UN
  // resultado por tarjeta (dedupe), con el primer campo que haya coincidido
  // (prioridad: primero > segundo > rutas).
  function buscarTarjetasGlobal(consultaNorm) {
    const resultados = [];
    const candidatos = candidatosPorIndice(indiceTarjetas, consultaNorm);
    const indices = candidatos ? Array.from(candidatos).sort((a, b) => a - b) : tarjetasCompletas.map((_, i) => i);
    indices.forEach((i) => {
      const t = tarjetasCompletas[i];
      let campoTexto = null;
      if (normalizarPalabra(t.primero || '').indexOf(consultaNorm) !== -1) {
        campoTexto = t.primero;
      } else if (t.segundo && normalizarPalabra(t.segundo).indexOf(consultaNorm) !== -1) {
        campoTexto = t.segundo;
      } else {
        const textoRutas = textoRutasDeTarjeta(t);
        if (textoRutas && normalizarPalabra(textoRutas).indexOf(consultaNorm) !== -1) {
          campoTexto = textoRutas;
        }
      }
      if (campoTexto !== null) resultados.push({ idx: i, texto: campoTexto });
    });
    return resultados;
  }

  function crearItemDropdownBuscador(etiquetaTema, texto, consultaNorm, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'item-dropdown-buscador';
    if (etiquetaTema) {
      const tag = document.createElement('span');
      tag.className = 'tag-tema-buscador';
      tag.textContent = etiquetaTema;
      btn.appendChild(tag);
    }
    btn.appendChild(resaltarCoincidenciaBuscador(texto, consultaNorm));
    btn.addEventListener('click', onClick);
    return btn;
  }

  function renderDropdownVacio(elDropdown) {
    const vacio = document.createElement('div');
    vacio.className = 'dropdown-buscador-vacio';
    vacio.textContent = 'Sin resultados';
    elDropdown.appendChild(vacio);
  }

  function cerrarDropdownBuscador(elDropdown) {
    elDropdown.classList.remove('abierto');
    elDropdown.innerHTML = '';
  }

  // ---------- Elegir Temas ----------

  function seleccionarResultadoBuscadorTemas(idx) {
    // OPTIMIZACIÓN: con el subT colapsado puede que su checkbox todavía no
    // exista (construcción perezosa). Se abre/construye el subT de 'idx'
    // ANTES de buscar el input (abrirYConstruir dispara 'toggle' sincrónico).
    const nombreGrupo = indiceAGrupoTema.get(idx);
    const info = nombreGrupo ? gruposTemaInfo.get(nombreGrupo) : null;
    if (info) {
      if (info.lazyCtrl) info.lazyCtrl.abrirYConstruir();
      const input = info.cuerpoEl ? info.cuerpoEl.querySelector('input[data-idx="' + idx + '"]') : null;
      if (input) {
        input.checked = true;
        input.dispatchEvent(new Event('change'));  // reutiliza actualizarResumenGrupo/actualizarConteoSeleccion
      }
    }
    cerrarDropdownBuscador(elDropdownBuscadorTemas);
    elInputBuscadorTemas.value = '';
  }

  function renderDropdownBuscadorTemas(consulta) {
    const consultaNorm = normalizarPalabra((consulta || '').trim());
    elDropdownBuscadorTemas.innerHTML = '';
    if (consultaNorm === '') { elDropdownBuscadorTemas.classList.remove('abierto'); return; }
    const resultados = buscarTarjetasGlobal(consultaNorm).slice(0, 30);
    if (resultados.length === 0) {
      renderDropdownVacio(elDropdownBuscadorTemas);
    } else {
      resultados.forEach((r) => {
        const t = tarjetasCompletas[r.idx];
        elDropdownBuscadorTemas.appendChild(
          crearItemDropdownBuscador('[' + nombreTema(t) + ']', r.texto, consultaNorm,
            () => seleccionarResultadoBuscadorTemas(r.idx))
        );
      });
    }
    elDropdownBuscadorTemas.classList.add('abierto');
  }

  // ---------- Modo Observador ----------

  function renderDropdownBuscadorObservador(consulta) {
    const consultaNorm = normalizarPalabra((consulta || '').trim());
    elDropdownBuscadorObservador.innerHTML = '';
    if (consultaNorm === '') { elDropdownBuscadorObservador.classList.remove('abierto'); return; }
    const resultados = buscarTarjetasGlobal(consultaNorm).slice(0, 30);
    if (resultados.length === 0) {
      renderDropdownVacio(elDropdownBuscadorObservador);
    } else {
      resultados.forEach((r) => {
        const t = tarjetasCompletas[r.idx];
        elDropdownBuscadorObservador.appendChild(
          crearItemDropdownBuscador('[' + nombreTema(t) + ']', r.texto, consultaNorm, () => {
            cerrarDropdownBuscador(elDropdownBuscadorObservador);
            elInputBuscadorObservador.value = '';
            abrirTarjetaObservador(r.idx);
          })
        );
      });
    }
    elDropdownBuscadorObservador.classList.add('abierto');
  }

  // ---------- Galería ----------

  // Busca por número de ruta (ej. "ruta 5") o por la descripción de CADA
  // imagen individual (datos.descripciones_rutas ahora es una lista por
  // ruta, una posición por imagen). Dedupe por índice de imagen: una misma
  // posición de INDICE_IMAGENES no se repite en la lista de resultados.
  function buscarGaleriaGlobal(consultaNorm) {
    const resultados = [];
    const posicionesUsadas = new Set();

    // 1) Coincidencias por número de ruta: apuntan a la primera imagen de
    //    esa ruta (el número de ruta no es específico de una imagen).
    Object.keys(rutasDisp).map(Number).sort((a, b) => a - b).forEach((ruta) => {
      if (normalizarPalabra('ruta ' + ruta).indexOf(consultaNorm) === -1) return;
      const posImg = INDICE_IMAGENES.findIndex((e) => e.ruta === ruta);
      if (posImg === -1 || posicionesUsadas.has(posImg)) return;
      resultados.push({ ruta: ruta, indice: 0, texto: 'Ruta ' + ruta, posImg: posImg });
      posicionesUsadas.add(posImg);
    });

    // 2) Coincidencias por descripción de CADA imagen individual: apuntan
    //    exactamente a esa imagen (no siempre a la primera de la ruta), para
    //    que en rutas con 2+ imágenes se navegue a la que realmente coincide.
    const candidatosI = candidatosPorIndice(indiceImagenes, consultaNorm);
    const posicionesACheck = candidatosI ? Array.from(candidatosI).sort((a, b) => a - b) : INDICE_IMAGENES.map((_, i) => i);
    posicionesACheck.forEach((posImg) => {
      if (posicionesUsadas.has(posImg)) return;
      const entrada = INDICE_IMAGENES[posImg];
      const desc = descripcionDeImagen(entrada.ruta, entrada.indice);
      if (desc && normalizarPalabra(desc).indexOf(consultaNorm) !== -1) {
        resultados.push({ ruta: entrada.ruta, indice: entrada.indice, texto: desc, posImg: posImg });
        posicionesUsadas.add(posImg);
      }
    });

    return resultados;
  }

  function etiquetaResultadoGaleria(r) {
    const totalImgsRuta = INDICE_IMAGENES.filter((e) => e.ruta === r.ruta).length;
    return totalImgsRuta > 1
      ? 'Ruta ' + r.ruta + ' (img ' + (r.indice + 1) + '/' + totalImgsRuta + '):'
      : 'Ruta ' + r.ruta + ':';
  }

  function renderDropdownBuscadorGaleria(consulta) {
    const consultaNorm = normalizarPalabra((consulta || '').trim());
    elDropdownBuscadorGaleria.innerHTML = '';
    if (consultaNorm === '') { elDropdownBuscadorGaleria.classList.remove('abierto'); return; }
    const resultados = buscarGaleriaGlobal(consultaNorm).slice(0, 30);
    if (resultados.length === 0) {
      renderDropdownVacio(elDropdownBuscadorGaleria);
    } else {
      resultados.forEach((r) => {
        elDropdownBuscadorGaleria.appendChild(
          crearItemDropdownBuscador(etiquetaResultadoGaleria(r), r.texto, consultaNorm, () => {
            if (!confirmarDescartarGaleria()) return;
            cerrarDropdownBuscador(elDropdownBuscadorGaleria);
            elInputBuscadorGaleria.value = '';
            galeriaPos = r.posImg;
            resetGaleriaNotaUI();
            mostrarPantalla('galeria');
            renderGaleria();
          })
        );
      });
    }
    elDropdownBuscadorGaleria.classList.add('abierto');
  }

  // ---------- Listeners comunes (debounce, cierre por click afuera / Esc) ----------

  elInputBuscadorTemas.addEventListener('input', debounce(() => renderDropdownBuscadorTemas(elInputBuscadorTemas.value), 200));
  elInputBuscadorObservador.addEventListener('input', debounce(() => renderDropdownBuscadorObservador(elInputBuscadorObservador.value), 200));
  elInputBuscadorGaleria.addEventListener('input', debounce(() => renderDropdownBuscadorGaleria(elInputBuscadorGaleria.value), 200));
  elInputBuscadorHuerfanas.addEventListener('input', debounce(() => renderDropdownBuscadorHuerfanas(elInputBuscadorHuerfanas.value), 200));

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#zona-buscador-temas')) cerrarDropdownBuscador(elDropdownBuscadorTemas);
    if (!e.target.closest('#zona-buscador-observador')) cerrarDropdownBuscador(elDropdownBuscadorObservador);
    if (!e.target.closest('#zona-buscador-galeria')) cerrarDropdownBuscador(elDropdownBuscadorGaleria);
    if (!e.target.closest('#zona-buscador-huerfanas')) cerrarDropdownBuscador(elDropdownBuscadorHuerfanas);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    cerrarDropdownBuscador(elDropdownBuscadorTemas);
    cerrarDropdownBuscador(elDropdownBuscadorObservador);
    cerrarDropdownBuscador(elDropdownBuscadorGaleria);
    cerrarDropdownBuscador(elDropdownBuscadorHuerfanas);
  });

  // NUEVO: ESC cierra modales y vuelve atrás en pantallas secundarias.
  document.addEventListener('keydown', (evento) => {
    if (evento.key !== 'Escape') return;
    // 4) Condición de foco: no interferir mientras se escribe en un input/textarea
    const activo = document.activeElement;
    if (activo && (activo.tagName === 'INPUT' || activo.tagName === 'TEXTAREA')) return;
    // 3) Excepción obligatoria: el aviso de Repaso Espaciado nunca se cierra con ESC
    if (document.getElementById('modal-srs-aviso').classList.contains('abierto')) return;

    // 1) Modales
    if (elModalSync.classList.contains('abierto')) { elModalSync.classList.remove('abierto'); return; }
    if (elModalListas.classList.contains('abierto')) { cerrarModalListas(); return; }
    if (elModalGuardarLista.classList.contains('abierto')) { cerrarModalGuardarLista(); return; }
    if (elModalModo.classList.contains('abierto')) { cancelarModalModo(); return; }
    if (elModalVincular.classList.contains('abierto')) { cerrarModalVincular(); return; }

    // 2) Pantallas: volver atrás (la tarjeta abierta del observador, antes que su lista)
    if (elAreaTarjeta.style.display !== 'none' && elAreaTarjeta.classList.contains('observador')) { salirObservador(); return; }
    if (elPantallaHistorial.style.display !== 'none') { mostrarPantalla('temas'); return; }
    if (elPantallaTienda.style.display !== 'none') { mostrarPantalla('temas'); return; }
    if (elPantallaObservador.style.display !== 'none') { mostrarPantalla('temas'); return; }
    if (elPantallaGaleria.style.display !== 'none') { volverListaDesdeGaleria(); return; }
    if (elPantallaHuerfanas.style.display !== 'none') { volverDesdeHuerfanas(); return; }
  });

  // ---------- Preview en el modo observador: "🖼 Datos de imágenes guardados" ----------

  // NUEVO: renderizado incremental de imágenes (mismo patrón que difíciles,
  // más simple: un solo grupo/lista, sin "de otros evaluadores").
  const nodosImagenes = new Map();   // hash -> fila

  // OPTIMIZACIÓN (lazy rendering): refs fijas + el único <details> se prepara
  // UNA sola vez; su contenido se arma recién al abrirlo (o de una si ya
  // estaba abierto).
  const elListaImagenesGuardadas = document.getElementById('lista-imagenes-guardadas');
  const elDetImagenes = document.getElementById('det-imagenes');
  const elSumaImagenes = document.getElementById('suma-imagenes');
  const elInfoImagenes = document.getElementById('nota-imagenes');
  const elVaciarImagenes = document.getElementById('btn-vaciar-imagenes');

  function construirImagenesLazy() {
    Object.keys(datosImagenes).forEach((h) => {
      if (nodosImagenes.has(h)) return;   // ya está (incremental previo)
      const fila = filaImagenGuardada(h, datosImagenes[h]);
      elListaImagenesGuardadas.appendChild(fila);
      nodosImagenes.set(h, fila);
    });
  }
  const lazyImagenes = prepararDetallesLazy(elDetImagenes, construirImagenesLazy);

  function actualizarContadoresImagenes() {
    const n = Object.keys(datosImagenes).length;
    elDetImagenes.style.display = n > 0 ? 'block' : 'none';
    elSumaImagenes.textContent = 'Con nota o marcadas difícil (' + n + ')';
    document.getElementById('btn-vincular-imagenes').style.display = contarOtrasImagenes() > 0 ? 'inline-block' : 'none';
    elInfoImagenes.textContent = n === 0
      ? 'Todavía no marcaste ni anotaste ninguna imagen (galería del modo observador).'
      : 'Guardadas en este navegador: ' + n;
    elVaciarImagenes.style.display = n > 0 ? 'inline-block' : 'none';
  }

  function actualizarFilaImagenIndividual(h) {
    const filaVieja = nodosImagenes.get(h);
    if (!datosImagenes[h]) {
      if (filaVieja) { filaVieja.remove(); nodosImagenes.delete(h); }
      actualizarContadoresImagenes();
      return;
    }
    if (filaVieja) filaVieja.remove();
    const fila = filaImagenGuardada(h, datosImagenes[h]);
    elListaImagenesGuardadas.appendChild(fila);
    nodosImagenes.set(h, fila);
    actualizarContadoresImagenes();
  }

  function filaImagenGuardada(hash, entrada) {
    const div = document.createElement('div');
    div.className = 'item-nota-otra';

    const titulo = document.createElement('div');
    titulo.textContent = 'Ruta ' + entrada.ruta + ' — imagen ' + (entrada.indice + 1) +
      (entrada.dificil ? ' · ★ difícil' : '');
    div.appendChild(titulo);

    const src = srcDeImagen(entrada.ruta, entrada.indice);
    if (src) {
      const img = document.createElement('img');
      img.src = src;
      img.alt = 'Miniatura';
      img.style.maxWidth = '90px';
      img.style.maxHeight = '90px';
      img.style.borderRadius = '6px';
      img.style.margin = '6px 0';
      img.style.cursor = 'zoom-in';
      img.addEventListener('click', () => abrirLightbox(src));
      div.appendChild(img);
    }

    if (entrada.nota) {
      const nota = document.createElement('div');
      nota.className = 'bloque-version';
      nota.textContent = entrada.nota;
      div.appendChild(nota);
    }

    const acc = document.createElement('div');
    acc.className = 'acciones-nota-dif';
    acc.appendChild(botonMini('🗑 Borrar', () => {
      if (!confirm('¿Borrar la nota y la marca de difícil de esta imagen?')) return;
      delete datosImagenes[hash];
      guardarImagenesStorage();
      actualizarFilaImagenIndividual(hash);   // CORREGIDO: solo esta fila, no todo el listado
    }));
    div.appendChild(acc);
    return div;
  }

  // OPTIMIZACIÓN (lazy rendering): ya no reconstruye las filas directamente;
  // invalida el grupo y lo refresca YA si ya estaba abierto, o deja la
  // reconstrucción pendiente para cuando se abra.
  function renderImagenesPreview() {
    nodosImagenes.clear();
    elListaImagenesGuardadas.innerHTML = '';
    actualizarContadoresImagenes();
    lazyImagenes.refrescar();
  }

  // NUEVO: fusiona una lista de imágenes importada (individual o dentro de un backup completo)
  function fusionarImagenesLista(lista) {
    let nuevas = 0, actualizadas = 0, conservadas = 0, iguales = 0, invalidas = 0;
    lista.forEach((item) => {
      if (!item || typeof item.h !== 'string' || !item.h ||
          typeof item.ruta !== 'number' || typeof item.indice !== 'number') {
        invalidas++;
        return;
      }
      const entrada = {
        ruta: item.ruta,
        indice: item.indice,
        nota: typeof item.nota === 'string' ? item.nota : '',
        base: typeof item.base === 'string' ? item.base : '',
        dificil: !!item.dificil,
        t: typeof item.t === 'number' ? item.t : 0,
      };
      const local = datosImagenes[item.h];
      if (!local) {
        datosImagenes[item.h] = entrada;
        nuevas++;
      } else if ((local.nota || '') === entrada.nota && !!local.dificil === entrada.dificil &&
                 (local.base || '') === entrada.base) {
        iguales++;
      } else if (entrada.t > (local.t || 0)) {
        datosImagenes[item.h] = entrada;
        actualizadas++;
      } else {
        conservadas++;
      }
    });
    guardarImagenesStorage();
    renderImagenesPreview();
    return { nuevas: nuevas, actualizadas: actualizadas, conservadas: conservadas, iguales: iguales, invalidas: invalidas };
  }

  document.getElementById('btn-vaciar-imagenes').addEventListener('click', () => {
    if (Object.keys(datosImagenes).length === 0) return;
    if (!confirm('¿Vaciar TODOS los datos de imágenes guardados? No se puede deshacer.')) return;
    datosImagenes = {};
    guardarImagenesStorage();
    renderImagenesPreview();
  });

  // NUEVO: arma el JSON de imágenes (usado por el botón individual y por el backup completo)
  function construirExportImagenes() {
    const lista = Object.keys(datosImagenes).map((h) => ({
      h: h,
      ruta: datosImagenes[h].ruta,
      indice: datosImagenes[h].indice,
      nota: datosImagenes[h].nota || '',
      base: datosImagenes[h].base || '',
      dificil: !!datosImagenes[h].dificil,
      t: datosImagenes[h].t || 0,
    }));
    return { version: 1, imagenes: lista };
  }

  document.getElementById('btn-exportar-imagenes').addEventListener('click', () => {
    const contenido = JSON.stringify(construirExportImagenes(), null, 2);
    const blob = new Blob([contenido], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'datos_imagenes.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  document.getElementById('btn-importar-imagenes').addEventListener('click', () => {
    document.getElementById('input-importar-imagenes').click();
  });

  document.getElementById('input-importar-imagenes').addEventListener('change', (evento) => {
    const archivo = evento.target.files && evento.target.files[0];
    evento.target.value = '';
    if (!archivo) return;

    const lector = new FileReader();
    lector.onload = () => {
      const elInfo = document.getElementById('nota-imagenes');
      try {
        const obj = JSON.parse(lector.result);
        const lista = Array.isArray(obj) ? obj : (obj && Array.isArray(obj.imagenes) ? obj.imagenes : null);
        if (!lista) throw new Error('formato');

        const r = fusionarImagenesLista(lista);
        elInfo.textContent = 'Importación: ' + r.nuevas + ' nueva(s), ' + r.actualizadas +
          ' actualizada(s) por ser más recientes, ' + r.conservadas + ' conservada(s) (la tuya era más reciente), ' +
          iguales + ' ya estaban igual' + (invalidas > 0 ? ', ' + invalidas + ' inválidas' : '');
      } catch (e) {
        elInfo.textContent = 'El archivo no es un JSON válido de imágenes.';
      }
    };
    lector.readAsText(archivo);
  });

  // ---------- Listeners de la galería ----------
  document.getElementById('btn-ver-imagenes').addEventListener('click', () => abrirGaleria());
  document.getElementById('btn-galeria-volver').addEventListener('click', () => volverListaDesdeGaleria());
  document.getElementById('btn-ver-huerfanas').addEventListener('click', () => abrirHuerfanas());
  document.getElementById('btn-huerfanas-volver').addEventListener('click', () => volverDesdeHuerfanas());
  document.getElementById('btn-galeria-anterior').addEventListener('click', () => navegarGaleria(-1));
  document.getElementById('btn-galeria-siguiente').addEventListener('click', () => navegarGaleria(1));
  elBtnGaleriaDificil.addEventListener('click', () => toggleDificilImagenActual());
  elBtnGaleriaNota.addEventListener('click', () => { galeriaNotaAbierta ? cerrarGaleriaNota() : abrirGaleriaNota(); });
  document.getElementById('btn-galeria-nota-editar').addEventListener('click', () => empezarEdicionGaleriaNota());
  document.getElementById('btn-galeria-nota-guardar').addEventListener('click', () => guardarEdicionGaleriaNota());
  document.getElementById('btn-galeria-nota-cancelar').addEventListener('click', () => cancelarEdicionGaleriaNota());
  document.getElementById('btn-galeria-nota-revertir').addEventListener('click', () => revertirGaleriaNota());
  document.getElementById('btn-galeria-ver-actual').addEventListener('click', () => { galeriaVerAnterior = false; renderGaleria(); });
  document.getElementById('btn-galeria-ver-anterior').addEventListener('click', () => { galeriaVerAnterior = true; renderGaleria(); });
  elGaleriaImg.addEventListener('click', () => { if (elGaleriaImg.src) abrirLightbox(elGaleriaImg.src); });

  // ---------- MODIFICADO: modos especiales ----------

  // Modo 1: evaluación completa (todas las tarjetas, ignora los checkboxes)
  document.getElementById('btn-modo-completo').addEventListener('click', () => {
    if (tarjetasCompletas.length === 0) return;
    abrirModalSeleccionModo(modoAleatorio ? mezclar(tarjetasCompletas) : [...tarjetasCompletas]);  // MODIFICADO: gamificación
  });

  // Modo 2: repasar las difíciles activas (solo las presentes en este evaluador)
  document.getElementById('btn-modo-dificiles').addEventListener('click', () => {
    const paraRepasar = tarjetasCompletas.filter((t) => esDificil(t));
    if (paraRepasar.length === 0) return;
    abrirModalSeleccionModo(modoAleatorio ? mezclar(paraRepasar) : paraRepasar);  // MODIFICADO: gamificación
  });

  // ---------- MODIFICADO: vista previa / gestión de difíciles ----------
  // Las desmarcadas NO se borran: pasan a la sección 'Desmarcadas' y pueden
  // re-activarse desde ahí (o desde el botón ☆ durante el estudio).

  // NUEVO: renderizado incremental — mapea cada hash a su fila (nodo DOM) y a
  // qué grupo pertenece actualmente, para no reconstruir TODO el listado
  // cuando cambia una sola entrada (p. ej. tildar/destildar el checkbox).
  const nodosDificiles = new Map();   // hash -> { fila, grupo: 'propia'|'otros'|'desm' }

  // OPTIMIZACIÓN (lazy rendering): refs fijas (la pantalla "temas" nunca se
  // destruye, solo se oculta/muestra) + los dos <details> colapsados
  // ("De otros evaluadores" y "Desmarcadas") se preparan UNA sola vez con
  // prepararDetallesLazy; su contenido se arma recién al abrirlos. Como los
  // datos pueden cambiar entre visitas a la pantalla, renderDificilesPreview()
  // no reconstruye sus filas directamente: solo recalcula contadores y pide
  // un refrescar() (que reconstruye YA si ya estaban abiertas, o lo deja
  // pendiente para la próxima vez que se abran).
  const elListaDificiles = document.getElementById('lista-dificiles');
  const elListaDificilesOtros = document.getElementById('lista-dificiles-otros');
  const elDetDificilesOtros = document.getElementById('det-dificiles-otros');
  const elSumaDificilesOtros = document.getElementById('suma-dificiles-otros');
  const elListaDificilesDesm = document.getElementById('lista-dificiles-desm');
  const elDetDificilesDesm = document.getElementById('det-dificiles-desm');
  const elSumaDificilesDesm = document.getElementById('suma-dificiles-desm');
  const elNotaDificiles = document.getElementById('nota-dificiles');

  // Arma (o completa) las filas de un grupo colapsado a partir de 'dificiles'
  // actual. Idempotente: si una fila para ese hash y grupo YA existe en
  // nodosDificiles (p. ej. se agregó individualmente antes de abrir la
  // sección), no la duplica.
  function construirGrupoDificilesLazy(grupo) {
    const elLista = grupo === 'otros' ? elListaDificilesOtros : elListaDificilesDesm;
    Object.keys(dificiles).forEach((h) => {
      const g = grupoActualDificil(h);
      if (g !== grupo) return;
      const cache = nodosDificiles.get(h);
      if (cache && cache.grupo === grupo) return;   // ya está (incremental previo)
      const v = dificiles[h] || {};
      let p = v.p || '', s = v.s || '';
      const enEste = tarjetasCompletas.find((t) => hashTarjeta(t) === h);
      if (enEste) { p = enEste.primero || ''; s = enEste.segundo || ''; }
      const fila = filaDificil(h, p, s, grupo !== 'desm');
      elLista.appendChild(fila);
      nodosDificiles.set(h, { fila: fila, grupo: grupo });
    });
  }

  const lazyDificilesOtros = prepararDetallesLazy(elDetDificilesOtros, () => construirGrupoDificilesLazy('otros'));
  const lazyDificilesDesm = prepararDetallesLazy(elDetDificilesDesm, () => construirGrupoDificilesLazy('desm'));

  function grupoActualDificil(h) {
    const v = dificiles[h];
    if (!v) return null;   // ya no existe
    if (!v.activa) return 'desm';
    return tarjetasCompletas.some((t) => hashTarjeta(t) === h) ? 'propia' : 'otros';
  }

  function contenedorDeGrupoDificil(grupo) {
    if (grupo === 'propia') return document.getElementById('lista-dificiles');
    if (grupo === 'otros') return document.getElementById('lista-dificiles-otros');
    return document.getElementById('lista-dificiles-desm');
  }

  // Recalcula SOLO los contadores y textos auxiliares (barato: son conteos
  // sobre el objeto 'dificiles', no tocan el DOM de las filas).
  function actualizarContadoresDificiles() {
    const btnD = document.getElementById('btn-modo-dificiles');
    let nAqui = 0, nOtros = 0, nDesm = 0;
    Object.keys(dificiles).forEach((h) => {
      const g = grupoActualDificil(h);
      if (g === 'propia') nAqui++;
      else if (g === 'otros') nOtros++;
      else if (g === 'desm') nDesm++;
    });
    elDetDificilesOtros.style.display = nOtros > 0 ? 'block' : 'none';
    elSumaDificilesOtros.textContent = 'De otros evaluadores (' + nOtros + ')';
    elDetDificilesDesm.style.display = nDesm > 0 ? 'block' : 'none';
    elSumaDificilesDesm.textContent = 'Desmarcadas (' + nDesm + ')';
    btnD.textContent = 'Repasar difíciles (' + nAqui + ')';
    btnD.disabled = nAqui === 0;
    const total = Object.keys(dificiles).length;
    elNotaDificiles.textContent = total === 0
      ? 'Todavía no marcaste ninguna tarjeta como difícil (botón ☆ Difícil durante el estudio).'
      : 'Guardadas: ' + (total - nDesm) + ' activa(s)' + (nDesm > 0 ? ', ' + nDesm + ' desmarcada(s)' : '');
  }

  // Actualización QUIRÚRGICA de una sola entrada: si el grupo no cambió, solo
  // actualiza el checkbox de su fila ya existente; si cambió de grupo, mueve
  // esa UNA fila al contenedor correcto. Nunca reconstruye el resto de la
  // lista. Se usa para cambios de un solo elemento; las operaciones masivas
  // (importar, vincular, vaciar) siguen llamando a renderDificilesPreview().
  function actualizarFilaDificilIndividual(h) {
    const cache = nodosDificiles.get(h);
    const nuevoGrupo = grupoActualDificil(h);
    if (!nuevoGrupo) {
      if (cache) { cache.fila.remove(); nodosDificiles.delete(h); }
      actualizarContadoresDificiles();
      return;
    }
    if (cache && cache.grupo === nuevoGrupo) {
      const check = cache.fila.querySelector('input[type="checkbox"]');
      if (check) check.checked = nuevoGrupo !== 'desm';
      actualizarContadoresDificiles();
      return;
    }
    if (cache) cache.fila.remove();
    const v = dificiles[h];
    const enTarjetas = tarjetasCompletas.find((t) => hashTarjeta(t) === h);
    const p = enTarjetas ? (enTarjetas.primero || '') : (v.p || '');
    const s = enTarjetas ? (enTarjetas.segundo || '') : (v.s || '');
    const fila = filaDificil(h, p, s, nuevoGrupo !== 'desm');
    contenedorDeGrupoDificil(nuevoGrupo).appendChild(fila);
    nodosDificiles.set(h, { fila: fila, grupo: nuevoGrupo });
    actualizarContadoresDificiles();
  }

  function filaDificil(h, p, s, activa) {
    const label = document.createElement('label');
    label.className = 'item-tema-check';

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = !!activa;
    check.addEventListener('change', () => {
      if (dificiles[h]) {
        dificiles[h].activa = check.checked;
      } else {
        dificiles[h] = { p: p, s: s, activa: check.checked };
      }
      dificiles[h].t = Date.now();   // NUEVO: para saber si hay cambios sin respaldar (§8)
      guardarDificiles();
      // CORREGIDO: actualización quirúrgica de ESTA fila, no todo el listado
      actualizarFilaDificilIndividual(h);
    });

    const texto = document.createElement('span');
    texto.textContent = p || '(sin texto)';

    label.appendChild(check);
    label.appendChild(texto);

    if (s) {
      const extra = document.createElement('span');
      extra.className = 'extra-dificil';
      extra.textContent = '— ' + s;
      label.appendChild(extra);
    }

    return label;
  }

  // OPTIMIZACIÓN (lazy rendering): esta función se llama cada vez que se
  // entra a "Elegir temas". El grupo "propia" (siempre visible, sin
  // colapsar) se sigue armando entero, como antes. "De otros evaluadores" y
  // "Desmarcadas" ya NO se reconstruyen acá: solo se invalida su cache y,
  // si ya estaban abiertas, se refrescan de una (refrescar()); si están
  // cerradas, quedan pendientes para armarse recién cuando se abran.
  function renderDificilesPreview() {
    elListaDificiles.innerHTML = '';
    // Se limpian también los nodos de 'otros'/'desm' (van a reconstruirse,
    // ya sea ahora mismo si están abiertas o más tarde al abrirlas).
    Array.from(nodosDificiles.entries()).forEach(([h, info]) => {
      if (info.grupo !== 'propia') nodosDificiles.delete(h);
    });
    elListaDificilesOtros.innerHTML = '';
    elListaDificilesDesm.innerHTML = '';

    // 1) Difíciles activas de este evaluador
    let nAqui = 0;
    tarjetasCompletas.forEach((t) => {
      const h = hashTarjeta(t);
      const e = dificiles[h];
      if (!e || !e.activa) return;
      nAqui++;
      const fila = filaDificil(h, t.primero || '', t.segundo || '', true);
      elListaDificiles.appendChild(fila);
      nodosDificiles.set(h, { fila: fila, grupo: 'propia' });
    });

    actualizarContadoresDificiles();
    lazyDificilesOtros.refrescar();
    lazyDificilesDesm.refrescar();
  }

  // NUEVO: arma el JSON de difíciles (usado por el botón individual y por el backup completo)
  function construirExportDificiles() {
    // Solo se exportan las activas (las desmarcadas son un estado local)
    const lista = Object.keys(dificiles)
      .filter((h) => dificiles[h].activa)
      .map((h) => ({
        h: h,
        p: dificiles[h].p || '',
        s: dificiles[h].s || '',
        t: dificiles[h].t || 0,
      }));
    return { version: 1, tarjetas: lista };
  }

  document.getElementById('btn-exportar-dificiles').addEventListener('click', () => {
    const contenido = JSON.stringify(construirExportDificiles(), null, 2);
    const blob = new Blob([contenido], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'dificiles_tarjetas.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  document.getElementById('btn-importar-dificiles').addEventListener('click', () => {
    document.getElementById('input-importar').click();
  });

  document.getElementById('input-importar').addEventListener('change', (evento) => {
    const archivo = evento.target.files && evento.target.files[0];
    evento.target.value = '';  // permitir re-importar el mismo archivo
    if (!archivo) return;

    const lector = new FileReader();
    lector.onload = () => {
      const elNota = document.getElementById('nota-dificiles');
      try {
        const obj = JSON.parse(lector.result);
        const lista = Array.isArray(obj)
          ? obj
          : (obj && Array.isArray(obj.tarjetas) ? obj.tarjetas : null);
        if (!lista) throw new Error('formato');

        const r = fusionarDificilesLista(lista);
        elNota.textContent = 'Importación: ' + r.nuevas + ' nueva(s), ' + r.repetidas +
          ' ya estaban' + (r.invalidas > 0 ? ', ' + r.invalidas + ' inválidas' : '');
      } catch (e) {
        elNota.textContent = 'El archivo no es un JSON válido de difíciles.';
      }
    };
    lector.readAsText(archivo);
  });

  // NUEVO: fusiona una lista de difíciles importada (individual o dentro de un backup completo)
  function fusionarDificilesLista(lista) {
    let nuevas = 0, repetidas = 0, invalidas = 0;
    lista.forEach((item) => {
      if (!item || typeof item.h !== 'string' || !item.h) { invalidas++; return; }
      if (dificiles[item.h]) { repetidas++; return; }  // fusionar: no duplica (regla ya existente)
      dificiles[item.h] = {
        p: typeof item.p === 'string' ? item.p : '',
        s: typeof item.s === 'string' ? item.s : '',
        activa: true,
        t: typeof item.t === 'number' ? item.t : Date.now(),
      };
      nuevas++;
    });
    guardarDificiles();
    renderDificilesPreview();
    return { nuevas: nuevas, repetidas: repetidas, invalidas: invalidas };
  }

  document.getElementById('btn-vaciar-desm').addEventListener('click', () => {
    const hashes = Object.keys(dificiles).filter((h) => !dificiles[h].activa);
    if (hashes.length === 0) return;
    if (!confirm('¿Borrar ' + hashes.length + ' desmarcada(s)? Esta acción no se puede deshacer.')) return;
    hashes.forEach((h) => delete dificiles[h]);
    guardarDificiles();
    renderDificilesPreview();
  });

  // ---------- NUEVO: notas en la pantalla "Elegir temas" ----------

  function bloqueVersion(etiqueta, texto, esTxt, vacio) {
    const div = document.createElement('div');
    div.className = 'bloque-version' + (esTxt ? ' txt' : '') + (vacio ? ' vacio' : '');
    const etq = document.createElement('span');
    etq.className = 'etq';
    etq.textContent = etiqueta;
    div.appendChild(etq);
    div.appendChild(document.createTextNode(texto));
    return div;
  }

  function botonMini(texto, onClick) {
    const b = document.createElement('button');
    b.className = 'boton-mini';
    b.textContent = texto;
    b.addEventListener('click', onClick);
    return b;
  }

  // NUEVO: renderizado incremental de notas (mismo patrón que difíciles).
  // Dos grupos posibles: 'dif' (distinta a la del txt, de ESTE evaluador) y
  // 'otras' (de otro evaluador, o cuyo hash ya no corresponde a ninguna
  // tarjeta actual). El contenido de una fila 'dif' puede cambiar sin
  // cambiar de grupo (p. ej. conflicto -> pendiente), así que ante CUALQUIER
  // cambio se reconstruye esa única fila — sigue siendo una actualización
  // quirúrgica (una fila), nunca el listado completo.
  const nodosNotas = new Map();   // hash -> { fila, grupo: 'dif'|'otras' }

  function tarjetaPorHash(h) {
    return tarjetasCompletas.find((t) => hashTarjeta(t) === h);
  }

  function grupoActualNota(h) {
    const t = tarjetaPorHash(h);
    if (t) return estadoNotaDe(t) === 'igual' ? null : 'dif';
    return notasLocales[h] ? 'otras' : null;
  }

  function contenedorDeGrupoNota(grupo) {
    return grupo === 'dif' ? elListaNotasDif : elListaNotasOtras;
  }

  // OPTIMIZACIÓN (lazy rendering): refs fijas + los dos <details> ("Distintas
  // a las del txt" y "De otros evaluadores") se preparan UNA sola vez; su
  // contenido se arma recién al abrirlos (o de una si ya estaban abiertos).
  const elListaNotasDif = document.getElementById('lista-notas-dif');
  const elDetNotasDif = document.getElementById('det-notas-dif');
  const elSumaNotasDif = document.getElementById('suma-notas-dif');
  const elListaNotasOtras = document.getElementById('lista-notas-otras');
  const elDetNotasOtras = document.getElementById('det-notas-otras');
  const elSumaNotasOtras = document.getElementById('suma-notas-otras');
  const elInfoNotas = document.getElementById('nota-notas');

  function construirGrupoNotasLazy(grupo) {
    const elLista = contenedorDeGrupoNota(grupo);
    if (grupo === 'dif') {
      const vistos = new Set();
      tarjetasCompletas.forEach((t) => {
        const h = hashTarjeta(t);
        if (vistos.has(h)) return;
        vistos.add(h);
        if (grupoActualNota(h) !== 'dif') return;
        const cache = nodosNotas.get(h);
        if (cache && cache.grupo === 'dif') return;   // ya está (incremental previo)
        const fila = filaNotaDif(t, estadoNotaDe(t));
        elLista.appendChild(fila);
        nodosNotas.set(h, { fila: fila, grupo: 'dif' });
      });
    } else {
      Object.keys(notasLocales).forEach((h) => {
        if (grupoActualNota(h) !== 'otras') return;
        const cache = nodosNotas.get(h);
        if (cache && cache.grupo === 'otras') return;
        const fila = filaNotaOtra(h, notasLocales[h] || {});
        elLista.appendChild(fila);
        nodosNotas.set(h, { fila: fila, grupo: 'otras' });
      });
    }
  }

  const lazyNotasDif = prepararDetallesLazy(elDetNotasDif, () => construirGrupoNotasLazy('dif'));
  const lazyNotasOtras = prepararDetallesLazy(elDetNotasOtras, () => construirGrupoNotasLazy('otras'));

  function actualizarContadoresNotas() {
    const hashesAqui = new Set(tarjetasCompletas.map((t) => hashTarjeta(t)));
    let nDif = 0, nConflicto = 0, nOtras = 0;
    tarjetasCompletas.forEach((t) => {
      const est = estadoNotaDe(t);
      if (est === 'igual') return;
      nDif++;
      if (est === 'conflicto') nConflicto++;
    });
    Object.keys(notasLocales).forEach((h) => { if (!hashesAqui.has(h)) nOtras++; });
    elDetNotasDif.style.display = nDif > 0 ? 'block' : 'none';
    elSumaNotasDif.textContent = 'Distintas a las del txt (' + nDif + ')' + (nConflicto > 0 ? ' — ' + nConflicto + ' con conflicto' : '');
    elDetNotasOtras.style.display = nOtras > 0 ? 'block' : 'none';
    elSumaNotasOtras.textContent = 'De otros evaluadores (' + nOtras + ')';
    const total = Object.keys(notasLocales).length;
    elInfoNotas.textContent = total === 0
      ? 'Todavía no editaste ninguna nota (botón 💡 durante el estudio).'
      : 'Guardadas en este navegador: ' + total;
    document.getElementById('btn-vaciar-notas').style.display = total > 0 ? 'inline-block' : 'none';
  }

  function actualizarFilaNotaIndividual(h) {
    const cache = nodosNotas.get(h);
    const nuevoGrupo = grupoActualNota(h);
    if (!nuevoGrupo) {
      if (cache) { cache.fila.remove(); nodosNotas.delete(h); }
      actualizarContadoresNotas();
      return;
    }
    if (cache) cache.fila.remove();
    const fila = nuevoGrupo === 'dif'
      ? filaNotaDif(tarjetaPorHash(h), estadoNotaDe(tarjetaPorHash(h)))
      : filaNotaOtra(h, notasLocales[h] || {});
    contenedorDeGrupoNota(nuevoGrupo).appendChild(fila);
    nodosNotas.set(h, { fila: fila, grupo: nuevoGrupo });
    actualizarContadoresNotas();
  }

  function filaNotaDif(t, est) {
    const det = document.createElement('details');
    det.className = 'item-nota-dif';

    const sum = document.createElement('summary');
    const tx = document.createElement('span');
    tx.textContent = t.primero || '(sin texto)';
    const badge = document.createElement('span');
    badge.className = 'badge-estado ' + est;
    badge.textContent = est === 'pendiente' ? 'pendiente de pasar al txt' : 'el txt cambió';
    sum.appendChild(tx);
    sum.appendChild(badge);
    det.appendChild(sum);

    const e = entradaNotaDe(t) || { nota: '' };
    const mia = e.nota || '';
    const txt = notaTxtDe(t);
    det.appendChild(bloqueVersion('Mi versión', mia !== '' ? mia : '(nota borrada)', false, mia === ''));
    det.appendChild(bloqueVersion('Versión del txt', txt !== '' ? txt : '(sin nota en el txt)', true, txt === ''));

    const acc = document.createElement('div');
    acc.className = 'acciones-nota-dif';
    if (est === 'conflicto') {
      acc.appendChild(botonMini('Quedarme con la mía', () => {
        conservarMiNota(t);
        actualizarFilaNotaIndividual(hashTarjeta(t));   // CORREGIDO: solo esta fila, no todo el listado
      }));
    }
    acc.appendChild(botonMini('Usar la del txt', () => {
      if (!confirm('Se descarta tu versión y se usa la del txt. ¿Continuar?')) return;
      descartarNotaLocal(t);
      actualizarFilaNotaIndividual(hashTarjeta(t));   // CORREGIDO: solo esta fila, no todo el listado
    }));
    det.appendChild(acc);
    return det;
  }

  function filaNotaOtra(h, v) {
    const div = document.createElement('div');
    div.className = 'item-nota-otra';
    const titulo = document.createElement('div');
    titulo.textContent = (v.p || '(sin texto)') + (v.s ? ' — ' + v.s : '');
    div.appendChild(titulo);
    const nota = v.nota || '';
    div.appendChild(bloqueVersion('Mi nota', nota !== '' ? nota : '(nota borrada)', false, nota === ''));
    const acc = document.createElement('div');
    acc.className = 'acciones-nota-dif';
    acc.appendChild(botonMini('🗑 Borrar', () => {
      if (!confirm('¿Borrar esta nota guardada? No se puede deshacer.')) return;
      delete notasLocales[h];
      guardarNotas();
      actualizarFilaNotaIndividual(h);   // CORREGIDO: solo esta fila, no todo el listado
    }));
    div.appendChild(acc);
    return div;
  }

  // OPTIMIZACIÓN (lazy rendering): ya no reconstruye las filas directamente;
  // invalida ambos grupos y los refresca YA si ya estaban abiertos, o deja
  // la reconstrucción pendiente para cuando se abran.
  function renderNotasPreview() {
    nodosNotas.clear();
    elListaNotasDif.innerHTML = '';
    elListaNotasOtras.innerHTML = '';
    actualizarContadoresNotas();
    lazyNotasDif.refrescar();
    lazyNotasOtras.refrescar();
  }

  // NUEVO: mismo patrón que "Vaciar todas" de razones
  // NUEVO: fusiona una lista de notas importada (individual o dentro de un backup completo)
  // Si la misma tarjeta tiene nota distinta, gana la más reciente (campo t)
  function fusionarNotasLista(lista) {
    let nuevas = 0, actualizadas = 0, conservadas = 0, iguales = 0, invalidas = 0;
    lista.forEach((item) => {
      if (!item || typeof item.h !== 'string' || !item.h || typeof item.nota !== 'string') {
        invalidas++;
        return;
      }
      const entrada = {
        p: typeof item.p === 'string' ? item.p : '',
        s: typeof item.s === 'string' ? item.s : '',
        nota: item.nota,
        base: typeof item.base === 'string' ? item.base : '',
        t: typeof item.t === 'number' ? item.t : 0,
      };
      const local = notasLocales[item.h];
      if (!local) {
        notasLocales[item.h] = entrada;
        nuevas++;
      } else if ((local.nota || '') === entrada.nota) {
        iguales++;
      } else if (entrada.t > (local.t || 0)) {
        notasLocales[item.h] = entrada;
        actualizadas++;
      } else {
        conservadas++;
      }
    });
    guardarNotas();
    renderNotasPreview();
    return { nuevas: nuevas, actualizadas: actualizadas, conservadas: conservadas, iguales: iguales, invalidas: invalidas };
  }

  document.getElementById('btn-vaciar-notas').addEventListener('click', () => {
    if (Object.keys(notasLocales).length === 0) return;
    if (!confirm('¿Vaciar TODAS las notas guardadas? No se puede deshacer.')) return;
    notasLocales = {};
    guardarNotas();
    renderNotasPreview();
  });

  // NUEVO: arma el JSON de notas (usado por el botón individual y por el backup completo)
  function construirExportNotas() {
    const lista = Object.keys(notasLocales).map((h) => ({
      h: h,
      p: notasLocales[h].p || '',
      s: notasLocales[h].s || '',
      nota: notasLocales[h].nota || '',
      base: notasLocales[h].base || '',
      t: notasLocales[h].t || 0,
    }));
    return { version: 1, notas: lista };
  }

  document.getElementById('btn-exportar-notas').addEventListener('click', () => {
    const contenido = JSON.stringify(construirExportNotas(), null, 2);
    const blob = new Blob([contenido], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'notas_tarjetas.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  document.getElementById('btn-importar-notas').addEventListener('click', () => {
    document.getElementById('input-importar-notas').click();
  });

  document.getElementById('input-importar-notas').addEventListener('change', (evento) => {
    const archivo = evento.target.files && evento.target.files[0];
    evento.target.value = '';
    if (!archivo) return;

    const lector = new FileReader();
    lector.onload = () => {
      const elInfo = document.getElementById('nota-notas');
      try {
        const obj = JSON.parse(lector.result);
        const lista = Array.isArray(obj)
          ? obj
          : (obj && Array.isArray(obj.notas) ? obj.notas : null);
        if (!lista) throw new Error('formato');

        const r = fusionarNotasLista(lista);
        elInfo.textContent = 'Importación: ' + r.nuevas + ' nueva(s), ' + r.actualizadas +
          ' actualizada(s) por ser más recientes, ' + conservadas + ' conservada(s) (la tuya era más reciente), ' +
          iguales + ' ya estaban igual' + (invalidas > 0 ? ', ' + invalidas + ' inválidas' : '');
      } catch (e) {
        elInfo.textContent = 'El archivo no es un JSON válido de notas.';
      }
    };
    lector.readAsText(archivo);
  });

  // ---------- NUEVO: "Revisar" guardadas en "Elegir temas" (solo primero + segundo) ----------

  function filaRevisarGuardada(hash, entrada) {
    const div = document.createElement('div');
    div.className = 'item-nota-otra';
    const titulo = document.createElement('div');
    titulo.textContent = (entrada.p || '(sin texto)') + (entrada.s ? ' — ' + entrada.s : '');
    div.appendChild(titulo);
    const acc = document.createElement('div');
    acc.className = 'acciones-nota-dif';
    acc.appendChild(botonMini('🗑 Quitar', () => {
      delete revisarEnMemoria[hash];
      renderRevisarGuardadoPreview();
    }));
    div.appendChild(acc);
    return div;
  }

  function renderRevisarGuardadoPreview() {
    const el = document.getElementById('lista-revisar-guardadas');
    const elInfo = document.getElementById('nota-revisar');
    const elVaciar = document.getElementById('btn-vaciar-revisar');
    el.innerHTML = '';
    const hashes = Object.keys(revisarEnMemoria);
    hashes.forEach((h) => el.appendChild(filaRevisarGuardada(h, revisarEnMemoria[h])));
    elInfo.textContent = hashes.length === 0
      ? 'No tenés tarjetas marcadas para revisar (botón 🚩 durante el estudio).'
      : 'Marcadas en esta sesión del navegador: ' + hashes.length + ' (se pierden al recargar la página)';
    elVaciar.style.display = hashes.length > 0 ? 'inline-block' : 'none';
  }

  document.getElementById('btn-vaciar-revisar').addEventListener('click', () => {
    if (Object.keys(revisarEnMemoria).length === 0) return;
    if (!confirm('¿Vaciar todas las tarjetas para revisar? No se puede deshacer.')) return;
    revisarEnMemoria = {};
    renderRevisarGuardadoPreview();
  });

  // ---------- NUEVO: razones guardadas en "Elegir temas" ----------

  // NUEVO: renderizado incremental de razones (mismo patrón que difíciles).
  const nodosRazones = new Map();   // hash -> { fila, grupo: 'aqui'|'otras' }

  function grupoActualRazon(h) {
    if (!razonesGuardadas[h]) return null;
    return tarjetasCompletas.some((t) => hashTarjeta(t) === h) ? 'aqui' : 'otras';
  }

  function contenedorDeGrupoRazon(grupo) {
    return grupo === 'aqui' ? elListaRazonesAqui : elListaRazonesOtras;
  }

  // OPTIMIZACIÓN (lazy rendering): refs fijas + los dos <details> se preparan
  // UNA sola vez; su contenido se arma recién al abrirlos (o de una si ya
  // estaban abiertos).
  const elListaRazonesAqui = document.getElementById('lista-razones-aqui');
  const elDetRazonesAqui = document.getElementById('det-razones-aqui');
  const elSumaRazonesAqui = document.getElementById('suma-razones-aqui');
  const elListaRazonesOtras = document.getElementById('lista-razones-otras');
  const elDetRazonesOtras = document.getElementById('det-razones-otras');
  const elSumaRazonesOtras = document.getElementById('suma-razones-otras');
  const elInfoRazones = document.getElementById('nota-razones');
  const elVaciarRazones = document.getElementById('btn-vaciar-razones');

  function construirGrupoRazonesLazy(grupo) {
    const elLista = contenedorDeGrupoRazon(grupo);
    Object.keys(razonesGuardadas).forEach((h) => {
      if (grupoActualRazon(h) !== grupo) return;
      const cache = nodosRazones.get(h);
      if (cache && cache.grupo === grupo) return;   // ya está (incremental previo)
      const fila = filaRazonGuardada(h, razonesGuardadas[h]);
      elLista.appendChild(fila);
      nodosRazones.set(h, { fila: fila, grupo: grupo });
    });
  }

  const lazyRazonesAqui = prepararDetallesLazy(elDetRazonesAqui, () => construirGrupoRazonesLazy('aqui'));
  const lazyRazonesOtras = prepararDetallesLazy(elDetRazonesOtras, () => construirGrupoRazonesLazy('otras'));

  function actualizarContadoresRazones() {
    const hashesAqui = new Set(tarjetasCompletas.map((t) => hashTarjeta(t)));
    let nAqui = 0, nOtras = 0;
    Object.keys(razonesGuardadas).forEach((h) => { hashesAqui.has(h) ? nAqui++ : nOtras++; });
    elDetRazonesAqui.style.display = nAqui > 0 ? 'block' : 'none';
    elSumaRazonesAqui.textContent = 'De este evaluador (' + nAqui + ')';
    elDetRazonesOtras.style.display = nOtras > 0 ? 'block' : 'none';
    elSumaRazonesOtras.textContent = 'De otros evaluadores (' + nOtras + ')';
    const total = nAqui + nOtras;
    elInfoRazones.textContent = total === 0
      ? 'Todavía no guardaste razones (botón 🏷 durante el estudio).'
      : 'Guardadas en este navegador: ' + total;
    elVaciarRazones.style.display = total > 0 ? 'inline-block' : 'none';
  }

  function actualizarFilaRazonIndividual(h) {
    const cache = nodosRazones.get(h);
    const nuevoGrupo = grupoActualRazon(h);
    if (!nuevoGrupo) {
      if (cache) { cache.fila.remove(); nodosRazones.delete(h); }
      actualizarContadoresRazones();
      return;
    }
    if (cache) cache.fila.remove();
    const fila = filaRazonGuardada(h, razonesGuardadas[h]);
    contenedorDeGrupoRazon(nuevoGrupo).appendChild(fila);
    nodosRazones.set(h, { fila: fila, grupo: nuevoGrupo });
    actualizarContadoresRazones();
  }

  function filaRazonGuardada(hash, entrada) {
    const div = document.createElement('div');
    div.className = 'item-nota-otra';
    const titulo = document.createElement('div');
    titulo.textContent = (entrada.p || '(sin texto)') + (entrada.s ? ' — ' + entrada.s : '');
    div.appendChild(titulo);

    const chips = document.createElement('div');
    chips.className = 'fila-chips-mini';
    const codigos = entrada.razones || [];
    if (codigos.length === 0) {
      const vacio = document.createElement('span');
      vacio.className = 'chip-razon chico vacio';
      vacio.textContent = '(sin razones)';
      chips.appendChild(vacio);
    } else {
      codigos.forEach((cod) => {
        const def = MAPA_RAZONES.get(cod);
        if (!def) return;
        const chip = document.createElement('span');
        chip.className = 'chip-razon chico';
        chip.textContent = def.texto;
        chip.style.backgroundColor = hexToRgba(def.color, 0.15);
        chip.style.borderColor = def.color;
        chip.style.color = def.color;
        chips.appendChild(chip);
      });
    }
    div.appendChild(chips);

    if (entrada.estado) {
      const nombres = { si: 'Entendida', no: 'No entendida', casi: 'Casi', saltar: 'Sin marcar' };
      const est = document.createElement('div');
      est.style.fontSize = '11px';
      est.style.color = '#999';
      est.textContent = 'Estado al guardar: ' + (nombres[entrada.estado] || entrada.estado);
      div.appendChild(est);
    }

    const acc = document.createElement('div');
    acc.className = 'acciones-nota-dif';
    acc.appendChild(botonMini('🗑 Borrar', () => {
      if (!confirm('¿Borrar por completo este registro de razones?')) return;
      delete razonesGuardadas[hash];
      guardarRazones();
      actualizarFilaRazonIndividual(hash);   // CORREGIDO: solo esta fila, no todo el listado
    }));
    div.appendChild(acc);
    return div;
  }

  // OPTIMIZACIÓN (lazy rendering): ya no reconstruye las filas directamente;
  // invalida ambos grupos y los refresca YA si ya estaban abiertos, o deja
  // la reconstrucción pendiente para cuando se abran.
  function renderRazonesPreview() {
    nodosRazones.clear();
    elListaRazonesAqui.innerHTML = '';
    elListaRazonesOtras.innerHTML = '';
    actualizarContadoresRazones();
    lazyRazonesAqui.refrescar();
    lazyRazonesOtras.refrescar();
  }

  // NUEVO: fusiona una lista de razones importada (individual o dentro de un backup completo)
  function fusionarRazonesLista(lista) {
    let nuevas = 0, actualizadas = 0, conservadas = 0, iguales = 0, invalidas = 0;
    lista.forEach((item) => {
      if (!item || typeof item.h !== 'string' || !item.h || !Array.isArray(item.razones)) {
        invalidas++;
        return;
      }
      const codigosValidos = item.razones.filter((c) => MAPA_RAZONES.has(c));
      const entrada = {
        p: typeof item.p === 'string' ? item.p : '',
        s: typeof item.s === 'string' ? item.s : '',
        razones: codigosValidos,
        estado: typeof item.estado === 'string' ? item.estado : null,
        t: typeof item.t === 'number' ? item.t : 0,
      };
      const local = razonesGuardadas[item.h];
      if (!local) {
        razonesGuardadas[item.h] = entrada;
        nuevas++;
      } else if (JSON.stringify((local.razones || []).slice().sort()) === JSON.stringify(entrada.razones.slice().sort()) &&
                 (local.estado || null) === entrada.estado) {
        iguales++;
      } else if (entrada.t > (local.t || 0)) {
        razonesGuardadas[item.h] = entrada;
        actualizadas++;
      } else {
        conservadas++;
      }
    });
    guardarRazones();
    renderRazonesPreview();
    return { nuevas: nuevas, actualizadas: actualizadas, conservadas: conservadas, iguales: iguales, invalidas: invalidas };
  }

  document.getElementById('btn-vaciar-razones').addEventListener('click', () => {
    if (Object.keys(razonesGuardadas).length === 0) return;
    if (!confirm('¿Vaciar TODAS las razones guardadas? No se puede deshacer.')) return;
    razonesGuardadas = {};
    guardarRazones();
    renderRazonesPreview();
  });

  // NUEVO: arma el JSON de razones (usado por el botón individual y por el backup completo)
  function construirExportRazones() {
    const lista = Object.keys(razonesGuardadas).map((h) => ({
      h: h,
      p: razonesGuardadas[h].p || '',
      s: razonesGuardadas[h].s || '',
      razones: razonesGuardadas[h].razones || [],
      estado: razonesGuardadas[h].estado || null,
      t: razonesGuardadas[h].t || 0,
    }));
    return { version: 1, razones: lista };
  }

  document.getElementById('btn-exportar-razones').addEventListener('click', () => {
    const contenido = JSON.stringify(construirExportRazones(), null, 2);
    const blob = new Blob([contenido], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'razones_tarjetas.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  document.getElementById('btn-importar-razones').addEventListener('click', () => {
    document.getElementById('input-importar-razones').click();
  });

  document.getElementById('input-importar-razones').addEventListener('change', (evento) => {
    const archivo = evento.target.files && evento.target.files[0];
    evento.target.value = '';
    if (!archivo) return;

    const lector = new FileReader();
    lector.onload = () => {
      const elInfo = document.getElementById('nota-razones');
      try {
        const obj = JSON.parse(lector.result);
        const lista = Array.isArray(obj) ? obj : (obj && Array.isArray(obj.razones) ? obj.razones : null);
        if (!lista) throw new Error('formato');

        const r = fusionarRazonesLista(lista);
        elInfo.textContent = 'Importación: ' + r.nuevas + ' nueva(s), ' + r.actualizadas +
          ' actualizada(s) por ser más recientes, ' + r.conservadas + ' conservada(s) (la tuya era más reciente), ' +
          r.iguales + ' ya estaban igual' + (r.invalidas > 0 ? ', ' + r.invalidas + ' inválidas' : '');
      } catch (e) {
        elInfo.textContent = 'El archivo no es un JSON válido de razones.';
      }
    };
    lector.readAsText(archivo);
  });

  // MODIFICADO: renderRevisarGuardadoPreview() y renderRazonesPreview() ya se
  // llaman dentro de mostrarPantalla('temas'), así que alcanza con esto.
  document.getElementById('btn-elegir-temas').addEventListener('click', () => {
    mostrarPantalla('temas');
  });

  // ---------- Sesión de estudio ----------

  function iniciarSesion(lista) {
    modoObservador = false;
    elAreaTarjeta.classList.remove('observador');
    tarjetasSesion = lista;
    resultados = new Array(lista.length).fill(null);
    tiempos = new Array(lista.length).fill(0);
    revelado = new Array(lista.length).fill(false);
    pistaMostrada = new Array(lista.length).fill(false);  // MODIFICADO
    respuestaMostrada = new Array(lista.length).fill(false);
    escritos = new Array(lista.length).fill('');
    evaluaciones = new Array(lista.length).fill(null);
    porComprension = new Array(lista.length).fill(false);
    pausaManual = false;
    elOverlayPausa.classList.remove('abierto');
    indiceActual = 0;
    cronometroActivo = lista.length > 0;
    indiceCongelado = -1;
    resetPanelesUI();
    tiempoInicioTarjeta = Date.now();

    // NUEVO: Gamificación — reset de variables de sesión. Usa el modo ya
    // elegido en el modal (configurarModoJuego ya seteó vidasMaximas).
    puntosSesion = 0;
    rachaActual = 0;
    mejorRacha = 0;
    multiplicadorActual = 1;
    vidasActuales = vidasMaximas;
    puntosPorTarjeta = new Array(lista.length).fill(0);
    pistaGratisUsadaPorTarjeta = new Array(lista.length).fill(false);
    gameOverDisparado = false;
    historialGuardadoEstaSesion = false;
    modoZombie = false;   // NUEVO: una sesión nueva nunca arranca en modo zombie
    reintentadasSesion = new Set();
    primerIntentoFallido = new Set();
    reintentoExitoso = new Set();
    segundaPendiente = false;
    srsSesionActiva = null;   // NUEVO: toda sesión nueva arranca como sesión normal
    actualizarBadgeSrs();     // NUEVO
    limpiarAviso();
    actualizarBarraGamificacion();

    mostrarPantalla('estudio');
    iniciarCronometro();
    renderTarjeta();
  }

  function quedanPendientes() {
    return resultados.some((r) => r === null);
  }

  function acumularTiempo() {
    const ahora = Date.now();
    tiempos[indiceActual] += (ahora - tiempoInicioTarjeta);
    tiempoInicioTarjeta = ahora;
  }

  // MODIFICADO: también congela con la nota o el panel de razones abiertos, o en pausa manual
  function cronometroCongelado() {
    return pausaManual || notaAbierta || razonesAbierto || indiceCongelado === indiceActual;
  }

  function renderTarjeta() {
    if (tarjetasSesion.length === 0) {
      elAreaTarjeta.innerHTML = '<p class="sin-tarjetas">No hay tarjetas para estudiar con los temas elegidos.</p>';
      return;
    }

    // Auto-parada del cronómetro: si ya no queda nada por marcar, el tiempo
    // queda congelado (navegar por las tarjetas ya no suma tiempo).
    if (cronometroActivo && !quedanPendientes()) {
      cronometroActivo = false;
      actualizarCronometro();
    }

    const actual = tarjetasSesion[indiceActual];
    const marcada = resultados[indiceActual];
    const mostrarSegundo = revelado[indiceActual] || marcada !== null || !actual.segundo;

    elTemaTexto.textContent = nombreTema(actual);
    elPrimero.textContent = actual.primero;
    elSegundo.textContent = actual.segundo;

    // Botón "Mostrar pista" (revela el segundo, sin calificar)
    if (mostrarSegundo) {
      elBtnPista.style.display = 'none';
      elSeparador.style.display = actual.segundo ? 'block' : 'none';
      elSegundo.style.display = actual.segundo ? 'block' : 'none';
    } else {
      elBtnPista.style.display = 'inline-block';
      elSeparador.style.display = 'none';
      elSegundo.style.display = 'none';
    }

    // Botón "Mostrar respuesta" (rutas): solo si la tarjeta tiene rutas válidas
    // y todavía no se mostraron. No se deshabilita si la tarjeta ya fue marcada.
    const validas = rutasValidasDe(actual);
    elBtnRespuesta.style.display = (validas.length > 0 && !respuestaMostrada[indiceActual])
      ? 'inline-block' : 'none';

    elProgresoTexto.textContent = 'Tarjeta ' + (indiceActual + 1) + ' de ' + tarjetasSesion.length;
    const marcadas = resultados.filter((r) => r !== null).length;
    elBarraRelleno.style.width = (marcadas / tarjetasSesion.length * 100) + '%';

    // Texto de estado (puede combinar marca + respuesta mostrada)
    let textoEstado = '';
    let claseEstado = 'estado-marca';

    if (marcada === 'si') {
      textoEstado = 'Ya marcada: Entendida' + (porComprension[indiceActual] ? ' (por comprensión)' : '') +
        (respuestaMostrada[indiceActual] ? ' — respuesta mostrada' : '');
      claseEstado = 'estado-marca si';
    } else if (marcada === 'casi') {
      textoEstado = 'Casi entendida (cuenta 0.5)';
      claseEstado = 'estado-marca casi';
    } else if (marcada === 'no') {
      textoEstado = respuestaMostrada[indiceActual]
        ? 'No entendida — respuesta mostrada'
        : 'Ya marcada: No entendida';
      claseEstado = 'estado-marca no';
    } else if (marcada === 'saltar') {
      textoEstado = 'Marcada como pasada sin marcar' + (respuestaMostrada[indiceActual] ? ' — respuesta mostrada' : '');
      claseEstado = 'estado-marca saltar';
    }

    elEstadoMarca.textContent = textoEstado;
    elEstadoMarca.className = claseEstado;

    const yaMarcada = marcada !== null;

    // NUEVO: si la respuesta escrita salió "Casi" o "Mal", el tilde se reemplaza por
    // "Entendida por comprensión" (permite corregir el veredicto del evaluador)
    const evActual = evaluaciones[indiceActual];
    const permiteComprension = !modoObservador && !!evActual &&
      (evActual.resultado === 'casi' || evActual.resultado === 'mal') && marcada !== 'si';
    elBtnSi.classList.toggle('comprension', permiteComprension);
    elBtnSi.textContent = permiteComprension ? '💡✓ Entendida por comprensión' : '✓';
    elBtnSi.title = permiteComprension
      ? 'Marcar como entendida aunque la evaluación no lo diera por bien (1 punto; 0.5 si usaste la pista)'
      : 'Entendido';
    elBtnSi.disabled = yaMarcada && !permiteComprension;
    elBtnNo.disabled = yaMarcada;
    elBtnSaltar.disabled = yaMarcada;

    elBtnAnterior.disabled = indiceActual === 0;
    elBtnSiguiente.disabled = indiceActual === tarjetasSesion.length - 1;

    // MODIFICADO: Zona de respuesta escrita:
    // - sin marcar y evaluable: editable, con botón Evaluar.
    // - ya marcada con texto escrito: queda visible en solo lectura,
    //   para poder comparar con la respuesta real ('Mostrar respuesta').
    const puedeEvaluar = (marcada === null) && evaluable(actual);
    const textoGuardado = escritos[indiceActual] || '';
    const mostrarZona = puedeEvaluar || (marcada !== null && textoGuardado.trim() !== '');
    elZonaEval.style.display = mostrarZona ? 'block' : 'none';
    elFilaEval.style.display = puedeEvaluar ? 'flex' : 'none';
    if (mostrarZona) {
      elTextoEval.value = textoGuardado;
      elTextoEval.readOnly = !puedeEvaluar;
    }
    if (puedeEvaluar) {
      elBtnEvaluar.disabled = (textoGuardado.trim() === '');
    }
    elResultadoEval.style.display = evaluaciones[indiceActual] ? 'block' : 'none';
    if (evaluaciones[indiceActual]) {
      renderResultadoEval(evaluaciones[indiceActual]);
    }

    // MODIFICADO: contador de rutas asociadas (esquina de la tarjeta)
    if (validas.length > 0) {
      elBadgeRutas.textContent = 'rutas = ' + validas.length;
      elBadgeRutas.style.display = 'block';
    } else {
      elBadgeRutas.style.display = 'none';
    }

    // NUEVO: botón Pausar (solo mientras el cronómetro corre) y toggle Revisar
    elBtnPausa.style.display = (cronometroActivo && !modoObservador) ? 'inline-block' : 'none';
    elBtnRevisar.classList.toggle('activa', esRevisar(actual));
    elBtnRevisar.textContent = esRevisar(actual) ? '🚩 Para revisar' : '🚩 Revisar';

    // MODIFICADO: estado del toggle Difícil (siempre activo, aunque ya marcada)
    const esDif = esDificil(actual);
    elBtnDificil.textContent = esDif ? '★ Difícil' : '☆ Difícil';
    elBtnDificil.classList.toggle('activa', esDif);

    renderNota(actual);  // NUEVO: botón 💡 + panel de nota
    renderRazones(actual);  // NUEVO: botón 🏷 + panel de razones

    renderRutas(actual);

    // Reiniciar animación de entrada
    elTarjeta.style.animation = 'none';
    void elTarjeta.offsetWidth;
    elTarjeta.style.animation = null;
  }

  // ---------- Rutas (acordeón de respuestas) ----------

  // Agrega una línea al acordeón resaltando (negrita + color) las palabras
  // que coinciden con las claves de la tarjeta actual. Construye nodos DOM,
  // así el texto nunca se interpreta como código.
  function agregarLineaResaltada(contenedor, linea, claves) {
    const div = document.createElement('div');
    div.className = 'ruta-linea';

    const regexPalabra = /[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+/g;
    let ultimo = 0;
    for (const m of linea.matchAll(regexPalabra)) {
      if (m.index > ultimo) {
        div.appendChild(document.createTextNode(linea.slice(ultimo, m.index)));
      }
      if (claves.has(normalizarPalabra(m[0]))) {
        const fuerte = document.createElement('strong');
        fuerte.className = 'palabra-clave';
        fuerte.textContent = m[0];
        div.appendChild(fuerte);
      } else {
        div.appendChild(document.createTextNode(m[0]));
      }
      ultimo = m.index + m[0].length;
    }
    if (ultimo < linea.length) {
      div.appendChild(document.createTextNode(linea.slice(ultimo)));
    }

    contenedor.appendChild(div);
  }

  function renderRutas(tarjeta) {
    elRutasContenedor.innerHTML = '';

    if (!respuestaMostrada[indiceActual]) {
      elRutasContenedor.style.display = 'none';
      return;
    }

    const validas = rutasValidasDe(tarjeta);
    if (validas.length === 0) {
      elRutasContenedor.style.display = 'none';
      return;
    }

    elRutasContenedor.style.display = 'block';

    // Claves de ESTA tarjeta: la misma ruta resalta palabras distintas
    // según qué tarjeta la invocó.
    const claves = new Set(tarjeta.claves || []);

    validas.forEach((id) => {
      const bloque = rutasDisp[id];

      const det = document.createElement('details');
      det.className = 'ruta-acordeon';

      const sum = document.createElement('summary');
      sum.textContent = 'Ruta ' + id;

      const contenido = document.createElement('div');
      contenido.className = 'ruta-contenido';

      (bloque.lineas || []).forEach((linea) => {
        if (esImagen(linea)) {
          const img = document.createElement('img');
          img.className = 'ruta-img-thumb';
          img.src = linea.substring(4);  // quitar el prefijo 'IMG:'
          img.alt = 'Imagen de la ruta ' + id;
          contenido.appendChild(img);
        } else {
          agregarLineaResaltada(contenido, linea, claves);
        }
      });

      det.appendChild(sum);
      det.appendChild(contenido);
      elRutasContenedor.appendChild(det);
    });
  }

  // ---------- Evaluación de respuesta escrita ----------

  // ---------- Evaluación: palabras vacías, raíces y tolerancia a tipeos ----------

  const SUFIJOS_RAIZ = ['imientos','imiento','aciones','acion','siones','sion','adoras','adores','adora','ador',
    'ivas','ivos','iva','ivo','ables','ibles','able','ible','anza','ante','ente','ados','idos','ado','ido','ando','iendo'];

  // Raíz simplificada para comparar singular/plural, género y algunas derivaciones.
  // Recibe cualquier palabra (se normaliza antes: minúsculas y sin tildes).
  // Ajustes respecto de la versión original (ver notas del cambio):
  //  - el plural en -s se quita también en -as/-os/-is/-us (sistemas -> sistema, polos -> polo)
  //  - los sufijos se quitan solo si la raíz queda con 4+ letras (evita estado = estable)
  function raiz(palabra) {
    let p = normalizarPalabra(palabra).trim();
    if (p.length <= 3) return p;
    if (p.endsWith('ces') && p.length > 4) return p.slice(0, -3) + 'z';
    if (p.endsWith('es') && p.length >= 5) {
      p = p.slice(0, -2);
    } else if (p.endsWith('s') && !p.endsWith('es')) {
      p = p.slice(0, -1);
    }
    const dim = p.match(/^(.+?)(itos|itas|ito|ita|illos|illas|illo|illa|icos|icas|ico|ica)$/);
    if (dim && dim[1].length >= 3) return dim[1];
    const aum = p.match(/^(.+?)(otes|otas|ote|ota|ones|onas|on|ona)$/);
    if (aum && aum[1].length >= 3) return aum[1];
    if (p.endsWith('mente') && p.length > 7) p = p.slice(0, -5);
    for (const suf of SUFIJOS_RAIZ) {
      if (p.endsWith(suf) && p.length - suf.length >= 4) { p = p.slice(0, -suf.length); break; }
    }
    for (const suf of ['ar', 'er', 'ir']) {
      if (p.endsWith(suf) && p.length - suf.length >= 3) { p = p.slice(0, -suf.length); break; }
    }
    if (p.length >= 5 && (p.endsWith('a') || p.endsWith('o'))) p = p.slice(0, -1);
    if (p.endsWith('c')) p = p.slice(0, -1) + 'z';
    return p;
  }

  // Una palabra en -es puede ser plural de una palabra en consonante (condiciones ->
  // condicion) o de una en -e (estables -> estable, partes -> parte). Como no se
  // puede saber cuál, se guardan las dos raíces posibles y basta que coincida una.
  function variantesRaiz(palabra) {
    const p = normalizarPalabra(palabra).trim();
    const v = [raiz(p)];
    if (p.endsWith('es') && p.length >= 5) {
      const alt = raiz(p.slice(0, -1));
      if (v.indexOf(alt) === -1) v.push(alt);
    }
    return v;
  }

  // true si a y b difieren en 1 letra como máximo (de más, de menos, cambiada,
  // o dos letras seguidas invertidas)
  function distanciaMax1(a, b) {
    if (a === b) return true;
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    let i = 0;
    while (i < la && i < lb && a[i] === b[i]) i++;
    if (la === lb) {
      if (a.slice(i + 1) === b.slice(i + 1)) return true;
      if (i + 1 < la && a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2)) return true;
      return false;
    }
    if (la > lb) return a.slice(i + 1) === b.slice(i);
    return b.slice(i + 1) === a.slice(i);
  }

  function crearEntrada(w, orig) {
    return { w: w, orig: orig, v: variantesRaiz(w) };
  }

  // Palabras de un texto, sin palabras vacías y sin repetir raíz.
  // Solo se usa para EVALUAR: el texto de las rutas se muestra completo.
  function analizarTexto(texto) {
    const vistos = new Set();
    const salida = [];
    for (const m of texto.matchAll(/[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+/g)) {
      const w = normalizarPalabra(m[0]);
      if (STOPWORDS.has(w)) continue;
      const e = crearEntrada(w, m[0]);
      if (vistos.has(e.v[0])) continue;
      vistos.add(e.v[0]);
      salida.push(e);
    }
    return salida;
  }

  function compartenRaiz(a, b) {
    return a.v.some((x) => b.v.indexOf(x) !== -1);
  }

  // Tolerancia de tipeo: 1 letra de diferencia en palabras (o raíces) de 5+ letras
  function seParecen(a, b) {
    if (a.w.length >= 5 && b.w.length >= 5 && distanciaMax1(a.w, b.w)) return true;
    for (const x of a.v) {
      for (const y of b.v) {
        if (x.length >= 5 && y.length >= 5 && distanciaMax1(x, y)) return true;
      }
    }
    return false;
  }

  // Empareja uno a uno las entradas de A con las de B: primero por raíz
  // (raiz(a) === raiz(b)) y, lo que quede, por tipeo. Devuelve Map indiceA -> indiceB.
  function emparejar(A, B) {
    const usadosB = new Set();
    const pares = new Map();
    [compartenRaiz, seParecen].forEach((comparar) => {
      A.forEach((a, i) => {
        if (pares.has(i)) return;
        for (let j = 0; j < B.length; j++) {
          if (usadosB.has(j)) continue;
          if (comparar(a, B[j])) {
            usadosB.add(j);
            pares.set(i, j);
            break;
          }
        }
      });
    });
    return pares;
  }

  // Claves para evaluar: mismo criterio del resaltado, sin palabras de 1-2 letras
  function clavesEvalDe(tarjeta) {
    return (tarjeta.claves || []).filter((c) => c.length >= 3 && !STOPWORDS.has(c));
  }

  // Texto de referencia: contenido de las rutas válidas (sin imágenes);
  // si no hay rutas, el propio 'segundo'
  function textoReferenciaDe(tarjeta) {
    const partes = [];
    rutasValidasDe(tarjeta).forEach((id) => {
      (rutasDisp[id].lineas || []).forEach((linea) => {
        if (!esImagen(linea)) partes.push(linea);
      });
    });
    return partes.length > 0 ? partes.join(' ') : (tarjeta.segundo || '');
  }

  function evaluable(tarjeta) {
    return clavesEvalDe(tarjeta).length > 0 || textoReferenciaDe(tarjeta).length > 0;
  }

  // MODIFICADO: resultado de la evaluación, en este orden:
  // 1) veredicto + claves, 2) mapeo de claves, 3) coincidencia con el contenido,
  // 4) palabras en común
  function renderResultadoEval(ev) {
    elResultadoEval.innerHTML = '';

    let textoVeredicto;
    if (ev.resultado === 'bien') textoVeredicto = '✓ Bien';
    else if (ev.resultado === 'casi') textoVeredicto = '✗ Casi';
    else textoVeredicto = '✗ Mal';

    // 1) veredicto + claves
    const linea1 = document.createElement('div');
    let t1 = textoVeredicto;
    if (ev.clavesTotal > 0) {
      t1 += ' — claves: ' + ev.acertadas.length + ' de ' + ev.clavesTotal +
        ' (' + Math.round(ev.propClaves * 100) + '%)';
      if (ev.acertadas.length > 0) t1 += ' — acertadas: ' + ev.acertadas.join(', ');
      if (ev.faltaron.length > 0) t1 += ' — faltaron: ' + ev.faltaron.join(', ');
    }
    linea1.textContent = t1;
    elResultadoEval.appendChild(linea1);

    // 2) mapeo de claves: con qué palabra tuya coincidió cada clave
    if (ev.mapeo && ev.mapeo.length > 0) {
      const enc = document.createElement('div');
      enc.textContent = 'Mapeo Claves:';
      elResultadoEval.appendChild(enc);
      ev.mapeo.forEach((m) => {
        const par = document.createElement('div');
        par.style.paddingLeft = '18px';
        par.textContent = m.clave + ' ≈ ' + m.palabra;
        elResultadoEval.appendChild(par);
      });
    }

    // 3) coincidencia con el contenido
    const linea2 = document.createElement('div');
    linea2.textContent = 'coincidencia con el contenido: ' + Math.round(ev.sim * 100) + '%' +
      ' (tu respuesta: ' + ev.totalEscritas + ' distintas, contenido: ' + ev.totalReferencia + ')' +
      (ev.conPista && ev.clavesTotal > 0
        ? ' — usaste la pista: para "Bien" se exige más de ' + Math.round(SIM_CON_PISTA * 100) + '%' : '');
    elResultadoEval.appendChild(linea2);

    // 4) palabras en común
    const linea3 = document.createElement('div');
    linea3.textContent = ev.comunes + ' palabra(s) en común';
    elResultadoEval.appendChild(linea3);

    if (porComprension[indiceActual]) {
      const extra = document.createElement('div');
      extra.textContent = '💡 Marcada como entendida por comprensión';
      elResultadoEval.appendChild(extra);
    }

    if (primerIntentoFallido.has(indiceActual)) {
      const extraR = document.createElement('div');
      extraR.textContent = reintentoExitoso.has(indiceActual)
        ? '🔁 Acertada en segunda oportunidad (recompensa reducida al ' + Math.round(FACTOR_RECOMPENSA_REINTENTO * 100) + ' %)'
        : '🔁 Segunda oportunidad usada: el reintento también falló';
      elResultadoEval.appendChild(extraR);
    }

    elResultadoEval.className = 'resultado-eval ' + ev.resultado;
  }

  // ============================================================
  // NUEVO: avisos y Segunda oportunidad
  // ============================================================
  let avisoTimer = null;

  function mostrarAviso(texto) {
    elAvisoJuego.textContent = texto;
    elAvisoJuego.style.display = 'block';
    if (avisoTimer !== null) clearTimeout(avisoTimer);
    avisoTimer = setTimeout(limpiarAviso, 10000);
  }

  function limpiarAviso() {
    if (avisoTimer !== null) { clearTimeout(avisoTimer); avisoTimer = null; }
    elAvisoJuego.textContent = '';
    elAvisoJuego.style.display = 'none';
  }

  // Solo aplica a un fallo de RESPUESTA ESCRITA (evaluada por el sistema). Nada de
  // esto corre en modo observador ni zombie (no se aplica nada, no se consume nada).
  function puedeSegundaOportunidad(idx) {
    if (modoObservador || modoZombie || segundaPendiente) return false;
    if (modoJuego === 'light') return false;   // CORREGIDO: consumible de Normal/Tryhard, no de Light
    if ((gamificacion.inventario.segundasOportunidades || 0) <= 0) return false;
    return !reintentadasSesion.has(tarjetasSesion[idx]);
  }

  let segundaCallback = null;

  function pedirSegundaOportunidad(alDecidir) {
    segundaCallback = alDecidir;
    const n = gamificacion.inventario.segundasOportunidades || 0;
    document.getElementById('modal-segunda-texto').textContent =
      'Tu respuesta no alcanzó. ¿Usar una segunda oportunidad para reintentar esta tarjeta? Te quedan ' + n + '.';
    document.getElementById('modal-segunda-ayuda').textContent =
      'Si acertás el reintento cobrás el ' + Math.round(FACTOR_RECOMPENSA_REINTENTO * 100) +
      ' % de los puntos y tu racha sigue creciendo. La tarjeta igual queda para repaso.';
    elModalSegunda.classList.add('abierto');
  }

  function resolverSegundaOportunidad(acepta) {
    elModalSegunda.classList.remove('abierto');
    const cb = segundaCallback;
    segundaCallback = null;
    if (cb) cb(acepta);
  }

  document.getElementById('btn-segunda-si').addEventListener('click', () => resolverSegundaOportunidad(true));
  document.getElementById('btn-segunda-no').addEventListener('click', () => resolverSegundaOportunidad(false));

  // Acepta: consume 1, marca la tarjeta como fallada-para-repaso y pide una nueva respuesta.
  // Racha y multiplicador NO se tocan (el fallo todavía no se aplica).
  function iniciarReintento(idx) {
    gamificacion.inventario.segundasOportunidades--;
    guardarGamificacion();
    reintentadasSesion.add(tarjetasSesion[idx]);
    primerIntentoFallido.add(idx);
    elTextoEval.value = '';
    escritos[idx] = '';
    elBtnEvaluar.disabled = true;
    actualizarBarraGamificacion();
    mostrarAviso('🔁 Segunda oportunidad usada (te quedan ' + gamificacion.inventario.segundasOportunidades +
      '): volvé a responder. Tu racha sigue intacta.');
    elTextoEval.focus();
  }

  // Factor de recompensa: solo baja si esta tarjeta falló el primer intento y ahora sale bien.
  function factorReintento(idx, resultado) {
    if (modoObservador || !primerIntentoFallido.has(idx)) return 1;
    if (resultado === 'si' || resultado === 'casi') {
      reintentoExitoso.add(idx);
      return FACTOR_RECOMPENSA_REINTENTO;
    }
    return 1;
  }

  function avisoReintentoAcertado(idx) {
    const pts = puntosPorTarjeta[idx] || 0;
    mostrarAviso('🔁 Reintento acertado: recompensa reducida al ' + Math.round(FACTOR_RECOMPENSA_REINTENTO * 100) + ' %' +
      (modoJuego !== 'light' ? ' (+' + pts + ' pts)' : '') + '. Tu racha crece normal.');
  }

  function evaluarRespuesta() {
    if (pausaManual || segundaPendiente) return;
    if (resultados[indiceActual] !== null) return;
    const texto = elTextoEval.value;
    if (texto.trim() === '') return;

    const actual = tarjetasSesion[indiceActual];
    const claves = clavesEvalDe(actual);
    const escritas = analizarTexto(texto);

    // Claves acertadas: cada clave contra las palabras escritas (raíz o tipeo)
    const entradasClaves = claves.map((c) => crearEntrada(c, c));
    const paresClaves = emparejar(entradasClaves, escritas);
    const acertadas = [];
    const faltaron = [];
    const mapeo = [];
    claves.forEach((c, i) => {
      if (paresClaves.has(i)) {
        acertadas.push(c);
        mapeo.push({ clave: c, palabra: escritas[paresClaves.get(i)].orig.toLowerCase() });
      } else {
        faltaron.push(c);
      }
    });
    const propClaves = claves.length > 0 ? acertadas.length / claves.length : null;

    // Coincidencia con el contenido (Dice sobre palabras con raíz equivalente)
    const referencia = analizarTexto(textoReferenciaDe(actual));
    const comunes = emparejar(escritas, referencia).size;
    const sim = (escritas.length === 0 || referencia.length === 0)
      ? 0 : (2 * comunes) / (escritas.length + referencia.length);

    // Calificación (ver tabla): con pista, para "Bien" la coincidencia debe ser
    // MAYOR a SIM_CON_PISTA; sin pista alcanza con SIM_SIN_PISTA.
    const conPista = !!pistaMostrada[indiceActual];
    let resultado;
    if (claves.length > 0) {
      if (propClaves === 1) {
        const cumple = conPista ? (sim > SIM_CON_PISTA) : (sim >= SIM_SIN_PISTA);
        resultado = cumple ? 'bien' : 'casi';
      } else if (propClaves >= UMBRAL_CASI) {
        resultado = (sim >= SIM_ALTA && (!conPista || sim > SIM_CON_PISTA)) ? 'bien' : 'casi';
      } else {
        resultado = 'mal';
      }
    } else {
      if (sim >= SIM_ALTA) resultado = 'bien';
      else if (sim >= UMBRAL_CASI) resultado = 'casi';
      else resultado = 'mal';
    }

    const detalleEval = {
      resultado,
      acertadas,
      faltaron,
      mapeo,
      conPista,
      clavesTotal: claves.length,
      propClaves,
      sim,
      comunes,
      totalEscritas: escritas.length,
      totalReferencia: referencia.length,
    };

    // NUEVO: cascada de fallo, paso 1 — Segunda oportunidad. Se pregunta ANTES de
    // consumir y ANTES de comprometer el resultado (si acepta, nada de esto se aplica).
    if (resultado === 'mal' && puedeSegundaOportunidad(indiceActual)) {
      const idx = indiceActual;
      segundaPendiente = true;
      elTextoEval.blur();
      pedirSegundaOportunidad((acepta) => {
        segundaPendiente = false;
        if (indiceActual !== idx || resultados[idx] !== null) return;
        if (acepta) iniciarReintento(idx);
        else confirmarEvaluacion(texto, resultado, detalleEval);
      });
      return;
    }
    confirmarEvaluacion(texto, resultado, detalleEval);
  }

  // Aplica la evaluación (lógica original, sin cambios salvo el factor del reintento).
  // Si es un fallo, procesarResultadoGamificacion sigue la cascada: escudo -> fallo normal.
  function confirmarEvaluacion(texto, resultado, detalleEval) {
    escritos[indiceActual] = texto;
    evaluaciones[indiceActual] = detalleEval;

    // Auto-marca: bien -> Entendida; casi -> Casi (0.5); mal -> No entendida.
    // NO avanza: el feedback queda a la vista y el avance es manual.
    if (cronometroActivo && !cronometroCongelado()) {
      acumularTiempo();
    }
    resultados[indiceActual] = (resultado === 'bien') ? 'si' : (resultado === 'casi' ? 'casi' : 'no');
    revelado[indiceActual] = true;

    // NUEVO: Gamificación — puntos, racha, vidas y sonido (nunca en modo observador)
    if (!modoObservador) {
      procesarResultadoGamificacion(indiceActual, resultados[indiceActual], {
        conPista: !!pistaMostrada[indiceActual],
        pistaGratisUsada: !!pistaGratisUsadaPorTarjeta[indiceActual],
        comodinUsado: false,
        factor: factorReintento(indiceActual, resultados[indiceActual]),
      });
      if (reintentoExitoso.has(indiceActual)) avisoReintentoAcertado(indiceActual);
    }

    renderTarjeta();
  }

  // ---------- Lightbox ----------

  function abrirLightbox(src) {
    elLightboxImg.src = src;
    elLightbox.classList.add('abierto');
  }

  function cerrarLightbox() {
    elLightbox.classList.remove('abierto');
    elLightboxImg.src = '';
  }

  elRutasContenedor.addEventListener('click', (evento) => {
    if (evento.target.classList.contains('ruta-img-thumb')) {
      abrirLightbox(evento.target.src);
    }
  });

  elLightbox.addEventListener('click', (evento) => {
    if (evento.target === elLightbox || evento.target.classList.contains('lightbox-cerrar')) {
      cerrarLightbox();
    }
  });

  // ---------- Marcado y navegación ----------

  function marcar(resultado) {
    if (pausaManual) return;
    if (resultados[indiceActual] !== null) return;
    if (!confirmarDescartarBorrador()) return;

    // MODIFICADO (guard): si el cronómetro está congelado (nota abierta) el
    // tiempo de lectura NO se suma; solo se reinicia la referencia.
    if (!cronometroCongelado()) {
      acumularTiempo();
    } else {
      tiempoInicioTarjeta = Date.now();
    }
    resetPanelesUI();

    // NUEVO: Gamificación — si se salta con un Comodín disponible, se
    // consume del inventario PERSISTENTE (no de la sesión) y esta tarjeta
    // no penaliza puntos ni vidas.
    let comodinUsado = false;
    if (!modoObservador && !modoZombie && modoJuego !== 'light' && resultado === 'saltar' && gamificacion.inventario.comodines > 0) {
      gamificacion.inventario.comodines--;
      comodinUsado = true;
      guardarGamificacion();
    }

    resultados[indiceActual] = resultado;
    revelado[indiceActual] = true;

    // NUEVO: Gamificación — puntos, racha, vidas y sonido (nunca en modo observador)
    if (!modoObservador) {
      procesarResultadoGamificacion(indiceActual, resultado, {
        conPista: !!pistaMostrada[indiceActual],
        pistaGratisUsada: !!pistaGratisUsadaPorTarjeta[indiceActual],
        comodinUsado: comodinUsado,
        factor: factorReintento(indiceActual, resultado),
      });
      if (reintentoExitoso.has(indiceActual)) avisoReintentoAcertado(indiceActual);
    }

    if (indiceActual < tarjetasSesion.length - 1) {
      indiceActual++;
      tiempoInicioTarjeta = Date.now();
    }

    renderTarjeta();
  }

  function mostrarRespuesta() {
    if (respuestaMostrada[indiceActual]) return;

    respuestaMostrada[indiceActual] = true;

    // NUEVO: en modo observador solo se muestran las rutas (sin cronómetro ni calificación)
    if (modoObservador) {
      renderTarjeta();
      return;
    }

    // Congelar el cronómetro de TODA la sesión (solo si sigue activo):
    // el tiempo de esta tarjeta queda contado hasta este momento;
    // el tiempo de lectura no cuenta.
    // MODIFICADO: si la nota ya estaba abierta el reloj ya está parado (no se
    // acumula de nuevo), pero igual se registra el congelado por respuesta para
    // que cerrar la nota no reanude el reloj mientras se lee la respuesta.
    if (cronometroActivo && indiceCongelado !== indiceActual) {
      if (!cronometroCongelado()) acumularTiempo();
      indiceCongelado = indiceActual;
    }

    // Auto-calificar como "No entendida" solo si todavía no estaba marcada.
    if (resultados[indiceActual] === null) {
      resultados[indiceActual] = 'no';

      // NUEVO: Gamificación — "Mostrar respuesta" auto-califica como 'no',
      // así que debe disparar exactamente la misma lógica de puntos/racha/
      // vidas que un "Mal" marcado a mano (nunca en observador).
      if (!modoObservador) {
        procesarResultadoGamificacion(indiceActual, 'no', {
          conPista: !!pistaMostrada[indiceActual],
          pistaGratisUsada: !!pistaGratisUsadaPorTarjeta[indiceActual],
          comodinUsado: false,
        });
      }
    }

    // NO avanza automáticamente: el usuario se queda leyendo y avanza con "Siguiente".
    actualizarCronometro();
    renderTarjeta();
  }

  // NUEVO: "Entendida por comprensión": cuando la respuesta escrita salió Casi o Mal,
  // permite marcar la tarjeta como entendida igual. Suma 1 punto, o 0.5 si se usó la pista.
  function marcarPorComprension() {
    if (pausaManual || modoObservador) return;
    const ev = evaluaciones[indiceActual];
    if (!ev || (ev.resultado !== 'casi' && ev.resultado !== 'mal')) return;
    if (resultados[indiceActual] === 'si') return;
    const resultadoOriginal = ev.resultado;   // 'casi' o 'mal', antes de la corrección
    resultados[indiceActual] = 'si';
    porComprension[indiceActual] = true;

    // NUEVO: Gamificación — solo ajusta el DELTA de puntos (la tarjeta ya
    // había sumado puntos como 'casi'/'mal'). No se REESCRIBE el pasado de
    // racha/vidas (limitación documentada), pero sí se reproduce el sonido
    // que corresponde a este resultado corregido:
    // - 'casi' -> 'si': el 'casi' YA había sumado un acierto a la racha en su
    //   momento (no rompe racha), así que acá NO se vuelve a incrementar
    //   (evita duplicar el conteo). Solo suena "bien".
    // - 'mal' -> 'si': el 'mal' original SÍ había roto la racha a 0 sin sumar
    //   ningún acierto. Como esta tarjeta ahora cuenta como entendida, se
    //   suma como acierto DE ACÁ EN ADELANTE (no retroactivo): puede subir el
    //   multiplicador y sonar "racha", además de "bien".
    ajustarPuntosGamificacion(indiceActual, 'si', {
      conPista: !!pistaMostrada[indiceActual],
      pistaGratisUsada: !!pistaGratisUsadaPorTarjeta[indiceActual],
      comodinUsado: false,
      factor: reintentoExitoso.has(indiceActual) ? FACTOR_RECOMPENSA_REINTENTO : 1,
    });
    if (resultadoOriginal === 'mal') {
      actualizarRachaTrasResultado('si', false);  // incrementa racha + recalcula multiplicador (puede sonar "racha")
    }
    reproducirSonido('bien');
    actualizarBarraGamificacion();

    renderTarjeta();
  }

  // NUEVO: pausa manual del cronómetro. Mientras dura, el tiempo no cuenta y un
  // overlay tapa la pantalla (no se puede leer, marcar ni navegar).
  function pausarManual() {
    if (modoObservador || !cronometroActivo || pausaManual) return;
    if (!cronometroCongelado()) acumularTiempo();
    pausaManual = true;
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    elOverlayPausa.classList.add('abierto');
    actualizarCronometro();
    elBtnReanudar.focus();
  }

  function reanudarManual() {
    if (!pausaManual) return;
    pausaManual = false;
    elOverlayPausa.classList.remove('abierto');
    if (cronometroActivo && !cronometroCongelado()) tiempoInicioTarjeta = Date.now();
    actualizarCronometro();
  }

  function navegar(delta) {
    if (pausaManual) return;
    const nuevo = indiceActual + delta;
    if (nuevo < 0 || nuevo >= tarjetasSesion.length) return;
    if (!confirmarDescartarBorrador()) return;

    if (!cronometroActivo) {
      // Sesión completa: el tiempo ya está congelado, navegar no suma
      indiceActual = nuevo;
    } else if (cronometroCongelado()) {
      // Se estaba viendo una respuesta: el tiempo de esa tarjeta ya fue
      // acumulado al congelar. Solo se reanuda el reloj para la nueva tarjeta.
      indiceActual = nuevo;
      tiempoInicioTarjeta = Date.now();
    } else {
      acumularTiempo();
      indiceActual = nuevo;
    }

    resetPanelesUI();  // NUEVO: nota y razones se cierran al cambiar de tarjeta
    renderTarjeta();
  }

  // ---------- NUEVO: nota de la tarjeta (ver / editar / versiones) ----------
  // No influye en la calificación; mientras el panel está abierto (viendo o
  // editando) el cronómetro queda pausado.

  function resetNotaUI() {
    notaAbierta = false;
    editandoNota = false;
    versionNota = 'local';
  }

  // NUEVO: cierra el panel de razones sin preguntar (usar resetPanelesUI para el caso general)
  function resetRazonesUI() {
    razonesAbierto = false;
    razonesPendientes = null;
  }

  // NUEVO: cierra nota y razones juntos (cambio de tarjeta, inicio de sesión, etc.)
  function resetPanelesUI() {
    resetNotaUI();
    resetRazonesUI();
  }

  function borradorSinGuardar() {
    if (!editandoNota) return false;
    const actual = tarjetasSesion[indiceActual];
    return actual ? (elNotaEditor.value.trim() !== notaEfectivaDe(actual)) : false;
  }

  // NUEVO: cambios sin guardar en el panel de razones (checkboxes tocados sin apretar Guardar)
  function razonesSinGuardar() {
    if (!razonesAbierto || razonesPendientes === null) return false;
    const actual = tarjetasSesion[indiceActual];
    if (!actual) return false;
    const guardado = razonesDeTarjeta(actual).slice().sort().join(',');
    const pendiente = razonesPendientes.slice().sort().join(',');
    return guardado !== pendiente;
  }

  // MODIFICADO: cubre tanto la nota como el panel de razones
  function confirmarDescartarBorrador() {
    if (!borradorSinGuardar() && !razonesSinGuardar()) return true;
    return confirm('Tenés cambios sin guardar (nota y/o razones). ¿Descartarlos?');
  }

  function renderNota(actual) {
    const efectiva = notaEfectivaDe(actual);
    const est = estadoNotaDe(actual);
    const hayNota = efectiva !== '';
    const hayDif = est !== 'igual';

    // Botón 💡: siempre visible (sin nota = punteado, invita a agregar); deshabilitado
    // mientras el panel de razones está abierto (solo un panel a la vez)
    elBtnNota.style.display = 'inline-flex';
    elBtnNota.disabled = razonesAbierto;
    elBtnNota.classList.toggle('activa', notaAbierta);
    elBtnNota.classList.toggle('vacia', !hayNota && !hayDif && !notaAbierta);
    elBtnNota.classList.toggle('dif', hayDif);
    elBtnNota.title = (hayNota || hayDif)
      ? 'Ver/ocultar nota (pausa el cronómetro)'
      : 'Agregar nota (pausa el cronómetro)';

    if (!notaAbierta) {
      elPanelNota.style.display = 'none';
      return;
    }
    elPanelNota.style.display = 'block';

    if (!hayDif) versionNota = 'local';

    // Estado + selector de versión (solo cuando difieren y no se está editando)
    elPanelNotaEstado.textContent = hayDif
      ? (est === 'pendiente' ? '· editada, pendiente de pasar al txt' : '· el txt cambió desde tu edición')
      : '';
    elPanelNotaVersiones.style.display = (hayDif && !editandoNota) ? 'flex' : 'none';
    elBtnVerLocal.classList.toggle('activo', versionNota === 'local');
    elBtnVerTxt.classList.toggle('activo', versionNota === 'txt');

    if (editandoNota) {
      elPanelNotaTexto.style.display = 'none';
      elNotaEditor.style.display = 'block';
      elBtnNotaEditar.style.display = 'none';
      elBtnNotaGuardar.style.display = 'inline-block';
      elBtnNotaCancelar.style.display = 'inline-block';
      elBtnNotaBorrar.style.display = hayNota ? 'inline-block' : 'none';
    } else {
      elNotaEditor.style.display = 'none';
      elPanelNotaTexto.style.display = 'block';
      let texto, vacio;
      if (versionNota === 'txt') {
        texto = notaTxtDe(actual);
        vacio = texto === '';
        if (vacio) texto = '(sin nota en el txt)';
      } else {
        texto = efectiva;
        vacio = texto === '';
        if (vacio) texto = hayDif ? '(borraste la nota)' : '(sin nota)';
      }
      elPanelNotaTexto.textContent = texto;
      elPanelNotaTexto.classList.toggle('vacio', vacio);
      elBtnNotaEditar.style.display = versionNota === 'local' ? 'inline-block' : 'none';
      elBtnNotaEditar.textContent = hayNota ? '✎ Editar' : '＋ Agregar nota';
      elBtnNotaGuardar.style.display = 'none';
      elBtnNotaCancelar.style.display = 'none';
      elBtnNotaBorrar.style.display = 'none';
    }
  }

  function abrirNota() {
    const actual = tarjetasSesion[indiceActual];
    if (!actual) return;
    // Abrir: guardar el tiempo corrido hasta ahora (si el reloj estaba andando)
    if (cronometroActivo && !cronometroCongelado()) acumularTiempo();
    notaAbierta = true;
    versionNota = 'local';
    // Sin nota y sin diferencias: directo a escribir
    editandoNota = (notaEfectivaDe(actual) === '' && estadoNotaDe(actual) === 'igual');
    if (editandoNota) elNotaEditor.value = '';
    actualizarCronometro();
    renderTarjeta();
    if (editandoNota) elNotaEditor.focus();
  }

  function cerrarNota() {
    // Cerrar: reanudar salvo que la respuesta siga mostrada (congelado propio)
    // o que el panel de razones haya quedado abierto (no se toca acá)
    const siguePorRespuesta = indiceCongelado === indiceActual;
    resetNotaUI();
    if (cronometroActivo && !siguePorRespuesta && !razonesAbierto) tiempoInicioTarjeta = Date.now();
    actualizarCronometro();
    renderTarjeta();
  }

  function toggleNota() {
    if (tarjetasSesion.length === 0) return;
    if (notaAbierta) {
      if (!confirmarDescartarBorrador()) return;
      cerrarNota();
    } else {
      abrirNota();
    }
  }

  function empezarEdicionNota() {
    const actual = tarjetasSesion[indiceActual];
    if (!actual) return;
    editandoNota = true;
    versionNota = 'local';
    elNotaEditor.value = notaEfectivaDe(actual);
    renderTarjeta();
    elNotaEditor.focus();
  }

  function guardarEdicionNota() {
    const actual = tarjetasSesion[indiceActual];
    if (!actual || !editandoNota) return;
    guardarNotaLocal(actual, elNotaEditor.value);
    editandoNota = false;
    versionNota = 'local';
    // Nota vacía y sin diferencias con el txt: no hay nada que mostrar, se cierra
    if (notaEfectivaDe(actual) === '' && estadoNotaDe(actual) === 'igual') {
      cerrarNota();
    } else {
      renderTarjeta();
    }
  }

  function cancelarEdicionNota() {
    const actual = tarjetasSesion[indiceActual];
    if (!confirmarDescartarBorrador()) return;
    editandoNota = false;
    if (actual && notaEfectivaDe(actual) === '' && estadoNotaDe(actual) === 'igual') {
      cerrarNota();
    } else {
      renderTarjeta();
    }
  }

  function borrarNotaActual() {
    const actual = tarjetasSesion[indiceActual];
    if (!actual) return;
    if (!confirm('¿Borrar la nota de esta tarjeta?')) return;
    guardarNotaLocal(actual, '');
    editandoNota = false;
    cerrarNota();
  }

  // ---------- NUEVO: razones (por qué se marcó así) ----------
  // Igual patrón que las notas locales: persisten en localStorage, no en el txt.
  // A diferencia de la nota, no hay "versión del txt": son solo 6 códigos fijos.
  const CLAVE_RAZONES = 'razones_tarjetas_v1';
  let razonesGuardadas = cargarRazones();

  function cargarRazones() {
    try {
      const crudo = localStorage.getItem(CLAVE_RAZONES);
      if (!crudo) return {};
      const obj = JSON.parse(crudo);
      return (obj && typeof obj === 'object' && !Array.isArray(obj)) ? obj : {};
    } catch (e) {
      return {};
    }
  }

  function guardarRazonesYa() {
    try {
      localStorage.setItem(CLAVE_RAZONES, JSON.stringify(razonesGuardadas));
    } catch (e) {
      // sin localStorage: quedan solo en memoria de esta sesión
    }
  }
  const guardarRazones = crearGuardadoDebounced(guardarRazonesYa, 250);

  function razonesDeTarjeta(t) {
    const e = razonesGuardadas[hashTarjeta(t)];
    return e ? (e.razones || []) : [];
  }

  function abrirRazones() {
    const actual = tarjetasSesion[indiceActual];
    if (!actual || notaAbierta) return;   // un panel a la vez
    if (cronometroActivo && !cronometroCongelado()) acumularTiempo();
    razonesAbierto = true;
    razonesPendientes = razonesDeTarjeta(actual).slice();
    actualizarCronometro();
    renderTarjeta();
  }

  function cerrarRazones() {
    if (!confirmarDescartarBorrador()) return;
    const siguePorRespuesta = indiceCongelado === indiceActual;
    resetRazonesUI();
    if (cronometroActivo && !siguePorRespuesta && !notaAbierta) tiempoInicioTarjeta = Date.now();
    actualizarCronometro();
    renderTarjeta();
  }

  function toggleRazonCheck(codigo) {
    if (razonesPendientes === null) return;
    const i = razonesPendientes.indexOf(codigo);
    if (i === -1) razonesPendientes.push(codigo);
    else razonesPendientes.splice(i, 1);
    renderTarjeta();
  }

  // "Guardar": persiste los códigos marcados (array vacío si no hay ninguno,
  // lo que equivale a borrar el registro de esta tarjeta)
  function guardarRazonesActual() {
    const actual = tarjetasSesion[indiceActual];
    if (!actual || razonesPendientes === null) return;
    const h = hashTarjeta(actual);
    if (razonesPendientes.length === 0) {
      delete razonesGuardadas[h];
    } else {
      razonesGuardadas[h] = {
        p: actual.primero || '',
        s: actual.segundo || '',
        razones: razonesPendientes.slice(),
        estado: resultados[indiceActual],
        t: Date.now(),
      };
    }
    guardarRazones();
    const siguePorRespuesta = indiceCongelado === indiceActual;
    resetRazonesUI();
    if (cronometroActivo && !siguePorRespuesta && !notaAbierta) tiempoInicioTarjeta = Date.now();
    actualizarCronometro();
    renderTarjeta();
  }

  function renderChipsRazones(actual) {
    const guardadas = razonesDeTarjeta(actual);
    elChipsRazones.innerHTML = '';
    if (guardadas.length === 0) {
      elChipsRazones.style.display = 'none';
      return;
    }
    elChipsRazones.style.display = 'flex';
    guardadas.forEach((cod) => {
      const def = MAPA_RAZONES.get(cod);
      if (!def) return;
      const chip = document.createElement('span');
      chip.className = 'chip-razon';
      chip.textContent = def.texto;
      chip.style.backgroundColor = hexToRgba(def.color, 0.15);
      chip.style.borderColor = def.color;
      chip.style.color = def.color;
      elChipsRazones.appendChild(chip);
    });
  }

  function renderRazones(actual) {
    const guardadas = razonesDeTarjeta(actual);

    elBadgeRazones.textContent = String(guardadas.length);
    elBadgeRazones.style.display = guardadas.length > 0 ? 'inline-block' : 'none';
    elBtnRazones.classList.toggle('activa', guardadas.length > 0 || razonesAbierto);
    // deshabilitado mientras la nota está abierta (solo un panel a la vez)
    elBtnRazones.disabled = notaAbierta;

    renderChipsRazones(actual);   // chips de solo lectura (lo ya guardado)

    elPanelRazones.style.display = razonesAbierto ? 'block' : 'none';
    if (!razonesAbierto) return;

    elListaCheckRazones.innerHTML = '';
    RAZONES_DEF.forEach((r) => {
      const label = document.createElement('label');
      label.className = 'item-check-razon';
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.checked = razonesPendientes.indexOf(r.codigo) !== -1;
      chk.addEventListener('change', () => toggleRazonCheck(r.codigo));
      const dot = document.createElement('span');
      dot.className = 'dot-razon';
      dot.style.background = r.color;
      const txt = document.createElement('span');
      txt.textContent = r.texto;
      label.appendChild(chk);
      label.appendChild(dot);
      label.appendChild(txt);
      elListaCheckRazones.appendChild(label);
    });
  }

  // MODIFICADO: registrar el uso de la pista (baja la nota a 0.5 si la tarjeta
  // termina como Entendida). NUEVO: si hay Pistas Gratis en el inventario, se
  // consume 1 y esta tarjeta no penaliza puntos por usar pista (15 en vez de 8).
  elBtnPista.addEventListener('click', () => {
    revelado[indiceActual] = true;
    pistaMostrada[indiceActual] = true;
    if (!modoObservador && !modoZombie && modoJuego !== 'light' && gamificacion.inventario.pistasGratis > 0) {
      gamificacion.inventario.pistasGratis--;
      pistaGratisUsadaPorTarjeta[indiceActual] = true;
      guardarGamificacion();
      actualizarBarraGamificacion();
    }
    renderTarjeta();
  });

  elBtnRespuesta.addEventListener('click', () => mostrarRespuesta());
  elBtnNota.addEventListener('click', () => toggleNota());
  elBtnNotaEditar.addEventListener('click', () => empezarEdicionNota());
  elBtnNotaGuardar.addEventListener('click', () => guardarEdicionNota());
  elBtnNotaCancelar.addEventListener('click', () => cancelarEdicionNota());
  elBtnNotaBorrar.addEventListener('click', () => borrarNotaActual());
  elBtnRazones.addEventListener('click', () => { if (razonesAbierto) cerrarRazones(); else abrirRazones(); });
  document.getElementById('btn-razones-guardar').addEventListener('click', () => guardarRazonesActual());
  document.getElementById('btn-razones-cerrar').addEventListener('click', () => cerrarRazones());
  elBtnVerLocal.addEventListener('click', () => { versionNota = 'local'; renderTarjeta(); });
  elBtnVerTxt.addEventListener('click', () => { versionNota = 'txt'; renderTarjeta(); });
  elNotaEditor.addEventListener('keydown', (evento) => {
    // Ctrl+Enter (o Cmd+Enter) guarda; Enter solo es salto de línea
    if (evento.key === 'Enter' && (evento.ctrlKey || evento.metaKey)) {
      evento.preventDefault();
      guardarEdicionNota();
    }
  });

  // MODIFICADO: toggle de difícil (persiste entre sesiones)
  elBtnDificil.addEventListener('click', () => toggleDificilActual());

  elBtnEvaluar.addEventListener('click', () => evaluarRespuesta());
  elTextoEval.addEventListener('input', () => {
    escritos[indiceActual] = elTextoEval.value;
    elBtnEvaluar.disabled = (elTextoEval.value.trim() === '');
  });
  elTextoEval.addEventListener('keydown', (evento) => {
    // Enter evalúa; Shift+Enter = salto de línea
    if (evento.key === 'Enter' && !evento.shiftKey) {
      evento.preventDefault();
      if (!elBtnEvaluar.disabled) evaluarRespuesta();
    }
  });

  elBtnSi.addEventListener('click', () => {
    if (elBtnSi.classList.contains('comprension')) marcarPorComprension();
    else marcar('si');
  });
  elBtnPausa.addEventListener('click', () => pausarManual());
  elBtnReanudar.addEventListener('click', () => reanudarManual());
  elBtnRevisar.addEventListener('click', () => toggleRevisarActual());
  elBtnNo.addEventListener('click', () => marcar('no'));
  elBtnSaltar.addEventListener('click', () => marcar('saltar'));
  elBtnAnterior.addEventListener('click', () => navegar(-1));
  elBtnSiguiente.addEventListener('click', () => navegar(1));
  elBtnFinalizar.addEventListener('click', () => finalizarSesion());

  document.addEventListener('keydown', (evento) => {
    if (elModalSegunda.classList.contains('abierto')) return;   // NUEVO: dialogo de segunda oportunidad abierto
    if (elModalContinuar.classList.contains('abierto')) {
      if (evento.key === 'Escape') cerrarModalContinuar();
      return;
    }
    if (pausaManual) {
      if (evento.key === 'Escape') reanudarManual();
      return;
    }
    if (elLightbox.classList.contains('abierto')) {
      if (evento.key === 'Escape') cerrarLightbox();
      return;
    }
    // NUEVO: Esc sale del cuadro de texto para poder usar las flechas
    if (evento.key === 'Escape' && (evento.target === elTextoEval || evento.target === elNotaEditor || evento.target === elGaleriaNotaEditor)) {
      evento.target.blur();
      return;
    }
    if (evento.target === elTextoEval || evento.target === elNotaEditor || evento.target === elGaleriaNotaEditor) return;  // no navegar mientras se escribe
    // NUEVO: flechas también en la galería de imágenes (Anterior/Siguiente)
    if (elPantallaGaleria.style.display !== 'none') {
      if (evento.key === 'ArrowLeft') navegarGaleria(-1);
      if (evento.key === 'ArrowRight') navegarGaleria(1);
      return;
    }
    if (elAreaTarjeta.style.display === 'none') return;
    if (evento.key === 'ArrowLeft') navegar(-1);
    if (evento.key === 'ArrowRight') navegar(1);
  });

  // ---------- Cronómetro ----------

  function actualizarCronometro() {
    const congelado = cronometroCongelado();
    const parado = !cronometroActivo;   // sesión completa: nada más por marcar
    const ahora = Date.now();
    const enCurso = (congelado || parado) ? 0 : (ahora - tiempoInicioTarjeta);
    const totalMs = tiempos.reduce((a, b) => a + b, 0) + enCurso;
    const icono = parado ? '⏹ ' : (congelado ? '⏸ ' : '⏱ ');
    elCronometro.textContent = icono + formatearTiempo(totalMs);
    elCronometro.className = (parado || congelado) ? 'cronometro congelado' : 'cronometro';
  }

  function iniciarCronometro() {
    detenerCronometro();
    actualizarCronometro();
    cronometroIntervalId = setInterval(actualizarCronometro, 1000);
  }

  function detenerCronometro() {
    if (cronometroIntervalId !== null) {
      clearInterval(cronometroIntervalId);
      cronometroIntervalId = null;
    }
  }

  // ---------- Resumen ----------

  function finalizarSesion() {
    if (!confirmarDescartarBorrador()) return;
    // El tiempo extra no cuenta si el cronómetro ya está parado
    // (sesión completa) o congelado (leyendo una respuesta).
    if (cronometroActivo && !cronometroCongelado()) {
      acumularTiempo();
    }
    for (let i = 0; i < resultados.length; i++) {
      if (resultados[i] === null) resultados[i] = 'saltar';
    }
    mostrarResumen();
  }

  // MODIFICADO: Entendida sin pista = 1 punto; Entendida con pista = 0.5;
  // Casi = 0.5; No entendida y Pasada sin marcar = 0
  function calcularNota() {
    let puntos = 0;
    resultados.forEach((r, i) => {
      if (r === 'si') puntos += (pistaMostrada[i] || reintentoExitoso.has(i)) ? 0.5 : 1;   // reintento acertado = 0.5
      else if (r === 'casi') puntos += 0.5;   // NUEVO: Casi = 0.5
    });
    return tarjetasSesion.length > 0 ? (10 * puntos) / tarjetasSesion.length : 0;
  }

  // NUEVO: arma el item de una tarjeta del resumen (conTema=false cuando ya está
  // dentro de un grupo de su subT). Incluye primero + segundo, tiempo, respuesta
  // escrita (si la hubo) y su evaluación; marca ☆ las difíciles.
  function crearItemResultado(i, conTema, mostrarRazones) {
    const tarjeta = tarjetasSesion[i];
    const item = document.createElement('div');
    item.className = 'item-lista';

    if (conTema) {
      const tema = document.createElement('span');
      tema.className = 'item-tema';
      tema.textContent = nombreTema(tarjeta);
      item.appendChild(tema);
    }

    const renglon = document.createElement('span');
    renglon.className = 'item-renglon';
    renglon.textContent = (esDificil(tarjeta) ? '☆ ' : '') + armarRenglon(tarjeta);
    item.appendChild(renglon);

    const tiempo = document.createElement('span');
    tiempo.className = 'item-tiempo';
    tiempo.textContent = '⏱ ' + formatearTiempo(tiempos[i]);
    item.appendChild(tiempo);

    // Respuesta escrita (si la hubo) + su evaluación
    if (escritos[i] && escritos[i].trim() !== '') {
      const escrito = document.createElement('span');
      escrito.className = 'item-escrito';
      escrito.textContent = '"' + escritos[i] + '"';
      item.appendChild(escrito);
    }

    const ev = evaluaciones[i];
    if (ev) {
      const resultado = document.createElement('span');
      resultado.className = 'item-resultado ' + ev.resultado;
      let txt = ev.resultado === 'bien' ? '✓ Bien' : (ev.resultado === 'casi' ? '✗ Casi' : '✗ Mal');
      if (porComprension[i]) txt += ' → 💡 entendida por comprensión';
      if (ev.clavesTotal > 0) {
        txt += ' — claves ' + ev.acertadas.length + '/' + ev.clavesTotal;
        if (ev.faltaron.length > 0) txt += ' (faltaron: ' + ev.faltaron.join(', ') + ')';
      }
      txt += ' — coincidencia ' + Math.round(ev.sim * 100) + '% (' + ev.comunes + ' en común)';
      resultado.textContent = txt;
      item.appendChild(resultado);
    }

    if (primerIntentoFallido.has(i)) {
      const tag = document.createElement('span');
      tag.className = 'item-reintento';
      tag.textContent = '🔁 Falló el primer intento (segunda oportunidad: ' +
        (reintentoExitoso.has(i) ? 'reintento acertado, cuenta 50 %' : 'reintento fallido') + ')';
      item.appendChild(tag);
    }

    // NUEVO: razones guardadas (no se muestran en la sección "Revisar")
    if (mostrarRazones) {
      const codigos = razonesDeTarjeta(tarjeta);
      if (codigos.length > 0) {
        const fila = document.createElement('div');
        fila.className = 'item-razones';
        fila.appendChild(document.createTextNode('🏷 '));
        codigos.forEach((cod) => {
          const def = MAPA_RAZONES.get(cod);
          if (!def) return;
          const chip = document.createElement('span');
          chip.className = 'chip-razon chico';
          chip.textContent = def.texto;
          chip.style.backgroundColor = hexToRgba(def.color, 0.15);
          chip.style.borderColor = def.color;
          chip.style.color = def.color;
          fila.appendChild(chip);
        });
        item.appendChild(fila);
      }
    }

    return item;
  }

  // OPTIMIZACIÓN (lazy rendering), DOS NIVELES:
  //  1) El <details class="lista-desplegable"> externo (Entendidas, Casi,
  //     No entendidas, Pasadas sin marcar, Revisar): su contenido (los
  //     subT agrupados) recién se arma al abrirlo.
  //  2) Cada <details class="sub-desplegable"> (un subT dentro de esa
  //     categoría): sus ítems (crearItemResultado, uno por tarjeta) recién
  //     se arman al abrir ESE subT puntual.
  // '_resumenListaDatos' guarda los índices/razones más recientes por
  // contenedor (mostrarResumen() puede llamarse de nuevo tras "continuar con
  // las no respondidas"); '_resumenListaCtrl' cachea el lazyCtrl del
  // <details> externo para no registrar el listener 'toggle' más de una vez.
  const _resumenListaDatos = new Map();   // idContenedor -> { indices, mostrarRazones }
  const _resumenListaCtrl = new Map();    // idContenedor -> lazyCtrl

  function construirListaResumenLazy(idContenedor) {
    const datos = _resumenListaDatos.get(idContenedor) || { indices: [], mostrarRazones: true };
    const indices = datos.indices, mostrarRazones = datos.mostrarRazones;
    const el = document.getElementById(idContenedor);
    el.innerHTML = '';

    if (indices.length === 0) {
      el.innerHTML = '<div class="lista-vacia">No hay tarjetas en esta categoría</div>';
      return;
    }

    const posTxt = new Map();
    tarjetasCompletas.forEach((t, i) => posTxt.set(t, i));
    const ordenTemas = agruparPorTema().map((g) => g.nombre);

    const grupos = new Map();
    indices.forEach((i) => {
      const nombre = nombreTema(tarjetasSesion[i]);
      if (!grupos.has(nombre)) grupos.set(nombre, []);
      grupos.get(nombre).push(i);
    });

    const nombres = Array.from(grupos.keys()).sort((a, b) => ordenTemas.indexOf(a) - ordenTemas.indexOf(b));
    nombres.forEach((nombre) => {
      const lista = grupos.get(nombre).sort((a, b) =>
        (posTxt.get(tarjetasSesion[a]) || 0) - (posTxt.get(tarjetasSesion[b]) || 0));

      const det = document.createElement('details');
      det.className = 'sub-desplegable';
      const sum = document.createElement('summary');
      sum.textContent = nombre + ' (' + lista.length + ')';
      det.appendChild(sum);
      // Segundo nivel: los ítems de ESTE subT recién se arman al abrirlo.
      prepararDetallesLazy(det, () => {
        lista.forEach((i) => det.appendChild(crearItemResultado(i, false, mostrarRazones)));
      });
      el.appendChild(det);
    });
  }

  // MODIFICADO: recibe índices y los agrupa en desplegables por subT.
  // Orden: subT en el orden del txt; dentro de cada subT, tarjetas en el orden del txt.
  // mostrarRazones=false se usa para la sección "Revisar" (sin chips de razones).
  function llenarLista(idContenedor, indices, mostrarRazones) {
    if (mostrarRazones === undefined) mostrarRazones = true;
    _resumenListaDatos.set(idContenedor, { indices: indices, mostrarRazones: mostrarRazones });
    let ctrl = _resumenListaCtrl.get(idContenedor);
    if (!ctrl) {
      const detExterno = document.getElementById(idContenedor).closest('details');
      ctrl = prepararDetallesLazy(detExterno, () => construirListaResumenLazy(idContenedor));
      _resumenListaCtrl.set(idContenedor, ctrl);
    }
    ctrl.refrescar();
  }

  function mostrarResumen() {
    // Se guardan índices en lugar de tarjetas
    const conteos = { si: 0, casi: 0, no: 0, saltar: 0 };
    const indices = { si: [], casi: [], no: [], saltar: [] };

    resultados.forEach((r, i) => {
      conteos[r]++;
      indices[r].push(i);
    });

    // Nota general de la sesión (verde >= 6, rojo < 6)
    const nota = calcularNota();
    const elNota = document.getElementById('nota-sesion');
    elNota.textContent = '🎓 Nota de la sesión: ' + nota.toFixed(1) + ' / 10';
    elNota.className = 'nota-sesion ' + (nota >= 6 ? 'aprobado' : 'desaprobado');

    document.getElementById('etq-si').textContent = 'Entendidas (' + conteos.si + ')';
    document.getElementById('etq-casi').textContent = 'Casi (' + conteos.casi + ')';
    document.getElementById('etq-no').textContent = 'No entendidas (' + conteos.no + ')';
    document.getElementById('etq-saltar').textContent = 'Pasadas sin marcar (' + conteos.saltar + ')';

    const tiempoTotalMs = tiempos.reduce((a, b) => a + b, 0);
    const promedioMs = tarjetasSesion.length > 0 ? tiempoTotalMs / tarjetasSesion.length : 0;
    document.getElementById('tiempo-total').textContent = formatearTiempo(tiempoTotalMs);
    document.getElementById('tiempo-promedio').textContent = formatearTiempo(promedioMs);

    llenarLista('lista-si', indices.si);
    llenarLista('lista-casi', indices.casi);
    llenarLista('lista-no', indices.no);
    llenarLista('lista-saltar', indices.saltar);

    // NUEVO: tarjetas marcadas con 🚩 Revisar durante la sesión
    const idxRevisar = [];
    tarjetasSesion.forEach((t, i) => { if (esRevisar(t)) idxRevisar.push(i); });
    llenarLista('lista-revisar', idxRevisar, false);   // NUEVO: sin razones en "Revisar"
    document.getElementById('etq-revisar').textContent = '🚩 Para revisar (' + idxRevisar.length + ')';

    // NUEVO: continuar con las no respondidas (pasadas sin marcar)
    const elBtnContinuar = document.getElementById('btn-continuar-pendientes');
    elBtnContinuar.style.display = conteos.saltar > 0 ? 'block' : 'none';
    elBtnContinuar.textContent = 'Continuar con las no respondidas (' + conteos.saltar + ')';
    elBtnContinuar.onclick = () => abrirModalContinuar();

    // "Casi" también se repasa (antes contaba como No entendida)
    const elBtnRepasarNo = document.getElementById('btn-repasar-no');
    // NUEVO: también van a repaso las que fallaron el primer intento (segunda oportunidad)
    const paraRepaso = (i) => resultados[i] === 'no' || resultados[i] === 'casi' || primerIntentoFallido.has(i);
    elBtnRepasarNo.style.display = tarjetasSesion.some((_, i) => paraRepaso(i)) ? 'block' : 'none';
    elBtnRepasarNo.onclick = () => {
      let pendientes = tarjetasSesion.filter((_, i) => paraRepaso(i));
      if (modoAleatorio) pendientes = mezclar(pendientes);
      iniciarSesion(pendientes);
    };

    document.getElementById('btn-reiniciar').onclick = () => {
      const nuevaLista = modoAleatorio ? mezclar(tarjetasSesion) : [...tarjetasSesion];
      iniciarSesion(nuevaLista);
    };

    // NUEVO: Gamificación — filas de puntos/racha/modo + guardado en historial
    renderResumenGamificacion(nota);
    guardarHistorialSesion(nota);

    // NUEVO: fue (o no) una sesión de Repaso Espaciado — se captura ANTES de
    // aplicar la progresión, porque esta limpia srsSesionActiva al terminar.
    const fueSesionSRS = !!srsSesionActiva;
    actualizarProgresionSRS();

    // NUEVO: botón "guardar como lista" — solo si esta sesión NO venía de una lista guardada NI del SRS
    elBtnResumenGuardarLista.style.display = (!modoObservador && !listaActivaNombre && !fueSesionSRS) ? 'block' : 'none';
    renderResumenListado();

    // NUEVO: botón "📊 Análisis de la sesión" (visibilidad; el contenido se
    // arma recién al abrir el modal, ver construirAnalisisSesion()).
    mostrarAnalisisSesion();

    mostrarPantalla('resumen');
  }

  // ---------- NUEVO: modal "Continuar con las no respondidas" ----------

  function abrirModalContinuar() {
    const n = resultados.filter((r) => r === 'saltar').length;
    document.getElementById('modal-continuar-texto').textContent =
      n + ' tarjeta(s) quedaron sin responder. ¿Cómo querés seguir?';
    elModalContinuar.classList.add('abierto');
  }

  function cerrarModalContinuar() {
    elModalContinuar.classList.remove('abierto');
  }

  // Opción 1: seguir en la MISMA sesión. Se conservan tiempos, aciertos y demás
  // resultados; solo las pasadas sin marcar vuelven a quedar pendientes.
  function continuarEnEstaSesion() {
    cerrarModalContinuar();
    // NUEVO: Gamificación — si esta sesión ya tuvo Game Over (0 vidas) y el
    // usuario igual elige seguir, entra en "modo zombie": puede seguir viendo
    // y respondiendo tarjetas, pero puntos/racha/vidas quedan CONGELADOS tal
    // como estaban en el momento del Game Over (no sigue sumando negativo).
    if (gameOverDisparado) {
      modoZombie = true;
    }
    resultados.forEach((r, i) => {
      if (r === 'saltar') {
        resultados[i] = null;
        // Al pasarla se había revelado el segundo sin usar la pista: se vuelve a ocultar
        revelado[i] = !!pistaMostrada[i];
      }
    });
    const primera = resultados.findIndex((r) => r === null);
    indiceActual = primera >= 0 ? primera : 0;
    cronometroActivo = primera >= 0;
    indiceCongelado = -1;
    resetPanelesUI();
    pausaManual = false;
    elOverlayPausa.classList.remove('abierto');
    tiempoInicioTarjeta = Date.now();
    mostrarPantalla('estudio');
    iniciarCronometro();
    renderTarjeta();
    window.scrollTo(0, 0);
  }

  // Opción 2: sesión nueva y aislada solo con las pendientes
  function iniciarNuevaRondaPendientes() {
    cerrarModalContinuar();
    let pendientes = tarjetasSesion.filter((_, i) => resultados[i] === 'saltar');
    if (modoAleatorio) pendientes = mezclar(pendientes);
    iniciarSesion(pendientes);
  }

  document.getElementById('btn-modal-sesion').addEventListener('click', () => continuarEnEstaSesion());
  document.getElementById('btn-modal-ronda').addEventListener('click', () => iniciarNuevaRondaPendientes());
  document.getElementById('btn-modal-cancelar').addEventListener('click', () => cerrarModalContinuar());
  elModalContinuar.addEventListener('click', (evento) => {
    if (evento.target === elModalContinuar) cerrarModalContinuar();
  });

  // NUEVO: listeners del modo observador
  document.getElementById('btn-modo-observador').addEventListener('click', () => entrarObservador());
  document.getElementById('btn-observador-salir').addEventListener('click', () => mostrarPantalla('temas'));
  document.getElementById('btn-observador-volver').addEventListener('click', () => salirObservador());


  // ============================================================
  // NUEVO: sesión y aviso de Repaso Espaciado (SRS)
  // ============================================================
  const elBadgeSrs = document.getElementById('badge-srs');
  function actualizarBadgeSrs() {
    elBadgeSrs.style.display = srsSesionActiva ? 'inline-block' : 'none';
  }

  // §5: arma una sesión TEMPORAL en RAM con las tarjetas de las listas
  // vencidas (sin tocar los hashes de las listas originales) y la arranca
  // como una sesión normal de estudio.
  function iniciarSesionSRS(listasVencidas) {
    const hashesPorLista = {};
    const todosLosHashes = new Set();
    listasVencidas.forEach((lista) => {
      hashesPorLista[lista.nombre] = new Set(lista.hashes);
      lista.hashes.forEach((h) => todosLosHashes.add(h));
    });
    let tarjetasSrs = tarjetasCompletas.filter((t) => todosLosHashes.has(hashTarjeta(t)));
    if (listasVencidas.length > 1) tarjetasSrs = mezclar(tarjetasSrs);   // varias listas: se mezclan en una sola sesión

    iniciarSesion(tarjetasSrs);   // esto resetea srsSesionActiva a null; se fija DESPUÉS
    srsSesionActiva = { nombres: listasVencidas.map((l) => l.nombre), hashesPorLista: hashesPorLista };
    actualizarBadgeSrs();
  }

  // §5.6: al terminar la sesión, por cada lista involucrada se calcula su
  // propio % de aciertos (sobre SUS tarjetas dentro de esta sesión) y se
  // aplica la tabla de progresión. Limpia srsSesionActiva al terminar, lo que
  // además evita aplicar la progresión dos veces si mostrarResumen() se
  // llamara más de una vez.
  function actualizarProgresionSRS() {
    if (!srsSesionActiva) return;
    const hashesPorLista = srsSesionActiva.hashesPorLista;
    srsSesionActiva.nombres.forEach((nombre) => {
      const lista = buscarLista(nombre);
      if (!lista) return;   // pudo haberse borrado durante la sesión; no hay nada que actualizar
      const hashesLista = hashesPorLista[nombre];
      let total = 0, exitos = 0;
      tarjetasSesion.forEach((t, i) => {
        if (!hashesLista.has(hashTarjeta(t))) return;
        total++;
        const r = resultados[i];
        if (r === 'si' || r === 'casi') exitos++;   // 'no', 'saltar' y sin responder cuentan como fallo
      });
      if (total === 0) return;
      const pct = exitos / total;
      const srs = obtenerSrsLista(lista);
      if (pct >= 0.8) srs.currentLevel += 1;
      else if (pct >= 0.6) { /* se mantiene */ }
      else if (pct >= 0.4) srs.currentLevel = Math.max(1, srs.currentLevel - 1);
      else srs.currentLevel = 1;

      if (srs.currentLevel > 6) {
        srs.graduated = true;
        srs.currentLevel = 6;
      }
      srs.lastReviewDate = Date.now();
      srs.snoozeUntil = 0;
      lista.modificada = Date.now();
    });
    guardarListas();
    srsSesionActiva = null;
    actualizarBadgeSrs();
  }

  // §4: al cargar el HTML (mostrarPantalla('temas') corre también en el
  // arranque) y cada vez que se vuelve a "Elegir temas". NUNCA interrumpe una
  // sesión en curso, porque solo se llama desde la rama 'temas' de
  // mostrarPantalla.
  let srsListasVencidasAviso = [];
  function evaluarAvisoSRS() {
    limpiarListasContraTarjetasActuales();
    const vencidas = listas.filter((l) => srsListaParaAviso(l));
    if (vencidas.length === 0) return;
    srsListasVencidasAviso = vencidas;
    const elLista = document.getElementById('srs-aviso-lista');
    elLista.innerHTML = '';
    vencidas.forEach((l) => {
      const li = document.createElement('li');
      li.textContent = l.nombre + ' (Nivel ' + l.srs.currentLevel + ' — ' + l.hashes.length + ' tarjeta' + (l.hashes.length === 1 ? '' : 's') + ')';
      elLista.appendChild(li);
    });
    document.getElementById('modal-srs-aviso').classList.add('abierto');
  }

  document.getElementById('btn-srs-repasar-ahora').addEventListener('click', () => {
    document.getElementById('modal-srs-aviso').classList.remove('abierto');
    iniciarSesionSRS(srsListasVencidasAviso);
  });
  document.getElementById('btn-srs-omitir-hoy').addEventListener('click', () => {
    const manana = new Date();
    manana.setHours(24, 0, 0, 0);   // medianoche local del día siguiente
    srsListasVencidasAviso.forEach((l) => {
      obtenerSrsLista(l).snoozeUntil = manana.getTime();
      l.modificada = Date.now();
    });
    guardarListas();
    document.getElementById('modal-srs-aviso').classList.remove('abierto');
  });
  document.getElementById('btn-srs-recordar-luego').addEventListener('click', () => {
    srsListasVencidasAviso.forEach((l) => {
      obtenerSrsLista(l).snoozeUntil = Date.now() + 7200000;   // 2 horas
      l.modificada = Date.now();
    });
    guardarListas();
    document.getElementById('modal-srs-aviso').classList.remove('abierto');
  });

  // ---------- Arranque ----------
  construirListaTemas();
  mostrarPantalla('temas');
</script>

</body>
</html>
"""


# ---------------- CONFIGURACIÓN ----------------

try:
    from tkinter import Toplevel, Label, Frame, Button
except ImportError:
    Toplevel = Label = Frame = Button = None

COLOR_DEFAULT = "rosadoClaro"


def elegir_color(raiz, colores, default):
    """
    Ventana modal con un botón por color (grilla de 3 columnas).
    Devuelve la clave elegida; si se cierra con la X, devuelve el default.
    NOTA: sin transient() — con la raíz oculta, en Windows el Toplevel
    transient se oculta y wait_window quedaría esperando para siempre.
    """
    eleccion = {"clave": None}

    def confirmar(nombre):
        eleccion["clave"] = nombre
        vent.destroy()

    vent = Toplevel(raiz)
    vent.title("Color de las tarjetas")
    vent.resizable(False, False)
    vent.configure(bg="#f0f0f0")
    vent.protocol("WM_DELETE_WINDOW", lambda: confirmar(default))

    Label(
        vent, text="Selecciona un color:",
        bg="#f0f0f0", fg="#222222", font=("Segoe UI", 12, "bold"),
    ).pack(pady=(14, 8))

    marco = Frame(vent, bg="#f0f0f0")
    marco.pack(padx=14)

    for i, (nombre, par) in enumerate(colores.items()):
        fila, col = divmod(i, 3)
        Button(
            marco, text=nombre, bg=par[0], fg=par[-1],
            activebackground=par[0], activeforeground=par[-1],
            width=14, bd=1, relief="raised", font=("Segoe UI", 9),
            cursor="hand2", command=lambda n=nombre: confirmar(n),
        ).grid(row=fila, column=col, padx=4, pady=3, sticky="nsew")

    Button(
        vent, text="Estilo por defecto", width=34, bd=1, relief="raised",
        bg="#ffffff", fg="#333333", font=("Segoe UI", 9), cursor="hand2",
        command=lambda: confirmar(default),
    ).pack(pady=(10, 14))

    vent.update_idletasks()
    x = (vent.winfo_screenwidth() - vent.winfo_reqwidth()) // 2
    y = (vent.winfo_screenheight() - vent.winfo_reqheight()) // 2
    vent.geometry(f"+{x}+{y}")

    vent.deiconify()
    vent.lift()
    vent.focus_force()
    vent.wait_visibility()
    vent.grab_set()

    raiz.wait_window(vent)
    return eleccion["clave"] if eleccion["clave"] else default


# Fuente única de la paleta: la usan la ventana "Selecciona un color" y --config.
PALETA_COLORES = {
    "amarillo": ["#DEDE00", "#5C5C5C"],
    "celeste": ["#00CCCC", "#5C5C5C"],
    "rojo": ["#FF0000", "#5C5C5C"],
    "verde": ["#66CC00", "#5C5C5C"],
    "morado": ["#7F00FF", "#D0D0D0"],
    "rosado": ["#FF66FF", "#5C5C5C"],     
    "azul": ["#0000FF", "#D0D0D0"],     
    "bordo": ["#FF0080", "#5C5C5C"],     
    "naranja": ["#FF8000", "#5C5C5C"],     
    "verdeAgua": ["#0BD0AF", "#5C5C5C"],     
    "gris": ["#999999", "#5C5C5C"],     
    "marron": ["#89552A", "#D0D0D0"],     
    "fucsia": ["#DC7BFF", "#5C5C5C"],     
    "azulGrisaceo": ["#3399FF", "#5C5C5C"],     
    "verdeOscuro": ["#14B866", "#5C5C5C"],     
    "naranjaOscuro": ["#FF5A36", "#5C5C5C"],     
    "rojoOscuro": ["#8B0000", "#D0D0D0"],     
    "amarilloOscuro": ["#556B2F", "#D0D0D0"],     
    "verdeClaro": ["#98FF98", "#5C5C5C"],     
    "grisClaro": ["#E6E6FA", "#5C5C5C"],     
    "rosadoClaro": ["#FFB6C1", "#5C5C5C"],     
    "azulClaro": ["#B3BDFF", "#5C5C5C"],     
    "moradoClaro": ["#696ADC", "#5C5C5C"],     
    "rojoClaro": ["#FF6347", "#5C5C5C"],     
    "marronClaro": ["#CD853F", "#5C5C5C"],     
    "amarilloClaro": ["#FFFF80", "#5C5C5C"],
}

def preguntar_resaltado(raiz):
    """
    Ventana modal personalizada (mismo estilo que elegir_color) para preguntar 
    si se desea aplicar el resaltado. Devuelve True (Sí) o False (No).
    """
    eleccion = {"aplicar": False}
    
    def confirmar(valor):
        eleccion["aplicar"] = valor
        vent.destroy()
        
    vent = Toplevel(raiz)
    vent.title("Resaltado en Drawio")
    vent.resizable(False, False)
    vent.configure(bg="#f0f0f0")
    vent.protocol("WM_DELETE_WINDOW", lambda: confirmar(False))
    
    Label(
        vent, text="¿Deseas aplicar el resaltado de palabras clave\nal archivo .drawio seleccionado?",
        bg="#f0f0f0", fg="#222222", font=("Segoe UI", 12, "bold"),
        justify="center"
    ).pack(pady=(20, 20))
    
    marco_botones = Frame(vent, bg="#f0f0f0")
    marco_botones.pack(pady=(0, 20))
    
    Button(
        marco_botones, text="Sí, aplicar", bg="#d4edda", fg="#155724",
        activebackground="#c3e6cb", activeforeground="#155724",
        width=14, bd=1, relief="raised", font=("Segoe UI", 10, "bold"),
        cursor="hand2", command=lambda: confirmar(True)
    ).grid(row=0, column=0, padx=10, sticky="nsew")
    
    Button(
        marco_botones, text="No, omitir", bg="#f8d7da", fg="#721c24",
        activebackground="#f5c6cb", activeforeground="#721c24",
        width=14, bd=1, relief="raised", font=("Segoe UI", 10, "bold"),
        cursor="hand2", command=lambda: confirmar(False)
    ).grid(row=0, column=1, padx=10, sticky="nsew")
    
    vent.update_idletasks()
    x = (vent.winfo_screenwidth() - vent.winfo_reqwidth()) // 2
    y = (vent.winfo_screenheight() - vent.winfo_reqheight()) // 2
    vent.geometry(f"+{x}+{y}")
    vent.deiconify()
    vent.lift()
    vent.focus_force()
    vent.wait_visibility()
    vent.grab_set()
    raiz.wait_window(vent)
    
    return eleccion["aplicar"]

# ---------------- PIPELINE (compartido por el modo interactivo y --config) ----------------

def generar_evaluador(recordatorio, respuestas, drawio, resaltar, colores, propagar=False):
    """
    Genera UN evaluador. Es el pipeline de siempre, sin cambios de lógica:
    parsear respuestas -> parsear recordatorio -> (resaltar drawio) ->
    extraer descripciones -> generar_html. ESPACIO_HASH = stem de 'respuestas'.

    'colores' es un NOMBRE de PALETA_COLORES (si no existe, se usa COLOR_DEFAULT).
    Devuelve (True, "") si salió bien o (False, motivo) si falló. Con
    propagar=True las excepciones no se capturan (así el modo interactivo
    conserva su comportamiento de siempre: traceback).
    """
    try:
        if colores in PALETA_COLORES:
            fill_color = PALETA_COLORES[colores][0]
            stroke_color = PALETA_COLORES[colores][-1]
        else:
            fill_color = PALETA_COLORES[COLOR_DEFAULT][0]
            stroke_color = PALETA_COLORES[COLOR_DEFAULT][-1]

        #Modo de estudio: 1 = secuencial (orden del recordatorio.txt), 2 = aleatorio
        #El mezclado real ocurre en el navegador (JS), así que "Reiniciar" vuelve a mezclar.
        modo = 2

        if respuestas:
            rutas = parsear_respuestas(respuestas)
        else:
            rutas = {}
            print("Aviso: no se seleccionó respuestas.txt; ninguna tarjeta tendrá botón 'Mostrar respuesta'.")

        tarjetas = parsear_recordatorio(recordatorio)
        reportar_asociaciones(tarjetas, rutas)

        #--- Resaltado del drawio (según la elección) ---
        if resaltar:
            fn_mapa = cargar_generador_respuestas()
            if fn_mapa is not None:
                try:
                    resaltar_drawio(drawio, fn_mapa, tarjetas)
                except Exception as error:
                    print(f"AVISO: falló el resaltado del drawio: {error}")
                    print("       Se continúa con la generación del evaluador.")
            else:
                print("AVISO: No se pudo cargar el módulo para resaltar el drawio.")
        else:
            print("El usuario eligió omitir el resaltado del drawio.")

        #--- NUEVO: extracción de descripciones para el Buscador Global (independiente
        #    del resaltado: se hace siempre que haya .drawio, sin tocar el archivo) ---
        descripciones_rutas = extraer_descripciones_drawio(drawio)
        print(f"Rutas con descripciones de imagen extraídas del drawio: {len(descripciones_rutas)}")

        generar_html(tarjetas, rutas, fill_color, stroke_color, modo == 2, respuestas, Path(respuestas).stem, descripciones_rutas)
    except Exception as error:
        if propagar:
            raise
        return False, f"{type(error).__name__}: {error}"
    return True, ""


# ---------------- MODO --config (sin ninguna UI) ----------------

# campo -> tipo JSON esperado. TODOS son obligatorios.
CAMPOS_CONFIG = {
    "recordatorio": str,
    "respuestas": str,
    "drawio": str,
    "resaltar": bool,
    "colores": str,
}
CAMPOS_RUTA = ("recordatorio", "respuestas", "drawio")


class ErrorConfig(Exception):
    """Error global del config (archivo ausente, JSON inválido, estructura...): aborta el lote."""


def _tipo_json(valor):
    if valor is None:
        return "null"
    if isinstance(valor, bool):
        return "booleano"
    if isinstance(valor, str):
        return "texto"
    if isinstance(valor, (int, float)):
        return "número"
    if isinstance(valor, list):
        return "lista"
    return "objeto"


def leer_config(ruta_config):
    """
    Lee el config (solo lectura; nunca se modifica) y devuelve (entradas, avisos).
    Formato obligatorio: {"entradas": [ ... ]}, incluso con un solo evaluador.
    """
    ruta = Path(ruta_config)
    if not ruta.is_file():
        raise ErrorConfig(f"no existe el archivo de configuración: {ruta}")
    try:
        # utf-8-sig: tolera el BOM que agrega el Bloc de notas
        with open(ruta, "r", encoding="utf-8-sig") as archivo:
            datos = json.load(archivo)
    except json.JSONDecodeError as error:
        raise ErrorConfig(f"JSON inválido en {ruta}: {error}")
    except (UnicodeDecodeError, OSError) as error:
        raise ErrorConfig(f"no se pudo leer {ruta}: {error}")

    avisos = []
    if not isinstance(datos, dict):
        raise ErrorConfig(
            "el config debe ser un objeto JSON con la forma {\"entradas\": [ ... ]} "
            f"(se encontró: {_tipo_json(datos)})"
        )
    if "entradas" not in datos:
        pista = ""
        if any(campo in datos for campo in CAMPOS_CONFIG):
            pista = " Parece una entrada suelta: envolvela en una lista, {\"entradas\": [ {...} ]}."
        raise ErrorConfig(
            "falta el campo 'entradas': el config debe tener la forma {\"entradas\": [ ... ]}, "
            "incluso con un solo evaluador." + pista
        )
    entradas = datos["entradas"]
    if not isinstance(entradas, list):
        raise ErrorConfig(f"'entradas' debe ser una lista (se encontró: {_tipo_json(entradas)})")
    if not entradas:
        raise ErrorConfig("'entradas' está vacía: no hay nada que generar")
    for clave in datos:
        if clave != "entradas":
            avisos.append(f"campo desconocido '{clave}' fuera de 'entradas' (se ignora)")
    return entradas, avisos


def validar_entrada(entrada, carpeta_config):
    """
    Validación estricta de UNA entrada. Devuelve (resuelta, errores, avisos).
    'resuelta' (dict con rutas absolutas como Path) es None si hay errores.
    Las rutas relativas se resuelven contra la carpeta del config, no contra el cwd.
    """
    errores, avisos = [], []
    if not isinstance(entrada, dict):
        return None, [f"la entrada debe ser un objeto JSON (se encontró: {_tipo_json(entrada)})"], avisos

    for campo in entrada:
        if campo not in CAMPOS_CONFIG:
            avisos.append(f"campo desconocido '{campo}' (se ignora)")

    valores = {}
    for campo, tipo in CAMPOS_CONFIG.items():
        if campo not in entrada:
            errores.append(f"falta el campo '{campo}'")
            continue
        valor = entrada[campo]
        if tipo is bool:
            valido = isinstance(valor, bool)
        else:
            valido = isinstance(valor, str)
        if not valido:
            esperado = "true/false" if tipo is bool else "texto"
            errores.append(f"campo '{campo}': debe ser {esperado} (se encontró: {_tipo_json(valor)})")
        elif tipo is str and not valor.strip():
            errores.append(f"campo '{campo}': está vacío")
        else:
            valores[campo] = valor

    for campo in CAMPOS_RUTA:
        if campo not in valores:
            continue
        ruta = Path(valores[campo])
        if not ruta.is_absolute():
            ruta = carpeta_config / ruta
        try:
            ruta = ruta.resolve()
            existe = ruta.is_file()
        except (OSError, ValueError):
            existe = False
        if existe:
            valores[campo] = ruta
        else:
            errores.append(f"campo '{campo}': no existe el archivo {ruta}")
            del valores[campo]

    if "colores" in valores and valores["colores"] not in PALETA_COLORES:
        errores.append(
            f"campo 'colores': '{valores['colores']}' no es un nombre válido "
            "(se distinguen mayúsculas y minúsculas; no se aceptan hex). "
            f"Válidos: {', '.join(PALETA_COLORES)}"
        )

    if errores:
        return None, errores, avisos
    return valores, errores, avisos


def _etiqueta_entrada(entrada, numero):
    """Nombre para la consola: stem de su respuestas.txt, o 'entrada N' si no se puede saber."""
    if isinstance(entrada, dict):
        resp = entrada.get("respuestas")
        if isinstance(resp, str) and resp.strip():
            return Path(resp).stem or f"entrada {numero}"
    return f"entrada {numero}"


def ejecutar_config(ruta_config):
    """Procesa TODAS las entradas aunque alguna falle. Devuelve el código de salida (0 = todo OK)."""
    try:
        entradas, avisos_globales = leer_config(ruta_config)
    except ErrorConfig as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for aviso in avisos_globales:
        print(f"AVISO: {aviso}")

    carpeta_config = Path(ruta_config).resolve().parent
    total = len(entradas)
    resultados = [None] * total   # (etiqueta, ok, motivo)
    validas = {}                  # posición -> (etiqueta, entrada resuelta)

    for pos, entrada in enumerate(entradas):
        etiqueta = _etiqueta_entrada(entrada, pos + 1)
        resuelta, errores, avisos = validar_entrada(entrada, carpeta_config)
        for aviso in avisos:
            print(f"AVISO [{etiqueta}]: {aviso}")
        if errores:
            resultados[pos] = (etiqueta, False, "; ".join(errores))
        else:
            validas[pos] = (etiqueta, resuelta)

    # Dos entradas con el mismo stem de respuestas escribirían el MISMO html: ninguna se genera.
    por_stem = {}
    for pos, (_, r) in validas.items():
        por_stem.setdefault(r["respuestas"].stem.casefold(), []).append(pos)
    for stem, posiciones in por_stem.items():
        if len(posiciones) > 1:
            numeros = ", ".join(str(p + 1) for p in posiciones)
            nombre = validas[posiciones[0]][1]["respuestas"].stem
            for p in posiciones:
                resultados[p] = (
                    validas[p][0], False,
                    f"las entradas {numeros} comparten el stem de respuestas '{nombre}' y pisarían "
                    f"el mismo evaluador_{nombre}.html; no se genera ninguna de ellas",
                )
                del validas[p]

    # Un .drawio compartido: el resaltado de una pasada limpia el de la anterior.
    por_drawio = {}
    for pos, (_, r) in validas.items():
        por_drawio.setdefault(os.path.normcase(str(r["drawio"])), []).append(pos)
    for posiciones in por_drawio.values():
        if len(posiciones) > 1:
            numeros = ", ".join(str(p + 1) for p in posiciones)
            print(
                f"AVISO: las entradas {numeros} usan el mismo .drawio: si más de una tiene resaltar=true, "
                "cada pasada limpia el resaltado de la anterior (queda solo el de la última)."
            )

    for pos in sorted(validas):
        etiqueta, r = validas[pos]
        print(f"\n[{pos + 1}/{total}] {etiqueta}")
        ok, motivo = generar_evaluador(
            str(r["recordatorio"]), str(r["respuestas"]), str(r["drawio"]),
            r["resaltar"], r["colores"],
        )
        resultados[pos] = (etiqueta, ok, motivo)

    print("\n==== RESUMEN ====")
    for etiqueta, ok, motivo in resultados:
        print(f"{etiqueta}: OK" if ok else f"{etiqueta}: FALLO: {motivo}")
    fallos = sum(1 for _, ok, _ in resultados if not ok)
    print(f"{total - fallos} de {total} evaluadores generados.")
    return 1 if fallos else 0


# ---------------- MODO INTERACTIVO (el flujo de siempre) ----------------

def main_interactivo():
    if Tk is None or filedialog is None:
        print("ERROR: tkinter no está disponible en este Python. Usá --config RUTA para correr sin ventanas.")
        raise SystemExit(1)

    raiz = Tk()
    raiz.withdraw()

    # --- Diálogo 1: recordatorio.txt (tarjetas) ---
    txt = filedialog.askopenfilename(
        title="Selecciona el archivo recordatorio.txt",
        filetypes=[("Archivo de Texto", "*.txt"), ("Todos los archivos", "*.*")]
    )

    # --- Diálogo 2: respuestas.txt (rutas del diagrama) ---
    txt_respuestas = filedialog.askopenfilename(
        title="Selecciona el archivo respuestas.txt",
        filetypes=[("Archivo de Texto", "*.txt"), ("Todos los archivos", "*.*")]
    )

    if not (txt and txt_respuestas):
        print("No se seleccionó recordatorio.txt. ni respuestas.txt. Saliendo.")
        raiz.destroy()
        raise SystemExit

    #--- Diálogo 3: drawio (AHORA OBLIGATORIO) ---
    ruta_drawio = filedialog.askopenfilename(
        title="Selecciona el archivo .drawio (Obligatorio)",
        filetypes=[("Diagrama drawio", ".drawio"), ("Todos los archivos", ".*")]
    )
    if not ruta_drawio:
        print("No se seleccionó el archivo .drawio. Saliendo.")
        raiz.destroy()
        raise SystemExit

    #--- Ventana de elección de color ---
    opciones = elegir_color(raiz, PALETA_COLORES, COLOR_DEFAULT)
    print(f"Color elegido: {opciones}")

    #--- Ventana personalizada para preguntar por el resaltado ---
    aplicar_resaltado = preguntar_resaltado(raiz)
    print(f"Resaltado en drawio: {'Sí' if aplicar_resaltado else 'No'}")

    generar_evaluador(txt, txt_respuestas, ruta_drawio, aplicar_resaltado, opciones, propagar=True)


def main():
    parser = argparse.ArgumentParser(
        description="Genera el evaluador. Sin argumentos: modo interactivo (ventanas). "
                    "Con --config: modo por lote, sin ninguna ventana.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--config", metavar="RUTA",
        help="JSON con la forma {\"entradas\": [...]} (obligatorio, aunque sea 1 solo evaluador); cada entrada: "
             "recordatorio, respuestas, drawio, resaltar, colores (todos obligatorios).",
    )
    args = parser.parse_args()
    if args.config is not None:
        raise SystemExit(ejecutar_config(args.config))
    main_interactivo()


if __name__ == "__main__":
    main()