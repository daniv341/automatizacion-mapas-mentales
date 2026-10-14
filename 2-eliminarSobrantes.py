# -*- coding: utf-8 -*-
# v4.1 — reescribe el archivo de entrada in place; antes de tocarlo guarda
#         el original como *_base.txt. Sin CLI: solo correr el script.
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

# [D1] Diccionario de verbos depurado (sin duplicados y ordenado por familias)
VERBOS = {
    # 1. Copulativos, auxiliares, modales y existencia
    "es","son","está","están","sea","sean","esté","estén","fue","fueron","ser","estar",
    "ha","han","hay","haber","tiene","tienen","tener","puede","pueden","pueda","puedan","poder",
    "debe","deben","deber","va","van","ir","da","dan","dar","hace","hacen","hacer",
    "existe","existen","existir",

    # 2. Definición, denominación y exposición teórica
    "define","definen","definir","llama","llaman","llamar","denomina","denominan","denominar",
    "conoce","conocen","conocer","describe","describen","describir","describirse",
    "escribirse","caracteriza","caracterizan","caracterizar","clasifica","clasifican","clasificar",
    "representa","representan","representar","manifiesta","manifiestan","manifestar",
    "expresa","expresan","expresar","indica","indican","indicar","señala","señalan","señalar",
    "explica","explican","explicar","implica","implican","implicar","significa","significan","significar",
    "considera","consideran","considerar","contempla","contemplan","contemplar",
    "asume","asumen","asumir","supone","suponen","suponer","plantea","plantean","plantear",
    "propone","proponen","proponer","sugiere","sugieren","sugerir","destaca","destacan","destacar",

    # 3. Enfoque, soporte y comportamiento (frecuentes con "se")
    "basa","basan","basar","centra","centran","centrar","enfoca","enfocan","enfocar",
    "orienta","orientan","orientar","apoya","apoyan","apoyar","sustenta","sustentan","sustentar",
    "fundamenta","fundamentan","fundamentar","comporta","comportan","comportar",
    "traduce","traducen","traducir","refleja","reflejan","reflejar","resume","resumen","resumir",
    "visualiza","visualizan","visualizar","observa","observan","observar",
    "realiza","realizan","realizar","efectúa","efectúan","efectuar","lleva","llevan","llevar",

    # 4. Aplicación, uso, utilidad y provisión
    "aplica","aplican","aplicar","usa","usan","usar","utiliza","utilizan","utilizar",
    "emplea","emplean","emplear","sirve","sirven","servir","funciona","funcionan","funcionar",
    "brinda","brindan","brindar","aporta","aportan","aportar","ofrece","ofrecen","ofrecer",
    "suministra","suministran","suministrar","provee","proveen","proveer",
    "proporciona","proporcionan","proporcionar","facilita","facilitan","facilitar",
    "favorece","favorecen","favorecer","permite","permiten","permitir",
    "garantiza","garantizan","garantizar","asegura","aseguran","asegurar",
    "cumple","cumplen","cumplir","satisface","satisfacen","satisfacer",

    # 5. Pertenencia, composición, estructura y conexión
    "contiene","contienen","contener","compone","componen","componer",
    "consta","constan","constar","consiste","consisten","consistir",
    "abarca","abarcan","abarcar","comprende","comprenden","comprender",
    "engloba","engloban","englobar","incluye","incluyen","incluir",
    "excluye","excluyen","excluir","incorpora","incorporan","incorporar",
    "reúne","reúnen","reunir","integra","integran","integrar",
    "combina","combinan","combinar","agrupa","agrupan","agrupar",
    "pertenece","pertenecen","pertenecer","corresponde","corresponden","corresponder",
    "radica","radican","radicar","reside","residen","residir",
    "encuentra","encuentran","encontrar","ubica","ubican","ubicar",
    "sitúa","sitúan","situar","localiza","localizan","localizar",
    "conecta","conectan","conectar","enlaza","enlazan","enlazar",
    "vincula","vinculan","vincular","asocia","asocian","asociar",
    "relaciona","relacionan","relacionar","acopla","acoplan","acoplar",
    "une","unen","unir","separa","separan","separar","divide","dividen","dividir",

    # 6. Causalidad, dependencia, transformación y efecto
    "determina","determinan","determinar","determinarse","depende","dependen","depender",
    "difiere","difieren","diferir","afecta","afectan","afectar",
    "causa","causan","causar","ocasiona","ocasionan","ocasione","ocasionar",
    "provoca","provocan","provocar","origina","originan","originar",
    "produce","producen","producir","genera","generan","generar",
    "convierte","convierten","convertir","transforma","transforman","transformar",
    "resulta","resultan","resultar","impide","impiden","impedir","evita","evitan","evitar",
    "requiere","requieren","requerir","necesita","necesitan","necesitar","exige","exigen","exigir",

    # 7. Ciclo de vida, inicio, evolución y finalización
    "inicia","inician","iniciar","comienza","comienzan","comenzar",
    "empieza","empiezan","empezar","arranca","arrancan","arrancar",
    "surge","surgen","surgir","nace","nacen","nacer",
    "proviene","provienen","provenir","deriva","derivan","derivar",
    "continúa","continúan","continuar","sigue","siguen","seguir",
    "avanza","avanzan","avanzar","evoluciona","evolucionan","evolucionar",
    "pasa","pasan","pasar","transcurre","transcurren","transcurrir",
    "tarda","tardan","tardar","persiste","persisten","persistir",
    "alcanza","alcanzan","alcanzar","llega","llegan","llegar","logra","logran","logre","lograr",
    "retorna","retornan","retornar","termina","terminan","terminar",
    "finaliza","finalizan","finalizar","concluye","concluyen","concluir",
    "acaba","acaban","acabar","cesa","cesan","cesar","detiene","detienen","detener",

    # 8. Gestión, dirección y procesos organizacionales
    "establece","establecen","establecer","establezca","mantiene","mantienen","mantener",
    "obtiene","obtienen","obtener","recoge","recogen","recoger",
    "trabaja","trabajan","trabajar","participa","participan","participar",
    "gestiona","gestionan","gestionar","administra","administran","administrar",
    "dirige","dirigen","dirigir","lidera","lideran","liderar",
    "coordina","coordinan","coordinar","supervisa","supervisan","supervisar",
    "monitorea","monitorean","monitorear","planifica","planifican","planificar",
    "organiza","organizan","organizar","asigna","asignan","asignar",
    "delega","delegan","delegar","distribuye","distribuyen","distribuir",
    "prioriza","priorizan","priorizar","decide","deciden","decidir",
    "elige","eligen","elegir","selecciona","seleccionan","seleccionar",
    "opta","optan","optar","resuelve","resuelven","resolver",
    "soluciona","solucionan","solucionar","atiende","atienden","atender",
    "aborda","abordan","abordar","comunica","comunican","comunicar",
    "informa","informan","informar","reporta","reportan","reportar",
    "notifica","notifican","notificar","capacita","capacitan","capacitar",
    "motiva","motivan","motivar","contrata","contratan","contratar",

    # 9. Diseño, informática, análisis y validación
    "diseña","diseñan","diseñar","construye","construyen","construir",
    "desarrolla","desarrollan","desarrollar","implementa","implementan","implementar",
    "configura","configuran","configurar","instala","instalan","instalar",
    "ejecuta","ejecutan","ejecutar","procesa","procesan","procesar",
    "almacena","almacenan","almacenar","guarda","guardan","guardar",
    "registra","registran","registrar","lee","leen","leer",
    "envía","envían","enviar","recibe","reciben","recibir",
    "transmite","transmiten","transmitir","extrae","extraen","extraer",
    "analiza","analizan","analizar","evalúa","evalúan","evaluar",
    "valora","valoran","valorar","estudia","estudian","estudiar",
    "examina","examinan","examinar","detecta","detectan","detectar",
    "identifica","identifican","identificar","reconoce","reconocen","reconocer",
    "verifica","verifican","verificar","valida","validan","validar",
    "comprueba","comprueban","comprobar","compara","comparan","comparar",
    "distingue","distinguen","distinguir",

    # 10. Dinámica física, matemática, variación y sistemas de control
    "calcula","calculan","calcular","mide","miden","medir",
    "estima","estiman","estimar","aproxima","aproximan","aproximar",
    "resta","restan","restar","multiplica","multiplican","multiplicar",
    "iguala","igualan","igualar","equivale","equivalen","equivaler",
    "varía","varían","variar","modifica","modifican","modificar",
    "altera","alteran","alterar","ajusta","ajustan","ajustar",
    "corrige","corrigen","corregir","crece","crecen","crecer",
    "aumenta","aumentan","aumentar","incrementa","incrementan","incrementar",
    "reduce","reducen","reducir","disminuye","disminuyen","disminuir",
    "maximiza","maximizan","maximizar","minimiza","minimizan","minimizar",
    "optimiza","optimizan","optimizar","supera","superan","superar",
    "excede","exceden","exceder","gobierna","gobiernan","gobernar",
    "regula","regulan","regule","regular","opera","operan","operar",
    "actúa","actúan","actuar","responde","responden","responder",
    "reacciona","reaccionan","reaccionar","oscila","oscilan","oscilar",
    "converge","convergen","converger","diverge","divergen","divergir",
    "estabiliza","estabilizan","estabilizar","compensa","compensan","compensar",
    "atenúa","atenúan","atenuar","amplifica","amplifican","amplificar",
    "filtra","filtran","filtrar","anula","anulan","anular",
    "cancela","cancelan","cancelar","satura","saturan","saturar",
    "amortigua","amortiguan","amortiguar","tiende","tienden","tender","tenderá","tenderán",
    "desciende","descienden","descender","descendiera",

    # 11. Homógrafos seguros (SOLO plural e infinitivo para no cortar ante sustantivos)
    # Nota: "cuenta"/"cuentan" se conservan por compatibilidad con el original;
    # del resto se omite el singular (muestra, parte, forma, planta, carga, control, etc.)
    "cuenta","cuentan","contar",
    "muestran","mostrar","parten","partir","forman","formar","plantan","plantar",
    "fuerzan","forzar","cargan","cargar","entregan","entregar","demandan","demandar",
    "mejoran","mejorar","prueban","probar","fallan","fallar","cambian","cambiar",
    "controlan","controlar","programan","programar","consultan","consultar",
    "suman","sumar","limitan","limitar","buscan","buscar","tratan","tratar",
    "marcan","marcar","causan","causar",
}

