def generar_diagrama():
    # Leer el contenido del archivo texto.txt y separarlo en renglones
    txt="texto.txt"

    with open(txt, "r", encoding="utf-8") as archivo:
        lineas = archivo.readlines()

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
    horizontal_index = 0 # Inicializa el índice para determinar la posición horizontal

    # Definir estilos según las palabras reservadas
    estilos = {
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
    tamanos = {
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
        "nuM": (45.52, 45.52 ),
        "pasoS": (180, 60),
        "preG": (162.77, 70),
        "comP": (156.6, 90)
    }

    # Cantidad de carateres que puede soportar cada cuadro, el segundo valor seria en nuevo ancho
    caracter = {
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

    #codigos de colores [color temas principales(temaS), color cuadros(imP, cuR), color de flechas, color gradiente en temas principales, color de fuente]
    colores = {
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
    #elegir color de cuadros, si usas uno de los colores claros debes cambiar de forma manual el color de los "temaP"
    opciones="verdeAgua" #aqui debes poner el color que quieras
    color1= colores[opciones][0]#color de flechas y temas principales
    color2= colores[opciones][1]#color de cuadros
    color3= colores[opciones][2]#color de flechas
    color4= colores[opciones][3]#color de gradiente
    color5= colores[opciones][4]#color de fuente

    # Agregar cada línea como un nuevo nodo mxCell con un id único y el estilo correspondiente
    for renglon in lineas:
        renglon = renglon.strip()  # Elimina espacios y saltos de línea extra
        if not renglon:  # Si la línea está vacía, continúa
            horizontal_index += 1  # Mover a la derecha
            y_pos = 20  # Reiniciar la posición vertical
            x_pos = 20 + horizontal_index * espaciado_x  # Ajustar la posición horizontal
            continue

        # Determinar el estilo y tamaño basados en la palabra reservada
        estilo = ''
        tamaño = (160, 40)  # Tamaño por defecto
        for palabra, style in estilos.items():
            if palabra in renglon:
                estilo = style
                ancho, alto = tamanos[palabra] 
                if len(renglon) > caracter[palabra][0]: #si el renglon tiene mas aracteres que el limite aumenta el ancho
                    ancho = caracter[palabra][1]
                tamaño = (ancho, alto)
                # Eliminar la palabra reservada del renglón
                renglon = renglon.replace(palabra, '').strip()  # Eliminar la palabra reservada
                break
        if estilo == '':
            estilo = 'rounded=0;whiteSpace=wrap;html=1;'  # Estilo por defecto si no hay palabra reservada

        # Añadir el cuadro con la posición ajustada
        contenido += f'''
        <mxCell id="{id_counter}" value="{renglon}" style="{estilo}" vertex="1" parent="1">
          <mxGeometry x="{x_pos}" y="{y_pos}" width="{tamaño[0]}" height="{tamaño[1]}" as="geometry" />
        </mxCell>'''

        # Incrementar la posición vertical para el siguiente cuadro
        y_pos += espaciado_y
        id_counter += 1  # Incrementar el ID para el siguiente cuadro
        j=id_counter
        final=id_counter
        # Leer el contenido del archivo texto.txt y separarlo en renglones
    with open(txt, "r", encoding="utf-8") as archivo:
      lineas = archivo.readlines()

    id_counter = 2

    # Recorremos las líneas del archivo
    for renglon in lineas:  # Usamos el índice para poder acceder a la siguiente línea
      renglon = renglon.strip()  # Elimina espacios y saltos de línea extra
      if renglon and id_counter <= final:  # Si la línea actual no esta vacia
          contenido += f'''
          <mxCell id="{j}" edge="1" parent="1" source="{id_counter}" target="{id_counter+1}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#CCCC00;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
            <mxGeometry relative="1" as="geometry"/>
          </mxCell>'''
          xml_bloque = f'''
          <mxCell id="{j}" edge="1" parent="1" source="{id_counter}" target="{id_counter+1}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#CCCC00;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
            <mxGeometry relative="1" as="geometry"/>
          </mxCell>'''
          id_counter+=1
          j+=1
      elif not renglon:  # Si la línea actual está vacía
          contenido=contenido.replace(xml_bloque, '')
        

    #separar flechas(and) el nodo bifurca en ramas
    # Abrir el archivo de entrada para leer
    with open(txt, 'r', encoding="utf-8") as archivo:
      lineas = [renglon.strip() for renglon in archivo if renglon.strip()]  # Ignorar líneas vacías

      base = None  # Almacena la línea base que comienza con #
      base_linea_num = None  # Número de la línea base

    # Lista de configuraciones a iterar
    configuraciones = [("*", "+"), ("^", "~"),("temaS","subT"),("`","-")] #por si no llegaras a usar subT(como en las preg del final de diseño), usa ^ (nodo) y ~ (rama))
    # en esta terna, si bien no necesariamente temaS se asocia con todos los subT es mejor hacerlo asi y luego separar los subT segun corresponda, para ahorrar tiempo
    # ("`","-") esta es otra alternativa para separar nodo y ramas, solo usarla cuando hagas mapas de comprension 

    for nodo, rama in configuraciones:
      for i, renglon in enumerate(lineas, start=1):  # Enumerar con el número de línea
        renglon = renglon.rstrip()  # Eliminar saltos de línea extra

        if nodo in renglon:  # Si la línea contiene el nodo
            if rama in renglon and base is not None:
                # Si ya había una base anterior, procesamos la combinación con las líneas previas
                contenido += f'''
          <mxCell id="{j}" edge="1" parent="1" source="{base_linea_num+1}" target="{i+1}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#CCCC00;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
            <mxGeometry relative="1" as="geometry"/>
          </mxCell>'''      
                j += 1
            base = renglon[len(nodo):].strip()  # Quitar el nodo y espacios
            base_linea_num = i  # Guardar el número de la línea base
        elif rama in renglon and base is not None:  # Si contiene la rama y hay una base
            contenido += f'''
          <mxCell id="{j}" edge="1" parent="1" source="{base_linea_num+1}" target="{i+1}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#CCCC00;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
            <mxGeometry relative="1" as="geometry"/>
          </mxCell>'''
            j += 1

    contenido = contenido.replace('*', '').replace('+', '').replace("~","").replace('^','')
    contenido = contenido.replace('`','').replace('-','') #comentar cuando no lo uses


    #volver a unir las flechas, las ramas vuelven a unirse a un nodo
    # Abrir el archivo de entrada para leer
    with open(txt, 'r', encoding="utf-8") as archivo:
        lineas = [linea.strip() for linea in archivo if linea.strip()]  # Ignorar líneas vacías

    grupo = []  # Almacena las líneas que comienzan con #

    # Recorrer las líneas, omitiendo las vacías
    rama="@" #palabras a unir
    nodo="$" #donde se uniran
    for i, linea in enumerate(lineas, start=1):  # Enumerar con el número de línea
        if rama in linea:
            grupo.append(i)  # Agregar número de línea al grupo
        elif nodo in linea and grupo:  # Si encuentra + y hay un grupo acumulado
            for num in grupo:
                contenido +=f'''
          <mxCell id="{j}" edge="1" parent="1" source="{num+1}" target="{i+1}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#CCCC00;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
            <mxGeometry relative="1" as="geometry"/>
          </mxCell>'''
                j+=1
            grupo = []  # Reiniciar el grupo después de procesar  

    contenido=contenido.replace('@','').replace('$','')

    #eliminar ultima flecha en caso de ser necesario, aunque en teoria siempre la eliminas por defecto al dejar un enter en la primera linea del txt a usar
    # ultima_flecha=f'''
    #      <mxCell id="{j-1}" edge="1" parent="1" source="{final-1}" target="{final}" style="edgeStyle=none;rounded=0;jumpStyle=none;html=1;shadow=0;labelBackgroundColor=none;startArrow=none;startFill=0;endArrow=classic;endFill=1;jettySize=auto;orthogonalLoop=1;strokeColor=#CCCC00;strokeWidth=4;fontFamily=Helvetica;fontSize=14;fontColor=#FFFFFF;spacing=5;endSize=8;">
    #        <mxGeometry relative="1" as="geometry"/>
    #      </mxCell>'''
    # contenido=contenido.replace(ultima_flecha, '')
    #(CAMBIADO)poner el cuadro de pasos para usarlo por si fuera necesario, ya que nose como aplicarlo como palabra reservada debido a que posee mas de una estructura
    #contenido += f'''
    #      <mxCell id="{j+1}" value="" style="group;overflow=block;connectable=1;" vertex="1" connectable="0" parent="1">
    #        <mxGeometry x="20" y="120" width="174" height="100" as="geometry" />
    #      </mxCell>
    #      <mxCell id="{j+2}" value="" style="rounded=1;whiteSpace=wrap;shadow=0;labelBackgroundColor=none;strokeColor=none;strokeWidth=3;fillColor=#DEDE00;fontFamily=Helvetica;fontSize=16;fontColor=#FFFFFF;align=center;spacing=5;arcSize=7;perimeterSpacing=2;fillStyle=auto;gradientColor=none;container=0;verticalAlign=middle;overflow=block;connectable=0;" vertex="1" parent="{j+1}">
    #        <mxGeometry x="24" y="40" width="150" height="60" as="geometry" />
    #      </mxCell>
    #      <mxCell id="{j+3}" value="" style="ellipse;whiteSpace=wrap;fontSize=16;fillColor=#DEDE00;strokeColor=none;fontColor=#FFFFFF;rounded=1;shadow=0;labelBackgroundColor=none;strokeWidth=3;spacing=5;arcSize=7;fillStyle=auto;gradientColor=none;container=0;fontFamily=Helvetica;verticalAlign=middle;overflow=block;connectable=0;" vertex="1" parent="{j+1}">
    #        <mxGeometry width="50" height="50" as="geometry" />
    #      </mxCell>
    #'''

    #reemplazar color de los objetos
    contenido=contenido.replace('fillColor=#CCCC00',color1) #reemplazar color de flechas y temas principales
    contenido=contenido.replace('fillColor=#DEDE00',color2) #reemplazar color de cuadros
    contenido=contenido.replace('strokeColor=#CCCC00',color3) #reemplazar color de flechas
    contenido=contenido.replace('gradientColor=#4F4F4F',color4) #reemplazar color de gradiente
    contenido=contenido.replace('fontColor=#FFFFFF',color5) #reemplzar color de fuente

    #reemplazar color de fuente de temaS a blanco si corresponde
    contenido=contenido.replace('fontFamily=Helvetica;fontColor=#5C5C5C','fontFamily=Helvetica;fontColor=#FFFFFF')

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

    # Crear el archivo diagrama.txt con el contenido generado
    with open("diagrama.xml", "w", encoding="utf-8") as archivo:
        archivo.write(contenido)
    print("diagrama creado")


def extraer_subT():
    txt="texto.txt"
    with open(txt, "r", encoding="utf-8") as archivo:
      lineas = archivo.readlines()

    # Busca renglones con la palabra reservada "subT" y guarda su contenido en recordatorio.txt
    with open("recordatorio.txt", "w", encoding="utf-8") as archivo:
        for renglon in lineas:
            renglon = renglon.strip()
            if "subT" in renglon:
                contenido = renglon.replace("^", "").replace("`", "").replace("~", "").strip()
                archivo.write(contenido + "\n")

# Llamada a la función
generar_diagrama()
extraer_subT()

