#define AppVersion "0.38"
#ifdef TestBuild
  #define AppIdentity "BrisaDock.Test.038"
  #define AppDisplayName "Brisa Dock prueba instalador"
  #define SetupName "BrisaDock-Setup-Test-0.38"
  #define MenuRoot "{app}\shortcuts\StartMenu"
  #define DesktopRoot "{app}\shortcuts\Desktop"
  #define StartupRoot "{app}\shortcuts\Startup"
#else
  #define AppIdentity "BrisaDock.Windows"
  #define AppDisplayName "Brisa Dock"
  #define SetupName "BrisaDock-Windows-0.38-Setup-RC"
  #define MenuRoot "{autoprograms}"
  #define DesktopRoot "{autodesktop}"
  #define StartupRoot "{autostartup}"
#endif
[Setup]
AppId={#AppIdentity}
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppPublisher=Brisa Dock contributors
VersionInfoVersion=0.38.0.0
VersionInfoProductName=BrisaDock
VersionInfoProductVersion=0.38.0.0
DefaultDirName={localappdata}\Programs\BrisaDock
DefaultGroupName={#AppDisplayName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
SourceDir=.
OutputDir=dist
OutputBaseFilename={#SetupName}
LicenseFile=LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\BrisaDock.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
SignedUninstaller=no
[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
[Tasks]
Name: "desktopicon"; Description: "Crear un acceso en el escritorio"; Flags: unchecked
Name: "startup"; Description: "Iniciar Brisa Dock al entrar en Windows"; Flags: unchecked
[Files]
Source: "dist\BrisaDock\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.md"; DestDir: "{app}"
Source: "*.py"; DestDir: "{app}\source"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}\source"
[Icons]
Name: "{#MenuRoot}\{#AppDisplayName}"; Filename: "{app}\BrisaDock.exe"; WorkingDir: "{app}"
Name: "{#DesktopRoot}\{#AppDisplayName}"; Filename: "{app}\BrisaDock.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{#StartupRoot}\{#AppDisplayName}"; Filename: "{app}\BrisaDock.exe"; WorkingDir: "{app}"; Tasks: startup
[Run]
Filename: "{app}\BrisaDock.exe"; Description: "Abrir Brisa Dock"; Flags: nowait postinstall skipifsilent
[Code]
var
  PurgeProfile: Boolean;

function ProfileDirectory(): String;
begin
#ifdef TestBuild
  Result := ExpandConstant('{app}\test-profile');
#else
  Result := ExpandConstant('{localappdata}\Brisa\BrisaDock');
#endif
end;

function HasReparsePoint(const Directory: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  if FindFirst(AddBackslash(Directory) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          if (FindRec.Attributes and $400) <> 0 then
          begin
            Result := True;
            Exit;
          end;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            if HasReparsePoint(AddBackslash(Directory) + FindRec.Name) then
            begin
              Result := True;
              Exit;
            end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure DeleteOwnShortcut(const Filename: String);
var
  Shell, Shortcut: Variant;
begin
  if not FileExists(Filename) then Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Shortcut := Shell.CreateShortcut(Filename);
    if CompareText(Shortcut.TargetPath, ExpandConstant('{app}\BrisaDock.exe')) = 0 then
      DeleteFile(Filename);
  except
    Log('No se pudo revisar un acceso propio; se conserva.');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not WizardIsTaskSelected('startup') then
      DeleteOwnShortcut(ExpandConstant('{#StartupRoot}\{#AppDisplayName}.lnk'));
    if not WizardIsTaskSelected('desktopicon') then
      DeleteOwnShortcut(ExpandConstant('{#DesktopRoot}\{#AppDisplayName}.lnk'));
  end;
end;

function ExplicitPurgeParameter(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/PURGEPROFILE') = 0 then
      Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  FindRec: TFindRec;
  RootIsLink: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    PurgeProfile := False;
    if UninstallSilent then
      PurgeProfile := ExplicitPurgeParameter()
    else
      PurgeProfile := MsgBox('¿Quieres eliminar también los ajustes y la caché de Brisa Dock?' + #13#10 +
        'Se conservarán si eliges No. No se eliminan tus archivos ni los destinos fijados.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  end;
  if (CurUninstallStep = usPostUninstall) and PurgeProfile then
  begin
    DataDir := ProfileDirectory();
    if not DirExists(DataDir) then Exit;
    if FileExists(AddBackslash(DataDir) + 'instance.lock') then
    begin
      Log('Perfil conservado: existe bloqueo de instancia. Cierra Brisa antes de eliminar datos.');
      Exit;
    end;
    RootIsLink := False;
    if FindFirst(DataDir, FindRec) then
    begin
      RootIsLink := (FindRec.Attributes and $400) <> 0;
      FindClose(FindRec);
    end;
    if RootIsLink or HasReparsePoint(DataDir) then
    begin
      Log('Perfil conservado: contiene una redirección o enlace. No se sigue para borrar datos.');
      Exit;
    end;
    if Length(DataDir) < 15 then Exit;
    if not DelTree(DataDir, True, True, True) then
      Log('No se pudo eliminar todo el perfil; se conservaron los archivos restantes.');
  end;
end;


