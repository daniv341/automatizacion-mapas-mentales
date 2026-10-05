# -*- coding: utf-8 -*-
# v4 — reescribe el archivo de entrada in place; antes de tocarlo guarda
#       el original como *_base.txt. Sin CLI: solo correr el script.
import re, os, sys, glob, unicodedata

# ==================== PARÁMETROS ====================
ARCHIVOS = ["modificables/borrador.txt"]   # o: sorted(glob.glob("[0-9]*.txt")) para el lote
SUFIJO_BASE = "_base"           # borrador.txt -> borrador_base.txt (copia del original)
MAX_PALABRAS = 5                # tope blando
ARTICULO_INICIAL = "conservar"  # "conservar" | "eliminar"
CONVERTIR_UN = True             # un -> 1 (salvo inicio de oración)
BREAK_PAREN = True              # "(" de varias palabras arranca línea

ARTICULOS = {"la","las","lo","los","el","de"}   # del/al/una jamás se tocan

DE_CONSERVAR    = {("general","de"),("caso","de"),("carga","de"),("dentro","de")}
DE_NEXT_PROTECT = {"acuerdo"}           # "de acuerdo"
RELATIVOS       = {"cual","cuales"}     # "la cual", "en el cual" conservan artículo
COLOCACIONES    = {("dado","por")}      # no cortar entre estos pares

CORTAR_ANTES = {"para","que","por","con","como","cómo","cuando","donde","según",
                "mediante","hacia","sobre","desde","si","sí","aunque","porque",
                "entre","dentro","hasta","excepto","entonces","pues","vía",
                "tras","ante","mientras","puesto","conforme","ya","tal","tales"}
BIGRAM_OPENERS = {("ya","que"),("por","tanto"),("por","lo"),("es","decir"),
                  ("tales","como"),("tal","como"),("a","fin"),
                  ("mientras","que"),("puesto","que")}
CORTAR_DESPUES = {"y","e","o","u","ni"}
MINIMO = {"en":3, "a":3}   # palabras mínimas en la línea para cortar ahí

VERBOS = {
    "es","son","está","están","sea","sean","esté","estén","fue","fueron","ser",
    "ha","han","hay","tiene","tienen","puede","pueden","debe","deben","va","van",
    "da","dan","dar","hace","hacen","hacer","define","definen","aplica","aplican",
    "usa","usan","utiliza","utilizan","brinda","brindan","aporta","aportan",
    "valora","evalúa","evalúan","ofrece","ofrecen","sirve","sirven","monitorea",
    "maximizan","aumenta","trabajan","participan","recoge","logra","logre",
    "llama","llaman","conoce","conocen","representa","representan","determina",
    "determinan","determinarse","afecta","afectan","contiene","contienen",
    "suministra","calcula","corresponde","corresponden","causa","causan","crece",
    "produce","producen","garantiza","garantizan","garantizar","reduce","asegura",
    "aseguran","genera","generan","encuentra","encuentran","requiere","requiere",
    "requieren","convierte","convierten","transforma","transforman","caracteriza",
    "caracterizan","manifiesta","manifiestan","denomina","difiere","ocasiona",
    "ocasione","retorna","tarda","alcanza","alcanzar","resulta","resultan",
    "existe","existen","permite","permiten","llega","llegar","establece",
    "establecen","establecer","establezca","obtiene","obtienen","obtener",
    "mantiene","mantienen","mantener","necesita","necesitan","lee","resta",
    "componen","evita","evitar","clasifica","clasificar","escribirse",
    "describirse","cuenta","cuentan","depende","dependen","persiste","persisten",
    "regule","gobierna","gobiernan","tenderá","tenderán","descendiera","pueda"}
CLITICOS = {"se","me","te","le","les","nos","os"}

FRASES_A_BORRAR = []   # p.ej. ["Hace seguimiento del trabajo del personal..."]
REEMPLAZOS = []        # p.ej. [("en algunos casos, los clientes", "en algunos casos de clientes")]
LIMPIEZA = {"&":"","“":"'","”":"'","\"":"'","—":""," / ":" y ","\u00a0":" "}
# ==================== FIN PARÁMETROS ====================

def sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")

