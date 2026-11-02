#define AppVersion "0.2.0-beta.1"
[Setup]
AppId={{36AA9C1C-FA00-4D31-BDE5-41C4647A19D7}
AppName=小鱼之家·可选组件
AppVersion={#AppVersion}
AppPublisher=小鱼之家项目
DefaultDirName={localappdata}\XiaoyuHome\components
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\artifacts
OutputBaseFilename=XiaoyuHome-{#AppVersion}-Components-Windows-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
LicenseFile=..\THIRD-PARTY-NOTICES.md
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
[Types]
Name: "full"; Description: "全部可选组件"
Name: "custom"; Description: "自定义组件"; Flags: iscustom
[Components]
Name: "dsh"; Description: "独立 DSH 运行环境（QQ 会话核心）"; Types: full
Name: "translation"; Description: "离线英译中运行时与 OPUS 模型"; Types: full
[Files]
Source: "..\..\components\dsh\*"; DestDir: "{app}\dsh"; Components: dsh; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\components\translation\*"; DestDir: "{app}\translation"; Components: translation; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"

Source: "..\third_party\*"; DestDir: "{app}\third_party"; Flags: ignoreversion recursesubdirs createallsubdirs
