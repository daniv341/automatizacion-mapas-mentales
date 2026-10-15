# -*- coding: utf-8 -*-
# v1.6 — Flujo en dos fases sobre el mismo archivo. Al iniciar, un modal ofrece:
#   [Curso normal]       FASE 1 (reemplazarInicio + ^ en subT y ~ en 1er renglón) -> modal -> FASE 2
#   [Fase 2 directamente] salta la fase 1 y aplica FASE 2 (asegurando ^ en subT y ~ en 1er renglón)
# Antes de aplicar la fase 2 (en cualquiera de los dos caminos) el archivo se copia a
# <archivo>_reemplazo.txt como respaldo.
# Sin CLI: solo correr el script.
import re, os, sys, shutil, importlib.util

# ==================== PARÁMETROS GENERALES ====================
nombre_archivo = "modificables/borrador.txt"   # mismo archivo para las dos fases
simbolos = {"temaP", "clasP", "temaS", "subT", "textS", "deF", "imP", "cuR", "clasS", "caR", "ejeM", "recoR", "nuM", "pasoS", "preG", "comP"}
ETIQ_TITULO = {"temaP", "clasP", "temaS", "subT"}

# ==================== FASE 1 ====================
def aplicar_prefijos_bloque_y_subt(lineas):
    """Agrega '^' a subT y '~' al primer renglón de cada bloque de texto sin duplicar."""
    salida = []
    en_bloque = False

    for linea in lineas:
        limpia = linea.strip()
        if not limpia:
            en_bloque = False
            salida.append(linea)
            continue

        sin_pre = limpia.lstrip("~^*+$@")
        es_subt = sin_pre.startswith("subT") and (len(sin_pre) == 4 or sin_pre[4].isspace())
        es_otro_titulo = any(
            sin_pre.startswith(t) and (len(sin_pre) == len(t) or sin_pre[len(t)].isspace())
            for t in (ETIQ_TITULO - {"subT"})
        )

        if es_subt:
            en_bloque = False
            if not limpia.startswith("^"):
                linea = "^" + linea.lstrip()
            salida.append(linea)
        elif es_otro_titulo or limpia.startswith("^"):
            en_bloque = False
            salida.append(linea)
        else:
            if not en_bloque:
                if not limpia.startswith("~"):
                    linea = "~" + linea.lstrip()
                en_bloque = True
            salida.append(linea)

    return salida

def procesar_lineas_con_simbolos(nombre_archivo, simbolos):
    with open(nombre_archivo, "r", encoding="utf-8") as archivo:
        lineas = archivo.readlines()

    resultado = []
    simbolo_actual = None

    for linea in lineas:
        linea_limpia = linea.strip()
        sin_pre = linea_limpia.lstrip("~^")

        if simbolo_actual:
            if linea_limpia == "" or any(sin_pre.startswith(s) for s in simbolos if s != simbolo_actual):
                simbolo_actual = None
            elif not sin_pre.startswith(simbolo_actual):
                pref = linea_limpia[:len(linea_limpia) - len(sin_pre)]
                resto = linea.lstrip()[len(pref):]
                linea = f"{pref}{simbolo_actual} {resto}"

        for simbolo in simbolos:
            if sin_pre.startswith(simbolo) and (len(sin_pre) == len(simbolo) or sin_pre[len(simbolo)].isspace()):
                simbolo_actual = simbolo
                break

        resultado.append(linea)

    resultado = aplicar_prefijos_bloque_y_subt(resultado)

    with open(nombre_archivo, "w", encoding="utf-8") as archivo:
        archivo.writelines(resultado)

    print(f"Fase 1: archivo procesado con etiquetas, '^subT' y '~' en '{nombre_archivo}'.")

# ==================== FASE 2 ====================
MAX_LINEAS_HOJA = 2            # una hoja intermedia ocupa como máximo N líneas
USAR_TAG_LISTA = True          # tronco + ≥3 líneas seguidas con otra etiqueta (caR) = lista vertical
REPORTAR_DUDAS = True          # imprime las decisiones por heurística débil

ETIQUETAS = ["temaP","clasP","temaS","subT","textS","deF","imP","cuR","clasS",
             "caR","ejeM","recoR","nuM","pasoS","preG","comP"]
ETIQ_LISTA  = {"caR"}

# ----- listas lingüísticas (ampliables) -----
CONJ = {"y","e","o","u","ni"}
PREPS = {"a","al","ante","bajo","con","contra","de","del","desde","en","entre",
         "hacia","hasta","para","por","según","sin","sobre","tras","mediante",
         "durante","como","vía"}
