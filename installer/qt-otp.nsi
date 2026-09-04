; NSIS installer for qt-otp.
;
;   makensis /DVERSION=1.4.0 installer\qt-otp.nsi
;
; Wraps the single PyInstaller executable - there is nothing else to lay down -
; so the installer only buys shortcuts, an Apps & features entry and a clean
; uninstall. The portable .exe stays a first-class way to run the app.
;
; Overridable on the command line with /D:
;   VERSION    x.y.z, must match otpvault.__version__   (required)
;   VERSION4   x.y.z.w for the Windows version resource (default "${VERSION}.0")
;   SRC_EXE    the built qt-otp.exe                     (default ..\dist\qt-otp.exe)
;   ICON       the app icon                             (default ..\build\qt-otp.ico)
;   LICENSE_FILE  text shown on the license page        (default ..\LICENSE)
;   OUT_FILE   installer to write                       (default ..\dist\qt-otp-setup.exe)

Unicode true
ManifestDPIAware true
SetCompressor /SOLID lzma

!ifndef VERSION
  !error "pass /DVERSION=x.y.z (it must match otpvault.__version__)"
!endif
!define /ifndef VERSION4  "${VERSION}.0"
!define /ifndef SRC_EXE   "..\dist\qt-otp.exe"
!define /ifndef ICON      "..\build\qt-otp.ico"
!define /ifndef LICENSE_FILE "..\LICENSE"
!define /ifndef OUT_FILE  "..\dist\qt-otp-setup.exe"

!define APP_NAME    "qt-otp"
!define APP_EXE     "qt-otp.exe"
!define PUBLISHER   "qt-otp"
!define APP_URL     "https://github.com/uberlinuxguy/qt-otp"
!define UNINST_KEY  "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

Name "${APP_NAME} ${VERSION}"
OutFile "${OUT_FILE}"
BrandingText "${APP_NAME} ${VERSION}"

; ---------------------------------------------------------------- install mode
;
; Per-user by default: the vault, the settings and the app itself are all one
; person's business, and a per-user install needs no administrator. "All users"
; stays available for anyone deploying to a shared machine.
;
; DEFAULT_REGISTRY_VALUENAME is read from HKCU first and then HKLM, so with a
; per-user default the value to look for is "AllUsers" - each install writes a
; value named after its own mode, and only a machine-wide one answers here.
!define MULTIUSER_EXECUTIONLEVEL Highest
!define MULTIUSER_MUI
!define MULTIUSER_INSTALLMODE_COMMANDLINE
!define MULTIUSER_INSTALLMODE_DEFAULT_CURRENTUSER
!define MULTIUSER_INSTALLMODE_INSTDIR "${APP_NAME}"
!define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_KEY "${UNINST_KEY}"
!define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_VALUENAME "AllUsers"
!define MULTIUSER_INSTALLMODE_INSTDIR_REGISTRY_KEY "${UNINST_KEY}"
!define MULTIUSER_INSTALLMODE_INSTDIR_REGISTRY_VALUENAME "InstallLocation"
!define MULTIUSER_USE_PROGRAMFILES64

!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "MultiUser.nsh"
!include "MUI2.nsh"

; ---------------------------------------------------------------------- pages
!define MUI_ICON   "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TITLE "${APP_NAME} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "An encrypted desktop vault for your TOTP authenticator codes.$\r$\n$\r$\nThis will install ${APP_NAME} ${VERSION}. Your vault file and settings are left alone by installing and by uninstalling, so upgrading over an existing copy is safe.$\r$\n$\r$\nClose ${APP_NAME} before continuing."

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Run ${APP_NAME} now"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchAsUser
!define MUI_FINISHPAGE_LINK "${APP_NAME} on GitHub"
!define MUI_FINISHPAGE_LINK_LOCATION "${APP_URL}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"
!insertmacro MULTIUSER_PAGE_INSTALLMODE
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ------------------------------------------------------------ version resource
VIProductVersion "${VERSION4}"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} installer"
VIAddVersionKey "FileVersion"     "${VERSION}"
VIAddVersionKey "ProductVersion"  "${VERSION}"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "LegalCopyright"  "Licensed under the Apache License 2.0"

; -------------------------------------------------------------------- helpers

; Leaves 0 in OUT when a copy of the app is running. Windows will not let us
; overwrite a running executable, so this turns a cryptic write failure into a
; fixable prompt. `find` exits 0 only when it matched, which is the whole test -
; no string searching needed on the NSIS side.
!macro AppIsRunning OUT
  nsExec::ExecToStack '"$SYSDIR\cmd.exe" /C tasklist /NH /FI "IMAGENAME eq ${APP_EXE}" | find /I "${APP_EXE}"'
  Pop ${OUT}
  Pop $R9   ; discard the captured output
!macroend

; LABEL keeps the jump targets unique between the installer and the uninstaller,
; which are compiled into the same script.
!macro WaitForAppToClose LABEL
  ; A silent run has nobody to ask: let the file operation fail loudly instead.
  IfSilent ${LABEL}_done
  ${LABEL}_retry:
  !insertmacro AppIsRunning $R8
  ${If} $R8 == 0
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
      "${APP_NAME} is still running.$\r$\n$\r$\nClose it - the tray icon too - and choose Retry." \
      /SD IDCANCEL IDRETRY ${LABEL}_retry
    Abort
  ${EndIf}
  ${LABEL}_done:
