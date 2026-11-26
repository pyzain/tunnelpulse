; TunnelPulse installer, compiled with Inno Setup 6.6 or newer.
;
; build_tunnelpulse.ps1 runs this after PyInstaller and passes the version and
; the app folder, e.g.
;   ISCC.exe /DAppVersion=1.0.0 /DAppBuildDir=C:\...\dist\TunnelPulse installer\TunnelPulse.iss
;
; What it does on the user's PC:
;   * installs TunnelPulse to Program Files, with Start Menu (and optional
;     desktop) shortcuts and an entry in Settings > Apps
;   * if WireGuard isn't installed, downloads the official MSI from
;     download.wireguard.com, checks its Authenticode signature, and installs it
;   * on uninstall, closes the app and removes any TunnelPulse tunnel services,
;     so no traffic is left routed into a tunnel that nothing manages

#if Ver < EncodeVer(6, 6, 0)
  #error Inno Setup 6.6 or newer is required (it adds dark mode to the wizard).
#endif

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppBuildDir
  #define AppBuildDir "..\dist\TunnelPulse"
#endif

#define AppName      "TunnelPulse"
#define AppExe       "TunnelPulse.exe"
#define AppPublisher "Zain Raza"
#define AppURL       "https://github.com/pyzain/tunnelpulse"