CLITICOS = {"se","me","te","le","les","nos","os"}

# [C1] Reglas para detección precisa de gerundios (incluye -yendo y enclíticos)
RE_GERUNDIO = re.compile(
    r"(?:ando|iendo|yendo)(?:se|me|te|le|les|lo|la|los|las|nos)?$"
)
NO_GERUNDIOS = {
    "comando","comandos","bando","bandos","contrabando","recomiendo",
    "entiendo","atiendo","tiendo","defiendo","asciendo","desciendo",
    "enciendo","extiendo","pretendo","dividendo","dividendos","memorando",
    "memorandos","educando","educandos","graduando","graduandos","sumando",
    "sumandos","multiplicando","sustraendo","minuendo",
}

# [C2] Abreviaturas protegidas para no partir oraciones en sus puntos
RE_ABREV = re.compile(
    r"\b(?:etc|p\.\s*ej|ej|aprox|máx|max|mín|min|fig|figs|cap|caps|"
    r"pág|págs|pag|pags|dr|dra|sr|sra|lic|ing|prof|art|arts|inc|"
    r"núm|num|nro|vol|vs|ref|sec|secc)\.",
    re.IGNORECASE
)

# [C3] Marcadores de listas: números (1., 1), 1.-), letras (a), A.), romanos y viñetas
RE_MARCADOR = re.compile(
    r"^(?:(\d{1,3})\s*(?:[.)]|-\.)"
    r"|([a-zA-Z])[.)]"
    r"|([IVXLCDMivxlcdm]{1,5})[.)]"
    r"|([-•*–]))\s+(.*)$"
)

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
    prev_real = ""     # [C4] última palabra efectivamente conservada
    prefijo_pend = ""  # [B1] paréntesis de apertura transferible si se borra artículo
    en_paren = False   # [B3] control de paréntesis multi-palabra abierto
    n = len(tokens)

    def cerrar():
        nonlocal actual
        if actual:
            lineas.append(" ".join(actual))
            actual = []

    for i, tok in enumerate(tokens):
        if prefijo_pend:
            tok = prefijo_pend + tok
            prefijo_pend = ""

        nxt = tokens[i + 1] if i + 1 < n else ""
        base = tok.lower().strip(",.;:()")
        base_next = nxt.lower().strip(",.;:()")

        if tok.startswith("\x00"):                     # marcador de <imagen>
            cerrar(); lineas.append(tok)
            prev = ""; prev_real = ""; en_paren = False
            continue

        if BREAK_PAREN and tok.startswith("(") and ")" not in tok:
            cerrar()                                   # paréntesis multi-palabra
            en_paren = True

        # --- artículos ---
        if base in ARTICULOS:
            queda = (base == "de" and tok.rstrip(")")[-1:] in ",;:") \
                 or (base == "de" and ((prev_real, base) in DE_CONSERVAR
                                       or base_next in DE_NEXT_PROTECT)) \
                 or (base == "lo" and prev_real == "por") \
                 or (base in ("el","la","los","las") and base_next in RELATIVOS)
            if not queda:
                if not actual and not lineas and ARTICULO_INICIAL == "conservar":
                    tok = tok.lower()
                else:
                    # [B1] Si el artículo abría paréntesis, pasarlo a la siguiente palabra
                    if tok.startswith("("):
                        prefijo_pend = "("
                    prev = base
                    continue

        if CONVERTIR_UN and base == "un" and (actual or lineas):
            tok = re.sub(r"(?i)\bun\b", "1", tok)      # conserva signos adyacentes

        # --- ¿abrir línea antes de este token? ---
        if actual:
            st = sin_tildes(tok.lower()).strip(",.;:()\"'")
            # [C1] Sufijo real de gerundio sin falsos positivos
            gerundio = (len(st) >= 7 and st not in NO_GERUNDIOS
                        and bool(RE_GERUNDIO.search(st)))
            opener = base in CORTAR_ANTES \
                  or (base, base_next) in BIGRAM_OPENERS or gerundio
            raiz_inf = re.sub(r"(?:se|me|te|le|les|lo|la|los|las|nos)$", "", base)
            verbo = base in VERBOS or (base in CLITICOS and base_next in VERBOS) or (raiz_inf != base and raiz_inf in VERBOS)
            abrir = False
            # [C4] Usa prev_real para que artículos eliminados no anulen conectores previos
            if (opener or verbo) and prev_real not in CORTAR_ANTES \
               and (prev_real, base) not in COLOCACIONES:
                abrir = len(actual) >= MINIMO.get(base, 1) if opener else len(actual) >= 2
            if abrir:
                lleva_no = base in CLITICOS and actual[-1].lower() == "no"
                if lleva_no: actual.pop()
                cerrar()
                if lleva_no: actual.append("no")       # "no se dispare" baja junto

        actual.append(tok)
        prev = base
        prev_real = base

        # --- ¿cerrar línea después de este token? ---
        cerrada = False
        # [B3] Cierra también al terminar paréntesis multi-palabra o con puntuación tras ")"
        if tok[-1:] in ",;:" or tok.rstrip(")")[-1:] in ",;:" or (en_paren and ")" in tok):
            cerrar(); cerrada = True
            if ")" in tok:
                en_paren = False
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
    # [C3] Detección ampliada de numeraciones, incisos y viñetas
    m = RE_MARCADOR.match(linea)
    if m:
        marcador = next(g for g in m.groups()[:-1] if g is not None)
        linea = m.group(5)

    bloques = []
    # [C2] Proteger abreviaturas comunes reemplazando sus puntos por \x01
    texto = RE_ABREV.sub(lambda m: m.group(0).replace(".", "\x01"), linea)
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