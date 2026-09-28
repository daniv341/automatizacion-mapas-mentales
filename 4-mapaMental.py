import argparse
from tkinter import Tk, Toplevel, Button, Label, Frame


# ==================== CONSTANTES (antes definidas dentro de generar_diagrama) ====================
# Estilos según las palabras reservadas
ESTILOS = {
    "temaP": 'shape=offPageConnector;whiteSpace=wrap;strokeColor=#000000;fillColor=#274B66;strokeWidth=4;fontSize=16;fontStyle=3;fontFamily=Helvetica;fontColor=#FFFFFF;labelBackgroundColor=none;overflow=block;',
    "clasP": 'ellipse;whiteSpace=wrap;fontSize=16;fillColor=#DEDE00;strokeColor=default;fontColor=#FFFFFF;rounded=1;shadow=0;labelBackgroundColor=none;strokeWidth=4;spacing=5;fontStyle=1;arcSize=7;fontFamily=Helvetica;gradientColor=#4F4F4F;overflow=block;',
    "temaS": 'shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;fixedSize=1;fontSize=16;fillColor=#CCCC00;fontColor=#FFFFFF;rounded=1;shadow=0;labelBackgroundColor=none;strokeWidth=4;spacing=5;fontStyle=1;arcSize=7;size=30;gradientColor=#4F4F4F;gradientDirection=south;fontFamily=Helvetica;overflow=block;',
    "subT": 'rhombus;whiteSpace=wrap;fontSize=16;fillColor=#DEDE00;strokeColor=default;fontColor=#FFFFFF;rounded=1;shadow=0;labelBackgroundColor=none;strokeWidth=4;spacing=5;arcSize=7;fillStyle=auto;gradientColor=none;fontFamily=Helvetica;fontStyle=1;overflow=block;',
    "textS": 'rounded=1;whiteSpace=wrap;shadow=0;labelBackgroundColor=none;strokeColor=none;strokeWidth=3;fillColor=#DEDE00;fontFamily=Helvetica;fontSize=16;fontColor=#FFFFFF;align=center;spacing=5;arcSize=7;perimeterSpacing=2;fillStyle=auto;gradientColor=none;overflow=block;',
    "deF": 'shape=document;whiteSpace=wrap;boundedLbl=1;strokeColor=none;fillColor=#DEDE00;fontFamily=Helvetica;fontSize=16;fontColor=#FFFFFF;labelBackgroundColor=none;fontStyle=2;overflow=block;',
    "imP": 'shape=process;whiteSpace=wrap;backgroundOutline=1;strokeWidth=3;fillColor=#DEDE00;fontSize=16;fontStyle=2;fontColor=#FFFFFF;overflow=block;',
    "cuR": 'ellipse;shape=cloud;whiteSpace=wrap;fontSize=16;fillColor=#DEDE00;strokeColor=none;fontColor=#FFFFFF;fontStyle=2;shadow=0;fontFamily=Helvetica;labelBackgroundColor=none;overflow=block;',
    "clasS": 'ellipse;whiteSpace=wrap;fontSize=16;fillColor=#DEDE00;strokeColor=none;fontColor=#FFFFFF;rounded=1;shadow=0;labelBackgroundColor=none;strokeWidth=3;spacing=5;arcSize=7;fillStyle=auto;gradientColor=none;fontFamily=Helvetica;fontStyle=2;overflow=block;',
    "caR": 'shape=display;whiteSpace=wrap;strokeColor=none;fillColor=#DEDE00;fontFamily=Helvetica;fontSize=16;fontColor=#FFFFFF;labelBackgroundColor=none;overflow=block;',
    "ejeM": 'shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;fixedSize=1;strokeColor=none;fillColor=#DEDE00;fontColor=#FFFFFF;labelBackgroundColor=none;fontSize=16;overflow=block;',
    "recoR": 'shape=note;whiteSpace=wrap;html=1;backgroundOutline=1;darkOpacity=0.05;strokeColor=#000000;strokeWidth=4;align=center;verticalAlign=middle;fontFamily=Helvetica;fontSize=16;fontColor=#FFFFFF;fontStyle=2;labelBackgroundColor=none;fillColor=#274B66;gradientColor=none;',
    "nuM": 'ellipse;whiteSpace=wrap;html=1;aspect=fixed;fontStyle=1;strokeWidth=3;fillColor=#DEDE00;fontColor=#FFFFFF;fontSize=16;',
    "pasoS": 'shape=step;perimeter=stepPerimeter;whiteSpace=wrap;html=1;fixedSize=1;strokeColor=default;fillColor=#DEDE00;strokeWidth=0;fontSize=16;fontStyle=2;fontColor=#FFFFFF;',
    "preG": 'shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=17.588292738970722;lid=0;fillColor=#DEDE00;strokeColor=none;dashed=1;dashPattern=8 8;fontColor=#FFFFFF;fontSize=16;fontStyle=1',
    "comP": 'shape=tape;whiteSpace=wrap;fillColor=#DEDE00;fontColor=#FFFFFF;fontSize=17;fontStyle=2;fontFamily=Georgia;size=0.4;dashed=1;dashPattern=8 8;align=center;verticalAlign=middle;strokeColor=none;'
}

