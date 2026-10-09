@echo off
setlocal enabledelayedexpansion

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


:: ------------------------------------------------------------
:: Verificar que haya cambios en guardados/evaluadores/
:: ------------------------------------------------------------

git add guardados/evaluadores/

git diff --cached --quiet

if %errorlevel% equ 0 (
    echo.
    echo No hay cambios en guardados/evaluadores/
    echo.
    pause
    exit /b 0
)


:: ------------------------------------------------------------
:: Obtener fecha
:: ------------------------------------------------------------

set FECHA=%date:~0,2%/%date:~3,2%/%date:~6,4%


:: ------------------------------------------------------------
:: Construir mensaje del commit
:: ------------------------------------------------------------

set MSG=
set TOTAL=0
set MOSTRADOS=0

for /f "tokens=1,* delims=	" %%a in ('git -c "core.quotePath=false" diff --cached --name-status') do (

    set /a TOTAL+=1

    if !MOSTRADOS! lss 3 (

        set "TIPO=%%a"
        set "ARCHIVO=%%b"

        set "ARCHIVO=!ARCHIVO:guardados/evaluadores/=!"

        if "!TIPO!"=="A" (
            set MSG=!MSG! + '!ARCHIVO!'
        ) else if "!TIPO!"=="M" (
            set MSG=!MSG! ~ '!ARCHIVO!'
        ) else if "!TIPO!"=="D" (
            set MSG=!MSG! - '!ARCHIVO!'
        )

        set /a MOSTRADOS+=1
    )
)


:: ------------------------------------------------------------
:: Indicar archivos restantes
:: ------------------------------------------------------------

set /a RESTANTES=TOTAL-MOSTRADOS

if !RESTANTES! gtr 0 (
    set MSG=!MSG! +!RESTANTES! mas
)


:: ------------------------------------------------------------
:: Quitar espacio inicial del mensaje
:: ------------------------------------------------------------

set MSG=!MSG:~1!


:: ------------------------------------------------------------
:: Mostrar informacion antes del commit
:: ------------------------------------------------------------

echo.
echo ============================================
echo Cambios detectados:
echo ============================================
echo.
git diff --cached --stat
echo.
echo Commit:
echo "Evaluadores %FECHA% - !MSG!"
echo.


:: ------------------------------------------------------------
:: Commit
:: ------------------------------------------------------------

git commit -m "Evaluadores %FECHA% - !MSG!"

if %errorlevel% neq 0 (
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

if %errorlevel% neq 0 (
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