ARTICULOS = {"el","la","los","las","un","una","unos","unas","1"}

REGIMEN = [("relacionad","con"),("asociad","a"),("asociad","con"),("enfrentarse","a"),
           ("enfrentar","a"),("referid","a"),("basad","en"),("vinculad","con"),
           ("vinculad","a"),("destinad","a"),("orientad","a"),("dedicad","a"),
           ("repercusión","a"),("repercusion","a"),("suma","a"),("sumar","a")]

# Palabras que abren una cláusula nueva (no un complemento) -> no es bifurcación
INICIO_CLAUSULA = {"se","si"}

RE_PART_INF = re.compile(r"^\w{3,}(ado|ada|ados|adas|ido|ida|idos|idas|ando|iendo|ar|er|ir)$")

VERBOS_BASE = {
    "es","son","está","están","vincula","permiten","permite","identifica","desarrolla",
    "proporciona","describe","organiza","mantiene","opta","aplican","crean","suelen",
    "utilizan","requieren","reconoce","recomienda","adapten","considerará","define",
    "multiplica","puede","pueden","consideran","hace","depende","dependen","tienen",
    "determina","determinan","describen","desean","acostumbra","considera","aproximan",
    "dan","forman","actúan","ajusten","mandan","dirigen","regulan","aplica","obtiene",
    "responde","filtra","establece","alcancen","analiza","vuelvan","reduce","ahorra",
    "implica","tiene","existe","corresponde","cumple","adapta","debe","conoce","hay",
    "compare","pueda","llama","consiste","efectúa","efectua","facilite","varía","varia",
    "mueven","adoptan","varian","varían","afectan","oscilaría","oscila","resulta",
    "denomina","extingue","ubica","estudia","responderá","tarda","vaya","alcance",
    "difiere","requiere","producen","tolera","calcula","garantiza","dispare","oscile",
    "deriva","suma","manifiesta","retorna","contiene","representan","encuentran"
}

def _cargar_verbos():
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "2-eliminarSobrantes.py")
    for r in (ruta, "/mnt/user-data/uploads/2-eliminarSobrantes.py"):
        try:
            spec = importlib.util.spec_from_file_location("sobrantes", r)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            return VERBOS_BASE | set(m.VERBOS)
        except Exception:
            continue
    return set(VERBOS_BASE)
VERBOS = _cargar_verbos()

# ==================== PARSEO ====================
RE_LINEA = re.compile(r"^(?P<pre>[~^*+$@]*)(?:(?P<tag>" + "|".join(ETIQUETAS) + r")(?:\s+|$))?(?P<txt>.*)$")

def parsear(linea):
    m = RE_LINEA.match(linea.strip())
    pre_raw = m.group("pre")
    # Conserva únicamente ~ y ^ sin duplicados
    pre = ("^" if "^" in pre_raw else "") + ("~" if "~" in pre_raw else "")
    return {"pre": pre, "tag": m.group("tag"), "txt": m.group("txt").strip()}

def palabras(l):  return [w.strip("()\".,;:").lower() for w in l["txt"].split()]
def ult(l):       p = palabras(l); return p[-1] if p else ""
def prim(l):      p = palabras(l); return p[0] if p else ""
def termina_coma(l):  return l["txt"][-1:] in ",;"
def termina_etc(l):   return l["txt"].lower().endswith(("etc.", "etc.)", "etc"))
def termina_conj(l):  return ult(l) in CONJ and not termina_coma(l)
def es_sep_ni(L, i):  return prim(L[i]) == "ni" and i + 1 < len(L) and prim(L[i+1]) == "ni"
def es_sep_conj_ini(L, i):
    return (
        i + 1 < len(L)
        and prim(L[i+1]) in {"o", "u"}
        and len(palabras(L[i+1])) == 2
        and not termina_coma(L[i+1])
    )
def es_sep(L, i):
    return termina_coma(L[i]) or termina_conj(L[i]) or es_sep_ni(L, i) or es_sep_conj_ini(L, i)
def tiene_verbo(l):   return any(w in VERBOS for w in palabras(l))
def es_encabezado(l): return bool(l["txt"]) and l["txt"].isupper()

def pide_complemento(l):
    """¿La línea acaba en algo que exige continuación (verbo, preposición, ':')?"""
    return l["txt"].endswith(":") or ult(l) in VERBOS or ult(l) in PREPS

def en_regimen(prev, sig):
    u, p = ult(prev), prim(sig)
    return any(u.startswith(r) and p == pr for r, pr in REGIMEN)

def es_parentesis_linea(l):
    t = l["txt"].strip()
    return t.startswith("(") and t.endswith(")")

