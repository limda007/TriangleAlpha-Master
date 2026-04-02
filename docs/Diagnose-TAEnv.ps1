<#
.SYNOPSIS
    TriangleAlpha 环境诊断一键脚本
.DESCRIPTION
    诊断目标机器对 UmiOCR、dm.dll、ONNX (DirectML) 的支持情况。
    需要以管理员权限运行。
.NOTES
    用法: powershell -ExecutionPolicy Bypass -File Diagnose-TAEnv.ps1
    可选参数: -BasePath "自定义安装路径"
#>

param(
    [string]$BasePath = "C:\Users\Administrator\Desktop\TriangleAlphaOOOOO"
)

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
$script:PassCount = 0
$script:FailCount = 0
$script:WarnCount = 0

function Write-Check {
    param([string]$Level, [string]$Message)
    switch ($Level) {
        "PASS" { Write-Host "  [PASS] $Message" -ForegroundColor Green; $script:PassCount++ }
        "FAIL" { Write-Host "  [FAIL] $Message" -ForegroundColor Red; $script:FailCount++ }
        "WARN" { Write-Host "  [WARN] $Message" -ForegroundColor Yellow; $script:WarnCount++ }
        "INFO" { Write-Host "  [INFO] $Message" -ForegroundColor Cyan }
        default { Write-Host "  $Message" }
    }
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor White
    Write-Host "  $Title" -ForegroundColor White
    Write-Host ("=" * 60) -ForegroundColor White
}

