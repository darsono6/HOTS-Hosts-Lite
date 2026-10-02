
#define MyAppName "HOTS Hosts Lite"
#define MyAppVersion "1.1"
#define MyAppPublisher "Darsono"
#define MyAppExeName "HOTS Hosts Lite.exe"
#define MyAppAssocName MyAppName + " File"
#define MyAppAssocExt ""
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt
#expr EmitLanguagesSection

[Setup]
AppId={{9A7DC768-D48F-4E32-8E6E-0CDAF1AB022B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}.0.0
VersionInfoTextVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt
OutputDir=Output
OutputBaseFilename=HOTS_Hosts_Lite_setup
SetupIconFile=icon.ico
SolidCompression=yes
WizardStyle=modern dynamic
; Must match the mutex held by hosts_editor_launcher.pyw.
AppMutex=Global\HOTS_HostsLite_SingleInstance_Mutex

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\hosts_editor_launcher.dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\hosts_editor_launcher.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "hots_uninstall_cleanup.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppAssocKey}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppAssocName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\hots_uninstall_cleanup.cmd"; Flags: runhidden waituntilterminated

[UninstallDelete]
; hots_uninstall_cleanup.cmd (UninstallRun) runs before this; {app} holds no user data.
Type: filesandordirs; Name: "{app}"

[Code]

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    // User data lives outside {app}, so wiping it is safe (also removes stale files on update).
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;

function GetAppLanguage(): String;
var
  SettingsPath: String;
  Lines: TArrayOfString;
  I, P1, P2: Integer;
  Line, Lang: String;
begin
  Result := 'en';
  SettingsPath := ExpandConstant('{userappdata}') + '\HOTS Hosts Lite\settings.json';
  if not FileExists(SettingsPath) then
    Exit;
  if not LoadStringsFromFile(SettingsPath, Lines) then
    Exit;

  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Lines[I];
    if Pos('"language"', Line) > 0 then
    begin
      P1 := Pos(':', Line);
      if P1 = 0 then
        Continue;
      Line := Copy(Line, P1 + 1, Length(Line) - P1);
      P1 := Pos('"', Line);
      if P1 = 0 then
        Continue;
      Line := Copy(Line, P1 + 1, Length(Line) - P1);
      P2 := Pos('"', Line);
      if P2 = 0 then
        Continue;
      Lang := Copy(Line, 1, P2 - 1);

      if (Lang = 'pl') or (Lang = 'fr') or (Lang = 'de') or (Lang = 'es')
         or (Lang = 'ru') or (Lang = 'pt') or (Lang = 'en') then
        Result := Lang;
      Exit;
    end;
  end;
end;

function GetUpdateVsUninstallText(): String;
var
  Lang: String;
begin
  Lang := GetAppLanguage();

  if Lang = 'pl' then
    Result :=
      'Jeśli planujesz zainstalować nowszą wersję HOTS Hosts Lite, nie musisz najpierw ' +
      'odinstalowywać programu — wystarczy uruchomić nowy instalator bezpośrednio. ' +
      'Zaktualizuje on program w miejscu, bez utraty ustawień ani listy domen.' + #13#10 + #13#10 +
      'Czy mimo to chcesz kontynuować i całkowicie usunąć HOTS Hosts Lite, cofając zmiany ' +
      'wprowadzone w systemie?'
  else if Lang = 'fr' then
    Result :=
      'Si vous êtes sur le point d''installer une version plus récente de HOTS Hosts Lite, ' +
      'il n''est pas nécessaire de désinstaller d''abord - lancez simplement le nouvel ' +
      'installateur directement. Il mettra à jour le programme sur place, sans perdre ' +
      'vos paramètres ni votre liste de domaines.' + #13#10 + #13#10 +
      'Voulez-vous tout de même continuer et supprimer complètement HOTS Hosts Lite, en ' +
      'annulant les modifications apportées à ce système ?'
  else if Lang = 'de' then
    Result :=
      'Wenn Sie eine neuere Version von HOTS Hosts Lite installieren möchten, müssen Sie ' +
      'das Programm nicht vorher deinstallieren - führen Sie einfach das neue ' +
      'Installationsprogramm direkt aus. Es aktualisiert das Programm an Ort und Stelle, ' +
      'ohne Ihre Einstellungen oder Ihre Domainliste zu verlieren.' + #13#10 + #13#10 +
      'Möchten Sie trotzdem fortfahren und HOTS Hosts Lite vollständig entfernen und die am ' +
      'System vorgenommenen Änderungen rückgängig machen?'
  else if Lang = 'es' then
    Result :=
      'Si estás a punto de instalar una versión más reciente de HOTS Hosts Lite, no es ' +
      'necesario desinstalar primero - simplemente ejecuta el nuevo instalador ' +
      'directamente. Actualizará el programa en su lugar, sin perder tu configuración ' +
      'ni tu lista de dominios.' + #13#10 + #13#10 +
      '¿Deseas continuar de todos modos y eliminar completamente HOTS Hosts Lite, revirtiendo ' +
      'los cambios realizados en este sistema?'
  else if Lang = 'ru' then
    Result :=
      'Если вы собираетесь установить более новую версию HOTS Hosts Lite, вам не нужно ' +
      'сначала удалять программу - просто запустите новый установщик напрямую. Он ' +
      'обновит программу на месте, не потеряв ваши настройки и список доменов.' + #13#10 + #13#10 +
      'Всё равно хотите продолжить и полностью удалить HOTS Hosts Lite, отменив изменения, ' +
      'внесённые в систему?'
  else if Lang = 'pt' then
    Result :=
      'Se está prestes a instalar uma versão mais recente do HOTS Hosts Lite, não precisa de ' +
      'desinstalar primeiro - basta executar o novo instalador diretamente. Ele ' +
      'atualizará o programa no local, sem perder as suas definições ou a sua lista de domínios.' + #13#10 + #13#10 +
      'Ainda assim, deseja continuar e remover completamente o HOTS Hosts Lite, revertendo as ' +
      'alterações feitas neste sistema?'
  else
    Result :=
      'If you are about to install a newer version of HOTS Hosts Lite, you do NOT need to ' +
      'uninstall first - just run the new installer directly. It will update the ' +
      'program in place, without losing your settings or domain list.' + #13#10 + #13#10 +
      'Do you want to continue and completely remove HOTS Hosts Lite, undoing the changes ' +
      'it made to this system?';
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;

  // Lite has no parental controls, so unlike the full version uninstall needs no password.
  if not UninstallSilent then
  begin
    if MsgBox(GetUpdateVsUninstallText(), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
      Result := False;
  end;
end;
