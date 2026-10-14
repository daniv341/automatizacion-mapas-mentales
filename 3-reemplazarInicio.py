# -*- coding: utf-8 -*-
# v1.1 — Flujo en dos fases sobre el mismo archivo. Al iniciar, un modal ofrece:
#   [Curso normal]       FASE 1 (reemplazarInicio) -> modal "¿avanzar a la fase 2?" -> FASE 2
#   [Fase 2 directamente] salta la fase 1 y aplica solo la FASE 2 (se presume la fase 1 ya aplicada)
#   FASE 1  reemplazarInicio : repite la palabra clave en cada línea de su bloque.
#   (pausa) modal            : aquí agregas a mano los ~ y ^ y guardas el archivo.
#   FASE 2  marcarBifurcaciones : detecta bifurcaciones (*, +) y uniones ($, @).
# Antes de aplicar la fase 2 (en cualquiera de los dos caminos) el archivo se copia a
# <archivo>_reemplazo.txt como respaldo.
# Sin CLI: solo correr el script.
import re, os, sys, shutil, importlib.util

# ==================== PARÁMETROS GENERALES ====================
nombre_archivo = "modificables/borrador.txt"   # mismo archivo para las dos fases
simbolos = {"temaP", "clasP", "temaS", "subT", "textS", "deF", "imP", "cuR", "clasS", "caR", "ejeM", "recoR", "nuM", "pasoS", "preG", "comP"} # Conjunto de palabras clave

# ==================== FASE 1 ====================
def procesar_lineas_con_simbolos(nombre_archivo, simbolos):

    with open(nombre_archivo, "r", encoding="utf-8") as archivo:
        lineas = archivo.readlines()

    resultado = []
    simbolo_actual = None

    for linea in lineas:
        linea_limpia = linea.strip()

        if simbolo_actual:
            if linea_limpia == "" or any(linea_limpia.startswith(simbolo) for simbolo in simbolos if simbolo != simbolo_actual):
                simbolo_actual = None
            else:
                linea = f"{simbolo_actual} {linea}"

        for simbolo in simbolos:
            if linea_limpia.startswith(simbolo):
                simbolo_actual = simbolo
                break

        resultado.append(linea)

    with open(nombre_archivo, "w", encoding="utf-8") as archivo:
        archivo.writelines(resultado)

    print(f"Archivo procesado y guardado en '{nombre_archivo}'.")

# ==================== FASE 2 ====================
# MODELO (lo que hay detrás de los símbolos)
#   Una BIFURCACIÓN es una coordinación de ≥2 elementos:  A, B, C y D
#       *  línea TRONCO : la línea que antecede al primer elemento ("que vincula")
#       +  línea HOJA   : primera línea de cada elemento
#   Una UNIÓN aparece cuando, tras el último elemento, la oración sigue y lo que
#   sigue se aplica a TODOS los elementos (no solo al último):
#       @  se pone en cada hoja que converge
#       $  se pone en la primera línea de lo que sigue (la "unión")
#   Si la unión es a la vez el tronco de otra bifurcación queda "$*".
#   Si la coordinación cierra la oración, no hay unión (hojas solo con "+").
# ---------- parámetros de la fase 2 ----------
MAX_LINEAS_HOJA = 2            # una hoja intermedia ocupa como máximo N líneas
USAR_TAG_LISTA = True          # tronco + ≥3 líneas seguidas con otra etiqueta (caR) = lista vertical
REPORTAR_DUDAS = True          # imprime las decisiones por heurística débil

# Etiquetas del txt (se conservan tal cual). Las de título no se procesan.
ETIQUETAS = ["temaP","clasP","temaS","subT","textS","deF","imP","cuR","clasS",
             "caR","ejeM","recoR","nuM","pasoS","preG","comP"]
ETIQ_TITULO = {"temaP","clasP","temaS","subT"}
ETIQ_LISTA  = {"caR"}          # etiquetas que indican "ítems de lista"

# ----- listas lingüísticas (ampliables) -----
CONJ = {"y","e","o","u","ni"}
PREPS = {"a","al","ante","bajo","con","contra","de","del","desde","en","entre",
         "hacia","hasta","para","por","según","sin","sobre","tras","mediante",
         "durante","como","vía"}
