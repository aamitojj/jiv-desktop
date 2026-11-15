; Jiv Desktop installer (NSIS 3, per-user). Build: installer/build_installer.sh
Unicode true
!ifndef VERSION
  !define VERSION "1.2.0"
!endif
!ifndef STAGE
  !define STAGE "stage"
!endif
!ifndef OUTFILE
  !define OUTFILE "JivSetup-${VERSION}.exe"
!endif
!define APPNAME "Jiv Desktop"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\JivDesktop"

Name "${APPNAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\JivDesktop"
InstallDirRegKey HKCU "${UNINSTKEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APPNAME} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "MIT License"

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "StrFunc.nsh"
${StrLoc}

!define MUI_ICON "jiv.ico"
!define MUI_UNICON "jiv.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "header.bmp"
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${APPNAME} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Jiv is a private AI assistant that runs on your own computer.$\r$\n$\r$\nIt installs just for you (no administrator rights needed) and can update itself later from the Updates page.$\r$\n$\r$\nIf you already use Ollama, your downloaded models are found automatically. If not, the Setup Wizard can install a free local model for you.$\r$\n$\r$\nClick Next to continue."
!define MUI_FINISHPAGE_RUN "$INSTDIR\JivDesktop.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch Jiv Desktop"
!define MUI_FINISHPAGE_TEXT "${APPNAME} is installed.$\r$\n$\r$\nYour chats, agents and settings live in %APPDATA%\JivDesktop and are kept when you upgrade."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_COMPONENTS
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Var OldVersion

Function .onInit
  ReadRegStr $OldVersion HKCU "${UNINSTKEY}" "DisplayVersion"
  ${If} $OldVersion != ""
    ReadRegStr $0 HKCU "${UNINSTKEY}" "InstallLocation"
    ${If} $0 != ""
      StrCpy $INSTDIR $0
    ${EndIf}
  ${EndIf}
FunctionEnd

Function WaitForJivExit
  StrCpy $R2 0
  loop:
    nsExec::ExecToStack 'tasklist /FI "IMAGENAME eq JivDesktop.exe" /NH'
    Pop $R0
    Pop $R1
    StrCpy $R3 0
    ${StrLoc} $R3 $R1 "JivDesktop.exe" ">"
    StrCmp $R3 "" done
    IntOp $R2 $R2 + 1
    IntCmp $R2 30 done
    Sleep 500
    Goto loop
  done:
FunctionEnd

Section "Jiv Desktop (required)" SecMain
  SectionIn RO
  ; close a running Jiv. Started from Jiv's Updates page, Jiv quits by itself: give it up to 15 s first,
  ; so this installer (its grandchild) is not caught by the tree kill below.
  Call WaitForJivExit
  nsExec::Exec 'taskkill /IM JivDesktop.exe /T /F'
  Sleep 1000
  ${If} $OldVersion != ""
    DetailPrint "Upgrading Jiv Desktop $OldVersion -> ${VERSION} (your data in %APPDATA%\JivDesktop is kept)"
  ${EndIf}
  ; replace program files completely (self-updates of the old version are superseded); user data is elsewhere
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  SetOutPath "$INSTDIR"
  File "${STAGE}\JivDesktop.exe"
  File "jiv.ico"
  File "LICENSE.txt"
  File /r "${STAGE}\python"
  File /r "${STAGE}\app"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\Jiv Desktop.lnk" "$INSTDIR\JivDesktop.exe" "" "$INSTDIR\jiv.ico" 0
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "Jiv Desktop"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\jiv.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"
SectionEnd

Function .onInstSuccess
  ; started from Jiv's Updates page: bring Jiv back when the upgrade is done (also in silent mode)
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/RELAUNCH" $R1
  ${IfNot} ${Errors}
    Exec '"$INSTDIR\JivDesktop.exe"'
  ${EndIf}
FunctionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\Jiv Desktop.lnk" "$INSTDIR\JivDesktop.exe" "" "$INSTDIR\jiv.ico" 0
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "The Jiv app, its private Python and the update system."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Put a Jiv Desktop icon on your desktop."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "un.Jiv Desktop (program files)" UnSecMain
  SectionIn RO
  nsExec::Exec 'taskkill /IM JivDesktop.exe /T /F'
  Sleep 1000
  Delete "$SMPROGRAMS\Jiv Desktop.lnk"
  Delete "$DESKTOP\Jiv Desktop.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\JivDesktop.exe"
  Delete "$INSTDIR\jiv.ico"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
SectionEnd

Section /o "un.Also delete my data (chats, agents, settings, backups)" UnSecData
  RMDir /r "$APPDATA\JivDesktop"
SectionEnd

Function un.onInit
  ; scripted uninstall: Uninstall.exe /S keeps your data; add /DELETEDATA to remove it as well
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/DELETEDATA" $R1
  ${IfNot} ${Errors}
    SectionSetFlags ${UnSecData} ${SF_SELECTED}
  ${EndIf}
FunctionEnd

!insertmacro MUI_UNFUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${UnSecMain} "Removes the program from $INSTDIR."
  !insertmacro MUI_DESCRIPTION_TEXT ${UnSecData} "Unticked = your data in %APPDATA%\JivDesktop is kept for a future reinstall. Ollama and its models are never touched."
!insertmacro MUI_UNFUNCTION_DESCRIPTION_END
