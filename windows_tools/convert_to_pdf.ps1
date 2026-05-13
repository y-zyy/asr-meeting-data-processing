<#
.SYNOPSIS
    PPTX 파일을 Microsoft Office를 사용하여 PDF로 변환합니다.

.DESCRIPTION
    Microsoft PowerPoint COM 자동화를 사용하여 PPTX/PPT 파일을 PDF로 변환합니다.
    Microsoft Office (PowerPoint)가 설치되어 있어야 합니다.

.PARAMETER InputPath
    변환할 PPTX/PPT 파일 경로 (단일 파일 또는 폴더)

.PARAMETER OutputDir
    출력 PDF 파일을 저장할 폴더 (기본값: 입력 파일과 동일한 폴더)

.EXAMPLE
    .\convert_to_pdf.ps1 -InputPath "C:\slides\presentation.pptx"

.EXAMPLE
    .\convert_to_pdf.ps1 -InputPath "C:\slides" -OutputDir "C:\output"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$InputPath,

    [string]$OutputDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Convert-PptxToPdf {
    param([string]$PptxPath, [string]$PdfPath)

    Write-Host "변환 중: $PptxPath -> $PdfPath"

    $ppt = $null
    $presentation = $null
    try {
        $ppt = New-Object -ComObject PowerPoint.Application
        $ppt.Visible = [Microsoft.Office.Core.MsoTriState]::msoFalse

        $presentation = $ppt.Presentations.Open(
            $PptxPath,
            [Microsoft.Office.Core.MsoTriState]::msoTrue,   # ReadOnly
            [Microsoft.Office.Core.MsoTriState]::msoFalse,  # Untitled
            [Microsoft.Office.Core.MsoTriState]::msoFalse   # WithWindow
        )

        # ppSaveAsPDF = 32
        $presentation.SaveAs($PdfPath, 32)
        Write-Host "  완료: $PdfPath" -ForegroundColor Green

    } finally {
        if ($presentation) {
            $presentation.Close()
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($presentation) | Out-Null
        }
        if ($ppt) {
            $ppt.Quit()
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) | Out-Null
        }
        [System.GC]::Collect()
        [System.GC]::WaitForPendingFinalizers()
    }
}

# ── 입력 경로 처리 ──
$inputItem = Get-Item -LiteralPath $InputPath -ErrorAction Stop

if ($inputItem.PSIsContainer) {
    # 폴더: 내부의 모든 pptx/ppt 처리
    $files = Get-ChildItem -Path $InputPath -Include "*.pptx","*.ppt" -Recurse
    if ($files.Count -eq 0) {
        Write-Warning "폴더에서 PPTX/PPT 파일을 찾을 수 없습니다: $InputPath"
        exit 1
    }
    $outDir = if ($OutputDir) { $OutputDir } else { $InputPath }
} else {
    # 단일 파일
    $files = @($inputItem)
    $outDir = if ($OutputDir) { $OutputDir } else { $inputItem.DirectoryName }
}

# 출력 폴더 생성
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir | Out-Null
}

$successCount = 0
$failCount = 0

foreach ($file in $files) {
    $pdfName = [System.IO.Path]::ChangeExtension($file.Name, ".pdf")
    $pdfPath = Join-Path $outDir $pdfName
    $absInputPath = $file.FullName

    try {
        Convert-PptxToPdf -PptxPath $absInputPath -PdfPath $pdfPath
        $successCount++
    } catch {
        Write-Error "변환 실패 ($($file.Name)): $_"
        $failCount++
    }
}

Write-Host ""
Write-Host "── 변환 완료 ──" -ForegroundColor Cyan
Write-Host "성공: $successCount 개" -ForegroundColor Green
if ($failCount -gt 0) {
    Write-Host "실패: $failCount 개" -ForegroundColor Red
}
