@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: ============================================================
:: INSTRUCCIONES DE USO
:: ============================================================
::
:: Este .bat SOLO commitea y pushea cambios dentro de:
::
::     guardados/evaluadores/
::
:: NO ejecuta ningun script Python.
:: Se asume que los evaluadores ya fueron generados/modificados.
::
:: IMPORTANTE:
:: Los demas cambios del repositorio NO seran incluidos.
:: Si modificaste archivos fuera de guardados/evaluadores/,
:: deberas hacer un commit normal por separado.
::
:: USO:
::
:: 1) Genera o modifica los evaluadores.
::
:: 2) Verifica que los archivos esten dentro de:
::        guardados/evaluadores/
::
:: 3) Desde la raiz del repositorio, ejecuta:
::
::        .\deploy-evaluadores.bat
::
:: 4) El script:
::        - Agrega solamente guardados/evaluadores/
::        - Detecta archivos nuevos, modificados y eliminados
::        - Prioriza creados, luego modificados y finalmente eliminados
::        - Genera automaticamente el mensaje del commit
::        - Hace commit
::        - Hace push
::
:: 5) Al finalizar, presiona una tecla para cerrar.
::
:: FORMATO DEL COMMIT:
::
:: "Evaluadores DD/MM/YYYY - + 'archivo nuevo.html' ~ 'archivo modificado.html' - 'archivo eliminado.html'"
::
:: Simbolos:
::
::     + = archivo creado
::     ~ = archivo modificado
::     - = archivo eliminado
::
:: Se muestran como maximo 3 evaluadores.
:: Si hay mas de 3, se agrega:
::
::     +N mas
::
:: Ejemplo:
::
:: "Evaluadores 05/10/2026 - + 'evaluador_Biologia.html' ~ 'evaluador_Gestion de Operaciones.html' +2 mas"
::
:: ============================================================


git add -- guardados/evaluadores/ guardados/antiguos/evaluadores/

if errorlevel 1 (
    echo.
    echo ERROR: No se pudieron agregar las carpetas.
    echo.
    pause
    exit /b 1
)


:: ------------------------------------------------------------
:: Verificar si hay cambios preparados
:: ------------------------------------------------------------

git diff --cached --quiet

if not errorlevel 1 (
    echo.
    echo No hay cambios en las carpetas de evaluadores.
    echo.
    pause
    exit /b 0
)


:: ------------------------------------------------------------
:: Obtener fecha
:: ------------------------------------------------------------

set "FECHA=%date:~0,2%/%date:~3,2%/%date:~6,4%"


:: ------------------------------------------------------------
:: Crear archivos temporales
:: ------------------------------------------------------------

set "ID_TEMP=%RANDOM%_%RANDOM%"

set "TEMP_COMUNES=%TEMP%\eval_comunes_%ID_TEMP%.txt"
set "TEMP_ANTIGUOS=%TEMP%\eval_antiguos_%ID_TEMP%.txt"

set "TEMP_CA=%TEMP%\eval_ca_%ID_TEMP%.txt"
set "TEMP_CM=%TEMP%\eval_cm_%ID_TEMP%.txt"
set "TEMP_CD=%TEMP%\eval_cd_%ID_TEMP%.txt"

set "TEMP_AA=%TEMP%\eval_aa_%ID_TEMP%.txt"
set "TEMP_AM=%TEMP%\eval_am_%ID_TEMP%.txt"
set "TEMP_AD=%TEMP%\eval_ad_%ID_TEMP%.txt"

type nul > "%TEMP_CA%"
type nul > "%TEMP_CM%"
type nul > "%TEMP_CD%"
type nul > "%TEMP_AA%"
type nul > "%TEMP_AM%"
type nul > "%TEMP_AD%"


:: ------------------------------------------------------------
:: Obtener cambios de las dos carpetas
:: ------------------------------------------------------------

git -c core.quotepath=false diff --cached --name-status -- guardados/evaluadores/ > "%TEMP_COMUNES%"

if errorlevel 1 (
    echo ERROR: No se pudieron obtener los cambios de evaluadores.
    goto ERROR_TEMP
)

git -c core.quotepath=false diff --cached --name-status -- guardados/antiguos/evaluadores/ > "%TEMP_ANTIGUOS%"

if errorlevel 1 (
    echo ERROR: No se pudieron obtener los cambios de antiguos.
    goto ERROR_TEMP
)


:: ------------------------------------------------------------
:: Clasificar los archivos sin dividir los nombres por espacios
:: ------------------------------------------------------------

set "TOTAL_COMUNES=0"
set "TOTAL_ANTIGUOS=0"

