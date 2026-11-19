; Installeur de l'agent BL-RMO (Inno Setup 6.3 ou plus)
; Construit par GitHub Actions (.github/workflows/agent.yml) :
;   ISCC /DAppVersion=1.0.0 agent\setup-agent-bl-rmo.iss
; Installation silencieuse possible :
;   setup-agent-bl-rmo-1.0.0.exe /VERYSILENT /URL=https://192.168.1.30 /TOKEN=...

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F3B2C1E-8D4A-4E59-9C2B-B1A7E0D5F3C8}
AppName=Agent BL-RMO
AppVersion={#AppVersion}
AppVerName=Agent BL-RMO {#AppVersion}
AppPublisher=BL-RMO
AppPublisherURL=https://github.com/LyonelB/BL-RMO
AppSupportURL=https://github.com/LyonelB/BL-RMO
DefaultDirName={commonpf64}\BL-RMO Agent
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=Output
OutputBaseFilename=setup-agent-bl-rmo-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Agent BL-RMO
UninstallDisplayIcon={app}\agent-bl-rmo.exe
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Agent BL-RMO
VersionInfoDescription=Installeur de l'agent BL-RMO

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.ServerTitle=Connexion à BL-RMO
fr.ServerDesc=Adresse de BL-RMO et jeton de ce PC
fr.ServerInfo=Le jeton de ce PC est affiché dans BL-RMO : Parc, onglet Appareils, fiche du PC (méthode « Agent »). Bouton « Copier », puis coller ici.
fr.UrlLabel=Adresse de BL-RMO :
fr.TokenLabel=Jeton de ce PC :
fr.UrlBad=Indiquez l'adresse de BL-RMO, par exemple https://192.168.1.30
fr.TokenBad=Collez le jeton de ce PC, copié depuis BL-RMO.
fr.Configuring=Réglages de Windows (tâche planifiée, pare-feu)...
fr.Testing=Premier envoi à BL-RMO...
fr.TestOk=L'agent est installé et BL-RMO a bien reçu le premier relevé de ce PC.%n%nIl démarre avec le PC, même sans session ouverte, et envoie ses relevés toutes les minutes.
fr.TestUnreachable=L'agent est installé, mais BL-RMO ne répond pas à l'adresse %1.%n%nL'agent réessaiera toutes les minutes. Si l'adresse est fausse, relancez l'installation pour la corriger.
fr.TestToken=L'agent est installé, mais BL-RMO a refusé le jeton.%n%nVérifiez que le PC est bien enregistré dans BL-RMO avec la méthode « Agent », puis relancez l'installation et collez son jeton.
fr.TestOther=L'agent est installé, mais le premier envoi a échoué (code %1).%n%nJournal de l'agent : %2
fr.TasksFailed=La tâche planifiée « BL-RMO Agent » n'a pas pu être créée (code %1). L'agent ne démarrera pas tout seul.
en.ServerTitle=Connection to BL-RMO
en.ServerDesc=BL-RMO address and token of this PC
en.ServerInfo=The token of this PC is shown in BL-RMO: Assets, Devices tab, this PC's entry ("Agent" method). Click "Copy", then paste it here.
en.UrlLabel=BL-RMO address:
en.TokenLabel=Token of this PC:
en.UrlBad=Enter the BL-RMO address, for example https://192.168.1.30
en.TokenBad=Paste the token of this PC, copied from BL-RMO.
en.Configuring=Windows settings (scheduled task, firewall)...
en.Testing=First report to BL-RMO...
en.TestOk=The agent is installed and BL-RMO received the first report from this PC.%n%nIt starts with the PC, even with no one logged on, and sends its reports every minute.
en.TestUnreachable=The agent is installed, but BL-RMO does not answer at %1.%n%nThe agent will retry every minute. If the address is wrong, run the installation again to fix it.
en.TestToken=The agent is installed, but BL-RMO refused the token.%n%nCheck that the PC is registered in BL-RMO with the "Agent" method, then run the installation again and paste its token.
en.TestOther=The agent is installed, but the first report failed (code %1).%n%nAgent log: %2
en.TasksFailed=The scheduled task "BL-RMO Agent" could not be created (code %1). The agent will not start by itself.

[Files]
Source: "dist\agent-bl-rmo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "install-tasks.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\BL-RMO\agent"

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install-tasks.ps1"" -Action uninstall -AppDir ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveAgentTask"

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\BL-RMO\agent"
Type: dirifempty; Name: "{commonappdata}\BL-RMO"

[Code]
var
  ServerPage: TInputQueryWizardPage;
  ResultText: String;

function ConfigFile(): String;
begin
  Result := ExpandConstant('{commonappdata}\BL-RMO\agent\agent.json');
end;

{ Valeur d'une clé texte d'un JSON simple : "cle": "valeur" }
function JsonValue(const S, Key: String): String;
var
  P: Integer;
  Rest: String;
begin
  Result := '';
  P := Pos('"' + Key + '"', S);
  if P = 0 then Exit;
  Rest := Copy(S, P + Length(Key) + 2, Length(S));
  P := Pos('"', Rest);
  if P = 0 then Exit;
  Rest := Copy(Rest, P + 1, Length(Rest));
  P := Pos('"', Rest);
  if P = 0 then Exit;
  Result := Copy(Rest, 1, P - 1);
end;

function JsonEscape(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure InitializeWizard();
var
  S: AnsiString;
  Url, Token: String;
begin
  ServerPage := CreateInputQueryPage(wpWelcome, CustomMessage('ServerTitle'), CustomMessage('ServerDesc'),
    CustomMessage('ServerInfo'));
  ServerPage.Add(CustomMessage('UrlLabel'), False);
  ServerPage.Add(CustomMessage('TokenLabel'), True);
  Url := 'https://192.168.1.30';
  Token := '';
  { Réinstallation ou mise à jour : on reprend les réglages en place }
  if LoadStringFromFile(ConfigFile(), S) then
  begin
    if JsonValue(String(S), 'url') <> '' then Url := JsonValue(String(S), 'url');
    Token := JsonValue(String(S), 'token');
  end;
  ServerPage.Values[0] := ExpandConstant('{param:URL|' + Url + '}');
  ServerPage.Values[1] := ExpandConstant('{param:TOKEN|' + Token + '}');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Url: String;
begin
  Result := True;
  if CurPageID = ServerPage.ID then
  begin
    Url := Trim(ServerPage.Values[0]);
    while (Length(Url) > 0) and (Url[Length(Url)] = '/') do
      Url := Copy(Url, 1, Length(Url) - 1);
    ServerPage.Values[0] := Url;
    ServerPage.Values[1] := Trim(ServerPage.Values[1]);
    if (Pos('http://', Lowercase(Url)) <> 1) and (Pos('https://', Lowercase(Url)) <> 1) then
    begin
      MsgBox(CustomMessage('UrlBad'), mbError, MB_OK);
      Result := False;
    end
    else if Length(ServerPage.Values[1]) < 16 then
    begin
      MsgBox(CustomMessage('TokenBad'), mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Mise à jour : l'agent en marche tient ses fichiers ouverts }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "BL-RMO Agent"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM agent-bl-rmo.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Json: String;
begin
  if CurStep <> ssPostInstall then Exit;
  ForceDirectories(ExpandConstant('{commonappdata}\BL-RMO\agent'));
  Json := '{"url": "' + JsonEscape(ServerPage.Values[0]) + '", "token": "' + JsonEscape(ServerPage.Values[1]) + '"}';
  SaveStringToFile(ConfigFile(), Json, False);

  WizardForm.StatusLabel.Caption := CustomMessage('Configuring');
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\install-tasks.ps1') + '" -Action install -AppDir "' +
    ExpandConstant('{app}') + '"', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    SuppressibleMsgBox(FmtMessage(CustomMessage('TasksFailed'), [IntToStr(Code)]), mbError, MB_OK, IDOK);

  WizardForm.StatusLabel.Caption := CustomMessage('Testing');
  Exec(ExpandConstant('{app}\agent-bl-rmo.exe'), 'test', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
  case Code of
    0: ResultText := CustomMessage('TestOk');
    2: ResultText := FmtMessage(CustomMessage('TestUnreachable'), [ServerPage.Values[0]]);
    3: ResultText := CustomMessage('TestToken');
  else
    ResultText := FmtMessage(CustomMessage('TestOther'), [IntToStr(Code), ExpandConstant('{commonappdata}\BL-RMO\agent\agent.log')]);
  end;

  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Run /TN "BL-RMO Agent"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and (ResultText <> '') then
    WizardForm.FinishedLabel.Caption := ResultText;
end;