# Tamaños por palabra reservada
TAMANOS = {
    "temaP": (150, 80),
    "clasP": (95, 90),
    "temaS": (153, 80),
    "subT": (175, 100),
    "textS": (150, 60),
    "deF": (148.71, 80),
    "imP": (151.12, 60),
    "cuR": (173.15, 90),
    "clasS": (96.62, 92),
    "caR": (160, 68),
    "ejeM": (151, 66),
    "recoR": (150, 80),
    "nuM": (45.52, 45.52),
    "pasoS": (180, 60),
    "preG": (162.77, 70),
    "comP": (156.6, 90)
}

# Cantidad de caracteres que puede soportar cada cuadro, el segundo valor sería el nuevo ancho
CARACTER = {
    "temaP": [40, 190],
    "clasP": [30, 120],
    "temaS": [45, 210],
    "subT": [45, 230],
    "textS": [70, 200],
    "deF": [55, 190],
    "imP": [45, 190],
    "cuR": [45, 200],
    "clasS": [30, 120],
    "caR": [60, 190],
    "ejeM": [60, 190],
    "recoR": [65, 190],
    "nuM": [4, 50],
    "pasoS": [60, 210],
    "preG": [55, 190],
    "comP": [25, 200],
}

# Códigos de colores [color temas principales(temaS), color cuadros(imP, cuR), color de flechas, color gradiente en temas principales, color de fuente]
COLORES = {
   "amarillo" : ['fillColor=#CCCC00','fillColor=#DEDE00','strokeColor=#CCCC00','gradientColor=#4F4F4F','fontColor=#5C5C5C'],
   "celeste" : ['fillColor=#007D7D','fillColor=#00CCCC','strokeColor=#007D7D','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "rojo" : ['fillColor=#CC0000','fillColor=#FF0000','strokeColor=#CC0000','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "verde" : ['fillColor=#009900','fillColor=#66CC00','strokeColor=#009900','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "morado" : ['fillColor=#5500AB','fillColor=#7F00FF','strokeColor=#5500AB','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "rosado" : ['fillColor=#F000DC','fillColor=#FF66FF','strokeColor=#F000DC','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "azul" : ['fillColor=#0000CC','fillColor=#0000FF','strokeColor=#0000CC','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "bordo" : ['fillColor=#99004D','fillColor=#FF0080','strokeColor=#99004D','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "naranja" : ['fillColor=#CC6600','fillColor=#FF8000','strokeColor=#CC6600','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "verdeAgua" : ['fillColor=#04866C','fillColor=#0BD0AF','strokeColor=#04866C','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "gris" : ['fillColor=#5C5C5C','fillColor=#999999','strokeColor=#5C5C5C','gradientColor=#FFFFFF','fontColor=#FFFFFF'],
   "marron" : ['fillColor=#622E04','fillColor=#89552A','strokeColor=#622E04','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "fucsia" : ['fillColor=#8F50A6','fillColor=#DC7BFF','strokeColor=#8F50A6','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "azulGrisaceo" : ['fillColor=#0060BF','fillColor=#3399FF','strokeColor=#0060BF','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "verdeOscuro" : ['fillColor=#0C703E','fillColor=#14B866','strokeColor=#0C703E','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "naranjaOscuro" : ['fillColor=#D44B2D','fillColor=#FF5A36','strokeColor=#D44B2D','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "rojoOscuro" : ['fillColor=#660000','fillColor=#8B0000','strokeColor=#660000','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "amarilloOscuro" : ['fillColor=#3B4A20','fillColor=#556B2F','strokeColor=#3B4A20','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "verdeClaro" : ['fillColor=#6EB86E','fillColor=#98FF98','strokeColor=#6EB86E','gradientColor=#FFFFFF','fontColor=#5C5C5C'],
   "grisClaro" : ['fillColor=#BBBBCC','fillColor=#E6E6FA','strokeColor=#BBBBCC','gradientColor=#FFFFFF','fontColor=#5C5C5C'],
   "rosadoClaro" : ['fillColor=#CF939D','fillColor=#FFB6C1','strokeColor=#CF939D','gradientColor=#FFFFFF','fontColor=#5C5C5C'],
   "azulClaro" : ['fillColor=#8C93C7','fillColor=#B3BDFF','strokeColor=#8C93C7','gradientColor=#FFFFFF','fontColor=#5C5C5C'],
   "moradoClaro" : ['fillColor=#454691','fillColor=#696ADC','strokeColor=#454691','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "rojoClaro" : ['fillColor=#C94E38','fillColor=#FF6347','strokeColor=#C94E38','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "marronClaro" : ['fillColor=#9C6530','fillColor=#CD853F','strokeColor=#9C6530','gradientColor=#4F4F4F','fontColor=#FFFFFF'],
   "amarilloClaro" : ['fillColor=#E3E372','fillColor=#FFFF80','strokeColor=#E3E372','gradientColor=#FFFFFF','fontColor=#5C5C5C'],
}


# ==================== FUNCIONES AUXILIARES ====================

def escapar_texto(texto):
    """Convierte los símbolos peligrosos en entidades seguras para el XML
    de draw.io. El '&' se reemplaza primero para no doble-escapar."""
    reemplazos = {
        "&": "&amp;",     # entidad XML (siempre primero)
        "<": "&lt;",      # inicio de etiqueta XML
        ">": "&gt;",      # fin de etiqueta XML
        '"': "&quot;",    # rompería el atributo value="..."
        "'": "&apos;",    # por seguridad
        "{": "&#123;",    # draw.io usa {} como placeholders
        "}": "&#125;",
    }
    for simbolo, entidad in reemplazos.items():
        texto = texto.replace(simbolo, entidad)
    return texto


def crear_flecha(id_, source, target, color="#CCCC00"):
    """Arma el bloque XML de una flecha (mxCell edge) entre dos nodos.
    Extraído porque el mismo bloque se repetía igual en cuatro lugares."""
    return f'''
          <mxCell id="{id_}" edge="1" parent="1" source="{source}" target="{target}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor={color};strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
            <mxGeometry relative="1" as="geometry"/>
          </mxCell>'''


def elegir_color(root, colores):
    """Muestra una ventana para elegir la paleta de colores del diagrama.
    Devuelve el nombre de la paleta, o None si se cierra la ventana sin
    elegir (se cancela el script)."""
    eleccion = {"color": None}

    ventana = Toplevel(root)
    ventana.title("Color del diagrama")
    ventana.attributes("-topmost", True)  # aparece siempre al frente
    ventana.resizable(False, False)
    ventana.grab_set()  # modal: obliga a elegir antes de continuar

    Label(ventana, text="Selecciona un color:",
          font=("Arial", 12, "bold")).pack(pady=(10, 5))

    frame = Frame(ventana)
    frame.pack(padx=10, pady=5)

    def seleccionar(nombre):
        eleccion["color"] = nombre
        ventana.destroy()

    # Botones pintados con su propio color, en grilla de 3 columnas
    # OJO: acá los valores son atributos completos ('fillColor=#XXXXXX'),
    # así que extraemos el hex con split('=')
    COLUMNAS = 3
    for i, (nombre, valores) in enumerate(colores.items()):
        fill = valores[1].split('=')[1]  # color de cuadros (el predominante)
        font = valores[4].split('=')[1]  # color de fuente
        Button(
            frame,
            text=nombre,
            bg=fill,
            fg=font,
            activebackground=fill,
            activeforeground=font,
            width=14,
            relief="groove",
            bd=2,
            command=lambda n=nombre: seleccionar(n),
        ).grid(row=i // COLUMNAS, column=i % COLUMNAS, padx=4, pady=4)

    root.wait_window(ventana)  # espera hasta que se cierre la ventana
    return eleccion["color"]


def leer_lineas(ruta_txt):
    """Lee el archivo de entrada una sola vez y devuelve:
    - crudas: líneas tal cual (con \\n, incluye blancos) — usadas para
      detectar separadores de sección.
    - filtradas: líneas sin espacios y sin blancos — usadas para las
      pasadas de nodo/rama (mismo criterio que la versión original)."""
    with open(ruta_txt, "r", encoding="utf-8") as archivo:
        crudas = archivo.readlines()
    filtradas = [renglon.strip() for renglon in crudas if renglon.strip()]
    return crudas, filtradas


# ==================== GENERACIÓN DEL DIAGRAMA ====================

def generar_diagrama(lineas_crudas, lineas_filtradas, salida_xml):
    # Inicio del contenido del archivo
    contenido = '''<mxfile host="app.diagrams.net">
  <diagram name="Página 1">
    <mxGraphModel dx="1100" dy="580" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="827" pageHeight="1169" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />'''

    # Inicializa el índice para los IDs y las posiciones
    id_counter = 2  # Comenzar el ID en 2
    x_pos = 20  # Coordenada x inicial
    y_pos = 20  # Coordenada y inicial
    espaciado_y = 110  # Espaciado vertical entre los cuadros
    espaciado_x = 200  # Espaciado horizontal al cambiar de línea
    horizontal_index = 0  # Inicializa el índice para determinar la posición horizontal

    # (CAMBIADO) en lugar del color hardcodeado, se abre la ventanita modal
    # elegir color de cuadros, si usas uno de los colores claros debes cambiar de forma manual el color de los "temaP"
    root = Tk()
    root.withdraw()  # oculta la ventana principal vacía de tkinter
    opciones = elegir_color(root, COLORES)
    root.destroy()
    if opciones is None:
        print("Selección de color cancelada.")
        return

    color1 = COLORES[opciones][0]  # color de flechas y temas principales
    color2 = COLORES[opciones][1]  # color de cuadros
    color3 = COLORES[opciones][2]  # color de flechas
    color4 = COLORES[opciones][3]  # color de gradiente
    color5 = COLORES[opciones][4]  # color de fuente

    # BUG LATENTE #1 (corregido): j y final solo se fijaban dentro del bucle,
    # si texto.txt viniera vacío o sin líneas no-blancas, referenciarlos más
    # abajo tiraba NameError. Se inicializan acá con el valor de arranque.
    j = id_counter
    final = id_counter

    # Agregar cada línea como un nuevo nodo mxCell con un id único y el estilo correspondiente
    for renglon in lineas_crudas:
        renglon = renglon.strip()  # Elimina espacios y saltos de línea extra
        if not renglon:  # Si la línea está vacía, continúa
            horizontal_index += 1  # Mover a la derecha
            y_pos = 20  # Reiniciar la posición vertical
            x_pos = 20 + horizontal_index * espaciado_x  # Ajustar la posición horizontal
            continue

        # Determinar el estilo y tamaño basados en la palabra reservada
        estilo = ''
        tamaño = (160, 40)  # Tamaño por defecto
        for palabra, style in ESTILOS.items():
            if palabra in renglon:
                estilo = style
                ancho, alto = TAMANOS[palabra]
                if len(renglon) > CARACTER[palabra][0]:  # si el renglon tiene más caracteres que el límite aumenta el ancho
                    ancho = CARACTER[palabra][1]
                tamaño = (ancho, alto)
                # Eliminar la palabra reservada del renglón
                renglon = renglon.replace(palabra, '').strip()  # Eliminar la palabra reservada
                break
        if estilo == '':
            estilo = 'rounded=0;whiteSpace=wrap;html=1;'  # Estilo por defecto si no hay palabra reservada

        # Añadir el cuadro con la posición ajustada
        contenido += f'''
        <mxCell id="{id_counter}" value="{escapar_texto(renglon)}" style="{estilo}" vertex="1" parent="1">
          <mxGeometry x="{x_pos}" y="{y_pos}" width="{tamaño[0]}" height="{tamaño[1]}" as="geometry" />
        </mxCell>'''

        # Incrementar la posición vertical para el siguiente cuadro
        y_pos += espaciado_y
        id_counter += 1  # Incrementar el ID para el siguiente cuadro
        j = id_counter
        final = id_counter

    id_counter = 2

    # BUG LATENTE #2 (corregido): xml_bloque solo se fijaba dentro del "if",
    # si la primera línea fuera blanca, el "elif" de abajo la usaba sin
    # existir todavía y tiraba NameError. Se inicializa acá en None.
    xml_bloque = None

    # Recorremos las líneas del archivo, conectando cada cuadro con el siguiente
    for renglon in lineas_crudas:  # Usamos el índice para poder acceder a la siguiente línea
        renglon = renglon.strip()  # Elimina espacios y saltos de línea extra
        if renglon and id_counter <= final:  # Si la línea actual no está vacía
            xml_bloque = crear_flecha(j, id_counter, id_counter + 1)
            contenido += xml_bloque
            id_counter += 1
            j += 1
        elif not renglon and xml_bloque:  # Si la línea actual está vacía, deshace la última conexión predicha
            contenido = contenido.replace(xml_bloque, '')

    # separar flechas (and) el nodo bifurca en ramas
    # Lista de configuraciones a iterar
    configuraciones = [("*", "+"), ("^", "~"), ("temaS", "subT"), ("`", "-")]  # por si no llegaras a usar subT(como en las preg del final de diseño), usa ^ (nodo) y ~ (rama))
    # en esta terna, si bien no necesariamente temaS se asocia con todos los subT es mejor hacerlo asi y luego separar los subT segun corresponda, para ahorrar tiempo
    # ("`","-") esta es otra alternativa para separar nodo y ramas, solo usarla cuando hagas mapas de comprension

    base = None  # Almacena la línea base que comienza con el marcador de nodo
    base_linea_num = None  # Número de la línea base

    for nodo, rama in configuraciones:
        for i, renglon in enumerate(lineas_filtradas, start=1):  # Enumerar con el número de línea
            renglon = renglon.rstrip()  # Eliminar saltos de línea extra

            if nodo in renglon:  # Si la línea contiene el nodo
                if rama in renglon and base is not None:
                    # Si ya había una base anterior, procesamos la combinación con las líneas previas
                    contenido += crear_flecha(j, base_linea_num + 1, i + 1)
                    j += 1
                base = renglon[len(nodo):].strip()  # Quitar el nodo y espacios
                base_linea_num = i  # Guardar el número de la línea base
            elif rama in renglon and base is not None:  # Si contiene la rama y hay una base
                contenido += crear_flecha(j, base_linea_num + 1, i + 1)
                j += 1

    contenido = contenido.replace('*', '').replace('+', '').replace("~", "").replace('^', '')
    contenido = contenido.replace('`', '').replace('-', '')  # comentar cuando no lo uses

    # volver a unir las flechas, las ramas vuelven a unirse a un nodo
    grupo = []  # Almacena los números de línea que comienzan con la rama de unión

    # Recorrer las líneas, omitiendo las vacías
    rama = "@"  # palabras a unir
    nodo = "$"  # donde se unirán
    for i, linea in enumerate(lineas_filtradas, start=1):  # Enumerar con el número de línea
        if rama in linea:
            grupo.append(i)  # Agregar número de línea al grupo
        elif nodo in linea and grupo:  # Si encuentra el nodo y hay un grupo acumulado
            for num in grupo:
                contenido += crear_flecha(j, num + 1, i + 1)
                j += 1
            grupo = []  # Reiniciar el grupo después de procesar

    contenido = contenido.replace('@', '').replace('$', '')

    # eliminar ultima flecha en caso de ser necesario, aunque en teoria siempre la eliminas por defecto al dejar un enter en la primera linea del txt a usar
    # ultima_flecha=f'''
    #      <mxCell id="{j-1}" edge="1" parent="1" source="{final-1}" target="{final}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#CCCC00;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
    #        <mxGeometry relative="1" as="geometry"/>
    #      </mxCell>'''
    # contenido=contenido.replace(ultima_flecha, '')
    # (CAMBIADO)poner el cuadro de pasos para usarlo por si fuera necesario, ya que nose como aplicarlo como palabra reservada debido a que posee mas de una estructura
    # contenido += f'''
    #      <mxCell id="{j+1}" value="" style="group;overflow=block;connectable=1;" vertex="1" connectable="0" parent="1">
    #        <mxGeometry x="20" y="120" width="174" height="100" as="geometry" />
    #      </mxCell>
    #      <mxCell id="{j+2}" value="" style="rounded=1;whiteSpace=wrap;shadow=0;labelBackgroundColor=none;strokeColor=none;strokeWidth=3;fillColor=#DEDE00;fontFamily=Helvetica;fontSize=16;fontColor=#FFFFFF;align=center;spacing=5;arcSize=7;perimeterSpacing=2;fillStyle=auto;gradientColor=none;container=0;verticalAlign=middle;overflow=block;connectable=0;" vertex="1" parent="{j+1}">
    #        <mxGeometry x="24" y="40" width="150" height="60" as="geometry" />
    #      </mxCell>
    #      <mxCell id="{j+3}" value="" style="ellipse;whiteSpace=wrap;fontSize=16;fillColor=#DEDE00;strokeColor=none;fontColor=#FFFFFF;rounded=1;shadow=0;labelBackgroundColor=none;strokeWidth=3;spacing=5;arcSize=7;fillStyle=auto;gradientColor=none;container=0;fontFamily=Helvetica;verticalAlign=middle;overflow=block;connectable=0;" vertex="1" parent="{j+1}">
    #        <mxGeometry width="50" height="50" as="geometry" />
    #      </mxCell>
    # '''

    # reemplazar color de los objetos
    contenido = contenido.replace('fillColor=#CCCC00', color1)  # reemplazar color de flechas y temas principales
    contenido = contenido.replace('fillColor=#DEDE00', color2)  # reemplazar color de cuadros
    contenido = contenido.replace('strokeColor=#CCCC00', color3)  # reemplazar color de flechas
    contenido = contenido.replace('gradientColor=#4F4F4F', color4)  # reemplazar color de gradiente
    contenido = contenido.replace('fontColor=#FFFFFF', color5)  # reemplazar color de fuente

    # reemplazar color de fuente de temaS a blanco si corresponde
    contenido = contenido.replace('fontFamily=Helvetica;fontColor=#5C5C5C', 'fontFamily=Helvetica;fontColor=#FFFFFF')

    # Agregar flecha principal y Cerrar las etiquetas XML
    contenido += f'''
          <mxCell id="{j}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#274B66;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;" edge="1" parent="1">
           <mxGeometry relative="1" as="geometry">
             <mxPoint x="190" y="30" as="sourcePoint" />
             <mxPoint x="190" y="100" as="targetPoint" />
           </mxGeometry>
          </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>'''

    # Crear el archivo de salida con el contenido generado
    with open(salida_xml, "w", encoding="utf-8") as archivo:
        archivo.write(contenido)
    print(f"diagrama creado -> {salida_xml}")


def extraer_subT(lineas_crudas, salida_recordatorio):
    # Busca renglones con la palabra reservada "subT" y guarda su contenido en el archivo de recordatorio
    with open(salida_recordatorio, "w", encoding="utf-8") as archivo:
        for renglon in lineas_crudas:
            renglon = renglon.strip()
            if "subT" in renglon:
                contenido = renglon.replace("^", "").replace("`", "").replace("~", "").strip()
                archivo.write(contenido + "\n")


def main():
    ap = argparse.ArgumentParser(
        description="Genera un mapa mental en XML de draw.io a partir de un archivo de texto.")
    ap.add_argument("entrada", nargs="?", default="modificables/texto.txt",
                     help="Archivo de texto de entrada (default: texto.txt).")
    ap.add_argument("--salida-xml", default="modificables/diagrama.xml",
                     help="Archivo XML de salida (default: diagrama.xml).")
    ap.add_argument("--salida-recordatorio", default="modificables/recordatorio.txt",
                     help="Archivo con los renglones 'subT' extraídos (default: recordatorio.txt).")
    args = ap.parse_args()

    try:
        lineas_crudas, lineas_filtradas = leer_lineas(args.entrada)
    except FileNotFoundError:
        print(f"ERROR: no se encontró el archivo '{args.entrada}'.")
        return
    except OSError as e:
        print(f"ERROR: no se pudo leer '{args.entrada}' ({e}).")
        return

    generar_diagrama(lineas_crudas, lineas_filtradas, args.salida_xml)
    extraer_subT(lineas_crudas, args.salida_recordatorio)


if __name__ == "__main__":
    main()