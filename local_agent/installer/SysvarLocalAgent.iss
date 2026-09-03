#define MyAppName "Sysvar Local Agent"
#define MyAppPublisher "Sysvar"
#define MyAppVersion "0.2.0"
#define MyAppExeName "SysvarLocalAgent.exe"

[Setup]
AppId={{F9E9B7A5-7B45-4C1C-8F2F-6E8310E0200A}}
AppName={#MyAppName}
AppPublisher={#MyAppPublisher}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Sysvar\LocalAgent
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=SysvarLocalAgent-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\SysvarLocalAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config.example.json"; DestDir: "{commonappdata}\Sysvar\LocalAgent"; DestName: "config.json"; Flags: onlyifdoesntexist uninsneveruninstall

[Dirs]
Name: "{commonappdata}\Sysvar\LocalAgent"; Permissions: system-full admins-full; Flags: uninsneveruninstall
Name: "{commonappdata}\Sysvar\LocalAgent\data"; Permissions: system-full admins-full; Flags: uninsneveruninstall
Name: "{commonappdata}\Sysvar\LocalAgent\logs"; Permissions: system-full admins-full; Flags: uninsneveruninstall

[Code]
var
  HadService: Boolean;
  WasServiceRunning: Boolean;
  ActivationPage: TInputQueryWizardPage;
  ConfigValidBeforeInstall: Boolean;

function SetEnvironmentVariable(lpName: String; lpValue: String): Boolean;
external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function ServiceName(): String;
begin
  Result := 'SysvarLocalAgent';
end;

function AgentExe(): String;
begin
  Result := ExpandConstant('{app}\{#MyAppExeName}');
end;

function AgentConfig(): String;
begin
  Result := ExpandConstant('{commonappdata}\Sysvar\LocalAgent\config.json');
end;

function BoolText(Value: Boolean): String;
begin
  if Value then
    Result := 'true'
  else
    Result := 'false';
end;

function ExecHiddenCode(FileName: String; Params: String; var ResultCode: Integer): Boolean;
begin
  Result := Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function ExecHidden(FileName: String; Params: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := ExecHiddenCode(FileName, Params, ResultCode);
end;

function ExecAgentWithConfig(Command: String): Boolean;
begin
  Result := ExecHidden(
    ExpandConstant('{cmd}'),
    '/C set "SYSVAR_AGENT_CONFIG=' + AgentConfig() + '" && "' + AgentExe() + '" ' + Command
  );
end;

function ExecAgentWithSetupEnvironment(Command: String): Boolean;
begin
  SetEnvironmentVariable('SYSVAR_AGENT_CONFIG', AgentConfig());
  try
    Result := ExecHidden(AgentExe(), Command);
  finally
    SetEnvironmentVariable('SYSVAR_AGENT_CONFIG', '');
  end;
end;

function ExecAgentActivation(Code: String): Boolean;
begin
  SetEnvironmentVariable('SYSVAR_AGENT_CONFIG', AgentConfig());
  SetEnvironmentVariable('SYSVAR_AGENT_ACTIVATION_CODE', Code);
  try
    Result := ExecHidden(AgentExe(), 'activate');
  finally
    SetEnvironmentVariable('SYSVAR_AGENT_ACTIVATION_CODE', '');
    SetEnvironmentVariable('SYSVAR_AGENT_CONFIG', '');
  end;
end;

function ExecAgentAdmin(Command: String): Boolean;
begin
  Result := ExecHidden(AgentExe(), Command);
end;

function ServiceExists(): Boolean;
begin
  Result := ExecHidden(ExpandConstant('{sys}\sc.exe'), 'query ' + ServiceName());
end;

function ServiceRunning(): Boolean;
begin
  Result := ExecHidden(
    ExpandConstant('{cmd}'),
    '/C sc query ' + ServiceName() + ' | find "RUNNING"'
  );
end;

procedure StopServiceIfPossible();
begin
  if ServiceExists() then begin
    HadService := True;
    WasServiceRunning := ServiceRunning();
    Log('Sysvar Local Agent uninstall/install: service exists; stopping if possible.');
    if FileExists(AgentExe()) then begin
      Log('Sysvar Local Agent uninstall/install: AgentExe exists at ' + AgentExe());
      ExecAgentAdmin('--wait 30 stop');
    end else begin
      Log('Sysvar Local Agent uninstall/install: AgentExe missing; using sc.exe stop.');
      ExecHidden(ExpandConstant('{sys}\sc.exe'), 'stop ' + ServiceName());
    end;
  end;
end;

function WaitServiceMissing(): Boolean;
var
  I: Integer;
begin
  for I := 1 to 30 do begin
    if not ServiceExists() then begin
      Result := True;
      Exit;
    end;
    Sleep(1000);
  end;
  Result := not ServiceExists();
end;

function DeleteServiceWithSc(): Boolean;
begin
  Log('Sysvar Local Agent uninstall: using fallback sc.exe delete.');
  ExecHidden(ExpandConstant('{sys}\sc.exe'), 'delete ' + ServiceName());
  Result := WaitServiceMissing();
end;

procedure RemoveServiceRobust();
var
  RemoveOk: Boolean;
begin
  if not ServiceExists() then begin
    Log('Sysvar Local Agent uninstall: service already absent.');
    Exit;
  end;

  Log('Sysvar Local Agent uninstall: starting service removal.');
  Log('Sysvar Local Agent uninstall: AgentExe=' + AgentExe());
  Log('Sysvar Local Agent uninstall: FileExists(AgentExe)=' + BoolText(FileExists(AgentExe())));
  StopServiceIfPossible();
  RemoveOk := False;

  if FileExists(AgentExe()) then begin
    RemoveOk := ExecAgentAdmin('remove');
    Log('Sysvar Local Agent uninstall: SysvarLocalAgent.exe remove result=' + BoolText(RemoveOk));
  end else begin
    Log('Sysvar Local Agent uninstall: AgentExe missing before remove command.');
  end;

  if (not RemoveOk) or ServiceExists() then begin
    Log('SysvarLocalAgent.exe remove failed or service still exists; trying sc.exe delete.');
    if not DeleteServiceWithSc() then begin
      RaiseException('Falha ao remover o serviço Sysvar Local Agent. Feche services.msc/processos relacionados e tente novamente.');
    end;
  end;
  Log('Sysvar Local Agent uninstall: service removal confirmed.');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopServiceIfPossible();
  Result := '';
end;

function InstallOrUpdateService(): Boolean;
begin
  if ServiceExists() then
    Result := ExecAgentWithConfig('update')
  else
    Result := ExecAgentWithConfig('install');
end;

function ConfigAllowsStart(): Boolean;
begin
  Result := ExecAgentWithSetupEnvironment('config-ok');
end;

function ActivationCode(): String;
begin
  if ActivationPage = nil then
    Result := ''
  else
    Result := Trim(UpperCase(ActivationPage.Values[0]));
end;

function ExistingConfigAllowsStart(): Boolean;
begin
  Result := FileExists(AgentExe()) and FileExists(AgentConfig()) and ConfigAllowsStart();
end;

procedure InitializeWizard();
begin
  ConfigValidBeforeInstall := ExistingConfigAllowsStart();
  ActivationPage := CreateInputQueryPage(
    wpSelectDir,
    'Ativação do Sysvar Local Agent',
    'Informe o código de ativação gerado no Sysvar.',
    'O código é necessário em instalações novas ou para recuperar uma configuração inválida.'
  );
  ActivationPage.Add('Código de ativação:', False);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (ActivationPage <> nil) and (PageID = ActivationPage.ID) and ConfigValidBeforeInstall;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (ActivationPage <> nil) and (CurPageID = ActivationPage.ID) and (ActivationCode() = '') then begin
    MsgBox('Informe o código de ativação gerado no Sysvar.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not ConfigAllowsStart() then begin
      if (ActivationCode() = '') or (not ExecAgentActivation(ActivationCode())) then begin
        RaiseException('Não foi possível ativar o Sysvar Local Agent. Verifique o código de ativação e a conexão com o Sysvar.');
      end;
    end;
    if not ConfigAllowsStart() then begin
      RaiseException('Configuração do Sysvar Local Agent não ficou válida após a ativação.');
    end;
    if not InstallOrUpdateService() then begin
      RaiseException('Falha ao registrar ou atualizar o serviço Sysvar Local Agent.');
    end;
    if WasServiceRunning or not HadService then begin
      ExecAgentWithConfig('start');
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Log('Sysvar Local Agent uninstall: InitializeUninstall started.');
  RemoveServiceRobust();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    Log('Sysvar Local Agent uninstall: usUninstall verification started.');
    RemoveServiceRobust();
  end;
end;