!macroend

; The finish-page checkbox runs from the installer process, which is elevated
; whenever the user is an administrator. Starting the app that way would give it
; the wrong %APPDATA% and a clipboard it cannot share with normal windows, so
; hand the launch to Explorer, which runs at the desktop's own integrity level.
Function LaunchAsUser
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\${APP_EXE}"'
FunctionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP \
      "${APP_NAME} is a 64-bit application and needs 64-bit Windows." /SD IDOK
    Abort
  ${EndIf}
  ; NSIS itself is a 32-bit process; without this every HKLM write would be
  ; redirected into Wow6432Node, where Apps & features never looks.
  SetRegView 64

  ; MULTIUSER_INIT assigns $INSTDIR unconditionally, which would silently throw
  ; away a directory given as /D= on the command line (NSIS has already put it
  ; in $INSTDIR by the time .onInit runs). Nothing else sets $INSTDIR before
  ; this point, so a non-empty value here can only have come from /D=.
  StrCpy $R0 $INSTDIR
  !insertmacro MULTIUSER_INIT
  ${If} $R0 != ""
    StrCpy $INSTDIR $R0
  ${EndIf}
FunctionEnd

Function un.onInit
  SetRegView 64
  !insertmacro MULTIUSER_UNINIT
FunctionEnd

; ------------------------------------------------------------------- sections

Section "!${APP_NAME}" SecCore
  SectionIn RO

  !insertmacro WaitForAppToClose inst

  SetOutPath "$INSTDIR"
  SetOverwrite on
  File "/oname=${APP_EXE}" "${SRC_EXE}"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; SHCTX is HKLM or HKCU to match the install mode chosen above.
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayName"     "${APP_NAME}"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayVersion"  "${VERSION}"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE},0"
  WriteRegStr SHCTX "${UNINST_KEY}" "Publisher"       "${PUBLISHER}"
  WriteRegStr SHCTX "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr SHCTX "${UNINST_KEY}" "URLInfoAbout"    "${APP_URL}"
  WriteRegStr SHCTX "${UNINST_KEY}" "HelpLink"        "${APP_URL}/issues"
  WriteRegDWORD SHCTX "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD SHCTX "${UNINST_KEY}" "NoRepair" 1
  ; The uninstaller cannot tell which mode it belongs to on its own, so the
  ; mode rides along on its command line.
  WriteRegStr SHCTX "${UNINST_KEY}" "UninstallString" \
    '"$INSTDIR\Uninstall.exe" /$MultiUser.InstallMode'
  WriteRegStr SHCTX "${UNINST_KEY}" "QuietUninstallString" \
    '"$INSTDIR\Uninstall.exe" /$MultiUser.InstallMode /S'
  ; ...and this value, named after the mode, is what the *next* installer reads
  ; to default to the same choice.
  WriteRegDWORD SHCTX "${UNINST_KEY}" "$MultiUser.InstallMode" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD SHCTX "${UNINST_KEY}" "EstimatedSize" $0
SectionEnd

Section "Start Menu shortcut" SecStartMenu
  ; /NoWorkingDir: a working directory pinned to $INSTDIR would keep the install
  ; folder busy, and the app does not need one.
  CreateShortcut /NoWorkingDir "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Section /o "Desktop shortcut" SecDesktop
  CreateShortcut /NoWorkingDir "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore} \
    "The ${APP_NAME} application and its entry in Apps & features."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} \
    "A ${APP_NAME} shortcut in the Start menu."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} \
    "A ${APP_NAME} shortcut on the desktop."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ------------------------------------------------------------------ uninstall

Section "Uninstall"
  !insertmacro WaitForAppToClose uninst

  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\Uninstall.exe"
  ; Non-recursive on purpose: $INSTDIR is user-chosen, and whatever else is in
  ; there is not ours to delete.
  RMDir "$INSTDIR"

  DeleteRegKey SHCTX "${UNINST_KEY}"

  ; The vault holds the only copy of the user's TOTP secrets, so removing it is
  ; opt-in and never happens unattended. The path is spelled out in the prompt
  ; because an elevated uninstall resolves %APPDATA% for whoever answered UAC.
  IfSilent keep_data
  SetShellVarContext current    ; the vault is per-user even for an all-users install
  ${If} $APPDATA != ""
  ${AndIf} ${FileExists} "$APPDATA\${APP_NAME}\*.*"
    MessageBox MB_YESNO|MB_ICONEXCLAMATION|MB_DEFBUTTON2 \
      "Also delete your vault and settings?$\r$\n$\r$\n$APPDATA\${APP_NAME}$\r$\n$\r$\nThis destroys the only copy of your TOTP secrets and cannot be undone. Choose No to keep them for a later reinstall." \
      /SD IDNO IDNO keep_data
    RMDir /r "$APPDATA\${APP_NAME}"
    DeleteRegKey HKCU "Software\${PUBLISHER}"
  ${EndIf}
  keep_data:
SectionEnd
