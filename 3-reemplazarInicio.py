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

# Ejemplo de uso
nombre_archivo= "borrador.txt"  # Nombre del archivo de entrada
simbolos = {"temaP", "clasP", "temaS", "subT", "textS", "deF", "imP", "cuR", "clasS", "caR", "ejeM", "recoR", "nuM", "pasoS", "preG", "comP"} # Conjunto de palabras clave
procesar_lineas_con_simbolos(nombre_archivo, simbolos)