# Builds plugin/dist/OrbisFoxBridge.jar without Gradle.
#   .\build.ps1 -ServerJar "C:\Hytale\Server\HytaleServer.jar"
# Needs a JDK 25 (javac and jar on PATH, or JAVA_HOME set).
param([Parameter(Mandatory = $true)][string]$ServerJar)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bin = if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME "bin" } else { "" }
$javac = if ($bin) { Join-Path $bin "javac.exe" } else { "javac" }
$jar = if ($bin) { Join-Path $bin "jar.exe" } else { "jar" }
$out = Join-Path $here "build\classes"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force $out | Out-Null
$sources = Get-ChildItem -Recurse (Join-Path $here "src\main\java") -Filter *.java | ForEach-Object { $_.FullName }
& $javac --release 25 -encoding UTF-8 -cp $ServerJar -d $out $sources
if ($LASTEXITCODE -ne 0) { throw "javac failed" }
Copy-Item (Join-Path $here "src\main\resources\*") $out -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $here "dist") | Out-Null
& $jar --create --file (Join-Path $here "dist\OrbisFoxBridge.jar") -C $out .
if ($LASTEXITCODE -ne 0) { throw "jar failed" }
Write-Host "Built plugin\dist\OrbisFoxBridge.jar"
