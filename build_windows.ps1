param([string]$Python = 'python', [string]$PythonRoot = '', [string]$OpenCVSite = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildArguments = @('build_portable.py')
if ($PythonRoot) { $buildArguments += @('--python-root', $PythonRoot) }
if ($OpenCVSite) { $buildArguments += @('--opencv-site', $OpenCVSite) }
& $Python @buildArguments
if ($LASTEXITCODE -ne 0) { throw 'Runtime build failed.' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/out:$PSScriptRoot\FilmBorderCropper.exe" "$PSScriptRoot\Launcher.cs"
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }
