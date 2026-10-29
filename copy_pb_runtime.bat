@ECHO OFF
SET rnt=C:\Program Files (x86)\Appeon\Common\PowerBuilder\Runtime 25.1.0.6430
REM %dst% jābūt definētam pirms šī skripta izsaukšanas

IF NOT EXIST "%dst%" MKDIR "%dst%"
IF NOT EXIST "%dst%\lang" MKDIR "%dst%\lang"

FOR %%F IN (
    AtlAuxiliary.dll
    PBACC.DLL
    PBAccessibility.dll
    PBDOM.pbx
    PBDWE.DLL
    PBODB.DLL
    PBPDF.dll
    PBSHR.DLL
    PBUIS.dll
    concrt140.dll
    mfc140u.dll
    msvcp140.dll
    pbXerces.dll
    pbcompression.dll
    pbcrypt.dll
    pbhttpclient.dll
    pbjson.dll
    pboauth.dll
    pbodb.ini
    pbpdfbuilder.dll
    pbresource.dll
    pbrestclient.dll
    pbsysfunc.dll
    pbtra.DLL
    pbvm.dll
    pbwebbrowser.dll
    pdflib.dll
    vcruntime140.dll
    vcruntime140_1.dll
    xerces-c.dll
    pbdom.pbd
    pbribbonbar.dll
) DO (
    COPY /Y "%rnt%\%%F" "%dst%\" >NUL || ECHO KLUDA: nav nokopēts %%F
)

FOR %%F IN (
    french.lng
    german.lng
) DO (
    COPY /Y "%rnt%\lang\%%F" "%dst%\lang\" >NUL || ECHO KLUDA: nav nokopēts lang\%%F
)
