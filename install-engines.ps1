# Скачивает движки нейросетей в bin/. Нужен один раз на машину.
#
# Модели (веса) приложение качает само при первом запуске, а вот сами
# ncnn-движки в репозиторий не попадают: распакованные они занимают
# около 100 МБ (70 из них — CPU-движок). Без них апскейл не запустится,
# поэтому запускай этот скрипт до run.bat.
#
#   powershell -ExecutionPolicy Bypass -File install-engines.ps1
#
# Файл сохранён в UTF-8 С BOM: PowerShell 5.1 читает .ps1 без BOM
# однобайтовой кодировкой и ломает русский текст. Не пересохраняй без BOM.
#
# Раскладка повторяет то, что ждёт server/config.py:
#   bin\realesrgan\realesrgan-ncnn-vulkan.exe
#   bin\realsr\realsr-ncnn-vulkan-20220728-windows\realsr-ncnn-vulkan.exe
#   bin\srmd\srmd-ncnn-vulkan-20220728-windows\srmd-ncnn-vulkan.exe

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bin = Join-Path $Root "bin"
$Tmp = Join-Path $Root "tmp\engines"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Ссылки сверены с releases через GitHub API, а не написаны по шаблону.
$Engines = @(
    @{
        Key     = "gpu"
        Title   = "Real-ESRGAN (Vulkan)"
        Url     = "https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases/download/v0.2.0/realesrgan-ncnn-vulkan-v0.2.0-windows.zip"
        ExeName = "realesrgan-ncnn-vulkan.exe"
        # Куда раскладывать: содержимое папки прямо сюда
        Target  = "realesrgan"
    },
    @{
        Key     = "cpu"
        Title   = "RealSR (процессор)"
        Url     = "https://github.com/nihui/realsr-ncnn-vulkan/releases/download/20220728/realsr-ncnn-vulkan-20220728-windows.zip"
        ExeName = "realsr-ncnn-vulkan.exe"
        # Имя папки зашито в config.py, поэтому переименовываем как надо
        Target  = "realsr\realsr-ncnn-vulkan-20220728-windows"
    },
    @{
        Key     = "srmd"
        Title   = "SRMD (Vulkan, быстрый)"
        Url     = "https://github.com/nihui/srmd-ncnn-vulkan/releases/download/20220728/srmd-ncnn-vulkan-20220728-windows.zip"
        ExeName = "srmd-ncnn-vulkan.exe"
        Target  = "srmd\srmd-ncnn-vulkan-20220728-windows"
    }
)

function Get-NativePath([string] $Path) {
    return $Path.Replace("/", "\")
}

function Install-Engine($Engine) {
    $targetDir = Join-Path $Bin (Get-NativePath $Engine.Target)
    $exePath = Join-Path $targetDir $Engine.ExeName
    if (Test-Path -LiteralPath $exePath) {
        Write-Host ("  [готово] {0} — уже скачан" -f $Engine.Title) -ForegroundColor DarkGray
        return $true
    }

    $zip = Join-Path $Tmp ("{0}.zip" -f $Engine.Key)
    $work = Join-Path $Tmp $Engine.Key
    Write-Host ("  [качаю] {0}" -f $Engine.Title) -ForegroundColor Cyan
    Write-Host ("           {0}" -f $Engine.Url) -ForegroundColor DarkGray

    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
    New-Item -ItemType Directory -Path $work -Force | Out-Null

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $client = New-Object Net.WebClient
    $client.Headers.Add("User-Agent", "PedroDevTools-installer")
    try {
        $client.DownloadFile($Engine.Url, $zip)
    } catch {
        Write-Host ("  [ошибка] не скачался: {0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host "            Проверь интернет или скачай zip вручную и распакуй в bin\" -ForegroundColor Yellow
        return $false
    } finally {
        $client.Dispose()
    }

    Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue

    # Внутри архива лежит папка с именем как у zip-файла; берём её по .exe,
    # чтобы не зависеть от точного имени.
    $exe = Get-ChildItem -LiteralPath $work -Recurse -File -Filter $Engine.ExeName |
           Select-Object -First 1
    if (-not $exe) {
        Write-Host ("  [ошибка] в архиве нет {0}" -f $Engine.ExeName) -ForegroundColor Red
        return $false
    }
    $source = $exe.Directory.FullName

    New-Item -ItemType Directory -Path (Split-Path -Parent $targetDir) -Force | Out-Null
    if (Test-Path -LiteralPath $targetDir) {
        # Папка уже есть (например, осталась от прошлой установки) — дописываем
        Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $targetDir -Recurse -Force
        }
    } else {
        Move-Item -LiteralPath $source -Destination $targetDir
    }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue

    if (Test-Path -LiteralPath $exePath) {
        Write-Host ("  [ок]     {0}" -f $Engine.Title) -ForegroundColor Green
        return $true
    }
    Write-Host ("  [ошибка] {0} не появился в {1}" -f $Engine.ExeName, $targetDir) -ForegroundColor Red
    return $false
}

Write-Host ""
Write-Host "PedroDevTools — установка движков" -ForegroundColor White
Write-Host "=================================" -ForegroundColor DarkGray

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    Write-Host "  [готово] ffmpeg найден" -ForegroundColor DarkGray
} else {
    Write-Host "  [внимание] ffmpeg не найден в PATH. Без него не работает ничего," -ForegroundColor Yellow
    Write-Host "              кроме метаданных: https://ffmpeg.org/download.html" -ForegroundColor Yellow
}

New-Item -ItemType Directory -Path $Bin -Force | Out-Null
New-Item -ItemType Directory -Path $Tmp -Force | Out-Null

$ok = $true
foreach ($engine in $Engines) {
    if (-not (Install-Engine $engine)) { $ok = $false }
}

Write-Host ""
if ($ok) {
    Write-Host "Все движки на месте. Запускай run.bat." -ForegroundColor Green
    Write-Host "Модели скачаются сами при первом апскейле." -ForegroundColor DarkGray
} else {
    Write-Host "Что-то не скачалось. Панель покажет это в строке состояния слева." -ForegroundColor Yellow
}
Write-Host ""
if (Test-Path -LiteralPath $Tmp) { Remove-Item -LiteralPath $Tmp -Recurse -Force -ErrorAction SilentlyContinue }
