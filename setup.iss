
#define MyAppName "HOTS Hosts Lite"
#define MyAppVersion "1.2"
#define MyAppPublisher "Darsono"
#define MyAppExeName "HOTS Hosts Lite.exe"
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

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\hots_uninstall_cleanup.cmd"; Parameters: """{app}\hots_uninst_choices.json"""; Flags: runhidden waituntilterminated

[UninstallDelete]
; hots_uninstall_cleanup.cmd (UninstallRun) runs before this; {app} holds no user data.
; Only a folder named like the program is removed (see IsOwnFolderName).
Type: filesandordirs; Name: "{app}"; Check: IsOwnFolderName

[Code]

function ChoicesFile(): String;
begin
  Result := ExpandConstant('{app}\hots_uninst_choices.json');
end;

// The install folder is wiped on update and on uninstall, so it must always be a folder
// dedicated to this program. Whatever location the user picks, the program is installed in
// a subfolder named "{#MyAppName}" (D:\Apps -> D:\Apps\{#MyAppName}), so these steps can
// never touch C:\Windows, C:\Program Files, a drive root, etc.
function IsOwnFolderName(): Boolean;
begin
  Result := CompareText(ExtractFileName(RemoveBackslash(ExpandConstant('{app}'))), '{#MyAppName}') = 0;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Dir := RemoveBackslash(WizardDirValue);
    if CompareText(ExtractFileName(Dir), '{#MyAppName}') <> 0 then
      WizardForm.DirEdit.Text := Dir + '\{#MyAppName}';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    // User data lives outside {app}, so wiping a previous install is safe (removes stale files on update).
    if IsOwnFolderName() and DirExists(ExpandConstant('{app}')) then
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
         or (Lang = 'ru') or (Lang = 'pt') or (Lang = 'it') or (Lang = 'en') then
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
  else if Lang = 'it' then
    Result :=
      'Se stai per installare una versione più recente di HOTS Hosts Lite, non è necessario ' +
      'disinstallare prima: basta avviare direttamente il nuovo programma di installazione. ' +
      'Aggiornerà il programma sul posto, senza perdere le tue impostazioni né l''elenco dei domini.' + #13#10 + #13#10 +
      'Vuoi comunque continuare e rimuovere completamente HOTS Hosts Lite, annullando le modifiche ' +
      'apportate al sistema?'
  else
    Result :=
      'If you are about to install a newer version of HOTS Hosts Lite, you do NOT need to ' +
      'uninstall first - just run the new installer directly. It will update the ' +
      'program in place, without losing your settings or domain list.' + #13#10 + #13#10 +
      'Do you want to continue and completely remove HOTS Hosts Lite, undoing the changes ' +
      'it made to this system?';
end;

function GetWizardFailedText(): String;
var
  Lang: String;
begin
  Lang := GetAppLanguage();

  if Lang = 'pl' then
    Result :=
      'Nie udało się otworzyć okna wyboru elementów do usunięcia. Jeśli kontynuujesz, program ' +
      'zostanie odinstalowany, ale Twoje ustawienia, blokady i kopie zapasowe pozostaną na komputerze.' + #13#10 + #13#10 +
      'Czy mimo to kontynuować?'
  else if Lang = 'fr' then
    Result :=
      'La fenêtre de choix des éléments à supprimer n''a pas pu s''ouvrir. Si vous continuez, le ' +
      'programme sera désinstallé, mais vos paramètres, blocages et sauvegardes resteront sur cet ordinateur.' + #13#10 + #13#10 +
      'Continuer quand même ?'
  else if Lang = 'de' then
    Result :=
      'Das Fenster zur Auswahl der zu entfernenden Elemente konnte nicht geöffnet werden. Wenn Sie ' +
      'fortfahren, wird das Programm deinstalliert, Ihre Einstellungen, Sperren und Sicherungen ' +
      'bleiben jedoch auf diesem Computer.' + #13#10 + #13#10 + 'Trotzdem fortfahren?'
  else if Lang = 'es' then
    Result :=
      'No se pudo abrir la ventana para elegir qué eliminar. Si continúas, el programa se ' +
      'desinstalará, pero tu configuración, bloqueos y copias de seguridad permanecerán en este equipo.' + #13#10 + #13#10 +
      '¿Continuar de todos modos?'
  else if Lang = 'ru' then
    Result :=
      'Не удалось открыть окно выбора элементов для удаления. Если продолжить, программа будет ' +
      'удалена, но ваши настройки, блокировки и резервные копии останутся на этом компьютере.' + #13#10 + #13#10 +
      'Всё равно продолжить?'
  else if Lang = 'pt' then
    Result :=
      'Não foi possível abrir a janela de escolha do que remover. Se continuar, o programa será ' +
      'desinstalado, mas as suas definições, bloqueios e cópias de segurança permanecerão neste computador.' + #13#10 + #13#10 +
      'Continuar mesmo assim?'
  else if Lang = 'it' then
    Result :=
      'Non è stato possibile aprire la finestra per scegliere cosa rimuovere. Se continui, il ' +
      'programma verrà disinstallato, ma le tue impostazioni, i blocchi e i backup resteranno su questo computer.' + #13#10 + #13#10 +
      'Continuare comunque?'
  else
    Result :=
      'The window for choosing what to remove could not be opened. If you continue, the program ' +
      'will be uninstalled, but your settings, blocks and backups will stay on this computer.' + #13#10 + #13#10 +
      'Continue anyway?';
end;

// Shows the "what should we undo?" wizard before anything is removed, so closing it or
// pressing Cancel really cancels the uninstall. The choices are applied later by
// hots_uninstall_cleanup.cmd. Exit codes of the wizard: 0 = confirmed, 3 = cancelled.
function RunUninstallWizard(): Boolean;
var
  ResultCode: Integer;
  Launched: Boolean;
begin
  Result := True;
  if UninstallSilent then
    Exit;

  DeleteFile(ChoicesFile());
  Launched := Exec(ExpandConstant('{app}\{#MyAppExeName}'),
                   '--uninstall-wizard "' + ChoicesFile() + '"',
                   '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode);

  if Launched and (ResultCode = 3) then
    Result := False
  else if not (Launched and (ResultCode = 0) and FileExists(ChoicesFile())) then
    Result := MsgBox(GetWizardFailedText(), mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES;

  if not Result then
    DeleteFile(ChoicesFile());
end;

procedure DeinitializeUninstall();
begin
  DeleteFile(ChoicesFile());
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;

  // Lite has no parental controls, so unlike the full version uninstall needs no password.
  if not UninstallSilent then
  begin
    if MsgBox(GetUpdateVsUninstallText(), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  end;

  Result := RunUninstallWizard();
end;