def continua_hoja(prev, sig, permitir_que=False):
    """¿`sig` sigue siendo parte de la hoja final?"""
    return (
        en_regimen(prev, sig)
        or es_parentesis_linea(sig)
        or (permitir_que and prim(sig) == "que" and len(palabras(prev)) >= 2)
    )

SUFIJOS_NO_FINITOS = ("ar","er","ir","arse","erse","irse","ando","iendo",
                      "ado","ada","ados","adas","ido","ida","idos","idas")
RELATIVOS_Q = {"que","cual","cuales","quien","quienes"}

def es_finito(w, ant=""):
    """Verbo conjugado (no infinitivo/gerundio/participio) y que no va tras un relativo."""
    return w in VERBOS and not w.endswith(SUFIJOS_NO_FINITOS) and ant not in RELATIVOS_Q

def tiene_finito(lineas):
    ant = ""
    for l in lineas:
        for w in palabras(l):
            if es_finito(w, ant): return True
            ant = w
    return False

def empieza_clausula(lineas):
    return bool(lineas) and prim(lineas[0]) in INICIO_CLAUSULA

def comparte_prefijo(a, b):
    """Dos líneas que arrancan con las mismas 2 palabras ("una sola salida" / "una sola entrada")."""
    pa, pb = palabras(a)[:2], palabras(b)[:2]
    return len(pa) == 2 and pa == pb

def _hojas_cortas_sin_coma(L, s1, pos, union_prev):
    """Si antes de `s1` (que termina en conjunción y tiene ≤2 palabras) hay ≥1 hojas cortas
    sin coma tras un tronco válido, devuelve el índice del tronco; si no, None."""
    if len(palabras(L[s1])) != 2 or prim(L[s1]) in (PREPS | ARTICULOS):
        return None
    j = s1
    while j - 1 > pos:
        cand = L[j - 1]
        if (
            len(palabras(cand)) <= 2
            and not pide_complemento(cand)
            and prim(cand) not in (PREPS | ARTICULOS)
            and not termina_coma(cand)
            and not es_encabezado(cand)
        ):
            j -= 1
        else:
            break
    if j < s1 and j - 1 >= pos:
        tr = L[j - 1]
        if not termina_coma(tr) and not es_encabezado(tr) and (
            (j - 1) == union_prev or pide_complemento(tr) or tiene_verbo(tr)
        ):
            return j - 1
    return None

