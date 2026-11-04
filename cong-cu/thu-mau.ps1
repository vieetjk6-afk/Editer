# =============================================================================
#  THU MẪU — gom dữ liệu mẫu để xây tool Editer
#
#  Script này CHỈ ĐỌC và COPY, không sửa hay xoá file nào của bạn. Nó gom:
#    1. Project CapCut: chỉ các file cấu hình (.json...), KHÔNG copy video/nhạc.
#    2. File .xmp của Camera Raw từ các job cũ (thông số chỉnh màu, vài KB/file).
#    3. Preset Camera Raw bạn tự tạo (thư mục Settings của Camera Raw).
#    4. Thông tin máy: phiên bản CapCut, card đồ hoạ.
#  Rồi nén tất cả thành 1 file .zip ngoài Desktop.
#
#  Chạy: bấm đúp thu-mau.bat (cùng thư mục).
# =============================================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ngay   = Get-Date -Format "yyyyMMdd-HHmm"
$tam    = Join-Path $env:TEMP "editer-mau-$ngay"
$zip    = Join-Path ([Environment]::GetFolderPath("Desktop")) "editer-mau-$ngay.zip"
$duoiMedia = @(".mp4",".mov",".mkv",".avi",".mts",".m4v",".mp3",".wav",".aac",".m4a",
               ".jpg",".jpeg",".png",".webp",".heic",".gif",".nef",".arw",".cr3",".cr2",".dng")
New-Item -ItemType Directory -Force -Path $tam | Out-Null
$baoCao = New-Object System.Collections.Generic.List[string]

function Hoi([string]$cau) { return (Read-Host $cau).Trim().Trim('"') }

