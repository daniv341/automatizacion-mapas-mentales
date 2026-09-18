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
    Devuelve claves normalizadas (minúsculas, sin acentos), sin duplicados.
    """
    if not segundo:
        return []
    claves = []
    for token in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+', segundo):
        normalizada = normalizar_palabra(token)
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

    for renglon in lineas:
        renglon = renglon.strip()

        if not renglon:
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
        })

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

    faltantes = sorted({n for t in tarjetas for n in t["rutas"] if n not in rutas})
    if faltantes:
        print("AVISO: estas rutas se citan pero NO existen en respuestas.txt "
              "(serán ignoradas): " + ", ".join(map(str, faltantes)))
    else:
        print("Todas las rutas citadas existen en respuestas.txt.")


# ============================================================
# RESALTADO EN EL DIAGRAMA DRAWIO (marcador amarillo)
# ============================================================

# Color del marcador sobre el diagrama (cambialo acá si querés otro)
COLOR_RESALTADO = "#FFFF00"

PATRON_PALABRA = re.compile(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+')
# Limpieza: quita exactamente los tags <font style="background-color:...">...</font>
# que este script insertó (cualquier color, por si se cambió COLOR_RESALTADO entre corridas)
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


def resaltar_texto(texto, claves):
    """
    Envuelve en <font style="background-color:..."> las palabras cuyo
    normalized match esté en claves. Devuelve (nuevo_texto, hubo_cambio).
    """
    partes = []
    ultimo = 0
    cambio = False
    for m in PATRON_PALABRA.finditer(texto):
        if normalizar_palabra(m.group(0)) in claves:
            partes.append(texto[ultimo:m.start()])
            partes.append(
                f'<font style="background-color:{COLOR_RESALTADO}">' + m.group(0) + "</font>"
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

    # 2) Resaltado nuevo
    resaltadas = 0
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
        nuevo, cambio = resaltar_texto(texto, claves)
        if cambio:
            elem.set(attr, nuevo)
            activar_html_en_celda(elem)  # drawio solo interpreta HTML con html=1
            resaltadas += 1

    tree.write(ruta_drawio, encoding="utf-8", xml_declaration=True)

    print(f"Drawio resaltado: {resaltadas} celdas modificadas "
          f"({convergencias_resueltas} convergencias resueltas).")
    citadas = {n for t in tarjetas for n in t.get("rutas", [])}
    ausentes = sorted(citadas - set(mapa_rutas.keys()))
    if ausentes:
        print("AVISO: rutas citadas que no aparecen en el mapa del drawio: "
              + ", ".join(map(str, ausentes)))


# ============================================================
# GENERACIÓN DEL HTML
# ============================================================

def generar_html(tarjetas, rutas, fill_color, stroke_color, modo_aleatorio, ruta_drawio):
    datos = {"tarjetas": tarjetas, "rutas": rutas}
    datos_json = json.dumps(datos, ensure_ascii=False)
    nombre_base = Path(ruta_drawio).stem

    html = HTML_TEMPLATE
    html = html.replace("__FILL_COLOR__", fill_color)
    html = html.replace("__STROKE_COLOR__", stroke_color)
    html = html.replace("__DATOS_JSON__", datos_json)
    html = html.replace("__MODO_ALEATORIO__", "true" if modo_aleatorio else "false")
    html = html.replace("__UMBRAL_CASI__", str(UMBRAL_CASI))
    html = html.replace("__SIM_ALTA__", str(SIM_ALTA))

    with open(f"guardados/html/evaluador_{nombre_base}.html", "w", encoding="utf-8") as archivo:
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
    padding: 24px;
    text-align: center;
  }

  h1 {
    font-size: 22px;
    font-weight: 600;
    margin-bottom: 18px;
    color: #444;
  }

  .subtitulo {
    font-size: 14px;
    color: #888;
    margin-bottom: 16px;
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
    margin-bottom: 24px;
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
    margin-bottom: 18px;
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
    min-height: 220px;
    border-radius: 6px;
    padding: 40px 30px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 20px;
    box-shadow: 0 10px 24px rgba(0,0,0,0.12);
    opacity: 0;
    transform: translateY(8px);
    animation: aparecer 0.35s ease forwards;
  }

  @keyframes aparecer {
    to { opacity: 1; transform: translateY(0); }
  }

  .primero {
    font-size: 19px;
    font-weight: 700;
    color: var(--stroke-color);
    line-height: 1.4;
  }

  .zona-segundo {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
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
    padding: 10px 22px;
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
    padding: 10px 22px;
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

  .botonera {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 18px;
    margin-top: 10px;
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
    width: 64px;
    height: 64px;
    border-radius: 50%;
    font-size: 26px;
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .boton-no { background: #e0574c; }
  .boton-si { background: #3aa76d; }

  .boton-saltar {
    padding: 12px 20px;
    border-radius: 24px;
    background: #ffffff;
    color: #888;
    border: 2px solid #d8d8e0;
  }

  .navegacion {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 26px;
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
    padding: 40px 30px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.1);
  }

  .pantalla-resumen h2 {
    margin-top: 0;
    color: #444;
  }

  .fila-resumen {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 6px;
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
    margin-top: 26px;
  }

  .boton-reiniciar, .boton-secundario {
    padding: 12px 26px;
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
    margin-top: 26px;
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
    <p class="subtitulo">Elegí qué temas querés estudiar</p>
    <div class="lista-temas" id="lista-temas"></div>
    <button class="boton-principal" id="btn-comenzar">Comenzar estudio</button>
  </div>

  <div id="area-tarjeta" style="display:none;">
    <div class="barra-superior">
      <div class="progreso-texto" id="progreso-texto"></div>
      <div class="cronometro" id="cronometro">⏱ 00:00</div>
    </div>
    <div class="barra-progreso"><div class="barra-progreso-relleno" id="barra-relleno"></div></div>

    <div class="tema" id="tema-actual">
      <span class="tema-rombo"></span>
      <span id="tema-texto"></span>
    </div>

    <div class="tarjeta" id="tarjeta">
      <div class="primero" id="tarjeta-primero"></div>
      <div class="zona-segundo" id="zona-segundo">
        <div class="fila-botones-tarjeta">
          <button class="boton-pista" id="btn-pista">Mostrar pista</button>
          <button class="boton-respuesta" id="btn-respuesta">Mostrar respuesta</button>
        </div>
        <div class="separador-tarjeta" id="separador-tarjeta" style="display:none;"></div>
        <div class="segundo" id="tarjeta-segundo" style="display:none;"></div>
      </div>
    </div>

    <div class="zona-eval" id="zona-eval">
      <textarea id="texto-eval" placeholder="Escribí la respuesta con tus palabras (opcional)"></textarea>
      <div class="fila-eval">
        <button class="boton-pista" id="btn-evaluar">Evaluar respuesta</button>
      </div>
    </div>

    <div class="rutas-contenedor" id="rutas-contenedor"></div>

    <div class="estado-marca" id="estado-marca"></div>

    <div class="resultado-eval" id="resultado-eval"></div>

    <div class="botonera">
      <button class="boton boton-circular boton-no" id="btn-no" title="No entendido">&#10007;</button>
      <button class="boton boton-saltar" id="btn-saltar">Pasar sin marcar</button>
      <button class="boton boton-circular boton-si" id="btn-si" title="Entendido">&#10003;</button>
    </div>

    <div class="navegacion">
      <button class="boton-nav" id="btn-anterior">&larr; Anterior</button>
      <button class="boton-finalizar" id="btn-finalizar">Finalizar sesión</button>
      <button class="boton-nav" id="btn-siguiente">Siguiente &rarr;</button>
    </div>
  </div>

  <div class="pantalla-resumen" id="pantalla-resumen">
    <h2>Resumen de la sesión</h2>
    <div class="fila-resumen">
      <span class="etiqueta-resumen"><span class="punto punto-si"></span> Entendidas</span>
      <span class="valor-resumen" id="conteo-si">0</span>
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
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-si"></span> Entendidas</span></summary>
        <div class="lista-contenido" id="lista-si"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-no"></span> No entendidas</span></summary>
        <div class="lista-contenido" id="lista-no"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen"><span class="punto punto-saltar"></span> Pasadas sin marcar</span></summary>
        <div class="lista-contenido" id="lista-saltar"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen">⏱ Tiempos por tarjeta</span></summary>
        <div class="lista-contenido" id="lista-tiempos"></div>
      </details>
      <details class="lista-desplegable">
        <summary><span class="etiqueta-resumen">📝 Respuestas escritas</span></summary>
        <div class="lista-contenido" id="lista-escritas"></div>
      </details>
    </div>

    <div class="acciones-resumen">
      <button class="boton-reiniciar" id="btn-repasar-no" style="display:none;">Repasar las que no entendí</button>
      <button class="boton-reiniciar" id="btn-reiniciar">Reiniciar esta ronda</button>
      <button class="boton-secundario" id="btn-elegir-temas">Elegir otros temas</button>
    </div>
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

  // ---------- Estado de la ronda actual ----------
  let tarjetasSesion = [];
  let resultados = [];
  let tiempos = [];
  let revelado = [];
  let respuestaMostrada = [];
  let escritos = [];       // texto escrito por tarjeta (aunque no se evalue)
  let evaluaciones = [];   // {resultado:'bien'|'casi'|'mal', faltaron:[], sim} o null
  let indiceActual = 0;
  let indiceCongelado = -1;   // índice de la tarjeta cuya vista congela el cronómetro
  let tiempoInicioTarjeta = 0;
  let cronometroIntervalId = null;

  // ---------- Elementos ----------
  const elPantallaTemas = document.getElementById('pantalla-temas');
  const elListaTemas = document.getElementById('lista-temas');
  const elAreaTarjeta = document.getElementById('area-tarjeta');
  const elPantallaResumen = document.getElementById('pantalla-resumen');

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
  const elResultadoEval = document.getElementById('resultado-eval');

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

    if (nombre !== 'estudio') {
      detenerCronometro();
    }
  }

  // ---------- Pantalla de selección de temas ----------

  function nombreTema(tarjeta) {
    return tarjeta.tema && tarjeta.tema.trim() !== '' ? tarjeta.tema : '(Sin tema)';
  }

  function construirListaTemas() {
    const vistos = new Set();
    const temas = [];
    tarjetasCompletas.forEach((t) => {
      const nombre = nombreTema(t);
      if (!vistos.has(nombre)) {
        vistos.add(nombre);
        temas.push(nombre);
      }
    });

    elListaTemas.innerHTML = '';
    temas.forEach((tema, i) => {
      const item = document.createElement('label');
      item.className = 'item-tema-check';

      const check = document.createElement('input');
      check.type = 'checkbox';
      check.checked = true;
      check.value = tema;
      check.id = 'tema-check-' + i;

      const texto = document.createElement('span');
      texto.textContent = tema;

      item.appendChild(check);
      item.appendChild(texto);
      elListaTemas.appendChild(item);
    });
  }

  function temasSeleccionados() {
    const marcados = elListaTemas.querySelectorAll('input:checked');
    return new Set(Array.from(marcados).map((c) => c.value));
  }

  document.getElementById('btn-comenzar').addEventListener('click', () => {
    const seleccion = temasSeleccionados();
    if (seleccion.size === 0) return;

    let filtradas = tarjetasCompletas.filter((t) => seleccion.has(nombreTema(t)));
    if (modoAleatorio) filtradas = mezclar(filtradas);

    iniciarSesion(filtradas);
  });

  document.getElementById('btn-elegir-temas').addEventListener('click', () => {
    mostrarPantalla('temas');
  });

  // ---------- Sesión de estudio ----------

  function iniciarSesion(lista) {
    tarjetasSesion = lista;
    resultados = new Array(lista.length).fill(null);
    tiempos = new Array(lista.length).fill(0);
    revelado = new Array(lista.length).fill(false);
    respuestaMostrada = new Array(lista.length).fill(false);
    escritos = new Array(lista.length).fill('');
    evaluaciones = new Array(lista.length).fill(null);
    indiceActual = 0;
    indiceCongelado = -1;
    tiempoInicioTarjeta = Date.now();

    mostrarPantalla('estudio');
    iniciarCronometro();
    renderTarjeta();
  }

  function acumularTiempo() {
    const ahora = Date.now();
    tiempos[indiceActual] += (ahora - tiempoInicioTarjeta);
    tiempoInicioTarjeta = ahora;
  }

  function cronometroCongelado() {
    return indiceCongelado === indiceActual;
  }

  function renderTarjeta() {
    if (tarjetasSesion.length === 0) {
      elAreaTarjeta.innerHTML = '<p class="sin-tarjetas">No hay tarjetas para estudiar con los temas elegidos.</p>';
      return;
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
      textoEstado = 'Ya marcada: Entendida' + (respuestaMostrada[indiceActual] ? ' — respuesta mostrada' : '');
      claseEstado = 'estado-marca si';
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
    elBtnSi.disabled = yaMarcada;
    elBtnNo.disabled = yaMarcada;
    elBtnSaltar.disabled = yaMarcada;

    elBtnAnterior.disabled = indiceActual === 0;
    elBtnSiguiente.disabled = indiceActual === tarjetasSesion.length - 1;

    // Zona de respuesta escrita: visible solo si la tarjeta es evaluable
    // y todavía no está marcada
    const puedeEvaluar = (marcada === null) && evaluable(actual);
    elZonaEval.style.display = puedeEvaluar ? 'block' : 'none';
    elResultadoEval.style.display = evaluaciones[indiceActual] ? 'block' : 'none';
    if (evaluaciones[indiceActual]) {
      renderResultadoEval(evaluaciones[indiceActual]);
    }
    if (puedeEvaluar) {
      elTextoEval.value = escritos[indiceActual] || '';
      elBtnEvaluar.disabled = (elTextoEval.value.trim() === '');
    }

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

  function tokenizar(texto) {
    const salida = [];
    for (const m of texto.matchAll(/[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+/g)) {
      salida.push(normalizarPalabra(m[0]));
    }
    return salida;
  }

  function similitudDice(tokensA, tokensB) {
    const a = new Set(tokensA), b = new Set(tokensB);
    if (a.size === 0 || b.size === 0) return 0;
    let inter = 0;
    for (const w of a) if (b.has(w)) inter++;
    return (2 * inter) / (a.size + b.size);
  }

  // Claves para evaluar: mismo criterio del resaltado, sin palabras de 1-2 letras
  function clavesEvalDe(tarjeta) {
    return (tarjeta.claves || []).filter((c) => c.length >= 3);
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

  function renderResultadoEval(ev) {
    let texto;
    if (ev.resultado === 'bien') texto = '✓ Bien';
    else if (ev.resultado === 'casi') texto = '✗ Casi';
    else texto = '✗ Mal';

    if (ev.faltaron.length > 0) texto += ' — faltaron: ' + ev.faltaron.join(', ');
    texto += ' — coincidencia con el contenido: ' + Math.round(ev.sim * 100) + '%';

    elResultadoEval.textContent = texto;
    elResultadoEval.className = 'resultado-eval ' + ev.resultado;
  }

  function evaluarRespuesta() {
    if (resultados[indiceActual] !== null) return;
    const texto = elTextoEval.value;
    if (texto.trim() === '') return;

    const actual = tarjetasSesion[indiceActual];
    const claves = clavesEvalDe(actual);
    const escritas = new Set(tokenizar(texto));

    const faltaron = claves.filter((c) => !escritas.has(c));
    const propClaves = claves.length > 0
      ? (claves.length - faltaron.length) / claves.length : null;

    const referencia = textoReferenciaDe(actual);
    const sim = similitudDice([...escritas], tokenizar(referencia));

    let resultado;
    if (claves.length > 0) {
      if (propClaves === 1 || (propClaves >= UMBRAL_CASI && sim >= SIM_ALTA)) resultado = 'bien';
      else if (propClaves >= UMBRAL_CASI) resultado = 'casi';
      else resultado = 'mal';
    } else {
      if (sim >= SIM_ALTA) resultado = 'bien';
      else if (sim >= UMBRAL_CASI) resultado = 'casi';
      else resultado = 'mal';
    }

    escritos[indiceActual] = texto;
    evaluaciones[indiceActual] = { resultado, faltaron, sim };

    // Auto-marca: bien -> Entendida; casi/mal -> No entendida.
    // NO avanza: el feedback queda a la vista y el avance es manual.
    acumularTiempo();
    resultados[indiceActual] = (resultado === 'bien') ? 'si' : 'no';
    revelado[indiceActual] = true;

    renderTarjeta();
  }

  function llenarListaEscritas() {
    const el = document.getElementById('lista-escritas');
    el.innerHTML = '';

    const filas = [];
    tarjetasSesion.forEach((tarjeta, i) => {
      if (evaluaciones[i]) filas.push({ tarjeta, i });
    });

    if (filas.length === 0) {
      el.innerHTML = '<div class="lista-vacia">No se evaluó ninguna respuesta escrita</div>';
      return;
    }

    filas.forEach(({ tarjeta, i }) => {
      const ev = evaluaciones[i];
      const item = document.createElement('div');
      item.className = 'item-lista';

      const tema = document.createElement('span');
      tema.className = 'item-tema';
      tema.textContent = nombreTema(tarjeta);
      item.appendChild(tema);

      const renglon = document.createElement('span');
      renglon.className = 'item-renglon';
      renglon.textContent = armarRenglon(tarjeta);
      item.appendChild(renglon);

      const escrito = document.createElement('span');
      escrito.className = 'item-escrito';
      escrito.textContent = '"' + (escritos[i] || '') + '"';
      item.appendChild(escrito);

      const resultado = document.createElement('span');
      resultado.className = 'item-resultado ' + ev.resultado;
      let txt = ev.resultado === 'bien' ? '✓ Bien' : (ev.resultado === 'casi' ? '✗ Casi' : '✗ Mal');
      if (ev.faltaron.length > 0) txt += ' — faltaron: ' + ev.faltaron.join(', ');
      txt += ' (coincidencia ' + Math.round(ev.sim * 100) + '%)';
      resultado.textContent = txt;
      item.appendChild(resultado);

      el.appendChild(item);
    });
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
    if (resultados[indiceActual] !== null) return;

    acumularTiempo();
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

    // Congelar el cronómetro de TODA la sesión: el tiempo de esta tarjeta
    // queda contado hasta este momento; el tiempo de lectura no cuenta.
    acumularTiempo();
    indiceCongelado = indiceActual;

    // Auto-calificar como "No entendida" solo si todavía no estaba marcada.
    if (resultados[indiceActual] === null) {
      resultados[indiceActual] = 'no';
    }

    // NO avanza automáticamente: el usuario se queda leyendo y avanza con "Siguiente".
    actualizarCronometro();
    renderTarjeta();
  }

  function navegar(delta) {
    const nuevo = indiceActual + delta;
    if (nuevo < 0 || nuevo >= tarjetasSesion.length) return;

    if (cronometroCongelado()) {
      // Se estaba viendo una respuesta: el tiempo de esa tarjeta ya fue
      // acumulado al congelar. Solo se reanuda el reloj para la nueva tarjeta.
      indiceActual = nuevo;
      tiempoInicioTarjeta = Date.now();
    } else {
      acumularTiempo();
      indiceActual = nuevo;
    }

    renderTarjeta();
  }

  elBtnPista.addEventListener('click', () => {
    revelado[indiceActual] = true;
    renderTarjeta();
  });

  elBtnRespuesta.addEventListener('click', () => mostrarRespuesta());

  elBtnEvaluar.addEventListener('click', () => evaluarRespuesta());
  elTextoEval.addEventListener('input', () => {
    escritos[indiceActual] = elTextoEval.value;
    elBtnEvaluar.disabled = (elTextoEval.value.trim() === '');
  });

  elBtnSi.addEventListener('click', () => marcar('si'));
  elBtnNo.addEventListener('click', () => marcar('no'));
  elBtnSaltar.addEventListener('click', () => marcar('saltar'));
  elBtnAnterior.addEventListener('click', () => navegar(-1));
  elBtnSiguiente.addEventListener('click', () => navegar(1));
  elBtnFinalizar.addEventListener('click', () => finalizarSesion());

  document.addEventListener('keydown', (evento) => {
    if (elLightbox.classList.contains('abierto')) {
      if (evento.key === 'Escape') cerrarLightbox();
      return;
    }
    if (evento.target === elTextoEval) return;  // no navegar mientras se escribe
    if (elAreaTarjeta.style.display === 'none') return;
    if (evento.key === 'ArrowLeft') navegar(-1);
    if (evento.key === 'ArrowRight') navegar(1);
  });

  // ---------- Cronómetro ----------

  function actualizarCronometro() {
    const congelado = cronometroCongelado();
    const ahora = Date.now();
    const enCurso = congelado ? 0 : (ahora - tiempoInicioTarjeta);
    const totalMs = tiempos.reduce((a, b) => a + b, 0) + enCurso;
    elCronometro.textContent = (congelado ? '⏸ ' : '⏱ ') + formatearTiempo(totalMs);
    elCronometro.className = congelado ? 'cronometro congelado' : 'cronometro';
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
    // Si se está viendo una respuesta (cronómetro congelado), el tiempo de
    // lectura NO se cuenta: no acumular nada extra.
    if (!cronometroCongelado()) {
      acumularTiempo();
    }
    for (let i = 0; i < resultados.length; i++) {
      if (resultados[i] === null) resultados[i] = 'saltar';
    }
    mostrarResumen();
  }

  function llenarLista(idContenedor, tarjetasCategoria) {
    const el = document.getElementById(idContenedor);
    el.innerHTML = '';

    if (tarjetasCategoria.length === 0) {
      el.innerHTML = '<div class="lista-vacia">No hay tarjetas en esta categoría</div>';
      return;
    }

    tarjetasCategoria.forEach((tarjeta) => {
      const item = document.createElement('div');
      item.className = 'item-lista';

      const tema = document.createElement('span');
      tema.className = 'item-tema';
      tema.textContent = nombreTema(tarjeta);
      item.appendChild(tema);

      const renglon = document.createElement('span');
      renglon.className = 'item-renglon';
      renglon.textContent = armarRenglon(tarjeta);
      item.appendChild(renglon);

      el.appendChild(item);
    });
  }

  function llenarListaTiempos() {
    const el = document.getElementById('lista-tiempos');
    el.innerHTML = '';

    const filas = tarjetasSesion.map((tarjeta, i) => ({ tarjeta, ms: tiempos[i] }));
    filas.sort((a, b) => b.ms - a.ms);

    filas.forEach((fila) => {
      const item = document.createElement('div');
      item.className = 'item-lista';

      const tema = document.createElement('span');
      tema.className = 'item-tema';
      tema.textContent = nombreTema(fila.tarjeta);
      item.appendChild(tema);

      const renglon = document.createElement('span');
      renglon.className = 'item-renglon';
      renglon.textContent = armarRenglon(fila.tarjeta);
      item.appendChild(renglon);

      const tiempo = document.createElement('span');
      tiempo.className = 'item-tiempo';
      tiempo.textContent = formatearTiempo(fila.ms);
      item.appendChild(tiempo);

      el.appendChild(item);
    });
  }

  function mostrarResumen() {
    const conteos = { si: 0, no: 0, saltar: 0 };
    const listas = { si: [], no: [], saltar: [] };

    resultados.forEach((r, i) => {
      conteos[r]++;
      listas[r].push(tarjetasSesion[i]);
    });

    document.getElementById('conteo-si').textContent = conteos.si;
    document.getElementById('conteo-no').textContent = conteos.no;
    document.getElementById('conteo-saltar').textContent = conteos.saltar;

    const tiempoTotalMs = tiempos.reduce((a, b) => a + b, 0);
    const promedioMs = tarjetasSesion.length > 0 ? tiempoTotalMs / tarjetasSesion.length : 0;
    document.getElementById('tiempo-total').textContent = formatearTiempo(tiempoTotalMs);
    document.getElementById('tiempo-promedio').textContent = formatearTiempo(promedioMs);

    llenarLista('lista-si', listas.si);
    llenarLista('lista-no', listas.no);
    llenarLista('lista-saltar', listas.saltar);
    llenarListaTiempos();
    llenarListaEscritas();

    const elBtnRepasarNo = document.getElementById('btn-repasar-no');
    elBtnRepasarNo.style.display = conteos.no > 0 ? 'block' : 'none';
    elBtnRepasarNo.onclick = () => {
      let pendientes = tarjetasSesion.filter((_, i) => resultados[i] === 'no');
      if (modoAleatorio) pendientes = mezclar(pendientes);
      iniciarSesion(pendientes);
    };

    document.getElementById('btn-reiniciar').onclick = () => {
      const nuevaLista = modoAleatorio ? mezclar(tarjetasSesion) : [...tarjetasSesion];
      iniciarSesion(nuevaLista);
    };

    mostrarPantalla('resumen');
  }

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

raiz = Tk()
raiz.withdraw()

# --- Diálogo 1: recordatorio.txt (tarjetas) ---
txt = filedialog.askopenfilename(
    title="Selecciona el archivo recordatorio.txt",
    filetypes=[("Archivo de Texto", "*.txt"), ("Todos los archivos", "*.*")]
)

if not txt:
    print("No se seleccionó recordatorio.txt. Saliendo.")
    raiz.destroy()
    raise SystemExit

# --- Diálogo 2: respuestas.txt (rutas del diagrama) ---
txt_respuestas = filedialog.askopenfilename(
    title="Selecciona el archivo respuestas.txt",
    filetypes=[("Archivo de Texto", "*.txt"), ("Todos los archivos", "*.*")]
)

# --- Diálogo 3: drawio (OPCIONAL, para resaltar el diagrama) ---
ruta_drawio = filedialog.askopenfilename(
    title="Selecciona el archivo .drawio (opcional - Cancelar para omitir)",
    filetypes=[("Diagrama drawio", "*.drawio"), ("Todos los archivos", "*.*")]
)

# --- Ventana de elección de color ---
opciones = elegir_color(raiz, colores, COLOR_DEFAULT)
print(f"Color elegido: {opciones}")

if opciones in colores:
    fill_color = colores[opciones][0]
    stroke_color = colores[opciones][-1]
else:
    fill_color = colores[COLOR_DEFAULT][0]
    stroke_color = colores[COLOR_DEFAULT][-1]

# Modo de estudio: 1 = secuencial (orden del recordatorio.txt), 2 = aleatorio
# El mezclado real ocurre en el navegador (JS), así que "Reiniciar" vuelve a mezclar.
modo = 2

if txt_respuestas:
    rutas = parsear_respuestas(txt_respuestas)
else:
    rutas = {}
    print("Aviso: no se seleccionó respuestas.txt; ninguna tarjeta tendrá botón 'Mostrar respuesta'.")

tarjetas = parsear_recordatorio(txt)
reportar_asociaciones(tarjetas, rutas)

# --- Resaltado del drawio (opcional; independiente de respuestas.txt) ---
if ruta_drawio:
    fn_mapa = cargar_generador_respuestas()
    if fn_mapa is not None:
        try:
            resaltar_drawio(ruta_drawio, fn_mapa, tarjetas)
        except Exception as error:
            print(f"AVISO: falló el resaltado del drawio: {error}")
            print("       Se continúa con la generación del evaluador.")
    else:
        print("Se omite el resaltado del drawio.")
else:
    print("No se seleccionó drawio; se genera solo el evaluador.")

generar_html(tarjetas, rutas, fill_color, stroke_color, modo == 2, ruta_drawio)

raiz.destroy()