def _hojas_paralelas_multilinea(L, s1, pos, n):
    """Detecta dos hojas de 2-3 líneas sin coma intermedia que arrancan con el mismo prefijo
    de 2 palabras (ej. 'En retroalimentación negativa ... y / en retroalimentación positiva ...')."""
    if s1 + 1 >= n:
        return None
    for ini_h1 in range(max(pos + 1, s1 - 2), s1):
        d_hoja = s1 - ini_h1
        if comparte_prefijo(L[ini_h1], L[s1 + 1]) and (s1 + 1 + d_hoja) < n:
            return ini_h1 - 1, [ini_h1, s1 + 1], s1 + 1 + d_hoja
    return None

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

    # --- tronco con ':' seguido hasta el final por ≥2 líneas que comparten prefijo ---
    for i in range(n - 2):
        if L[i]["txt"].endswith(":") and all(comparte_prefijo(L[i+1], L[k]) for k in range(i + 2, n)):
            marcas[i].add("*")
            for k in range(i + 1, n): marcas[k].add("+")
            return marcas

    sep = [i for i in range(n) if es_sep(L, i)]
    while True:
        # 1) primer separador válido desde `pos`
        s1 = next((i for i in sep if i >= pos and not (i == 0 and termina_coma(L[i]))), None)
        if s1 is None: break
        # coma al inicio que actúa como tronco ("es una capacidad natural,", "Ordenamiento componentes físicos,", "especificada por entrada,")
        tronco_forzado = None
        if termina_coma(L[s1]) and not pide_complemento(L[s1]):
            nxt = next((i for i in sep if i > s1), None)
            frase_tronco = (
                nxt == s1 + 1
                and (
                    tiene_verbo(L[s1])
                    or (
                        termina_conj(L[nxt])
                        and len(palabras(L[s1])) >= 3
                        and (s1 == 0 or es_encabezado(L[s1-1]) or (not pide_complemento(L[s1-1]) and not tiene_verbo(L[s1-1])))
                    )
                )
            )
            if frase_tronco:
                tronco_forzado, s1 = s1, nxt
        if s1 == 0: pos = 1; continue

        # 2) agrupar separadores
        grupo = [s1]; cierre_coma = False; fin_en_conj = False
        while True:
            ult_s = grupo[-1]
            if es_sep_conj_ini(L, ult_s):
                break
            if termina_conj(L[ult_s]) and ult_s + 1 < n and prim(L[ult_s+1]) in CONJ:
                fin_en_conj = True; break
            sig = next((i for i in sep if i > ult_s), None)
            if sig is None: break
            if es_sep_ni(L, ult_s) != es_sep_ni(L, sig):
                break
            d = sig - ult_s
            if termina_coma(L[ult_s]) or es_sep_ni(L, ult_s):
                if d != 1: break
            elif d > MAX_LINEAS_HOJA: break
            grupo.append(sig)
            if termina_conj(L[ult_s]) and termina_coma(L[sig]):
                cierre_coma = True; break                  # "tiempo y / energía," cierra

        hay_conj = any(termina_conj(L[i]) or es_sep_ni(L, i) or es_sep_conj_ini(L, i) for i in grupo)
        tronco_colon = s1 > 0 and L[s1-1]["txt"].endswith(":")
        lista_etc = len(grupo) >= 2 and grupo[-1] + 1 < n and termina_etc(L[grupo[-1] + 1])
        lista_comas_bloque = (
            len(grupo) >= 3
            and all(termina_coma(L[i]) for i in grupo)
            and grupo[-1] + 1 == n - 1
        )
        if lista_comas_bloque and s1 >= 2 and pide_complemento(L[s1-2]) and len(palabras(L[s1-1])) == 1:
            s1 = s1 - 1
            grupo = [s1] + grupo

        if not (hay_conj or (tronco_colon and len(grupo) >= 2) or lista_etc or lista_comas_bloque):
            pos = s1 + 1; continue

        # 3) tronco, hojas y fin de la bifurcación
        tr_cortas = (
            _hojas_cortas_sin_coma(L, s1, pos, union_prev)
            if (tronco_forzado is None and len(grupo) == 1 and termina_conj(L[s1]) and s1 + 1 < n)
            else None
        )
        par_multi = (
            _hojas_paralelas_multilinea(L, s1, pos, n)
            if (tronco_forzado is None and len(grupo) == 1 and termina_conj(L[s1]))
            else None
        )

        if fin_en_conj and len(grupo) == 1:
            # hojas sin separador entre sí: hermanas que arrancan igual ("una sola salida / una sola entrada y")
            ult_g = grupo[0]; j = ult_g
            while j - 2 >= pos and comparte_prefijo(L[j-1], L[ult_g]): j -= 1
            if j == ult_g:
                pos = ult_g + 1; continue
            prev = j - 1; inicios = list(range(j, ult_g + 1)); fin = ult_g
        elif par_multi is not None:
            prev, inicios, fin = par_multi
        elif tr_cortas is not None:
            # ≥3 hojas cortas sin coma en las primeras ("mandan / dirigen o / regulan", "sistema / subsistema / proceso u / objeto")
            prev = tr_cortas
            inicios = list(range(prev + 1, s1 + 2))
            fin = s1 + 1
            while fin + 1 < n and continua_hoja(L[fin], L[fin+1], fin > s1 + 1):
                fin += 1
        else:
            prev = tronco_forzado if tronco_forzado is not None else s1 - 1
            if tronco_forzado is None:
                if s1 == 2 and palabras(L[0]) == ["se"]:
                    prev = 0                         # "*se / +deriva / con respecto al tiempo y..."
                elif not pide_complemento(L[prev]) and union_prev is not None and s1 - union_prev == 2:
                    prev = union_prev                # la unión hace de tronco ("$*")
                    dudas.append(f"tronco = unión previa: «{L[prev]['txt']}»")
                elif (
                    s1 - 2 >= pos
                    and not pide_complemento(L[s1-1])
                    and (prim(L[s1-1]) in PREPS or prim(L[s1-1]) == "que")
                    and "que" in palabras(L[s1-1])
                ):
                    prev = s1 - 2                    # 1ª hoja de 2 líneas con "(en) que ..."
            inicios = []; ini = prev + 1
            for s in grupo:
                inicios.append(ini); ini = s + 1
            if cierre_coma or fin_en_conj:
                fin = grupo[-1]
            else:
                f = grupo[-1] + 1
                if f >= n:                           # coordinación sin hoja final: no es bifurcación
                    pos = grupo[-1] + 1; continue
                inicios.append(f); fin = f
                # Tercer ítem adjetival suelto al final con ':' ("+integral y / +proporcional / +derivativa:")
                if (
                    fin + 1 == n - 1
                    and L[fin+1]["txt"].endswith(":")
                    and len(palabras(L[fin+1])) == 1
                    and len(palabras(L[f])) == 1
                ):
                    inicios.append(fin + 1)
                    fin = fin + 1
                else:
                    h0_tiene_que = any("que" in palabras(L[k]) for k in range(inicios[0], grupo[0] + 1))
                    if not termina_coma(L[f]):
                        while fin + 1 < n and continua_hoja(L[fin], L[fin+1], fin > f or h0_tiene_que):
                            fin += 1

        # 4) filtros: ¿de verdad es una lista de elementos coordinados?
        rangos = [(a, (inicios[k+1] - 1) if k + 1 < len(inicios) else fin) for k, a in enumerate(inicios)]
        motivo = None
        h0_finito = tiene_finito(L[rangos[0][0]:rangos[0][1]+1]) or any(tiene_verbo(L[k]) for k in range(rangos[0][0], rangos[0][1]+1))
        tronco_se = palabras(L[prev]) == ["se"]
        if any(((empieza_clausula(L[a:b+1]) and not tronco_se) or (tiene_finito(L[a:b+1]) and not h0_finito)) for a, b in rangos[1:]):
            motivo = "cláusulas coordinadas (hay verbo conjugado o inicio de cláusula en una hoja)"
        elif cierre_coma and all(tiene_finito(L[a:b+1]) for a, b in rangos):
            motivo = "cláusulas coordinadas cerradas por coma"
        elif prev == union_prev and not pide_complemento(L[prev]) and len(palabras(L[prev])) >= 2 and prim(L[inicios[0]]) in PREPS:
            motivo = "complemento preposicional de la unión previa, no una lista"
        elif (
            fin + 1 < n
            and es_finito(prim(L[fin+1]))
            and not prim(L[fin+1]).endswith("n")
            and "cual" not in palabras(L[prev])
        ):
            motivo = "cláusulas coordinadas (la hoja final es sujeto de un verbo singular)"
        else:
            preps = {prim(L[a]) for a, _ in rangos if prim(L[a]) in PREPS}
            if len(preps) >= 2:
                motivo = "complementos distintos (preposiciones diferentes), no una lista"
        if motivo:
            dudas.append(f"descartada ({motivo}): «{L[prev]['txt']}» …")
            pos = s1 + 1; continue

        # Corte de unión si sigue subordinada "tal manera" tras "$*" o cláusula principal con "se ... :"
        corta_union = (
            fin + 1 < n
            and (
                (L[fin+1]["txt"].lower().startswith("tal manera") and "$" in marcas[prev])
                or (prim(L[fin+1]) == "se" and L[fin+1]["txt"].endswith(":"))
            )
        )
        union = (fin + 1) if (fin + 1 < n and not corta_union) else None
        marcas[prev].add("*")
        for a, b in rangos:
            marcas[a].add("+")
            if union is not None: marcas[b].add("@")
        if union is not None:
            marcas[union].add("$")
        union_prev = union
        if union is not None:
            pos = union
        elif corta_union:
            pos = fin + 1
        else:
            break
    return marcas

