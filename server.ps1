$ErrorActionPreference = "Stop"
$port = 8765
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:$port/")

try { $listener.Start() }
catch {
  Write-Host "Port $port jest zajety. Zamknij poprzednie okno Moj Pulpit i sprobuj ponownie." -ForegroundColor Red
  Read-Host "Nacisnij Enter, aby zamknac"
  exit 1
}

Write-Host "Moj Pulpit dziala lokalnie." -ForegroundColor Green
Write-Host "Adres: http://127.0.0.1:$port/"
Write-Host "Zostaw to okno otwarte podczas pracy."
Start-Process "http://127.0.0.1:$port/"

$types = @{ ".html"="text/html; charset=utf-8"; ".js"="application/javascript; charset=utf-8"; ".css"="text/css; charset=utf-8"; ".png"="image/png"; ".jpg"="image/jpeg"; ".jpeg"="image/jpeg"; ".svg"="image/svg+xml"; ".json"="application/json" }
try {
  while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $relative = [Uri]::UnescapeDataString($ctx.Request.Url.AbsolutePath.TrimStart('/'))
    if ([string]::IsNullOrWhiteSpace($relative)) { $relative = "index.html" }
    $path = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($root)) -or -not (Test-Path $path -PathType Leaf)) {
      $ctx.Response.StatusCode = 404; $ctx.Response.Close(); continue
    }
    $bytes = [IO.File]::ReadAllBytes($path)
    $ext = [IO.Path]::GetExtension($path).ToLowerInvariant()
    if ($types.ContainsKey($ext)) { $ctx.Response.ContentType = $types[$ext] }
    $ctx.Response.ContentLength64 = $bytes.Length
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.OutputStream.Close()
  }
} finally { $listener.Stop() }

