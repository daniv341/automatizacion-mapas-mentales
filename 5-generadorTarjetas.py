import re  # para el manejo del sufijo "& números"
from tkinter import Tk, Toplevel, filedialog, Button, Label, Frame

# Diccionario de colores (fuera de la función para que la ventana pueda usarlo)
colores = {
    "amarillo": ["#DEDE00", "#5C5C5C"],
    "celeste": ["#00CCCC", "#FFFFFF"],
    "rojo": ["#FF0000", "#FFFFFF"],
    "verde": ["#66CC00", "#FFFFFF"],
    "morado": ["#7F00FF", "#FFFFFF"],
    "rosado": ["#FF66FF", "#FFFFFF"],
    "azul": ["#0000FF", "#FFFFFF"],
    "bordo": ["#FF0080", "#FFFFFF"],
    "naranja": ["#FF8000", "#FFFFFF"],
    "verdeAgua": ["#0BD0AF", "#FFFFFF"],
    "gris": ["#999999", "#FFFFFF"],
    "marron": ["#89552A", "#FFFFFF"],
    "fucsia": ["#DC7BFF", "#FFFFFF"],
    "azulGrisaceo": ["#3399FF", "#FFFFFF"],
    "verdeOscuro": ["#14B866", "#FFFFFF"],
    "naranjaOscuro": ["#FF5A36", "#FFFFFF"],
    "rojoOscuro": ["#8B0000", "#FFFFFF"],
    "amarilloOscuro": ["#556B2F", "#FFFFFF"],
    "verdeClaro": ["#98FF98", "#5C5C5C"],
    "grisClaro": ["#E6E6FA", "#5C5C5C"],
    "rosadoClaro": ["#FFB6C1", "#5C5C5C"],
    "azulClaro": ["#B3BDFF", "#5C5C5C"],
    "moradoClaro": ["#696ADC", "#FFFFFF"],
    "rojoClaro": ["#FF6347", "#FFFFFF"],
    "marronClaro": ["#CD853F", "#FFFFFF"],
    "amarilloClaro": ["#FFFF80", "#5C5C5C"],
}


# NUEVO: escapa los caracteres especiales para que el XML no se rompa
def escapar_texto(texto):
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


# Ventanita para elegir el color
def elegir_color(root, colores):
    eleccion = {"color": None}

    ventana = Toplevel(root)
    ventana.title("Color de las tarjetas")
    ventana.attributes("-topmost", True)
    ventana.resizable(False, False)
    ventana.grab_set()

    Label(ventana, text="Selecciona un color:",
          font=("Arial", 12, "bold")).pack(pady=(10, 5))

    frame = Frame(ventana)
    frame.pack(padx=10, pady=5)

    def seleccionar(nombre):
        eleccion["color"] = nombre
        ventana.destroy()

    COLUMNAS = 3
    for i, (nombre, valores) in enumerate(colores.items()):
        fill, font = valores[0], valores[-1]
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

    Button(ventana, text="Estilo por defecto", width=32,
           command=lambda: seleccionar("")).pack(pady=(5, 10))

    root.wait_window(ventana)
    return eleccion["color"]


def generar_tarjetas():
    root = Tk()
    root.withdraw()
    txt = filedialog.askopenfilename(
        title="Selecciona el archivo",
        filetypes=[("Archivo de Texto", "*.txt"), ("Todos los archivos", "*.*")]
    )
    if not txt:
        print("No se seleccionó ningún archivo. Cancelado.")
        root.destroy()
        return

    opciones = elegir_color(root, colores)
    root.destroy()
    if opciones is None:
        print("Selección de color cancelada.")
        return

    with open(txt, "r", encoding="utf-8") as archivo:
        lineas = archivo.readlines()

    contenido = '''<mxfile host="app.diagrams.net">
  <diagram name="Página 1">
    <mxGraphModel dx="1100" dy="580" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="827" pageHeight="1169" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />'''

    id_counter = 2
    x_pos = 20
    y_pos = 20
    espaciado_y = 130
    espaciado_x = 260
    horizontal_index = 0

    estilo_defecto = "shape=card;whiteSpace=wrap;html=1;"
    ancho = 220
    alto = 100

    # Patrón para el sufijo "& números/lista de números" al final del renglón
    PATRON_AMP = re.compile(r'\s*&[\d,\s]*\d[\d,\s]*$')
    # NUEVO: ignorar desde "|" en adelante (incluido el |)
    PATRON_PIPE = re.compile(r'\s*\|.*$')

    if opciones and opciones in colores:
        fillColor = colores[opciones][0]
        fontColor = colores[opciones][-1]
        estilo = f"shape=card;whiteSpace=wrap;html=1;fillColor={fillColor};strokeWidth=3;fontSize=16;strokeColor={fontColor};fontColor={fontColor};"
    else:
        fillColor = "#DEDE00"
        fontColor = "#FFFFFF"
        estilo = estilo_defecto

    estilo_subT = f"rhombus;whiteSpace=wrap;fontSize=16;fillColor={fillColor};strokeColor=default;fontColor={fontColor};rounded=1;shadow=0;labelBackgroundColor=none;strokeWidth=4;spacing=5;arcSize=7;fillStyle=auto;gradientColor=none;fontFamily=Helvetica;fontStyle=1;overflow=block;"

    for renglon in lineas:
        renglon = renglon.strip()

        # NUEVO: quitar desde "|" en adelante (PRIMERO, para que el
        # patrón de "& números" pueda limpiar lo que quede al final)
        renglon = PATRON_PIPE.sub('', renglon).strip()

        # Quitar el sufijo "& números/lista de números" del final (si existe)
        renglon = PATRON_AMP.sub('', renglon).strip()

        if not renglon:
            horizontal_index += 1
            y_pos = 20
            x_pos = 20 + horizontal_index * espaciado_x
            continue

        es_subT = "subT" in renglon
        if es_subT:
            renglon = renglon.replace("subT", "").strip()

        if "+" in renglon:
            primero, segundo = renglon.split("+", 1)
            # NUEVO: se escapa DESPUÉS del upper() (si fuera al revés,
            # las entidades como &lt; se convertirían en &LT; y se romperían)
            primero = escapar_texto(primero.strip().upper())
            segundo = escapar_texto(segundo.strip())
            valor = f"{primero}&lt;br&gt;&lt;br&gt;{segundo}"
        else:
            valor = escapar_texto(renglon.strip())

        estilo_final = estilo_subT if es_subT else estilo

        contenido += f'''
        <mxCell id="{id_counter}" value="{valor}" style="{estilo_final}" vertex="1" parent="1">
          <mxGeometry x="{x_pos}" y="{y_pos}" width="{ancho}" height="{alto}" as="geometry" />
        </mxCell>'''

        y_pos += espaciado_y
        id_counter += 1

    contenido += '''
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>'''

    with open("modificables/tarjetas.xml", "w", encoding="utf-8") as archivo:
        archivo.write(contenido)
    print("diagrama creado")


generar_tarjetas()