# Palabra + preposición que van juntas: la línea siguiente NO es unión, es parte de la hoja.
# Se compara por raíz: ("relacionad","con") cubre relacionada/relacionados/...
REGIMEN = [("relacionad","con"),("asociad","a"),("asociad","con"),("enfrentarse","a"),
           ("enfrentar","a"),("referid","a"),("basad","en"),("vinculad","con"),
           ("vinculad","a"),("destinad","a"),("orientad","a"),("dedicad","a")]
# Una hoja que arranca así es una cláusula nueva, no un complemento -> no es bifurcación
INICIO_CLAUSULA = {"se"}

RE_PART_INF = re.compile(r"^\w{3,}(ado|ada|ados|adas|ido|ida|idos|idas|ando|iendo|ar|er|ir)$")

# Reutiliza el diccionario de verbos de tu script si está en la misma carpeta
def _cargar_verbos():
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "2-eliminarSobrantes.py")
    for r in (ruta, "/mnt/user-data/uploads/2-eliminarSobrantes.py"):
        try:
            spec = importlib.util.spec_from_file_location("sobrantes", r)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            return set(m.VERBOS)
        except Exception:
            continue
    return {"es","son","está","están","vincula","permiten","permite","identifica","desarrolla"}
VERBOS = _cargar_verbos()

# ==================== PARSEO ====================
RE_LINEA = re.compile(r"^(?P<pre>[~^*+$@]*)(?:(?P<tag>" + "|".join(ETIQUETAS) + r")(?:\s+|$))?(?P<txt>.*)$")

def parsear(linea):
    m = RE_LINEA.match(linea.strip())
    pre = m.group("pre").replace("*","").replace("+","").replace("$","").replace("@","")
    return {"pre": pre, "tag": m.group("tag"), "txt": m.group("txt").strip()}

def palabras(l):  return [w.strip("()\".,;:").lower() for w in l["txt"].split()]
def ult(l):       p = palabras(l); return p[-1] if p else ""
def prim(l):      p = palabras(l); return p[0] if p else ""
def termina_coma(l):  return l["txt"][-1:] in ",;"
def termina_conj(l):  return ult(l) in CONJ and not termina_coma(l)
def es_sep(l):        return termina_coma(l) or termina_conj(l)
def tiene_verbo(l):   return any(w in VERBOS for w in palabras(l))

def pide_complemento(l):
    """¿La línea acaba en algo que exige continuación (verbo, preposición, ':')?"""
    return l["txt"].endswith(":") or ult(l) in VERBOS or ult(l) in PREPS

def en_regimen(prev, sig):
    u, p = ult(prev), prim(sig)
    return any(u.startswith(r) and p == pr for r, pr in REGIMEN)

def abre_union(prev, sig):
    """¿`sig` (línea tras el último elemento) abre una unión en vez de continuar la hoja?"""
    w = prim(sig)
    if w == "que" or en_regimen(prev, sig):
        return False
    return w in PREPS or bool(RE_PART_INF.match(w))