for /f "usebackq tokens=1,* delims=	" %%A in ("%TEMP_COMUNES%") do (

    set "TIPO=%%A"
    set "ARCHIVO=%%B"
    set "ARCHIVO=!ARCHIVO:guardados/evaluadores/=!"

    set /a TOTAL_COMUNES+=1

    if "!TIPO!"=="A" (
        >>"%TEMP_CA%" echo(!ARCHIVO!
    ) else if "!TIPO!"=="M" (
        >>"%TEMP_CM%" echo(!ARCHIVO!
    ) else if "!TIPO!"=="D" (
        >>"%TEMP_CD%" echo(!ARCHIVO!
    )
)

for /f "usebackq tokens=1,* delims=	" %%A in ("%TEMP_ANTIGUOS%") do (

    set "TIPO=%%A"
    set "ARCHIVO=%%B"
    set "ARCHIVO=!ARCHIVO:guardados/antiguos/evaluadores/=!"

    set /a TOTAL_ANTIGUOS+=1

    if "!TIPO!"=="A" (
        >>"%TEMP_AA%" echo(!ARCHIVO!
    ) else if "!TIPO!"=="M" (
        >>"%TEMP_AM%" echo(!ARCHIVO!
    ) else if "!TIPO!"=="D" (
        >>"%TEMP_AD%" echo(!ARCHIVO!
    )
)


:: ------------------------------------------------------------
:: Determinar el tipo de mensaje y los archivos que se mostraran
:: ------------------------------------------------------------

set "MSG="
set "MOSTRADOS=0"
set "TOTAL=0"

if !TOTAL_COMUNES! gtr 0 (
    set "PREFIJO=Evaluadores"
    set "TOTAL=!TOTAL_COMUNES!"
    set "TEMP_CREADOS=%TEMP_CA%"
    set "TEMP_MODIFICADOS=%TEMP_CM%"
    set "TEMP_ELIMINADOS=%TEMP_CD%"
) else (
    set "PREFIJO=Evaluadores Antiguos"
    set "TOTAL=!TOTAL_ANTIGUOS!"
    set "TEMP_CREADOS=%TEMP_AA%"
    set "TEMP_MODIFICADOS=%TEMP_AM%"
    set "TEMP_ELIMINADOS=%TEMP_AD%"
)


:: ------------------------------------------------------------
:: Construir el resumen respetando la prioridad
:: Creados, modificados y eliminados
::
:: IMPORTANTE:
:: "delims=" conserva cada nombre completo, incluidos espacios.
:: ------------------------------------------------------------

for %%T in (CREADOS MODIFICADOS ELIMINADOS) do (

    if !MOSTRADOS! lss 3 (

        if "%%T"=="CREADOS" (
            set "ARCHIVO_TEMP=%TEMP_CREADOS%"
            set "SIMBOLO=+"
        )

        if "%%T"=="MODIFICADOS" (
            set "ARCHIVO_TEMP=%TEMP_MODIFICADOS%"
            set "SIMBOLO=~"
        )

        if "%%T"=="ELIMINADOS" (
            set "ARCHIVO_TEMP=%TEMP_ELIMINADOS%"
            set "SIMBOLO=-"
        )

        for /f "usebackq delims=" %%F in ("!ARCHIVO_TEMP!") do (

            if !MOSTRADOS! lss 3 (
                set "MSG=!MSG! !SIMBOLO! '%%F'"
                set /a MOSTRADOS+=1
            )
        )
    )
)


:: ------------------------------------------------------------
:: Indicar archivos restantes
:: ------------------------------------------------------------

set /a RESTANTES=TOTAL-MOSTRADOS

if !RESTANTES! gtr 0 (
    set "MSG=!MSG! +!RESTANTES! mas"
)


:: ------------------------------------------------------------
:: Si hay cambios en ambas carpetas, agregar la cantidad
:: total de archivos antiguos creados, modificados y eliminados
:: ------------------------------------------------------------

if !TOTAL_COMUNES! gtr 0 if !TOTAL_ANTIGUOS! gtr 0 (
    set "MSG=!MSG! + !TOTAL_ANTIGUOS! antiguos"
)


:: ------------------------------------------------------------
:: Quitar espacio inicial del mensaje
:: ------------------------------------------------------------

if defined MSG set "MSG=!MSG:~1!"


:: ------------------------------------------------------------
:: Mostrar informacion antes del commit
:: ------------------------------------------------------------

echo.
echo ============================================
echo Cambios detectados:
echo ============================================
echo.

git diff --cached --stat -- guardados/evaluadores/ guardados/antiguos/evaluadores/

echo.
echo Commit:
echo "!PREFIJO! %FECHA% - !MSG!"
echo.


:: ------------------------------------------------------------
:: Limpiar archivos temporales
:: ------------------------------------------------------------

del "%TEMP_COMUNES%" 2>nul
del "%TEMP_ANTIGUOS%" 2>nul
del "%TEMP_CA%" 2>nul
del "%TEMP_CM%" 2>nul
del "%TEMP_CD%" 2>nul
del "%TEMP_AA%" 2>nul
del "%TEMP_AM%" 2>nul
del "%TEMP_AD%" 2>nul


:: ------------------------------------------------------------
:: Commit
:: ------------------------------------------------------------

git commit -m "!PREFIJO! %FECHA% - !MSG!"

if errorlevel 1 (
    echo.
    echo ERROR: No se pudo realizar el commit.
    echo.
    pause
    exit /b 1
)


:: ------------------------------------------------------------
:: Push
:: ------------------------------------------------------------

echo.
echo Subiendo cambios a GitHub...
echo.

git push

if errorlevel 1 (
    echo.
    echo ERROR: El commit se realizo, pero el push fallo.
    echo.
    pause
    exit /b 1
)


:: ------------------------------------------------------------
:: Final
:: ------------------------------------------------------------

echo.
echo ============================================
echo Evaluadores publicados correctamente.
echo ============================================
echo.
pause

endlocal
exit /b 0


:: ------------------------------------------------------------
:: Error al obtener cambios: limpiar temporales
:: ------------------------------------------------------------

:ERROR_TEMP
del "%TEMP_COMUNES%" 2>nul
del "%TEMP_ANTIGUOS%" 2>nul
del "%TEMP_CA%" 2>nul
del "%TEMP_CM%" 2>nul
del "%TEMP_CD%" 2>nul
del "%TEMP_AA%" 2>nul
del "%TEMP_AM%" 2>nul
del "%TEMP_AD%" 2>nul
echo.
echo ERROR: No se pudo preparar el resumen de cambios.
echo.
pause
exit /b 1