# ----------------------------------------------------------------- 1. CAPCUT
Write-Host "`n=== 1/3  PROJECT CAPCUT ===" -ForegroundColor Cyan
$goc = Join-Path $env:LOCALAPPDATA "CapCut\User Data\Projects\com.lveditor.draft"
if (-not (Test-Path $goc)) {
    Write-Host "Không thấy thư mục project CapCut mặc định."
    Write-Host "Mở CapCut > Cài đặt (Settings) > Dự án/Projects > 'Vị trí bản nháp' (Draft location), copy đường dẫn dán vào đây."
    $goc = Hoi "Đường dẫn thư mục bản nháp (Enter để bỏ qua)"
}
if ($goc -and (Test-Path $goc)) {
    $dsProject = @(Get-ChildItem $goc -Directory | Where-Object { $_.Name -notlike ".*" } |
                   Sort-Object LastWriteTime -Descending)
    for ($i = 0; $i -lt [Math]::Min($dsProject.Count, 30); $i++) {
        Write-Host ("  [{0,2}] {1}   ({2:dd/MM/yyyy})" -f ($i+1), $dsProject[$i].Name, $dsProject[$i].LastWriteTime)
    }
    Write-Host "Chọn 1-3 project: nên có 1 phim cưới đã dựng xong + 1 project thử nhỏ (vài clip, cắt theo nhạc, 1 dòng chữ)."
    $chon = Hoi "Nhập số, cách nhau bằng dấu phẩy (vd: 1,3)"
    foreach ($so in ($chon -split "[,; ]+" | Where-Object { $_ -match "^\d+$" })) {
        $idx = [int]$so - 1
        if ($idx -lt 0 -or $idx -ge $dsProject.Count) { continue }
        $p = $dsProject[$idx]
        $dich = Join-Path $tam "capcut\$($p.Name)"
        Get-ChildItem $p.FullName -Recurse -File | Where-Object {
            ($duoiMedia -notcontains $_.Extension.ToLower()) -and $_.Length -lt 20MB
        } | ForEach-Object {
            $tuongDoi = $_.FullName.Substring($p.FullName.Length).TrimStart('\')
            $f = Join-Path $dich $tuongDoi
            New-Item -ItemType Directory -Force -Path (Split-Path $f) | Out-Null
            Copy-Item $_.FullName $f
        }
        # Kiểm tra draft_content.json có bị mã hoá không (JSON thường bắt đầu bằng '{')
        $dc = Join-Path $p.FullName "draft_content.json"
        if (Test-Path $dc) {
            $noiDung = [System.IO.File]::ReadAllText($dc).TrimStart()
            $tt = if ($noiDung.Length -gt 0 -and $noiDung[0] -eq '{') { "JSON đọc được" } else { "CÓ VẺ ĐÃ MÃ HOÁ" }
            $baoCao.Add("capcut | $($p.Name) | draft_content.json: $tt")
            Write-Host "  -> $($p.Name): draft_content.json $tt" -ForegroundColor Yellow
        } else {
            $baoCao.Add("capcut | $($p.Name) | không có draft_content.json")
        }
    }
}
$appCapcut = Join-Path $env:LOCALAPPDATA "CapCut\Apps"
if (Test-Path $appCapcut) {
    $ver = (Get-ChildItem $appCapcut -Directory | Sort-Object Name -Descending | Select-Object -First 3).Name -join ", "
    $baoCao.Add("capcut | phiên bản cài: $ver")
}

# ----------------------------------------------------------------- 2. XMP
Write-Host "`n=== 2/3  FILE XMP CAMERA RAW ===" -ForegroundColor Cyan
Write-Host "Dán thư mục chứa các job đã chỉnh (có file .xmp cạnh ảnh RAW). Nhiều thư mục thì cách nhau bằng dấu ;"
$dsThuMuc = Hoi "Thư mục (Enter để bỏ qua)"
$tong = 0
foreach ($tm in ($dsThuMuc -split ";" | ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ })) {
    if (-not (Test-Path $tm)) { Write-Host "  Không thấy: $tm" -ForegroundColor Red; continue }
    $ten = Split-Path $tm -Leaf
    $n = 0
    Get-ChildItem $tm -Recurse -File -Filter *.xmp -ErrorAction SilentlyContinue | ForEach-Object {
        if ($tong -ge 20000) { return }
        # chỉ lấy xmp có thông số Camera Raw (crs:)
        if (-not (Select-String -Path $_.FullName -Pattern "crs:" -SimpleMatch -Quiet)) { return }
        $tuongDoi = $_.FullName.Substring($tm.Length).TrimStart('\')
        $f = Join-Path $tam "xmp\$ten\$tuongDoi"
        New-Item -ItemType Directory -Force -Path (Split-Path $f) | Out-Null
        Copy-Item $_.FullName $f
        $n++; $tong++
    }
    $baoCao.Add("xmp | $tm | $n file")
    Write-Host "  -> $tm : $n file .xmp"
}

# Preset và profile Camera Raw do bạn tự tạo
foreach ($sub in @("Settings","CameraProfiles")) {
    $src = Join-Path $env:APPDATA "Adobe\CameraRaw\$sub"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $tam "camera-raw-$sub") -Recurse -Force
        $baoCao.Add("camera raw | đã copy $sub")
    }
}

# ----------------------------------------------------------------- 3. MÁY
Write-Host "`n=== 3/3  THÔNG TIN MÁY ===" -ForegroundColor Cyan
Get-CimInstance Win32_VideoController | ForEach-Object { $baoCao.Add("gpu | $($_.Name) | driver $($_.DriverVersion)") }
$os = Get-CimInstance Win32_OperatingSystem
$baoCao.Add("os | $($os.Caption) $($os.Version) | RAM $([Math]::Round($os.TotalVisibleMemorySize/1MB)) GB")
$baoCao | Set-Content -Encoding UTF8 (Join-Path $tam "bao-cao.txt")
$baoCao | ForEach-Object { Write-Host "  $_" }

# ----------------------------------------------------------------- NÉN
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $tam "*") -DestinationPath $zip
Remove-Item $tam -Recurse -Force
$mb = [Math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "`nXONG: $zip ($mb MB)" -ForegroundColor Green
if ($mb -gt 25) {
    Write-Host "File lớn hơn 25MB: hãy tải lên mục Releases của repo (xem README), không dùng 'Upload files'." -ForegroundColor Yellow
}
Read-Host "Nhấn Enter để đóng"
