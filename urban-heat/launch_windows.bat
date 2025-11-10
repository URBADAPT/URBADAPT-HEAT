@echo off
setlocal

REM === CONFIGURA QUI ===
set "ENV_NAME=urbanheat"
set "PROJECT_DIR=H:\Il mio Drive\adaptation_infrastructure_database\URBADAPT\urban-heat"
set "ENV_FILE=%PROJECT_DIR%\environment.yml"

REM === INIZIALIZZA CONDA PER I FILE .BAT ===
REM Se usi Anaconda:
REM CALL "%UserProfile%\anaconda3\Scripts\activate.bat" base
REM Se usi Miniconda (altrimenti lascia commentata):
CALL "%UserProfile%\miniconda3\Scripts\activate.bat" base

REM === VERIFICA/CREA L'AMBIENTE ===
CALL conda activate %ENV_NAME% >NUL 2>&1
IF ERRORLEVEL 1 (
  echo [i] L'ambiente "%ENV_NAME%" non esiste. Lo creo da "%ENV_FILE%"...
  IF NOT EXIST "%ENV_FILE%" (
    echo [!] File environment.yml non trovato a: "%ENV_FILE%".
    echo     Controlla PROJECT_DIR o il nome del file.
    pause
    exit /b 1
  )
  cd /d "%PROJECT_DIR%"
  CALL conda env create -f "%ENV_FILE%"
  IF ERRORLEVEL 1 (
    echo [!] Creazione dell'ambiente fallita.
    pause
    exit /b 1
  )
  set "JUST_CREATED=1"
  CALL conda activate %ENV_NAME%
  IF ERRORLEVEL 1 (
    echo [!] Impossibile attivare l'ambiente appena creato.
    pause
    exit /b 1
  )
) ELSE (
  set "JUST_CREATED=0"
)

REM === VAI NELLA CARTELLA DEL PROGETTO ===
cd /d "%PROJECT_DIR%"

REM === REGISTRA IL KERNEL JUPYTER SOLO ALLA PRIMA CREAZIONE ===
IF "%JUST_CREATED%"=="1" (
  echo [i] Registro il kernel Jupyter per "%ENV_NAME%".
  python -m ipykernel install --user --name %ENV_NAME% --display-name "Python (%ENV_NAME%)"
)

REM === AVVIA JUPYTER NOTEBOOK ===
jupyter notebook

REM === LASCIA LA FINESTRA APERTA PER VEDERE EVENTUALI ERRORI ===
pause
