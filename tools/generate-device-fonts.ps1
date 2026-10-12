param(
    [Parameter(Mandatory = $true)]
    [string]$FontFile,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $PSScriptRoot "..\firmware\body\main\boards\esp_vocat\customer_ui\fonts"
}

$fontFilePath = (Resolve-Path -LiteralPath $FontFile).Path
if ([System.IO.Path]::GetFileName($fontFilePath) -notmatch "^SourceHanSans.*\.otf$") {
    throw "Use an official SourceHanSans OTF covered by SIL OFL 1.1."
}
$fontHash = (Get-FileHash -LiteralPath $fontFilePath -Algorithm SHA256).Hash
$fontDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $fontDirectory | Out-Null
$privateFonts = [System.Drawing.Text.PrivateFontCollection]::new()
$privateFonts.AddFontFile($fontFilePath)
if ($privateFonts.Families.Count -ne 1) { throw "Expected one private font family." }
$privateFamily = $privateFonts.Families[0]
$FontFamily = $privateFamily.Name
if ($FontFamily -notmatch "Source Han Sans|\u601d\u6e90\u9ed1\u4f53") {
    throw "Unexpected font family '$FontFamily'; refusing a system-font fallback."
}

function Get-GlyphData {
    param(
        [int]$CodePoint,
        [int]$PixelSize,
        [System.Drawing.Font]$Font,
        [System.Drawing.StringFormat]$Format
    )

    $canvasSize = 64
    $originX = 8
    $bitmap = [System.Drawing.Bitmap]::new(
        $canvasSize,
        $canvasSize,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.Clear([System.Drawing.Color]::Black)
        $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $character = [char]::ConvertFromUtf32($CodePoint)
        $graphics.DrawString($character, $Font, [System.Drawing.Brushes]::White, $originX, 0, $Format)

        $minX = $canvasSize
        $minY = $canvasSize
        $maxX = -1
        $maxY = -1
        for ($y = 0; $y -lt $canvasSize; $y++) {
            for ($x = 0; $x -lt $canvasSize; $x++) {
                if ($bitmap.GetPixel($x, $y).R -gt 0) {
                    $minX = [Math]::Min($minX, $x)
                    $minY = [Math]::Min($minY, $y)
                    $maxX = [Math]::Max($maxX, $x)
                    $maxY = [Math]::Max($maxY, $y)
                }
            }
        }
        if ($maxX -lt 0 -or $maxY -lt 0) {
            throw "Font '$FontFamily' did not render U+$($CodePoint.ToString('X4'))"
        }

        $width = $maxX - $minX + 1
        $height = $maxY - $minY + 1
        $nibbles = [System.Collections.Generic.List[int]]::new()
        for ($y = $minY; $y -le $maxY; $y++) {
            for ($x = $minX; $x -le $maxX; $x++) {
                $value = [Math]::Round($bitmap.GetPixel($x, $y).R / 17.0)
                $nibbles.Add([Math]::Min(15, [Math]::Max(0, $value)))
            }
        }

        $bytes = [System.Collections.Generic.List[int]]::new()
        for ($index = 0; $index -lt $nibbles.Count; $index += 2) {
            $high = $nibbles[$index]
            $low = if ($index + 1 -lt $nibbles.Count) { $nibbles[$index + 1] } else { 0 }
            $bytes.Add(($high -shl 4) -bor $low)
        }

        $baselineY = if ($PixelSize -eq 18) { 20 } else { 27 }
        return [pscustomobject]@{
            CodePoint = $CodePoint
            Bytes = $bytes.ToArray()
            Width = $width
            Height = $height
            OffsetX = [Math]::Floor(($PixelSize - $width) / 2)
            OffsetY = $baselineY - ($maxY + 1)
        }
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function Write-DeviceFont {
    param(
        [string]$Name,
        [int]$PixelSize,
        [string]$Symbols,
        [string]$FallbackName,
        [int]$LineHeight,
        [int]$BaseLine
    )

    $codePoints = @(
        $Symbols.ToCharArray() |
            ForEach-Object { [int]$_ } |
            Sort-Object -Unique
    )
    $font = [System.Drawing.Font]::new(
        $privateFamily,
        $PixelSize,
        [System.Drawing.FontStyle]::Regular,
        [System.Drawing.GraphicsUnit]::Pixel
    )
    $format = [System.Drawing.StringFormat]::GenericTypographic.Clone()
    try {
        $glyphs = @($codePoints | ForEach-Object { Get-GlyphData $_ $PixelSize $font $format })
    }
    finally {
        $format.Dispose()
        $font.Dispose()
    }

    $bitmapLines = [System.Collections.Generic.List[string]]::new()
    $descriptorLines = [System.Collections.Generic.List[string]]::new()
    $bitmapIndex = 0
    foreach ($glyph in $glyphs) {
        $bitmapLines.Add("    /* U+$($glyph.CodePoint.ToString('X4')) */")
        for ($index = 0; $index -lt $glyph.Bytes.Count; $index += 12) {
            $last = [Math]::Min($index + 11, $glyph.Bytes.Count - 1)
            $chunk = $glyph.Bytes[$index..$last] | ForEach-Object { "0x$($_.ToString('X2'))" }
            $bitmapLines.Add("    $($chunk -join ', '),")
        }
        $descriptorLines.Add(
            "    {.bitmap_index = $bitmapIndex, .adv_w = $($PixelSize * 16), .box_w = $($glyph.Width), .box_h = $($glyph.Height), .ofs_x = $($glyph.OffsetX), .ofs_y = $($glyph.OffsetY)}, /* U+$($glyph.CodePoint.ToString('X4')) */"
        )
        $bitmapIndex += $glyph.Bytes.Count
    }

    $rangeStart = $codePoints[0]
    $rangeLength = $codePoints[-1] - $rangeStart + 1
    $unicodeOffsets = $codePoints | ForEach-Object { "0x$(($_ - $rangeStart).ToString('X'))" }
    $outputPath = Join-Path $fontDirectory "$Name.c"
    $content = @"
/* Generated by tools/generate-device-fonts.ps1.
 * DeskBot Device bitmap subset, derived from Source Han Sans.
 * Copyright 2014-2021 Adobe. Licensed under SIL OFL 1.1.
 * See licenses/SourceHanSans-OFL.txt and THIRD_PARTY.md.
 * Source font SHA256: $fontHash
 */
#include "../alarm_manager.h"

static const uint8_t ${Name}_glyph_bitmap[] = {
$($bitmapLines -join "`n")
};

static const lv_font_fmt_txt_glyph_dsc_t ${Name}_glyph_dsc[] = {
    {.bitmap_index = 0, .adv_w = 0, .box_w = 0, .box_h = 0, .ofs_x = 0, .ofs_y = 0},
$($descriptorLines -join "`n")
};

static const uint16_t ${Name}_unicode_list[] = {
    $($unicodeOffsets -join ', ')
};

static const lv_font_fmt_txt_cmap_t ${Name}_cmaps[] = {
    {
        .range_start = $rangeStart,
        .range_length = $rangeLength,
        .glyph_id_start = 1,
        .unicode_list = ${Name}_unicode_list,
        .glyph_id_ofs_list = NULL,
        .list_length = $($codePoints.Count),
        .type = LV_FONT_FMT_TXT_CMAP_SPARSE_TINY,
    },
};

static const lv_font_fmt_txt_dsc_t ${Name}_font_dsc = {
    .glyph_bitmap = ${Name}_glyph_bitmap,
    .glyph_dsc = ${Name}_glyph_dsc,
    .cmaps = ${Name}_cmaps,
    .kern_dsc = NULL,
    .kern_scale = 0,
    .cmap_num = 1,
    .bpp = 4,
    .kern_classes = 0,
    .bitmap_format = 0,
};

const lv_font_t $Name = {
    .get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt,
    .get_glyph_bitmap = lv_font_get_bitmap_fmt_txt,
    .line_height = $LineHeight,
    .base_line = $BaseLine,
    .subpx = LV_FONT_SUBPX_NONE,
    .underline_position = -2,
    .underline_thickness = 1,
    .dsc = &${Name}_font_dsc,
    .fallback = &$FallbackName,
    .user_data = NULL,
};
"@
    [System.IO.File]::WriteAllText($outputPath, $content, [System.Text.UTF8Encoding]::new($false))
    Write-Host "${Name}: $($codePoints.Count) glyphs, $bitmapIndex bitmap bytes -> $outputPath"
}

$bodySymbols = ConvertFrom-Json '"\u00b7\u4e00\u90e8\u6b65\u8bbe\u5907\u80fd\u529b\u53ea\u8bfb\u8bca\u65ad\u4e0d\u63a7\u5236\u786c\u4ef6\u5e95\u5ea7\u4ee3\u7801\u5df2\u63a5\u5165\u53cc\u9ea6\u9876\u90e8\u6478\u5934\u4e09\u78c1\u573a\u9065\u6d4b\u5f85\u611f\u77e5\u672a\u542f\u7528\u771f\u5b9e\u6570\u636e\u9875\u5c06\u5728\u540e\u7eed\u589e\u52a0\u72b6\u6001\u6b63\u5e38\u5173\u95ed\u672a\u77e5\u8bb0\u5f55\u5916\u58f3\u5217\u8868\u6807\u5b9a\u91c7\u6837\u4f20\u611f\u5668\u8f74\u6837\u672c\u4e0a\u4e0b\u5de6\u53f3\u524d\u540e\u5f3a\u5ea6\u7a33\u5b9a\u65b0\u540d\u79f0\u4fdd\u5b58\u5220\u9664\u91cd\u65b0\u8bc6\u522b\u5e73\u5747\u504f\u5dee\u8ddd\u79bb\u9608\u503c\u4eca\u65e5\u529f\u5fb7\u5706\u6ee1\u5b9e\u65f6\u79fb\u52a8\u94c1\u89c2\u5bdf\u53d8\u5316\u8fde\u63a5\u7b49\u5f85\u8bf7\u786e\u8ba4\u6b63\u5728\u5b66\u4e60\u4fdd\u6301\u81ea\u52a8\u53ef\u80fd\u662f\u8fd8\u6ca1\u6709\u6863\u6848\u5f53\u524d\u7f6e\u4fe1\u5ea6\u4e0e\u5df2\u6709\u4e2d\u566a\u58f0\u5426\u7b2c\u8fd4\u56de\u6682\u65e0\u5bf9\u5c0f\u667a\u8bf4\u53ef\u6539\u540d\u6216\u5220\u9664\u66f4\u65b0\u5931\u8d25\u5fd9\u6ce2\u52a8\u8fc7\u5927\u91cd\u8bd5\u6a21\u5757"'
$titleSymbols = ConvertFrom-Json '"\u8bbe\u5907\u80fd\u529b\u78c1\u573a\u8bc6\u522b\u5916\u58f3\u5217\u8868\u611f\u77e5"'

try {
    Write-DeviceFont "ui_font_Device18" 18 $bodySymbols "ui_font_Heiti18" 21 5
    Write-DeviceFont "ui_font_Device24" 24 $titleSymbols "ui_font_Heiti24" 28 6
}
finally { $privateFonts.Dispose() }