# ==================== ARCHIVO ====================
def procesar(entrada):
    if not os.path.isfile(entrada):
        print(f"AVISO: no existe '{entrada}', se omite.", file=sys.stderr); return
    with open(entrada, encoding="utf-8") as f:
        texto = f.read()

    lineas = [parsear(l) for l in texto.splitlines()]
    salida, bloque = [], []
    def volcar():
        if not bloque: return
        # Asegura '~' estrictamente en el primer renglón de cada bloque sin duplicar
        if "~" not in bloque[0]["pre"]:
            bloque[0]["pre"] += "~"
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
            volcar()
            # Solo subT recibe '^' automáticamente (sin duplicar si ya existía)
            if l["tag"] == "subT" and "^" not in l["pre"]:
                l["pre"] = "^" + l["pre"]
            salida.append(f"{l['pre']}{(l['tag']+' ') if l['tag'] else ''}{l['txt']}".rstrip())
            continue
        if "~" in l["pre"]: volcar()
        bloque.append(l)
    volcar()
    with open(entrada, "w", encoding="utf-8") as f:
        f.write("\n".join(salida).rstrip() + "\n")
    print(f"Fase 2: {entrada} reescrito con '^subT', '~', bifurcaciones y uniones.")

# ==================== MODAL + FLUJO ====================
def preguntar_fase2():
    msg = ("Fase 1 terminada (con '^subT' y '~' en el primer renglón de cada bloque).\n\n"
           "Si quieres ajustar o agregar algún ~ o ^ extra a mano en el archivo, "
           "hazlo ahora, guárdalo y luego pulsa Sí.\n"
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