# ==================== DETECCIÓN ====================
def detectar(L, dudas):
    """L: líneas de UN bloque. Devuelve dict idx -> set(simbolos)."""
    n = len(L); marcas = {i: set() for i in range(n)}
    pos = 0                       # a partir de aquí se busca la siguiente bifurcación
    union_prev = None             # índice de la unión de la bifurcación anterior

    # --- lista vertical por etiqueta (tronco imP + ≥3 líneas caR sin separadores) ---
    if USAR_TAG_LISTA:
        for i in range(1, n):
            if L[i]["tag"] in ETIQ_LISTA and L[i-1]["tag"] not in ETIQ_LISTA:
                j = i
                while j < n and L[j]["tag"] in ETIQ_LISTA: j += 1
                if j - i >= 3 and j == n:
                    marcas[i-1].add("*")
                    for k in range(i, n): marcas[k].add("+")
                    return marcas

    sep = [i for i in range(n) if es_sep(L[i])]
    while True:
        # 1) primer separador válido desde `pos`
        s1 = next((i for i in sep if i >= pos and not (i == 0 and termina_coma(L[i]))), None)
        if s1 is None: break
        # coma con verbo al inicio ("es una capacidad natural,"): esa línea es tronco, no hoja
        if termina_coma(L[s1]) and tiene_verbo(L[s1]) and not pide_complemento(L[s1]):
            nxt = next((i for i in sep if i > s1), None)
            if nxt is None: pos = s1 + 1; continue
            tronco_forzado, s1 = s1, nxt
        else:
            tronco_forzado = None
        if s1 == 0: pos = 1; continue

        # 2) agrupar separadores consecutivos (hojas de ≤ MAX_LINEAS_HOJA líneas)
        grupo = [s1]; cierre_coma = False
        while True:
            ult_s = grupo[-1]
            sig = next((i for i in sep if i > ult_s), None)
            if sig is None or sig - ult_s > MAX_LINEAS_HOJA: break
            if termina_conj(L[ult_s]) and termina_coma(L[sig]):
                grupo.append(sig); cierre_coma = True; break   # "tiempo y / energía," cierra
            grupo.append(sig)
        hay_conj = any(termina_conj(L[i]) for i in grupo)
        tronco_colon = L[s1-1]["txt"].endswith(":") if s1 > 0 else False
        if not (hay_conj or tronco_colon or tronco_forzado is not None):
            pos = s1 + 1; continue

        # 3) tronco
        prev = tronco_forzado if tronco_forzado is not None else s1 - 1
        if tronco_forzado is None and not pide_complemento(L[prev]) \
           and union_prev is not None and s1 - union_prev == 2:
            prev = union_prev                    # la unión hace de tronco ("$*")
            dudas.append(f"tronco = unión previa: «{L[prev]['txt']}»")
        if prev < pos and prev != union_prev: prev = max(prev, 0)

        # 4) hoja final y unión
        if cierre_coma:
            fin = grupo[-1]
        else:
            f = grupo[-1] + 1
            if f >= n:                           # coordinación sin hoja final: no es bifurcación
                pos = grupo[-1] + 1; continue
            fin = f
            if not termina_coma(L[f]):
                while fin + 1 < n and not abre_union(L[fin], L[fin+1]) \
                      and not es_sep(L[fin+1]):
                    fin += 1
        # hojas = inicio de cada hoja
        inicios = []
        ini = prev + 1
        for s in grupo:
            inicios.append(ini); ini = s + 1
        if not cierre_coma: inicios.append(grupo[-1] + 1)
        # filtro: hoja que arranca como cláusula nueva ("se mantiene") -> no bifurcar
        if any(prim(L[i]) in INICIO_CLAUSULA for i in inicios[1:]):
            dudas.append(f"descartada (cláusula coordinada): «{L[prev]['txt']}» …")
            pos = fin + 1; continue

        union = fin + 1 if fin + 1 < n else None
        marcas[prev].add("*")
        for i in inicios:
            marcas[i].add("+")
            if union is not None: marcas[i].add("@")
        if union is not None:
            marcas[union].add("$")
        union_prev = union
        pos = (union if union is not None else n)
        if union is None: break
    return marcas

# ==================== ARCHIVO ====================
def procesar(entrada):
    if not os.path.isfile(entrada):
        print(f"AVISO: no existe '{entrada}', se omite.", file=sys.stderr); return
    with open(entrada, encoding="utf-8") as f:
        texto = f.read()
    # (el respaldo lo hace el flujo principal: borrador_reemplazo.txt)

    lineas = [parsear(l) for l in texto.splitlines()]
    # bloques: cada línea con ~ o ^ abre uno; los títulos van solos
    salida, bloque = [], []
    def volcar():
        if not bloque: return
        dudas = []
        marcas = detectar(bloque, dudas)
        for i, l in enumerate(bloque):
            m = marcas[i]
            sim = l["pre"] + ("$" if "$" in m else "") + ("*" if "*" in m else "") \
                  + ("+" if "+" in m else "") + ("@" if "@" in m else "")
            etq = (l["tag"] + " ") if l["tag"] else ""
            salida.append(f"{sim}{etq}{l['txt']}")
        if REPORTAR_DUDAS and dudas:
            for d in dudas: print("  · " + d)
        bloque.clear()
    for l in lineas:
        if not l["txt"] and not l["tag"] and not l["pre"]:
            volcar(); salida.append(""); continue
        titulo = l["tag"] in ETIQ_TITULO or "^" in l["pre"]
        if titulo:
            volcar(); salida.append(f"{l['pre']}{(l['tag']+' ') if l['tag'] else ''}{l['txt']}".rstrip()); continue
        if "~" in l["pre"]: volcar()
        bloque.append(l)
    volcar()
    with open(entrada, "w", encoding="utf-8") as f:
        f.write("\n".join(salida).rstrip() + "\n")
    print(f"Fase 2: {entrada} reescrito con bifurcaciones y uniones.")

