param([string]$JdkPath = $env:JAVA_HOME, [string]$SdkPath = $env:ANDROID_HOME, [string]$GradlePath = '')
$ErrorActionPreference = 'Stop'
if (-not $JdkPath -or -not $SdkPath) { throw 'Provide -JdkPath (JDK 17) and -SdkPath (Android SDK 35).' }
$env:JAVA_HOME = (Resolve-Path -LiteralPath $JdkPath).Path
$env:ANDROID_HOME = (Resolve-Path -LiteralPath $SdkPath).Path
Push-Location -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'android')
try {
    $taskGradle = if ($GradlePath) { (Resolve-Path -LiteralPath $GradlePath).Path } else { '.\gradlew.bat' }
    & $taskGradle :app:assembleDebug :app:assembleDebugAndroidTest :app:testDebugUnitTest :app:lintDebug --console=plain
    if ($LASTEXITCODE -ne 0) { throw 'Android build or checks failed.' }
}
finally { Pop-Location }
