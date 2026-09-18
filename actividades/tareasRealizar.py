def generar_combinacion(temas, max_horas, max_aburrimiento, contextos):
    combinacion = []
    total_horas = 0
    total_aburrimiento = 0
    contextos_usados = set()

    temas_ordenados = sorted(temas, key=lambda x: (x['prioridad'], x['horas'], x['aburrimiento']))

    for tema in temas_ordenados:
        if (
            total_horas + tema['horas'] <= max_horas and
            total_aburrimiento + tema['aburrimiento'] <= max_aburrimiento and
            tema['contexto'] not in contextos_usados
        ):
            tema['nombre'] = f"{tema['nombre']}"
            combinacion.append(tema)
            total_horas += tema['horas']
            total_aburrimiento += tema['aburrimiento']
            contextos_usados.add(tema['contexto'])

    return combinacion, total_horas, total_aburrimiento

def exportar_resultados(dias_combinaciones, temas_restantes, contextos):
    with open("evaluador/TAREAS!!!!.txt", "w") as file:
        for dia, (combinacion, total_horas, total_aburrimiento) in enumerate(dias_combinaciones, start=1):
            file.write(f"Dia {dia} - Combinacion de estudio seleccionada:\n")
            for i, tema in enumerate(combinacion, start=1):
                contexto_nombre = contextos.get(tema['contexto'], 'Desconocido')
                file.write(f"{i}. {tema['nombre']} - {tema['horas']} horas, Aburrimiento: {tema['aburrimiento']}, Prioridad: {tema['prioridad']}, Contexto: {contexto_nombre}\n")
            file.write(f"\nTotal de horas: {total_horas}\n")
            file.write(f"Total de aburrimiento: {total_aburrimiento}\n\n")

        if temas_restantes:
            file.write("Tareas no asignadas:\n")
            for tema in temas_restantes:
                contexto_nombre = contextos.get(tema['contexto'], 'Desconocido')
                file.write(f"- {tema['nombre']} (Contexto: {contexto_nombre})\n")

def main():
    contextos = {
        1: "MP IO", 
        2: "Final Redes", 
        3: "Vitualizacion", 
        4: "Buscar Material Complementario"
    }

    temas = [
        {"nombre": "MP pronostico", "horas": 1, "aburrimiento": 6, "prioridad": 3, "contexto": 1},
        {"nombre": "MP virtualizacion", "horas": 1.5, "aburrimiento": 7, "prioridad": 3, "contexto": 3},
        {"nombre": "tarea Virtualizacion", "horas": 2, "aburrimiento": 8, "prioridad": 2, "contexto": 3},
        #{"nombre": "buscar material", "horas": 1.5, "aburrimiento": 6, "prioridad": 4, "contexto": 4},
        {"nombre": "estudiar U1-1 redes", "horas": 1, "aburrimiento": 7, "prioridad": 1, "contexto": 2},
        {"nombre": "estudiar U1-3 redes", "horas": 1, "aburrimiento": 7, "prioridad": 1, "contexto": 2},
        {"nombre": "estudiar U2-1 redes", "horas": 1, "aburrimiento": 7, "prioridad": 1, "contexto": 2},
    ]

    max_horas = 4
    max_aburrimiento = 15

    temas_restantes = temas.copy()
    dias_combinaciones = []

    for _ in range(3): #dias a visualizar
        combinacion, total_horas, total_aburrimiento = generar_combinacion(temas_restantes, max_horas, max_aburrimiento, contextos)
        dias_combinaciones.append((combinacion, total_horas, total_aburrimiento))
        temas_restantes = [t for t in temas_restantes if t not in combinacion]

    exportar_resultados(dias_combinaciones, temas_restantes, contextos)

if __name__ == "__main__":
    main()

#cada tarea pertenece a un contexto, si el mismo no lo posee, lo tomara como desconocido

#la escala de prioridad es de 1 a 5 (1 es la maxima prioridad y 5 la minima)
#ten en cuenta que sin importar que tanto dure la tarea, el programa tratara de completar las horas maximas

#la escala de aburrimiento es de 1 a 10 (10 es muy aburrido y 1 es nada aburrido), 
#ten en cuenta que sin importar que tan aburrida sea la tarea, el programa tratara de completar los puntos de aburrimiento maximo

#puedes modificar el orden de prioridad de los parametros en la linea 7, el orden actual es prioridad-horas-aburrimiento
#siendo "prioridad" la mas alta

#en la linea 66 puedes elegir la cantidad de dias

#linea 60 la cantidad max de horas
#linea 61 la cantidad max de aburrimiento
