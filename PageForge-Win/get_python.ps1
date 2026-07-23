# Downloads a relocatable standalone CPython 3.12 into .\python (run by build_local.bat)
$ErrorActionPreference = 'Stop'
$headers = @{ 'User-Agent' = 'pageforge' }
Write-Host 'Querying python-build-standalone latest release...'
$rel = Invoke-RestMethod 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest' -Headers $headers
$asset = $rel.assets |
    Where-Object { $_.name -match '^cpython-3\.12\.\d+\+.*x86_64-pc-windows-msvc.*install_only\.tar\.gz$' -and $_.name -notmatch 'stripped' } |
    Select-Object -First 1
if (-not $asset) { throw 'No standalone CPython 3.12 windows asset found in the latest release.' }
Write-Host ("Downloading " + $asset.name)
Invoke-WebRequest $asset.browser_download_url -OutFile 'py.tar.gz'
Write-Host 'Extracting...'
tar -xzf py.tar.gz
if (-not (Test-Path 'python\python.exe')) { throw 'Extract did not produce python\python.exe' }
Write-Host 'Python ready.'
