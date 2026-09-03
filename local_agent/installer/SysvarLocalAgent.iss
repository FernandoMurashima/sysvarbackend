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

function ExecHidden(FileName: String; Params: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function ExecAgentWithConfig(Command: String): Boolean;
begin
  Result := ExecHidden(
    ExpandConstant('{cmd}'),
    '/C set "SYSVAR_AGENT_CONFIG=' + AgentConfig() + '" && "' + AgentExe() + '" ' + Command
  );
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
    if FileExists(AgentExe()) then begin
      ExecAgentWithConfig('stop --wait 30');
    end else begin
      ExecHidden(ExpandConstant('{sys}\sc.exe'), 'stop ' + ServiceName());
    end;
  end;
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
  Result := ExecAgentWithConfig('config-ok');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not InstallOrUpdateService() then begin
      RaiseException('Falha ao registrar ou atualizar o serviço Sysvar Local Agent.');
    end;
    if ConfigAllowsStart() and (WasServiceRunning or not HadService) then begin
      ExecAgentWithConfig('start');
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    if FileExists(AgentExe()) then begin
      ExecAgentWithConfig('stop --wait 30');
      ExecAgentWithConfig('remove');
    end else begin
      ExecHidden(ExpandConstant('{sys}\sc.exe'), 'stop ' + ServiceName());
      ExecHidden(ExpandConstant('{sys}\sc.exe'), 'delete ' + ServiceName());
    end;
  end;
end;