[Setup]
; AppId identifies the app across versions, so a new installer upgrades the old one. Never change it.
AppId={{EFB08205-1FB4-478F-A190-D84B95FB3C8D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
; Installing the WireGuard driver and tunnel services needs admin rights anyway.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Follows the Windows light/dark setting, the same way the app does.
WizardStyle=modern dynamic
WizardImageFile=..\assets\wizard.png
WizardSmallImageFile=..\assets\wizard-small.png
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=TunnelPulse-Setup
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Tasks]
Name: "wireguard"; Description: "Download and install WireGuard from wireguard.com (needed to connect)"; GroupDescription: "Required component:"; Check: not IsWireGuardInstalled
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; PyInstaller keeps its libraries in _internal; clear it so an upgrade doesn't leave old files behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#AppBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; runasoriginaluser: the app runs without admin rights and only asks for them when connecting.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
Type: dirifempty; Name: "{app}"

[Code]
const
  WgListUrl     = 'https://download.wireguard.com/windows-client/latest.sig';
  WgFilesUrl    = 'https://download.wireguard.com/windows-client/';
  WgInstallPage = 'https://www.wireguard.com/install/';

function WireGuardExe: String;
begin
  Result := ExpandConstant('{commonpf64}\WireGuard\wireguard.exe');
end;

function IsWireGuardInstalled: Boolean;
begin
  Result := FileExists(WireGuardExe);
end;

function PowerShellExe: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

{ Quote a value for use inside a single-quoted PowerShell string. }
function PsQuote(const Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '''', '''''', True);
  Result := '''' + Result + '''';
end;

{ Windows PowerShell started from PowerShell 7 inherits its module path and then
  can't load its own built-in commands (Get-AuthenticodeSignature, Get-Service).
  Putting its own module folder first avoids that. }
function RunPowerShell(const Script: String; var ExitCode: Integer): Boolean;
begin
  Result := Exec(PowerShellExe,
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
    '$env:PSModulePath = (Join-Path $PSHOME ''Modules'') + '';'' + $env:PSModulePath; ' + Script + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
end;

procedure SetStatus(const Msg: String);
begin
  WizardForm.StatusLabel.Caption := Msg;
  Log(Msg);
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax > 0 then
    WizardForm.StatusLabel.Caption :=
      'Downloading WireGuard... ' + IntToStr((Progress * 100) div ProgressMax) + '%';
  Result := True;
end;

{ latest.sig is WireGuard's signed list of current downloads. After two header
  lines, each line is "<hash>  <file name>" (two spaces). We only need the name
  of the MSI for this machine's architecture; the MSI's own signature is
  checked separately before it runs. }
function FindMsiName(const ListFile, Arch: String; var Name: String): Boolean;
var
  Lines: TArrayOfString;
  I, P: Integer;
  Candidate, Prefix: String;
begin
  Result := False;
  Prefix := 'wireguard-' + Arch + '-';
  if not LoadStringsFromFile(ListFile, Lines) then
    Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    P := Pos('  ', Lines[I]);
    if P > 0 then
    begin
      Candidate := Trim(Copy(Lines[I], P + 2, Length(Lines[I])));
      if (Pos(Prefix, Candidate) = 1) and
         (CompareText(Copy(Candidate, Length(Candidate) - 3, 4), '.msi') = 0) and
         (Pos('/', Candidate) = 0) and (Pos('\', Candidate) = 0) and (Pos(':', Candidate) = 0) then
      begin
        Name := Candidate;
        Result := True;
        Exit;
      end;
    end;
  end;
end;

{ Valid Authenticode signature from WireGuard LLC, checked by Windows itself. }
function IsSignedByWireGuard(const FileName: String): Boolean;
var
  ExitCode: Integer;
  ReportFile: String;
  Report: AnsiString;
begin
  ReportFile := ExpandConstant('{tmp}\wireguard-signature.txt');
  Result := RunPowerShell(
    '$ok = $false; ' +
    'try { $s = Get-AuthenticodeSignature -LiteralPath ' + PsQuote(FileName) + ' -ErrorAction Stop; ' +
    '$r = $s.Status.ToString() + '' | '' + $s.SignerCertificate.Subject; ' +
    '$ok = ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject -like ''*WireGuard LLC*'') } ' +
    'catch { $r = ''error: '' + $_.Exception.Message }; ' +
    'Set-Content -Encoding ASCII -LiteralPath ' + PsQuote(ReportFile) + ' -Value $r; ' +
    'if ($ok) { exit 0 } else { exit 1 }',
    ExitCode) and (ExitCode = 0);
  if LoadStringFromFile(ReportFile, Report) then
    Log('WireGuard MSI signature: ' + Trim(String(Report)))
  else
    Log('WireGuard MSI signature: no report (PowerShell exit code ' + IntToStr(ExitCode) + ')');
end;

function InstallWireGuard(var Reason: String): Boolean;
var
  Arch, MsiName, MsiPath, MsiLog: String;
  ExitCode: Integer;
begin
  Result := False;
  if IsArm64 then Arch := 'arm64' else Arch := 'amd64';
  try
    SetStatus('Finding the latest WireGuard release...');
    DownloadTemporaryFile(WgListUrl, 'wireguard-latest.sig', '', @OnDownloadProgress);
    if not FindMsiName(ExpandConstant('{tmp}\wireguard-latest.sig'), Arch, MsiName) then
    begin
      Reason := 'the WireGuard download list did not contain a ' + Arch + ' installer';
      Exit;
    end;
    Log('WireGuard MSI: ' + MsiName);
    DownloadTemporaryFile(WgFilesUrl + MsiName, MsiName, '', @OnDownloadProgress);
  except
    Reason := 'download failed (' + GetExceptionMessage + ')';
    Exit;
  end;

  MsiPath := ExpandConstant('{tmp}\') + MsiName;
  SetStatus('Checking the WireGuard installer''s signature...');
  if not IsSignedByWireGuard(MsiPath) then
  begin
    Reason := 'the downloaded file is not signed by WireGuard LLC, so it was not run';
    Exit;
  end;

  SetStatus('Installing WireGuard...');
  MsiLog := ExpandConstant('{tmp}\wireguard-msi.log');
  if not Exec(ExpandConstant('{sys}\msiexec.exe'),
              '/i "' + MsiPath + '" /qn /norestart DO_NOT_LAUNCH=1 /l*v "' + MsiLog + '"',
              '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
  begin
    Reason := 'msiexec could not be started';
    Exit;
  end;
  Log('WireGuard msiexec exit code: ' + IntToStr(ExitCode));
  { 3010 and 1641 mean success, with a restart requested. }
  if not ((ExitCode = 0) or (ExitCode = 3010) or (ExitCode = 1641)) then
  begin
    Reason := 'the WireGuard installer failed (code ' + IntToStr(ExitCode) + ')';
    Exit;
  end;
  if not IsWireGuardInstalled then
  begin
    Reason := 'WireGuard reported success but wireguard.exe was not found';
    Exit;
  end;
  Log('WireGuard installed successfully.');
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Reason: String;
  ErrorCode: Integer;
begin
  if (CurStep <> ssPostInstall) or not WizardIsTaskSelected('wireguard') then
    Exit;
  if InstallWireGuard(Reason) then
    Exit;
  Log('WireGuard was not installed: ' + Reason);
  if WizardSilent then
    Exit;
  if MsgBox('TunnelPulse is installed, but WireGuard could not be set up automatically:' + #13#10 +
            Reason + '.' + #13#10#13#10 +
            'TunnelPulse needs WireGuard to connect. Open the official WireGuard download page now?',
            mbError, MB_YESNO) = IDYES then
    ShellExecAsOriginalUser('open', WgInstallPage, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

{ Removes every WireGuard tunnel service TunnelPulse created
  (named WireGuardTunnel$TunnelPulse-<profile id>). }
procedure RemoveTunnelServices;
var
  ExitCode: Integer;
begin
  RunPowerShell(
    '$wg = ' + PsQuote(WireGuardExe) + '; ' +
    'Get-Service -Name ''WireGuardTunnel$TunnelPulse-*'' -ErrorAction SilentlyContinue | ForEach-Object { ' +
    '  $tunnel = $_.Name.Substring(16); ' +
    '  if (Test-Path -LiteralPath $wg) { Start-Process -FilePath $wg -ArgumentList ''/uninstalltunnelservice'', $tunnel -Wait -WindowStyle Hidden } ' +
    '  else { Stop-Service -Name $_.Name -Force -ErrorAction SilentlyContinue; sc.exe delete $_.Name | Out-Null } ' +
    '}',
    ExitCode);
  Log('Tunnel service cleanup finished (exit code ' + IntToStr(ExitCode) + ').');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExitCode: Integer;
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
    RemoveTunnelServices;
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    { Profiles hold the private keys from imported .conf files. Keep them unless
      the user says otherwise; a silent uninstall always keeps them. }
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also delete your saved VPN profiles and logs?' + #13#10#13#10 +
                'They include the private keys from the .conf files you imported. ' +
                'Choose No to keep them for a later reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
