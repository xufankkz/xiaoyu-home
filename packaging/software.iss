#define AppVersion "0.2.0-beta.1"
[Setup]
AppId={{5417E42E-296B-4DDA-9FB2-9E2A93BC012A}
AppName=小鱼之家·测试发行版
AppVersion={#AppVersion}
AppPublisher=小鱼之家项目
DefaultDirName={localappdata}\Programs\XiaoyuHomeBeta
DefaultGroupName=小鱼之家·测试发行版
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\artifacts
OutputBaseFilename=XiaoyuHome-{#AppVersion}-Windows-x64-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\XiaoyuHome.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes
LicenseFile=..\LICENSE
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
[Files]
Source: "..\..\dist-longpath\XiaoyuHome\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"
Source: "..\LICENSE"; DestDir: "{app}"
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"
Source: "..\ADDITIONAL_TERMS.md"; DestDir: "{app}"
Source: "..\NOTICE.md"; DestDir: "{app}"
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs
[Icons]
Name: "{autodesktop}\小鱼之家·测试发行版"; Filename: "{app}\XiaoyuHome.exe"; WorkingDir: "{app}"
Name: "{group}\小鱼之家·测试发行版"; Filename: "{app}\XiaoyuHome.exe"; WorkingDir: "{app}"
Name: "{group}\卸载测试发行版"; Filename: "{uninstallexe}"
[InstallDelete]
Type: files; Name: "{app}\_internal\source\xiaoyu-web\local_thought_worker.py"
[Run]
Filename: "{app}\XiaoyuHome.exe"; Description: "打开小鱼之家·测试发行版"; Flags: nowait postinstall skipifsilent