# ==================== MODAL + FLUJO ====================
def preguntar_fase2():
    msg = ("Fase 1 terminada.\n\n"
           "Agrega tus ~ y ^ en el archivo, guárdalo y luego pulsa Sí.\n"
           "Antes de aplicar la fase 2 se guardará un respaldo en borrador_reemplazo.txt.\n\n"
           "¿Avanzar a la fase 2?")
    try:
        import tkinter as tk
        from tkinter import messagebox
        raiz = tk.Tk(); raiz.withdraw(); raiz.attributes("-topmost", True)
        r = messagebox.askyesno("Fase 2: bifurcaciones y uniones", msg, parent=raiz)
        raiz.destroy()
        return r
    except Exception:
        return input(msg.replace("pulsa Sí", "responde s") + " [s/n]: ").strip().lower() in ("s", "si", "sí", "y")

def elegir_modo():
    """Modal inicial con 2 botones. Devuelve 'normal', 'fase2' o None (ventana cerrada)."""
    try:
        import tkinter as tk
        eleccion = {"v": None}
        ventana = tk.Tk()
        ventana.title("¿Cómo quieres correr el script?")
        ventana.attributes("-topmost", True)
        ventana.resizable(False, False)
        tk.Label(ventana, justify="left", padx=20, pady=15,
                 text=("Curso normal: fase 1 y luego (si quieres) la fase 2.\n"
                       "Fase 2 directamente: solo la fase 2 sobre el archivo actual\n"
                       "(se presume que la fase 1 ya fue aplicada).")).pack()
        fila = tk.Frame(ventana, pady=10); fila.pack()
        def elegir(v):
            eleccion["v"] = v; ventana.destroy()
        tk.Button(fila, text="Curso normal", width=22, command=lambda: elegir("normal")).pack(side="left", padx=8)
        tk.Button(fila, text="Fase 2 directamente", width=22, command=lambda: elegir("fase2")).pack(side="left", padx=8)
        ventana.protocol("WM_DELETE_WINDOW", ventana.destroy)
        ventana.mainloop()
        return eleccion["v"]
    except Exception:
        r = input("1 = Curso normal (fase 1 y luego fase 2)\n"
                  "2 = Fase 2 directamente (la fase 1 ya fue aplicada)\n"
                  "Elige [1/2]: ").strip()
        return {"1": "normal", "2": "fase2"}.get(r)

def aplicar_fase2():
    raiz, ext = os.path.splitext(nombre_archivo)
    respaldo = f"{raiz}_reemplazo{ext}"            # modificables/borrador_reemplazo.txt
    try:
        shutil.copyfile(nombre_archivo, respaldo)  # copia (no mueve): la fase 2 sigue sobre el original
    except OSError as e:
        print(f"ERROR: no se pudo crear el respaldo '{respaldo}' ({e}); la fase 2 no se aplica.", file=sys.stderr)
        return
    print(f"Respaldo guardado en '{respaldo}'.")
    procesar(nombre_archivo)

def main():
    modo = elegir_modo()
    if modo is None:
        print("Cancelado: no se hizo ningún cambio.")
        return
    if modo == "normal":
        procesar_lineas_con_simbolos(nombre_archivo, simbolos)
        if not preguntar_fase2():
            print("Fase 2 omitida. El archivo quedó como lo dejó la fase 1 / tu edición.")
            return
    aplicar_fase2()

if __name__ == "__main__":
    main()