; FResucitary Pro 2.1.3 — Production NSIS installer
; Build from project root with packaging\build_release.ps1

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

!define APP_NAME      "FResucitary Pro"
!define APP_VERSION   "2.1.3"
!define APP_PUBLISHER "FResucitary"
!define APP_EXE       "FResucitary.exe"
!define APP_GUID      "{8E754BCB-4B1A-41A4-A05F-29D11E29BB31}"
!define REG_KEY       "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_GUID}"

Name              "${APP_NAME} ${APP_VERSION}"
OutFile           "..\dist\FResucitary-Pro-Setup-${APP_VERSION}-x64.exe"
InstallDir        "$PROGRAMFILES64\FResucitary Pro"
InstallDirRegKey  HKLM "${REG_KEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor     /SOLID lzma
SetCompressorDictSize 64

VIProductVersion  "2.1.3.0"
VIAddVersionKey   /LANG=1033 "ProductName" "${APP_NAME}"
VIAddVersionKey   /LANG=1033 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey   /LANG=1033 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey   /LANG=1033 "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey   /LANG=1033 "FileVersion" "${APP_VERSION}"

!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "French"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "${APP_NAME} nécessite Windows 64 bits."
    Abort
  ${EndIf}
  SetRegView 64
FunctionEnd

Section "Application" SecMain
  SectionIn RO
  SetShellVarContext all
  SetRegView 64

  SetOutPath "$INSTDIR"
  File /r "..\dist\FResucitary\*.*"
  File "..\README.md"
  File "..\LICENSE.txt"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  WriteRegStr   HKLM "${REG_KEY}" "DisplayName"       "${APP_NAME}"
  WriteRegStr   HKLM "${REG_KEY}" "DisplayVersion"    "${APP_VERSION}"
  WriteRegStr   HKLM "${REG_KEY}" "Publisher"         "${APP_PUBLISHER}"
  WriteRegStr   HKLM "${REG_KEY}" "InstallLocation"   "$INSTDIR"
  WriteRegStr   HKLM "${REG_KEY}" "UninstallString"   '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKLM "${REG_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegStr   HKLM "${REG_KEY}" "DisplayIcon"       '"$INSTDIR\${APP_EXE}"'
  WriteRegDWORD HKLM "${REG_KEY}" "NoModify"          1
  WriteRegDWORD HKLM "${REG_KEY}" "NoRepair"          1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKLM "${REG_KEY}" "EstimatedSize" $0

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Désinstaller.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  SetRegView 64

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Désinstaller.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  DeleteRegKey HKLM "${REG_KEY}"
  RMDir /r "$INSTDIR"
SectionEnd
