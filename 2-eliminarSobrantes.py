def reemplazar_caracteres(nombre_archivo):
    # Definir los caracteres a reemplazar
    eliminar1 = ['<', '>','&']
    eliminar2 = ['“', '”', '"']

    # Leer el archivo
    with open(nombre_archivo, 'r', encoding='utf-8') as archivo:
        contenido = archivo.read()

    # Reemplazar los caracteres por espacio vacío
    for caracter in eliminar1:
        contenido = contenido.replace(caracter, '')
        
    for caracter in eliminar2:
        contenido = contenido.replace(caracter, "'")

    # Guardar el archivo con los cambios
    with open(nombre_archivo, 'w', encoding='utf-8') as archivo:
        archivo.write(contenido)

def eliminar_articulos_y_guardar(nombre_archivo_entrada):
    
    # Lista de palabras a eliminar
    articulos = {"la", "las", "lo", "los", "el", "de"}

    # Leer el contenido del archivo proporcionado
    with open(nombre_archivo_entrada, "r", encoding="utf-8") as archivo:
        texto = archivo.read()

    # Procesar línea por línea para respetar los saltos
    lineas = texto.splitlines()
    lineas_filtradas = []

    for linea in lineas:
        palabras = linea.split()
        palabras_filtradas = [palabra for palabra in palabras if palabra.lower() not in articulos]
        linea_filtrada = " ".join(palabras_filtradas)
        # Reemplazar puntos con puntos seguidos de un salto de línea
        #linea_filtrada = linea_filtrada.replace("(", " \n(")
        linea_filtrada = linea_filtrada.replace(" por ", "\npor ")
        linea_filtrada = linea_filtrada.replace(" en ", "\nen ")
        linea_filtrada = linea_filtrada.replace(" con ", "\ncon ")
        linea_filtrada = linea_filtrada.replace(" para ", "\npara ")
        linea_filtrada = linea_filtrada.replace(" que ", "\nque ")
        linea_filtrada = linea_filtrada.replace(" como ", "\ncomo ")
        linea_filtrada = linea_filtrada.replace(" entre ", "\nentre ")
        linea_filtrada = linea_filtrada.replace(" a ", "\na ")
        linea_filtrada = linea_filtrada.replace(" o ", " o \n")
        linea_filtrada = linea_filtrada.replace(" e ", " e \n")
        linea_filtrada = linea_filtrada.replace(" y ", " y \n")

        linea_filtrada = linea_filtrada.replace("- ", " \n_")
        linea_filtrada = linea_filtrada.replace(". ", "\n\n")
        linea_filtrada = linea_filtrada.replace(".", "\n")
        linea_filtrada = linea_filtrada.replace(", ", ",\n")
        linea_filtrada = linea_filtrada.replace(": ", ":\n")

        linea_filtrada = linea_filtrada.replace(" un ", " 1 ")

        linea_filtrada = linea_filtrada.replace("&", "")
        #linea_filtrada = linea_filtrada.replace("<", "")
        #linea_filtrada = linea_filtrada.replace(">", "")

        linea_filtrada = linea_filtrada.replace("“", "")
        linea_filtrada = linea_filtrada.replace("”", "")
        linea_filtrada = linea_filtrada.replace('"', "'")


        lineas_filtradas.append(linea_filtrada)
        
    # Reconstruir el texto respetando los saltos de línea
    texto_filtrado = "\n".join(lineas_filtradas)

    # Guardar el texto resultante en un archivo .txt
    with open(nombre_archivo_entrada, "w", encoding="utf-8") as archivo:
        archivo.write(texto_filtrado)

    print(f"Texto filtrado guardado en '{nombre_archivo_entrada}'.")

# Ejemplo de uso
nombre_archivo = "borrador.txt"  # Nombre del archivo de entrada
eliminar_articulos_y_guardar(nombre_archivo)
#reemplazar_caracteres(nombre_archivo)