def limpiar(t):
    for a, b in LIMPIEZA.items():
        t = t.replace(a, b)
    # "RR. HH." -> "RR HH" (para que el punto no parta la línea)
    return re.sub(r"\b((?:[A-ZÁÉÍÓÚÜÑ]{1,4}\.\s*)+[A-ZÁÉÍÓÚÜÑ]{1,4}\.?)",
                  lambda m: m.group(0).replace(".", "").strip(), t)

def es_titulo(linea):
    s = linea.strip()
    if s.lower().startswith("subt"):
        return True
    letras = [c for c in s if c.isalpha()]
    return len(letras) >= 3 and \
           sum(c.isupper() for c in letras) / len(letras) >= 0.8

def procesar_titulo(linea):
    t = " ".join(limpiar(linea).split())
    t = re.sub(r"\s+de\s+(la|las|los)\s+", " ", t, flags=re.I)
    t = re.sub(r"\s+de\s+", " ", t, flags=re.I)   # "del" no se toca
    return t.strip()

def partir(oracion):
    tokens = oracion.split()
    lineas, actual = [], []
    prev = ""
    n = len(tokens)

    def cerrar():
        nonlocal actual
        if actual:
            lineas.append(" ".join(actual))
            actual = []

    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < n else ""
        base = tok.lower().strip(",.;:()")
        base_next = nxt.lower().strip(",.;:()")

        if tok.startswith("\x00"):                     # marcador de <imagen>
            cerrar(); lineas.append(tok); prev = ""; continue

        if BREAK_PAREN and tok.startswith("(") and ")" not in tok:
            cerrar()                                   # paréntesis multi-palabra

        # --- artículos ---
        if base in ARTICULOS:
            queda = (base == "de" and tok[-1:] in ",;:") \
                 or (base == "de" and ((prev, base) in DE_CONSERVAR
                                       or base_next in DE_NEXT_PROTECT)) \
                 or (base == "lo" and prev == "por") \
                 or (base in ("el","la","los","las") and base_next in RELATIVOS)
            if not queda:
                if not actual and not lineas and ARTICULO_INICIAL == "conservar":
                    tok = tok.lower()
                else:
                    prev = base; continue

        if CONVERTIR_UN and base == "un" and (actual or lineas):
            tok = "1"

        # --- ¿abrir línea antes de este token? ---
        if actual:
            st = sin_tildes(tok.lower())
            gerundio = len(st) >= 7 and ("iendo" in st or "ando" in st)
            opener = base in CORTAR_ANTES \
                  or (base, base_next) in BIGRAM_OPENERS or gerundio
            verbo = base in VERBOS or (base in CLITICOS and base_next in VERBOS)
            abrir = False
            if (opener or verbo) and prev not in CORTAR_ANTES \
               and (prev, base) not in COLOCACIONES:
                abrir = len(actual) >= MINIMO.get(base, 1) if opener else len(actual) >= 2
            if abrir:
                lleva_no = base in CLITICOS and actual[-1].lower() == "no"
                if lleva_no: actual.pop()
                cerrar()
                if lleva_no: actual.append("no")       # "no se dispare" baja junto

        actual.append(tok)
        prev = base

        # --- ¿cerrar línea después de este token? ---
        cerrada = False
        if tok[-1:] in ",;:":
            cerrar(); cerrada = True
        elif base in CORTAR_DESPUES and len(actual) >= 2:
            p = actual[-2]
            pl = p.strip("()\".,;:")
            if p.endswith(")") or (pl.isalpha() and len(pl) >= 2):
                cerrar(); cerrada = True
        # tope blando: no corta si lo que sigue es artículo/conector o lleva coma
        if not cerrada and len(actual) >= MAX_PALABRAS and nxt:
            nb = nxt.lower().strip(",.;:()")
            if nxt[-1:] not in ",;:" and nb not in CORTAR_ANTES \
               and nb not in ARTICULOS and nb not in CORTAR_DESPUES:
                cerrar()
    cerrar()
    return lineas

def procesar_linea(linea):
    linea = linea.strip()
    marcador = None
    m = re.match(r"^(\d{1,2})\s*[.)]\s+(.*)$", linea)       # "1. ..." / "1) ..."
    m2 = re.match(r"^([a-z])\)\s+(.*)$", linea)             # "a) ..."
    if m:   marcador, linea = m.group(1), m.group(2)
    elif m2: marcador, linea = m2.group(1), m2.group(2)

    bloques = []
    texto = linea.replace("etc.", "etc\x01")
    for oracion in re.split(r"(?<=\w)\.\s+", texto):
        oracion = oracion.replace("\x01", ".").strip(" .;:")
        if oracion:
            ls = partir(oracion)
            if ls: bloques.append("\n".join(ls))
    if marcador and bloques:
        bloques[0] = marcador + "\n" + bloques[0]
    return bloques

