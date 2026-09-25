import json
import re
import sys
import importlib.util
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import Tk, filedialog

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
    if tag == "object":
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
    if elem.tag.split('}')[-1] == 'object':
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


# ============================================================
# GENERACIÓN DEL HTML
# ============================================================

def generar_html(tarjetas, rutas, fill_color, stroke_color, modo_aleatorio, txt_respuestas, espacio_hash):
    datos = {"tarjetas": tarjetas, "rutas": rutas}
    datos_json = json.dumps(datos, ensure_ascii=False)
    nombre_base = Path(txt_respuestas).stem

    html = HTML_TEMPLATE
    html = html.replace("__FILL_COLOR__", fill_color)
    html = html.replace("__STROKE_COLOR__", stroke_color)
    html = html.replace("__DATOS_JSON__", datos_json)
    html = html.replace("__MODO_ALEATORIO__", "true" if modo_aleatorio else "false")
    html = html.replace("__UMBRAL_CASI__", str(UMBRAL_CASI))
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
    accent-color: var(--stroke-color);
    cursor: pointer;
  }

  .boton-principal {
    padding: 14px 30px;
    border-radius: 26px;
    background: var(--stroke-color);
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
    color: var(--stroke-color);
    border: 2px solid var(--stroke-color);
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
    color: var(--stroke-color);
    border-color: var(--stroke-color);
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
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }

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
    background: var(--stroke-color);
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
    color: var(--stroke-color);
    background: rgba(255,255,255,0.65);
    border: 1.5px solid var(--stroke-color);
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
    padding: clamp(8px, 2.5vw, 10px) clamp(15px, 5vw, 22px);
    border-radius: 20px;
    border: 2px solid var(--stroke-color);
    background: rgba(255,255,255,0.5);
    color: var(--stroke-color);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }

  .boton-respuesta {
    padding: clamp(8px, 2.5vw, 10px) clamp(15px, 5vw, 22px);
    border-radius: 20px;
    border: 2px solid var(--stroke-color);
    background: var(--stroke-color);
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
    border: 2px solid var(--stroke-color);
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
    background: var(--stroke-color);
    color: #fff;
    border-color: var(--stroke-color);
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
    border: 2px solid var(--stroke-color); border-radius: 10px;
    padding: 10px 12px; font-family: inherit; font-size: 14px; color: #333;
    background: #fff;
  }

  .zona-eval textarea:focus {
    outline: 2px solid var(--stroke-color); outline-offset: 1px;
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
    color: var(--stroke-color);
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
    color: var(--stroke-color);
    border: 2px solid var(--stroke-color);
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
    background: var(--stroke-color);
    color: #fff;
  }

  .boton-secundario {
    background: #ffffff;
    color: var(--stroke-color);
    border: 2px solid var(--stroke-color);
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
    color: var(--stroke-color);
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
    accent-color: var(--stroke-color);
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
    accent-color: var(--stroke-color);
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
    color: var(--stroke-color);
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

  .boton-pausa:hover { border-color: var(--stroke-color); color: var(--stroke-color); }

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
  .titulo-pausa { font-size: 26px; font-weight: 700; color: var(--stroke-color); }
  .texto-pausa { font-size: 14px; color: #777; max-width: 340px; }

  /* NUEVO: botón "Revisar" (esquina superior izquierda, pasado el corte del clip-path) */
  .boton-revisar {
    position: absolute;
    top: 10px;
    left: calc(14% + 8px);
    font-size: 12px;
    font-weight: 700;
    color: var(--stroke-color);
    background: rgba(255,255,255,0.65);
    border: 1.5px solid var(--stroke-color);
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

  .modal-caja h3 { font-size: 18px; color: var(--stroke-color); margin-bottom: 2px; }
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

  .item-check-razon input { width: 17px; height: 17px; cursor: pointer; accent-color: var(--stroke-color); }
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
    border: 2px solid var(--stroke-color);
    background: #fff;
    cursor: zoom-in;
  }

  .galeria-vacia { color: #888; padding: 30px 0; }

  .sin-tarjetas {
    color: #888;
    font-size: 15px;
  }
</style>
</head>
<body>

<div class="contenedor">
  <h1>Tarjetas de Estudio</h1>

  <div id="pantalla-temas">
    <p class="subtitulo">Elegí qué temas (o tarjetas) querés estudiar</p>
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
        <span class="fila-dificiles-botones">
          <button class="boton-mini" id="btn-exportar-dificiles" title="Descarga las difíciles activas como JSON">Exportar</button>
          <button class="boton-mini" id="btn-importar-dificiles" title="Agrega las difíciles de un JSON (fusiona con las actuales)">Importar</button>
        </span>
      </div>
      <input type="file" id="input-importar" accept=".json,application/json" style="display:none">
      <div id="lista-dificiles"></div>
      <details id="det-dificiles-otros" style="display:none">
        <summary id="suma-dificiles-otros">De otros evaluadores</summary>
        <div id="lista-dificiles-otros"></div>
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
        <span class="fila-dificiles-botones">
          <button class="boton-mini" id="btn-exportar-notas" title="Descarga tus notas editadas como JSON">Exportar</button>
          <button class="boton-mini" id="btn-importar-notas" title="Fusiona las notas de un JSON (gana la más reciente)">Importar</button>
        </span>
      </div>
      <input type="file" id="input-importar-notas" accept=".json,application/json" style="display:none">
      <details id="det-notas-dif" style="display:none">
        <summary id="suma-notas-dif">Distintas a las del txt</summary>
        <div id="lista-notas-dif"></div>
      </details>
      <details id="det-notas-otras" style="display:none">
        <summary id="suma-notas-otras">De otros evaluadores</summary>
        <div id="lista-notas-otras"></div>
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
        <span class="fila-dificiles-botones">
          <button class="boton-mini" id="btn-exportar-razones">Exportar</button>
          <button class="boton-mini" id="btn-importar-razones">Importar</button>
        </span>
      </div>
      <input type="file" id="input-importar-razones" accept=".json,application/json" style="display:none">
      <details id="det-razones-aqui" style="display:none">
        <summary id="suma-razones-aqui">De este evaluador</summary>
        <div id="lista-razones-aqui"></div>
      </details>
      <details id="det-razones-otras" style="display:none">
        <summary id="suma-razones-otras">De otros evaluadores</summary>
        <div id="lista-razones-otras"></div>
      </details>
      <p class="nota-dificiles" id="nota-razones"></p>
      <button class="boton-mini" id="btn-vaciar-razones" style="display:none;">Vaciar todas</button>
    </div>
  </div>

  <!-- NUEVO: modo observador (lista de tarjetas por subT; al tocar una se abre) -->
  <div id="pantalla-observador" style="display:none;">
    <p class="subtitulo">Modo observador: tocá una tarjeta para verla (sin evaluarte)</p>
    <div class="lista-temas" id="lista-observador"></div>
    <!-- MODIFICADO: se agrega "Ver imágenes" junto al botón de volver -->
    <div class="fila-obs-botones">
      <button class="boton-secundario" id="btn-observador-salir">&larr; Volver a temas</button>
      <button class="boton-secundario" id="btn-ver-imagenes">🖼 Ver imágenes</button>
    </div>

    <!-- NUEVO: notas y difíciles de imágenes (independiente de las de tarjetas) -->
    <div class="lista-temas zona-dificiles" id="zona-imagenes">
      <div class="cabecera-dificiles">
        <span class="titulo-dificiles">🖼 Datos de imágenes guardados</span>
        <span class="fila-dificiles-botones">
          <button class="boton-mini" id="btn-exportar-imagenes">Exportar</button>
          <button class="boton-mini" id="btn-importar-imagenes">Importar</button>
        </span>
      </div>
      <input type="file" id="input-importar-imagenes" accept=".json,application/json" style="display:none">
      <details id="det-imagenes" style="display:none">
        <summary id="suma-imagenes">Con nota o marcadas difícil</summary>
        <div id="lista-imagenes-guardadas"></div>
      </details>
      <p class="nota-dificiles" id="nota-imagenes"></p>
      <button class="boton-mini" id="btn-vaciar-imagenes" style="display:none;">Vaciar todas</button>
    </div>
  </div>

  <!-- NUEVO: galería de imágenes de las rutas, vista secuencial e independiente -->
  <div id="pantalla-galeria" style="display:none;">
    <div class="galeria-indicador" id="galeria-indicador"></div>
    <div class="galeria-imagen-wrap">
      <img id="galeria-img" class="galeria-img" alt="Imagen de una ruta" style="display:none;">
    </div>
    <p class="galeria-vacia" id="galeria-vacia" style="display:none;">No hay imágenes en las rutas de respuestas.txt.</p>

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

  <div id="area-tarjeta" style="display:none;">
    <div class="barra-superior">
      <div class="progreso-texto" id="progreso-texto"></div>
      <div class="grupo-cronometro">
        <div class="cronometro" id="cronometro">⏱ 00:00</div>
        <!-- NUEVO: pausa manual (por si dejás de estudiar un rato) -->
        <button class="boton-pausa" id="btn-pausa" title="Pausar el cronómetro">⏸ Pausar</button>
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
          <button class="boton-pista" id="btn-pista">Mostrar pista</button>
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

    <div class="estado-marca" id="estado-marca"></div>

    <div class="resultado-eval" id="resultado-eval"></div>

    <div class="botonera">
      <!-- MODIFICADO: fila de decisión (cambian resultados[] / avanzan la tarjeta) -->
      <div class="fila-decision">
        <button class="boton boton-circular boton-no" id="btn-no" title="No entendido">&#10007;</button>
        <button class="boton boton-saltar" id="btn-saltar">Pasar sin marcar</button>
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
    <h2>Resumen de la sesión</h2>
    <div class="nota-sesion" id="nota-sesion">🎓 Nota de la sesión: 0.0 / 10</div>
    <div class="fila-resumen">
      <span class="etiqueta-resumen"><span class="punto punto-si"></span> Entendidas</span>
      <span class="valor-resumen" id="conteo-si">0</span>
    </div>
    <div class="fila-resumen">
      <span class="etiqueta-resumen"><span class="punto punto-casi"></span> Casi</span>
      <span class="valor-resumen" id="conteo-casi">0</span>
    </div>
    <div class="fila-resumen">
      <span class="etiqueta-resumen"><span class="punto punto-no"></span> No entendidas</span>
      <span class="valor-resumen" id="conteo-no">0</span>
    </div>
    <div class="fila-resumen">
      <span class="etiqueta-resumen"><span class="punto punto-saltar"></span> Pasadas sin marcar</span>
      <span class="valor-resumen" id="conteo-saltar">0</span>
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
        <summary><span class="etiqueta-resumen"><span class="punto punto-si"></span> Entendidas</span></summary>
        <div class="lista-contenido" id="lista-si"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-casi"></span> Casi</span></summary>
        <div class="lista-contenido" id="lista-casi"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-no"></span> No entendidas</span></summary>
        <div class="lista-contenido" id="lista-no"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-saltar"></span> Pasadas sin marcar</span></summary>
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

  function guardarDificiles() {
    try {
      localStorage.setItem(CLAVE_DIFICILES, JSON.stringify(dificiles));
    } catch (e) {
      // Sin localStorage disponible: solo quedan en memoria de esta sesión
    }
  }

  // NUEVO: identifica a ESTE evaluador (nombre de su recordatorio.txt). Se mezcla
  // en el hash para que dos evaluadores distintos con una tarjeta de texto
  // idéntico ("primero + segundo" igual) no compartan difíciles, notas ni razones.
  // Regenerar el HTML desde el MISMO recordatorio.txt mantiene este valor igual
  // (no invalida lo ya guardado); solo cambia si el archivo cambia de nombre.
  const ESPACIO_HASH = __ESPACIO__;

  function hashTarjeta(t) {
    const base = ESPACIO_HASH + '\\u0001' + (t.primero || '') + '\\u0000' + (t.segundo || '');
    const norm = normalizarPalabra(base).replace(/\\s+/g, ' ').trim();
    // djb2 -> hash de 32 bits (suficiente y sin dependencias; se mantiene corto
    // aunque se agregue el espacio, porque solo se usa como entrada del hash)
    let h = 5381;
    for (let i = 0; i < norm.length; i++) {
      h = ((h << 5) + h + norm.charCodeAt(i)) >>> 0;
    }
    return 'h' + h.toString(36);
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
    } else if (entrada) {
      entrada.activa = true;
    } else {
      dificiles[h] = { p: t.primero || '', s: t.segundo || '', activa: true };
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

  function guardarNotas() {
    try {
      localStorage.setItem(CLAVE_NOTAS, JSON.stringify(notasLocales));
    } catch (e) {
      // Sin localStorage: quedan solo en memoria de esta sesión
    }
  }

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
  const elBtnGaleriaDificil = document.getElementById('btn-galeria-dificil');
  const elBtnGaleriaNota = document.getElementById('btn-galeria-nota');
  const elPanelGaleriaNota = document.getElementById('panel-galeria-nota');
  const elGaleriaNotaTexto = document.getElementById('galeria-nota-texto');
  const elGaleriaNotaEditor = document.getElementById('galeria-nota-editor');
  const elGaleriaNotaVersiones = document.getElementById('galeria-nota-versiones');
  const elBtnGaleriaAnterior = document.getElementById('btn-galeria-anterior');
  const elBtnGaleriaSiguiente = document.getElementById('btn-galeria-siguiente');
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

    if (nombre === 'observador') {
      renderImagenesPreview();   // NUEVO: refresca también al entrar por primera vez (mismo motivo que "temas")
    }

    if (nombre === 'temas') {
      renderDificilesPreview();  // MODIFICADO: actualizar la vista de difíciles
      renderNotasPreview();      // NUEVO: notas distintas a las del txt
      renderRevisarGuardadoPreview();  // NUEVO: también al volver por "Volver a temas" del observador
      renderRazonesPreview();    // NUEVO: corrige que no aparecieran al abrir el HTML por primera vez
      actualizarMarcasTemas();   // NUEVO: refresca ★/💡/🏷 sin resetear las casillas marcadas
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

  // Sincroniza el check del subT (marcado / parcial) y su contador n/m
  function actualizarEstadoGrupo(det) {
    const tarjetas = det.querySelectorAll('input[data-idx]');
    let marcadas = 0;
    tarjetas.forEach((c) => { if (c.checked) marcadas++; });
    const cg = det.querySelector('input.check-grupo');
    cg.checked = tarjetas.length > 0 && marcadas === tarjetas.length;
    cg.indeterminate = marcadas > 0 && marcadas < tarjetas.length;
    det.querySelector('.cuenta-grupo').textContent = marcadas + '/' + tarjetas.length;
  }

  function indicesSeleccionados() {
    const set = new Set();
    elListaTemas.querySelectorAll('input[data-idx]:checked').forEach((c) => set.add(Number(c.dataset.idx)));
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

  function construirListaTemas() {
    elListaTemas.innerHTML = '';
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

      const cuerpo = document.createElement('div');
      cuerpo.className = 'cuerpo-grupo';
      g.indices.forEach((i) => {
        const fila = document.createElement('label');
        fila.className = 'item-tarjeta-check';
        const c = document.createElement('input');
        c.type = 'checkbox';
        c.checked = true;
        c.dataset.idx = String(i);
        c.addEventListener('change', () => {
          actualizarEstadoGrupo(det);
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

      // Marcar/desmarcar el subT marca/desmarca todas sus tarjetas
      checkGrupo.addEventListener('change', () => {
        cuerpo.querySelectorAll('input[data-idx]').forEach((c) => { c.checked = checkGrupo.checked; });
        actualizarEstadoGrupo(det);
        actualizarConteoSeleccion();
      });

      elListaTemas.appendChild(det);
      actualizarEstadoGrupo(det);
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

    iniciarSesion(filtradas);
  });

  // ---------- NUEVO: modo observador ----------
  // Recorre las tarjetas en el orden del txt SIN evaluarse: sin cronómetro,
  // sin Entendí / No entendí, sin Finalizar. Sí se pueden ver pista y rutas,
  // editar notas y marcar ☆ Difícil. Reutiliza la vista de tarjeta del evaluador.

  function construirListaObservador() {
    elListaObservador.innerHTML = '';
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
      elListaObservador.appendChild(det);
    });
  }

  function entrarObservador() {
    if (tarjetasCompletas.length === 0) return;
    construirListaObservador();
    mostrarPantalla('observador');
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
    construirListaObservador();   // refresca ★ y 💡
    mostrarPantalla('observador');
    // Volver al lugar donde se estaba: abrir su subT y mostrar la fila
    const fila = elListaObservador.querySelector('[data-idx="' + ultimo + '"]');
    if (fila) {
      const grupo = fila.closest('details');
      if (grupo) grupo.open = true;
      if (fila.scrollIntoView) fila.scrollIntoView({ block: 'center' });
    }
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

  function guardarImagenesStorage() {
    try {
      localStorage.setItem(CLAVE_IMAGENES, JSON.stringify(datosImagenes));
    } catch (e) {
      // sin localStorage: quedan solo en memoria de esta sesión
    }
  }

  // Hash liviano: espacio del evaluador + número de ruta + índice de la imagen
  // DENTRO de esa ruta (solo contando líneas 'IMG:'). Sin base64, sin subT.
  function hashImagen(ruta, indice) {
    const base = ESPACIO_HASH + '\u0001' + ruta + '\u0000' + indice;
    let h = 5381;
    for (let i = 0; i < base.length; i++) {
      h = ((h << 5) + h + base.charCodeAt(i)) >>> 0;
    }
    return 'i' + h.toString(36);   // prefijo 'i' (imagen) para no confundir con 'h' de tarjeta
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
    renderGaleria();
  }

  function volverListaDesdeGaleria() {
    if (!confirmarDescartarGaleria()) return;
    resetGaleriaNotaUI();
    // MODIFICADO: no se reconstruye la lista del observador (a diferencia de
    // salirObservador), así los subT que estaban abiertos quedan como estaban.
    // renderImagenesPreview() ya se llama dentro de mostrarPantalla('observador').
    mostrarPantalla('observador');
  }

  // ---------- Preview en el modo observador: "🖼 Datos de imágenes guardados" ----------

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
      renderImagenesPreview();
    }));
    div.appendChild(acc);
    return div;
  }

  function renderImagenesPreview() {
    const elLista = document.getElementById('lista-imagenes-guardadas');
    const elDet = document.getElementById('det-imagenes');
    const elSuma = document.getElementById('suma-imagenes');
    const elInfo = document.getElementById('nota-imagenes');
    const elVaciar = document.getElementById('btn-vaciar-imagenes');
    elLista.innerHTML = '';

    const hashes = Object.keys(datosImagenes);
    hashes.forEach((h) => elLista.appendChild(filaImagenGuardada(h, datosImagenes[h])));
    elDet.style.display = hashes.length > 0 ? 'block' : 'none';
    elSuma.textContent = 'Con nota o marcadas difícil (' + hashes.length + ')';
    elInfo.textContent = hashes.length === 0
      ? 'Todavía no marcaste ni anotaste ninguna imagen (galería del modo observador).'
      : 'Guardadas en este navegador: ' + hashes.length;
    elVaciar.style.display = hashes.length > 0 ? 'inline-block' : 'none';
  }

  document.getElementById('btn-vaciar-imagenes').addEventListener('click', () => {
    if (Object.keys(datosImagenes).length === 0) return;
    if (!confirm('¿Vaciar TODOS los datos de imágenes guardados? No se puede deshacer.')) return;
    datosImagenes = {};
    guardarImagenesStorage();
    renderImagenesPreview();
  });

  document.getElementById('btn-exportar-imagenes').addEventListener('click', () => {
    const lista = Object.keys(datosImagenes).map((h) => ({
      h: h,
      ruta: datosImagenes[h].ruta,
      indice: datosImagenes[h].indice,
      nota: datosImagenes[h].nota || '',
      base: datosImagenes[h].base || '',
      dificil: !!datosImagenes[h].dificil,
      t: datosImagenes[h].t || 0,
    }));
    const contenido = JSON.stringify({ version: 1, imagenes: lista }, null, 2);
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
        elInfo.textContent = 'Importación: ' + nuevas + ' nueva(s), ' + actualizadas +
          ' actualizada(s) por ser más recientes, ' + conservadas + ' conservada(s) (la tuya era más reciente), ' +
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
    iniciarSesion(modoAleatorio ? mezclar(tarjetasCompletas) : [...tarjetasCompletas]);
  });

  // Modo 2: repasar las difíciles activas (solo las presentes en este evaluador)
  document.getElementById('btn-modo-dificiles').addEventListener('click', () => {
    const paraRepasar = tarjetasCompletas.filter((t) => esDificil(t));
    if (paraRepasar.length === 0) return;
    iniciarSesion(modoAleatorio ? mezclar(paraRepasar) : paraRepasar);
  });

  // ---------- MODIFICADO: vista previa / gestión de difíciles ----------
  // Las desmarcadas NO se borran: pasan a la sección 'Desmarcadas' y pueden
  // re-activarse desde ahí (o desde el botón ☆ durante el estudio).

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
      guardarDificiles();
      renderDificilesPreview();
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

  function renderDificilesPreview() {
    const elLista = document.getElementById('lista-dificiles');
    const elOtros = document.getElementById('lista-dificiles-otros');
    const elDetOtros = document.getElementById('det-dificiles-otros');
    const elSumaOtros = document.getElementById('suma-dificiles-otros');
    const elDesm = document.getElementById('lista-dificiles-desm');
    const elDetDesm = document.getElementById('det-dificiles-desm');
    const elSumaDesm = document.getElementById('suma-dificiles-desm');
    const elNota = document.getElementById('nota-dificiles');
    elLista.innerHTML = '';
    elOtros.innerHTML = '';
    elDesm.innerHTML = '';

    // 1) Difíciles activas de este evaluador
    const hashesVistos = new Set();
    let nAqui = 0;
    tarjetasCompletas.forEach((t) => {
      const h = hashTarjeta(t);
      const e = dificiles[h];
      if (!e || !e.activa) return;
      hashesVistos.add(h);
      nAqui++;
      elLista.appendChild(filaDificil(h, t.primero || '', t.segundo || '', true));
    });

    // 2) Activas de otros evaluadores (nunca se borran solas)
    let nOtros = 0;
    Object.keys(dificiles).forEach((h) => {
      if (hashesVistos.has(h)) return;
      const v = dificiles[h] || {};
      if (!v.activa) return;
      hashesVistos.add(h);
      nOtros++;
      elOtros.appendChild(filaDificil(h, v.p || '', v.s || '', true));
    });

    // 3) Desmarcadas: siguen guardadas y se pueden re-activar desde acá
    let nDesm = 0;
    Object.keys(dificiles).forEach((h) => {
      const v = dificiles[h] || {};
      if (v.activa) return;
      nDesm++;
      let p = v.p || '', s = v.s || '';
      const enEste = tarjetasCompletas.find((t) => hashTarjeta(t) === h);
      if (enEste) {
        p = enEste.primero || '';
        s = enEste.segundo || '';
      }
      elDesm.appendChild(filaDificil(h, p, s, false));
    });

    elDetOtros.style.display = nOtros > 0 ? 'block' : 'none';
    elSumaOtros.textContent = 'De otros evaluadores (' + nOtros + ')';
    elDetDesm.style.display = nDesm > 0 ? 'block' : 'none';
    elSumaDesm.textContent = 'Desmarcadas (' + nDesm + ')';

    // Contador del modo Repasar difíciles
    const btnD = document.getElementById('btn-modo-dificiles');
    btnD.textContent = 'Repasar difíciles (' + nAqui + ')';
    btnD.disabled = nAqui === 0;

    const total = Object.keys(dificiles).length;
    elNota.textContent = total === 0
      ? 'Todavía no marcaste ninguna tarjeta como difícil (botón ☆ Difícil durante el estudio).'
      : 'Guardadas: ' + (total - nDesm) + ' activa(s)' +
        (nDesm > 0 ? ', ' + nDesm + ' desmarcada(s)' : '');
  }

  document.getElementById('btn-exportar-dificiles').addEventListener('click', () => {
    // Solo se exportan las activas (las desmarcadas son un estado local)
    const lista = Object.keys(dificiles)
      .filter((h) => dificiles[h].activa)
      .map((h) => ({
        h: h,
        p: dificiles[h].p || '',
        s: dificiles[h].s || '',
      }));
    const contenido = JSON.stringify({ version: 1, tarjetas: lista }, null, 2);
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

        let nuevas = 0, repetidas = 0, invalidas = 0;
        lista.forEach((item) => {
          if (!item || typeof item.h !== 'string' || !item.h) { invalidas++; return; }
          if (dificiles[item.h]) { repetidas++; return; }  // fusionar: no duplica
          dificiles[item.h] = {
            p: typeof item.p === 'string' ? item.p : '',
            s: typeof item.s === 'string' ? item.s : '',
            activa: true,
          };
          nuevas++;
        });
        guardarDificiles();
        renderDificilesPreview();
        elNota.textContent = 'Importación: ' + nuevas + ' nueva(s), ' + repetidas +
          ' ya estaban' + (invalidas > 0 ? ', ' + invalidas + ' inválidas' : '');
      } catch (e) {
        elNota.textContent = 'El archivo no es un JSON válido de difíciles.';
      }
    };
    lector.readAsText(archivo);
  });

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
        renderNotasPreview();
      }));
    }
    acc.appendChild(botonMini('Usar la del txt', () => {
      if (!confirm('Se descarta tu versión y se usa la del txt. ¿Continuar?')) return;
      descartarNotaLocal(t);
      renderNotasPreview();
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
      renderNotasPreview();
    }));
    div.appendChild(acc);
    return div;
  }

  function renderNotasPreview() {
    const elDif = document.getElementById('lista-notas-dif');
    const elDetDif = document.getElementById('det-notas-dif');
    const elSumaDif = document.getElementById('suma-notas-dif');
    const elOtras = document.getElementById('lista-notas-otras');
    const elDetOtras = document.getElementById('det-notas-otras');
    const elSumaOtras = document.getElementById('suma-notas-otras');
    const elInfo = document.getElementById('nota-notas');
    elDif.innerHTML = '';
    elOtras.innerHTML = '';

    // 1) Tarjetas de ESTE evaluador cuya nota local difiere de la del txt
    const hashesAqui = new Set();
    let nDif = 0, nConflicto = 0;
    tarjetasCompletas.forEach((t) => {
      const h = hashTarjeta(t);
      if (hashesAqui.has(h)) return;
      hashesAqui.add(h);
      const est = estadoNotaDe(t);
      if (est === 'igual') return;
      nDif++;
      if (est === 'conflicto') nConflicto++;
      elDif.appendChild(filaNotaDif(t, est));
    });

    // 2) Notas guardadas que no corresponden a ninguna tarjeta de este txt
    //    (otro evaluador, o el primero/segundo cambió y el hash ya no coincide)
    let nOtras = 0;
    Object.keys(notasLocales).forEach((h) => {
      if (hashesAqui.has(h)) return;
      nOtras++;
      elOtras.appendChild(filaNotaOtra(h, notasLocales[h] || {}));
    });

    elDetDif.style.display = nDif > 0 ? 'block' : 'none';
    elSumaDif.textContent = 'Distintas a las del txt (' + nDif + ')' +
      (nConflicto > 0 ? ' — ' + nConflicto + ' con conflicto' : '');
    elDetOtras.style.display = nOtras > 0 ? 'block' : 'none';
    elSumaOtras.textContent = 'De otros evaluadores (' + nOtras + ')';

    const total = Object.keys(notasLocales).length;
    elInfo.textContent = total === 0
      ? 'Todavía no editaste ninguna nota (botón 💡 durante el estudio).'
      : 'Guardadas en este navegador: ' + total;
    document.getElementById('btn-vaciar-notas').style.display = total > 0 ? 'inline-block' : 'none';
  }

  // NUEVO: mismo patrón que "Vaciar todas" de razones
  document.getElementById('btn-vaciar-notas').addEventListener('click', () => {
    if (Object.keys(notasLocales).length === 0) return;
    if (!confirm('¿Vaciar TODAS las notas guardadas? No se puede deshacer.')) return;
    notasLocales = {};
    guardarNotas();
    renderNotasPreview();
  });

  document.getElementById('btn-exportar-notas').addEventListener('click', () => {
    const lista = Object.keys(notasLocales).map((h) => ({
      h: h,
      p: notasLocales[h].p || '',
      s: notasLocales[h].s || '',
      nota: notasLocales[h].nota || '',
      base: notasLocales[h].base || '',
      t: notasLocales[h].t || 0,
    }));
    const contenido = JSON.stringify({ version: 1, notas: lista }, null, 2);
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

        // Fusión: si la misma tarjeta tiene nota distinta, gana la más reciente (campo t)
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
        elInfo.textContent = 'Importación: ' + nuevas + ' nueva(s), ' + actualizadas +
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
      renderRazonesPreview();
    }));
    div.appendChild(acc);
    return div;
  }

  function renderRazonesPreview() {
    const elAqui = document.getElementById('lista-razones-aqui');
    const elDetAqui = document.getElementById('det-razones-aqui');
    const elSumaAqui = document.getElementById('suma-razones-aqui');
    const elOtras = document.getElementById('lista-razones-otras');
    const elDetOtras = document.getElementById('det-razones-otras');
    const elSumaOtras = document.getElementById('suma-razones-otras');
    const elInfo = document.getElementById('nota-razones');
    const elVaciar = document.getElementById('btn-vaciar-razones');
    elAqui.innerHTML = '';
    elOtras.innerHTML = '';

    const hashesAqui = new Set(tarjetasCompletas.map((t) => hashTarjeta(t)));
    let nAqui = 0, nOtras = 0;
    Object.keys(razonesGuardadas).forEach((h) => {
      if (hashesAqui.has(h)) { nAqui++; elAqui.appendChild(filaRazonGuardada(h, razonesGuardadas[h])); }
      else { nOtras++; elOtras.appendChild(filaRazonGuardada(h, razonesGuardadas[h])); }
    });
    elDetAqui.style.display = nAqui > 0 ? 'block' : 'none';
    elSumaAqui.textContent = 'De este evaluador (' + nAqui + ')';
    elDetOtras.style.display = nOtras > 0 ? 'block' : 'none';
    elSumaOtras.textContent = 'De otros evaluadores (' + nOtras + ')';
    const total = nAqui + nOtras;
    elInfo.textContent = total === 0
      ? 'Todavía no guardaste razones (botón 🏷 durante el estudio).'
      : 'Guardadas en este navegador: ' + total;
    elVaciar.style.display = total > 0 ? 'inline-block' : 'none';
  }

  document.getElementById('btn-vaciar-razones').addEventListener('click', () => {
    if (Object.keys(razonesGuardadas).length === 0) return;
    if (!confirm('¿Vaciar TODAS las razones guardadas? No se puede deshacer.')) return;
    razonesGuardadas = {};
    guardarRazones();
    renderRazonesPreview();
  });

  document.getElementById('btn-exportar-razones').addEventListener('click', () => {
    const lista = Object.keys(razonesGuardadas).map((h) => ({
      h: h,
      p: razonesGuardadas[h].p || '',
      s: razonesGuardadas[h].s || '',
      razones: razonesGuardadas[h].razones || [],
      estado: razonesGuardadas[h].estado || null,
      t: razonesGuardadas[h].t || 0,
    }));
    const contenido = JSON.stringify({ version: 1, razones: lista }, null, 2);
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
        elInfo.textContent = 'Importación: ' + nuevas + ' nueva(s), ' + actualizadas +
          ' actualizada(s) por ser más recientes, ' + conservadas + ' conservada(s) (la tuya era más reciente), ' +
          iguales + ' ya estaban igual' + (invalidas > 0 ? ', ' + invalidas + ' inválidas' : '');
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

    elResultadoEval.className = 'resultado-eval ' + ev.resultado;
  }

  function evaluarRespuesta() {
    if (pausaManual) return;
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

    escritos[indiceActual] = texto;
    evaluaciones[indiceActual] = {
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

    // Auto-marca: bien -> Entendida; casi -> Casi (0.5); mal -> No entendida.
    // NO avanza: el feedback queda a la vista y el avance es manual.
    if (cronometroActivo && !cronometroCongelado()) {
      acumularTiempo();
    }
    resultados[indiceActual] = (resultado === 'bien') ? 'si' : (resultado === 'casi' ? 'casi' : 'no');
    revelado[indiceActual] = true;

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
    resultados[indiceActual] = resultado;
    revelado[indiceActual] = true;

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
    resultados[indiceActual] = 'si';
    porComprension[indiceActual] = true;
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

  function guardarRazones() {
    try {
      localStorage.setItem(CLAVE_RAZONES, JSON.stringify(razonesGuardadas));
    } catch (e) {
      // sin localStorage: quedan solo en memoria de esta sesión
    }
  }

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
  // termina como Entendida)
  elBtnPista.addEventListener('click', () => {
    revelado[indiceActual] = true;
    pistaMostrada[indiceActual] = true;
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
      if (r === 'si') puntos += pistaMostrada[i] ? 0.5 : 1;
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

  // MODIFICADO: recibe índices y los agrupa en desplegables por subT.
  // Orden: subT en el orden del txt; dentro de cada subT, tarjetas en el orden del txt.
  // mostrarRazones=false se usa para la sección "Revisar" (sin chips de razones).
  function llenarLista(idContenedor, indices, mostrarRazones) {
    if (mostrarRazones === undefined) mostrarRazones = true;
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
      lista.forEach((i) => det.appendChild(crearItemResultado(i, false, mostrarRazones)));
      el.appendChild(det);
    });
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

    document.getElementById('conteo-si').textContent = conteos.si;
    document.getElementById('conteo-casi').textContent = conteos.casi;
    document.getElementById('conteo-no').textContent = conteos.no;
    document.getElementById('conteo-saltar').textContent = conteos.saltar;

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
    elBtnRepasarNo.style.display = (conteos.no + conteos.casi) > 0 ? 'block' : 'none';
    elBtnRepasarNo.onclick = () => {
      let pendientes = tarjetasSesion.filter((_, i) => resultados[i] === 'no' || resultados[i] === 'casi');
      if (modoAleatorio) pendientes = mezclar(pendientes);
      iniciarSesion(pendientes);
    };

    document.getElementById('btn-reiniciar').onclick = () => {
      const nuevaLista = modoAleatorio ? mezclar(tarjetasSesion) : [...tarjetasSesion];
      iniciarSesion(nuevaLista);
    };

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

  // ---------- Arranque ----------
  construirListaTemas();
  mostrarPantalla('temas');
</script>

</body>
</html>
"""


# ---------------- CONFIGURACIÓN ----------------

from tkinter import Toplevel, Label, Frame, Button

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


colores = {
    "amarillo": ["#DEDE00", "#5C5C5C"],
    "celeste": ["#00CCCC", "#5C5C5C"],
    "rojo": ["#FF0000", "#5C5C5C"],
    "verde": ["#66CC00", "#5C5C5C"],
    "morado": ["#7F00FF", "#5C5C5C"],
    "rosado": ["#FF66FF", "#5C5C5C"],
    "azul": ["#0000FF", "#5C5C5C"],
    "bordo": ["#FF0080", "#5C5C5C"],
    "naranja": ["#FF8000", "#5C5C5C"],
    "verdeAgua": ["#0BD0AF", "#5C5C5C"],
    "gris": ["#999999", "#5C5C5C"],
    "marron": ["#89552A", "#5C5C5C"],
    "fucsia": ["#DC7BFF", "#5C5C5C"],
    "azulGrisaceo": ["#3399FF", "#5C5C5C"],
    "verdeOscuro": ["#14B866", "#5C5C5C"],
    "naranjaOscuro": ["#FF5A36", "#5C5C5C"],
    "rojoOscuro": ["#8B0000", "#5C5C5C"],
    "amarilloOscuro": ["#556B2F", "#5C5C5C"],
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
opciones = elegir_color(raiz, colores, COLOR_DEFAULT)
print(f"Color elegido: {opciones}")
if opciones in colores:
    fill_color = colores[opciones][0]
    stroke_color = colores[opciones][-1]
else:
    fill_color = colores[COLOR_DEFAULT][0]
    stroke_color = colores[COLOR_DEFAULT][-1]

#--- Ventana personalizada para preguntar por el resaltado ---
aplicar_resaltado = preguntar_resaltado(raiz)
print(f"Resaltado en drawio: {'Sí' if aplicar_resaltado else 'No'}")

#Modo de estudio: 1 = secuencial (orden del recordatorio.txt), 2 = aleatorio
#El mezclado real ocurre en el navegador (JS), así que "Reiniciar" vuelve a mezclar.
modo = 2

if txt_respuestas:
    rutas = parsear_respuestas(txt_respuestas)
else:
    rutas = {}
    print("Aviso: no se seleccionó respuestas.txt; ninguna tarjeta tendrá botón 'Mostrar respuesta'.")

tarjetas = parsear_recordatorio(txt)
reportar_asociaciones(tarjetas, rutas)

#--- Resaltado del drawio (según la elección de la ventanita personalizada) ---
if aplicar_resaltado:
    fn_mapa = cargar_generador_respuestas()
    if fn_mapa is not None:
        try:
            resaltar_drawio(ruta_drawio, fn_mapa, tarjetas)
        except Exception as error:
            print(f"AVISO: falló el resaltado del drawio: {error}")
            print("       Se continúa con la generación del evaluador.")
    else:
        print("AVISO: No se pudo cargar el módulo para resaltar el drawio.")
else:
    print("El usuario eligió omitir el resaltado del drawio.")

generar_html(tarjetas, rutas, fill_color, stroke_color, modo == 2, txt_respuestas, Path(txt_respuestas).stem)
raiz.destroy()