# ──────────────────────────────────────────────
# 系统信息
# ──────────────────────────────────────────────
Write-Section "系统信息"
Write-Check "INFO" "主机名: $env:COMPUTERNAME"
Write-Check "INFO" "用户: $env:USERNAME"
Write-Check "INFO" "系统: $([System.Environment]::OSVersion.VersionString)"
$arch = if ([System.Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' }
Write-Check "INFO" "架构: $arch"
Write-Check "INFO" "安装路径: $BasePath"

if (-not (Test-Path $BasePath)) {
    Write-Check "FAIL" "安装路径不存在！"
    Write-Host "`n请使用 -BasePath 参数指定正确的安装路径" -ForegroundColor Red
    exit 1
}

# ──────────────────────────────────────────────
# .NET Framework 检查
# ──────────────────────────────────────────────
Write-Section ".NET Framework"
try {
    $ndpKey = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" -ErrorAction Stop
    $release = $ndpKey.Release
    $ver = "unknown"
    if ($release -ge 533320) { $ver = "4.8.1+" }
    elseif ($release -ge 528040) { $ver = "4.8" }
    elseif ($release -ge 461808) { $ver = "4.7.2" }
    elseif ($release -ge 461308) { $ver = "4.7.1" }
    elseif ($release -ge 460798) { $ver = "4.7" }
    elseif ($release -ge 394802) { $ver = "4.6.2" }
    else { $ver = "4.6.1 or lower" }
    if ($release -ge 461808) {
        Write-Check "PASS" ".NET Framework $ver (release=$release)"
    } else {
        Write-Check "FAIL" ".NET Framework $ver — 需要 4.7.2+"
    }
} catch {
    Write-Check "FAIL" ".NET Framework 4 未安装"
}

# ──────────────────────────────────────────────
# VC++ 运行库检查
# ──────────────────────────────────────────────
Write-Section "Visual C++ 运行库"
$vcKeys = @(
    "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X64"
)
$vcFound = $false
foreach ($key in $vcKeys) {
    try {
        $vc = Get-ItemProperty $key -ErrorAction Stop
        if ($vc.Installed -eq 1) {
            Write-Check "PASS" "VC++ 2015-2022 x64 已安装 (v$($vc.Major).$($vc.Minor))"
            $vcFound = $true
            break
        }
    } catch {}
}
# 也检查 x86
try {
    $vcx86 = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X86" -ErrorAction Stop
    if ($vcx86.Installed -eq 1) {
        Write-Check "PASS" "VC++ 2015-2022 x86 已安装 (v$($vcx86.Major).$($vcx86.Minor))"
    }
} catch {
    Write-Check "WARN" "VC++ 2015-2022 x86 未检测到（dm.dll 可能需要）"
}
if (-not $vcFound) {
    Write-Check "FAIL" "VC++ 2015-2022 x64 未安装 — OCR/ONNX 可能无法运行"
}

# ──────────────────────────────────────────────
# GPU 信息
# ──────────────────────────────────────────────
Write-Section "GPU 信息"
try {
    $gpus = Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop
    foreach ($gpu in $gpus) {
        $vram = if ($gpu.AdapterRAM -gt 0) { "$([math]::Round($gpu.AdapterRAM / 1GB, 1)) GB" } else { "N/A" }
        Write-Check "INFO" "$($gpu.Name) | 驱动: $($gpu.DriverVersion) | VRAM: $vram"
        # 检查是否为虚拟 GPU
        if ($gpu.Name -match "QXL|VirtIO|SVGA|Microsoft Basic|Hyper-V|Standard VGA") {
            Write-Check "WARN" "检测到虚拟显卡 — DirectML GPU 加速不可用"
        }
    }
} catch {
    Write-Check "FAIL" "无法获取 GPU 信息: $($_.Exception.Message)"
}

# ──────────────────────────────────────────────
# 1. UmiOCR 诊断
# ──────────────────────────────────────────────
Write-Section "1. UmiOCR (OCR 文字识别)"

# 1a. 文件检查
$ocrExe = Join-Path $BasePath "OCR\OCR.exe"
if (Test-Path $ocrExe) {
    $size = [math]::Round((Get-Item $ocrExe).Length / 1MB, 1)
    Write-Check "PASS" "OCR.exe 存在 ($size MB)"
} else {
    Write-Check "FAIL" "OCR.exe 不存在: $ocrExe"
}

# 1b. 进程检查
$ocrProc = Get-Process -Name "OCR" -ErrorAction SilentlyContinue
if ($ocrProc) {
    Write-Check "PASS" "OCR 进程运行中 (PID=$($ocrProc.Id), 启动=$($ocrProc.StartTime))"
} else {
    Write-Check "WARN" "OCR 进程未运行"
}

# 1c. RapidOCR-json 检查
$rapidProcs = Get-Process -Name "RapidOCR-json" -ErrorAction SilentlyContinue
if ($rapidProcs) {
    $count = @($rapidProcs).Count
    if ($count -eq 1) {
        Write-Check "PASS" "RapidOCR-json 运行中 - 1 个实例"
    } else {
        Write-Check "WARN" "RapidOCR-json 有 $count 个实例 - 应为 1 个"
    }
} else {
    Write-Check "WARN" "RapidOCR-json 未运行"
}

# 1d. HTTP 接口检查
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:1224/api/ocr" -Method POST `
        -Body '{"base64":"","options":{}}' -ContentType "application/json" -TimeoutSec 5 -ErrorAction Stop
    Write-Check "PASS" "OCR HTTP 接口可达 (状态码=$($resp.StatusCode))"
} catch {
    $msg = $_.Exception.Message
    if ($msg -match "Unable to connect|无法连接") {
        if (-not $ocrProc) {
            Write-Check "WARN" "OCR HTTP 接口不可达（OCR 未运行，属正常状态）"
        } else {
            Write-Check "FAIL" "OCR HTTP 接口不可达（OCR 进程在运行但端口 1224 未监听）"
        }
    } else {
        Write-Check "WARN" "OCR HTTP 接口异常: $msg"
    }
}

# ──────────────────────────────────────────────
# 2. dm.dll (大漠插件) 诊断
# ──────────────────────────────────────────────
Write-Section "2. dm.dll (大漠键鼠驱动)"

# 2a. 文件检查
$dmDll = Join-Path $BasePath "dm\libs\dm.dll"
if (Test-Path $dmDll) {
    $size = [math]::Round((Get-Item $dmDll).Length / 1MB, 1)
    Write-Check "PASS" "dm.dll 存在 ($size MB)"
} else {
    # 兼容旧路径
    $dmDll2 = Join-Path $BasePath "键鼠\libs\dm.dll"
    if (Test-Path $dmDll2) {
        Write-Check "PASS" "dm.dll 存在 (旧路径: $dmDll2)"
    } else {
        Write-Check "FAIL" "dm.dll 不存在"
    }
}

# 2b. COM 注册检查
$clsid = $null
try {
    $clsid = (Get-ItemProperty "HKLM:\SOFTWARE\Classes\dm.dmsoft\CLSID" -ErrorAction Stop).'(default)'
} catch {}
if (-not $clsid) {
    try {
        $clsid = (Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Classes\dm.dmsoft\CLSID" -ErrorAction Stop).'(default)'
    } catch {}
}
if ($clsid) {
    Write-Check "PASS" "dm.dmsoft COM 已注册 (CLSID=$clsid)"
} else {
    Write-Check "FAIL" "dm.dmsoft COM 未注册 — 需要运行 regsvr32"
}

# 2c. DmHelper 文件检查
$dmExe = Join-Path $BasePath "dm\RegisterDmSoftConsoleApp.exe"
if (-not (Test-Path $dmExe)) {
    $dmExe = Join-Path $BasePath "键鼠\RegisterDmSoftConsoleApp.exe"
}
if (Test-Path $dmExe) {
    Write-Check "PASS" "DmHelper 存在: $dmExe"
} else {
    Write-Check "FAIL" "DmHelper (RegisterDmSoftConsoleApp.exe) 不存在"
}

# 2d. DmHelper 进程检查
$dmProc = Get-Process -Name "RegisterDmSoftConsoleApp" -ErrorAction SilentlyContinue
if ($dmProc) {
    Write-Check "PASS" "DmHelper 运行中 (PID=$($dmProc.Id), 启动=$($dmProc.StartTime))"
} else {
    Write-Check "WARN" "DmHelper 未运行"
}

# 2e. 命名管道检查
# 注意: DmHelper 管道为单连接模式，TestDemo 占用期间管道对外不可见
$pipeExists = Test-Path "\\.\pipe\MyWpfDmHelperPipe"
if ($pipeExists) {
    Write-Check "PASS" "命名管道 MyWpfDmHelperPipe 存在"
} else {
    if ($dmProc) {
        # DmHelper 在运行但管道不存在 — 可能是 TestDemo 正在占用（单连接模式）
        $testdemoProc = Get-Process -Name "TestDemo" -ErrorAction SilentlyContinue
        if ($testdemoProc) {
            Write-Check "WARN" "命名管道不可见（TestDemo 正在占用，属正常行为）"
        } else {
            Write-Check "FAIL" "DmHelper 在运行但管道不存在 — COM 初始化可能失败"
        }
    } else {
        Write-Check "WARN" "命名管道不存在（DmHelper 未运行）"
    }
}

# 2f. 管道连接测试
if ($pipeExists) {
    try {
        $pipe = New-Object System.IO.Pipes.NamedPipeClientStream(".", "MyWpfDmHelperPipe",
            [System.IO.Pipes.PipeDirection]::InOut, [System.IO.Pipes.PipeOptions]::Asynchronous)
        $pipe.Connect(5000)
        if ($pipe.IsConnected) {
            # 发送 ping 命令
            $payload = [System.Text.Encoding]::UTF8.GetBytes('{"Command":"ping","Parameters":[]}')
            $lenBytes = [BitConverter]::GetBytes($payload.Length)
            $pipe.Write($lenBytes, 0, 4)
            $pipe.Write($payload, 0, $payload.Length)
            $pipe.Flush()

            # 读回应
            $respLen = New-Object byte[] 4
            $read = $pipe.Read($respLen, 0, 4)
            if ($read -eq 4) {
                $len = [BitConverter]::ToInt32($respLen, 0)
                $respBuf = New-Object byte[] $len
                $totalRead = 0
                while ($totalRead -lt $len) {
                    $n = $pipe.Read($respBuf, $totalRead, $len - $totalRead)
                    if ($n -eq 0) { break }
                    $totalRead += $n
                }
                $resp = [System.Text.Encoding]::UTF8.GetString($respBuf, 0, $totalRead)
                Write-Check "PASS" "管道通信成功: $resp"
            } else {
                Write-Check "WARN" "管道已连接但无响应"
            }
        }
        $pipe.Close()
    } catch {
        Write-Check "FAIL" "管道连接/通信失败: $($_.Exception.Message)"
    }
}

# 2g. COM 对象测试（32-bit PowerShell）
try {
    $comScript = 'try { $dm = New-Object -ComObject dm.dmsoft; $ver = $dm.Ver(); [System.Runtime.InteropServices.Marshal]::ReleaseComObject($dm) | Out-Null; Write-Output "PASS:dm.dmsoft v$ver" } catch { Write-Output "FAIL:$($_.Exception.Message)" }'
    $comResult = & "$env:SystemRoot\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -Command $comScript 2>$null
    if ($comResult -and $comResult.StartsWith("PASS:")) {
        Write-Check "PASS" "COM 对象可用: $($comResult.Substring(5))"
    } elseif ($comResult -and $comResult.StartsWith("FAIL:")) {
        Write-Check "FAIL" "COM 对象创建失败: $($comResult.Substring(5))"
    } else {
        Write-Check "WARN" "COM 对象测试无结果"
    }
} catch {
    Write-Check "WARN" "无法测试 COM 对象（32-bit PS 不可用）"
}

# ──────────────────────────────────────────────
# 3. ONNX (DirectML GPU) 诊断
# ──────────────────────────────────────────────
Write-Section "3. ONNX Runtime (DirectML GPU)"

# 3a. onnxruntime.dll 搜索
$onnxDll = $null
$onnxPaths = @(
    (Join-Path $BasePath "onnxruntime.dll"),
    (Join-Path $BasePath "runtimes\win-x64\native\onnxruntime.dll")
)
foreach ($p in $onnxPaths) {
    if (Test-Path $p) {
        $size = [math]::Round((Get-Item $p).Length / 1MB, 1)
        Write-Check "PASS" "onnxruntime.dll 找到: $p ($size MB)"
        $onnxDll = $p
        break
    }
}
if (-not $onnxDll) {
    # onnxruntime.dll 通过 Costura 嵌入在 TestDemo 中，运行时才释放
    # 检查 diag.log 是否有成功加载记录
    $diagLog = "C:\ProgramData\.ta\logs\diag.log"
    $onnxLoaded = $false
    if (Test-Path $diagLog) {
        $onnxLogLine = Select-String -Path $diagLog -Pattern "onnxruntime\.dll loaded" -Encoding UTF8 -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($onnxLogLine) {
            Write-Check "PASS" "onnxruntime.dll (Costura 嵌入) 运行时加载成功: $($onnxLogLine.Line.Trim())"
            $onnxLoaded = $true
        }
    }
    if (-not $onnxLoaded) {
        Write-Check "FAIL" "onnxruntime.dll 磁盘未找到且 diag.log 无加载成功记录"
    }
}

# 3b. LoadLibrary 测试
if ($onnxDll) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class NativeLoader {
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern IntPtr LoadLibrary(string path);
    [DllImport("kernel32.dll")]
    public static extern bool FreeLibrary(IntPtr handle);
}
"@ -ErrorAction SilentlyContinue

    $h = [NativeLoader]::LoadLibrary($onnxDll)
    if ($h -ne [IntPtr]::Zero) {
        Write-Check "PASS" "onnxruntime.dll LoadLibrary 成功 (handle=0x$($h.ToString('X')))"
        [NativeLoader]::FreeLibrary($h) | Out-Null
    } else {
        $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
        Write-Check "FAIL" "onnxruntime.dll LoadLibrary 失败 (Win32 err=$err)"
        if ($err -eq 126) {
            Write-Check "INFO" "错误 126 = 依赖 DLL 缺失（检查 VC++ 运行库）"
        } elseif ($err -eq 193) {
            Write-Check "INFO" "错误 193 = 架构不匹配（需要 x64 版本）"
        }
    }
}

# 3c. OpenCvSharpExtern.dll 检查
$ocvPaths = @(
    (Join-Path $env:TEMP "TA_native\OpenCvSharpExtern.dll"),
    (Join-Path $env:TEMP "Costura")
)
$ocvFound = $false
foreach ($p in $ocvPaths) {
    if ($p -match "Costura$") {
        if (Test-Path $p) {
            $files = Get-ChildItem -Path $p -Recurse -Filter "opencvsharpextern.dll" -ErrorAction SilentlyContinue
            if ($files) {
                $size = [math]::Round($files[0].Length / 1MB, 1)
                Write-Check "PASS" "OpenCvSharpExtern.dll 找到 (Costura): $($files[0].FullName) ($size MB)"
                $ocvFound = $true
                break
            }
        }
    } else {
        if (Test-Path $p) {
            $size = [math]::Round((Get-Item $p).Length / 1MB, 1)
            Write-Check "PASS" "OpenCvSharpExtern.dll 找到: $p ($size MB)"
            $ocvFound = $true
            break
        }
    }
}
if (-not $ocvFound) {
    Write-Check "FAIL" "OpenCvSharpExtern.dll 未找到（图像识别不可用）"
}

# 3d. DirectML 检查
$dmlFound = $false
$dmlPaths = @(
    "$env:SystemRoot\System32\DirectML.dll",
    (Join-Path $BasePath "DirectML.dll")
)
foreach ($p in $dmlPaths) {
    if (Test-Path $p) {
        $ver = (Get-Item $p).VersionInfo.FileVersion
        Write-Check "PASS" "DirectML.dll 找到: $p (v$ver)"
        $dmlFound = $true
        break
    }
}
if (-not $dmlFound) {
    Write-Check "WARN" "DirectML.dll 未在常见位置找到 — GPU 加速可能不可用"
}

# 3e. diag.log ONNX 预热结果
$diagLog = "C:\ProgramData\.ta\logs\diag.log"
if (Test-Path $diagLog) {
    Write-Host ""
    Write-Host "  --- 最近 ONNX 预热日志 ---" -ForegroundColor DarkGray
    Select-String -Path $diagLog -Pattern "ONNX|GPU|DirectML|onnxruntime" -Encoding UTF8 -ErrorAction SilentlyContinue |
        Select-Object -Last 5 | ForEach-Object { Write-Host "  $($_.Line)" -ForegroundColor DarkGray }
}

# ──────────────────────────────────────────────
# 4. 运行日志检查
# ──────────────────────────────────────────────
Write-Section "4. 运行日志检查"
$logsDir = "C:\ProgramData\.ta\logs"

if (Test-Path $logsDir) {
    # 4a. exceptions.log 最近错误
    $excLog = Join-Path $logsDir "exceptions.log"
    if (Test-Path $excLog) {
        $excSize = [math]::Round((Get-Item $excLog).Length / 1KB, 1)
        Write-Check "INFO" "exceptions.log 大小: $excSize KB"

        $critErrors = Select-String -Path $excLog -Pattern "OpenCvSharp|TimeoutException|IOException.*pipe|DmClient" -Encoding UTF8 -ErrorAction SilentlyContinue
        $critCount = @($critErrors).Count
        if ($critCount -gt 0) {
            Write-Check "WARN" "发现 $critCount 条关键异常"
            $critErrors | Select-Object -Last 3 | ForEach-Object {
                Write-Host "    $($_.Line)" -ForegroundColor DarkYellow
            }
        } else {
            Write-Check "PASS" "无关键异常"
        }
    }

    # 4b. diag.log 启动状态
    if (Test-Path $diagLog) {
        $startupLines = Select-String -Path $diagLog -Pattern "STARTUP.*100.*启动完成" -Encoding UTF8 -ErrorAction SilentlyContinue
        $lastStartup = $startupLines | Select-Object -Last 1
        if ($lastStartup) {
            Write-Check "INFO" "最近启动: $($lastStartup.Line)"
        }
    }
} else {
    Write-Check "WARN" "日志目录不存在: $logsDir"
}

# ──────────────────────────────────────────────
# 5. 进程总览
# ──────────────────────────────────────────────
Write-Section "5. TriangleAlpha 进程总览"
$procNames = @("TestDemo", "RegisterDmSoftConsoleApp", "OCR", "RapidOCR-json", "Launcher", "steam")
foreach ($name in $procNames) {
    $procs = Get-Process -Name $name -ErrorAction SilentlyContinue
    if ($procs) {
        foreach ($p in $procs) {
            Write-Check "INFO" "$($p.ProcessName) — PID=$($p.Id), 内存=$([math]::Round($p.WorkingSet64/1MB))MB"
        }
    }
}

# ──────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 60) -ForegroundColor White
Write-Host "  诊断汇总" -ForegroundColor White
Write-Host ("=" * 60) -ForegroundColor White
Write-Host "  通过: $script:PassCount" -ForegroundColor Green
Write-Host "  警告: $script:WarnCount" -ForegroundColor Yellow
Write-Host "  失败: $script:FailCount" -ForegroundColor Red
Write-Host ""

if ($script:FailCount -eq 0) {
    Write-Host "  ✅ 环境检查全部通过！" -ForegroundColor Green
} elseif ($script:FailCount -le 2) {
    Write-Host "  ⚠️ 有少量问题，请根据上述 [FAIL] 项修复。" -ForegroundColor Yellow
} else {
    Write-Host "  ❌ 存在多个问题，请逐项修复后重新运行诊断。" -ForegroundColor Red
}
Write-Host ""
