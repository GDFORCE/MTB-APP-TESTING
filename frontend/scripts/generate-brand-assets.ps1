Add-Type -AssemblyName System.Drawing

$images = Join-Path $PSScriptRoot "..\assets\images"

function New-Canvas([int]$width, [int]$height, [string]$background) {
  $bitmap = [System.Drawing.Bitmap]::new($width, $height)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.Clear([System.Drawing.ColorTranslator]::FromHtml($background))
  return @($bitmap, $graphics)
}

function Draw-MtbMark($graphics, [float]$x, [float]$y, [float]$size) {
  $tile = [System.Drawing.RectangleF]::new($x, $y, $size, $size)
  $gradient = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
    $tile,
    [System.Drawing.ColorTranslator]::FromHtml("#BE4A63"),
    [System.Drawing.ColorTranslator]::FromHtml("#6B1437"),
    45
  )
  $radius = $size * 0.23
  $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $diameter = $radius * 2
  $path.AddArc($x, $y, $diameter, $diameter, 180, 90)
  $path.AddArc($x + $size - $diameter, $y, $diameter, $diameter, 270, 90)
  $path.AddArc($x + $size - $diameter, $y + $size - $diameter, $diameter, $diameter, 0, 90)
  $path.AddArc($x, $y + $size - $diameter, $diameter, $diameter, 90, 90)
  $path.CloseFigure()
  $graphics.FillPath($gradient, $path)

  $pen = [System.Drawing.Pen]::new(
    [System.Drawing.ColorTranslator]::FromHtml("#FBF2E8"),
    $size * 0.078
  )
  $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $points = [System.Drawing.PointF[]]@(
    [System.Drawing.PointF]::new($x + $size * 0.234, $y + $size * 0.711),
    [System.Drawing.PointF]::new($x + $size * 0.352, $y + $size * 0.336),
    [System.Drawing.PointF]::new($x + $size * 0.500, $y + $size * 0.609),
    [System.Drawing.PointF]::new($x + $size * 0.648, $y + $size * 0.336),
    [System.Drawing.PointF]::new($x + $size * 0.766, $y + $size * 0.711)
  )
  $graphics.DrawLines($pen, $points)

  $dotBrush = [System.Drawing.SolidBrush]::new(
    [System.Drawing.ColorTranslator]::FromHtml("#E69B5C")
  )
  $dotSize = $size * 0.112
  $graphics.FillEllipse(
    $dotBrush,
    $x + ($size - $dotSize) / 2,
    $y + $size * 0.256,
    $dotSize,
    $dotSize
  )

  $dotBrush.Dispose()
  $pen.Dispose()
  $path.Dispose()
  $gradient.Dispose()
}

function Save-Png($bitmap, $graphics, [string]$name) {
  $path = Join-Path $images $name
  $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $graphics.Dispose()
  $bitmap.Dispose()
}

$icon = New-Canvas 1024 1024 "#FBF2E8"
Draw-MtbMark $icon[1] 142 142 740
Save-Png $icon[0] $icon[1] "mtb-icon.png"

$adaptive = New-Canvas 1024 1024 "#FBF2E8"
Draw-MtbMark $adaptive[1] 238 238 548
Save-Png $adaptive[0] $adaptive[1] "mtb-adaptive-icon.png"

$splash = New-Canvas 1242 2436 "#FBF2E8"
Draw-MtbMark $splash[1] 451 764 340
$titleFont = [System.Drawing.Font]::new("Arial", 70, [System.Drawing.FontStyle]::Bold)
$subtitleFont = [System.Drawing.Font]::new("Arial", 31, [System.Drawing.FontStyle]::Regular)
$titleBrush = [System.Drawing.SolidBrush]::new(
  [System.Drawing.ColorTranslator]::FromHtml("#2E1B33")
)
$subtitleBrush = [System.Drawing.SolidBrush]::new(
  [System.Drawing.ColorTranslator]::FromHtml("#7B5F73")
)
$center = [System.Drawing.StringFormat]::new()
$center.Alignment = [System.Drawing.StringAlignment]::Center
$splash[1].DrawString(
  "My Trial Board",
  $titleFont,
  $titleBrush,
  [System.Drawing.RectangleF]::new(80, 1150, 1082, 110),
  $center
)
$splash[1].DrawString(
  "Clinical trial visits, connected.",
  $subtitleFont,
  $subtitleBrush,
  [System.Drawing.RectangleF]::new(80, 1275, 1082, 70),
  $center
)
$center.Dispose()
$subtitleBrush.Dispose()
$titleBrush.Dispose()
$subtitleFont.Dispose()
$titleFont.Dispose()
Save-Png $splash[0] $splash[1] "mtb-splash.png"