def procesar(entrada):
    if not os.path.isfile(entrada):
        print(f"AVISO: no existe '{entrada}', se omite.", file=sys.stderr)
        return
    try:
        with open(entrada, encoding="utf-8") as f:
            texto = f.read()
    except UnicodeDecodeError:
        print(f"AVISO: '{entrada}' no está en UTF-8, se omite.", file=sys.stderr)
        return
    except OSError as e:
        print(f"AVISO: no se pudo leer '{entrada}' ({e}), se omite.", file=sys.stderr)
        return

    raiz, ext = os.path.splitext(entrada)
    base = f"{raiz}{SUFIJO_BASE}{ext}"        # modificables/borrador.txt -> borrador_base.txt

    # 1) guardar el original ANTES de modificar nada
    try:
        with open(base, "w", encoding="utf-8") as f:
            f.write(texto)
    except OSError as e:
        print(f"ERROR: no se pudo escribir '{base}' ({e}); '{entrada}' no se toca.",
              file=sys.stderr)
        return

    # 2) procesar
    tags = {}                                              # proteger <imagen> etc.
    def guardar(m):
        k = f"\x00{len(tags)}\x00"; tags[k] = m.group(0); return k
    texto = re.sub(r"<[^>\n]{1,60}>", guardar, texto)
    texto = limpiar(texto)
    for frase in FRASES_A_BORRAR: texto = texto.replace(frase, "")
    for a, b in REEMPLAZOS:       texto = texto.replace(a, b)

    bloques = []
    for linea in texto.splitlines():
        ln = linea.strip()
        if not ln: continue
        if es_titulo(ln):
            bloques.append(procesar_titulo(ln)); continue
        if re.fullmatch(r"(?:\x00\d+\x00\s*)+", ln):       # línea solo de imágenes
            for k in re.findall(r"\x00\d+\x00", ln):
                if bloques: bloques[-1] += "\n" + k
                else:       bloques.append(k)
            continue
        bloques.extend(procesar_linea(ln))

    salida = re.sub(r"\n{3,}", "\n\n", "\n\n".join(bloques)).strip() + "\n"
    if tags:                                               # restaurar <imagen> en un solo paso
        salida = re.sub(r"\x00\d+\x00", lambda m: tags[m.group(0)], salida)

    # 3) reescribir el archivo original
    try:
        with open(entrada, "w", encoding="utf-8") as f:
            f.write(salida)
    except OSError as e:
        print(f"ERROR: no se pudo reescribir '{entrada}' ({e}) (el original quedó en '{base}').",
              file=sys.stderr)
        return
    print(f"{entrada} reescrito (original guardado en {base})")


# ==================== AUTO-TEST ====================
# Sin banderas ya no corre desde la CLI: para usarlo, en el bloque final
# reemplazá main() por autotest().
CASOS_DORADOS = [
    ("El proceso se aplica cuando el sistema lo requiere",
     ["el proceso", "se aplica", "cuando sistema", "requiere"]),
    ("Esto depende de la carga de trabajo del equipo",
     ["Esto depende carga de trabajo", "del equipo"]),
]

def autotest():
    fallos = 0
    for entrada, esperado in CASOS_DORADOS:
        obtenido = partir(entrada)
        estado = "OK" if obtenido == esperado else "FALLO"
        if estado == "FALLO":
            fallos += 1
        print(f"[{estado}] {entrada!r}\n  esperado: {esperado}\n  obtenido: {obtenido}")
    print(f"\n{len(CASOS_DORADOS) - fallos}/{len(CASOS_DORADOS)} casos OK")
    return fallos == 0


def main():
    archivos = ARCHIVOS or sorted(glob.glob("[0-9]*.txt"))
    if not archivos:
        print("No hay archivos para procesar (completá ARCHIVOS).", file=sys.stderr)
        sys.exit(1)
    for a in archivos:
        procesar(a)


if __name__ == "__main__":
    main()