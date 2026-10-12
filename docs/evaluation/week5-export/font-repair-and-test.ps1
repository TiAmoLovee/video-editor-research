param(
    [switch]$Execute,
    [string]$ApiImage = 'clipforge-api'
)
$ErrorActionPreference = 'Stop'
$deliveryFolder = $PSScriptRoot
$manifestPath = Join-Path $deliveryFolder 'container-inputs.json'
$review = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($file in $review.files) {
    if (-not (Test-Path -LiteralPath $file.path -PathType Leaf)) { throw "Reviewed input missing: $($file.path)" }
    if ((Get-FileHash -LiteralPath $file.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) {
        throw "Reviewed input changed: $($file.path). Stop and ask Codex to refresh the manifest."
    }
}
Write-Output 'Reviewed runtime repair inputs OK. Uses an existing local image plus FFmpeg/FFprobe and Noto CJK.'
if (-not $Execute) {
    Write-Output 'No Docker command or download. Use -Execute to build an independent runtime image and run isolated acceptance.'
    exit 0
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is not installed or not in PATH.' }
$baseIdentity = & docker image inspect --format '{{.Id}}' $ApiImage
if ($LASTEXITCODE -ne 0 -or -not $baseIdentity) { throw 'Existing local API image not found. No pull or full application build attempted.' }
$dockerContext = & docker context show
if ($LASTEXITCODE -ne 0 -or -not $dockerContext) { throw 'Cannot identify the current Docker context.' }
$builderInfo = @(& docker buildx inspect $dockerContext)
if ($LASTEXITCODE -ne 0 -or (($builderInfo -join "`n") -notmatch '(?m)^Driver:\s+docker\s*$')) {
    throw 'The current context needs its local Docker builder. No remote builder or image pull attempted.'
}
$baselineServices = @(& docker ps --format '{{.ID}}|{{.Names}}|{{.Image}}' | Sort-Object)
if ($LASTEXITCODE -ne 0) { throw 'Docker engine check failed; no build started.' }
$runToken = [Guid]::NewGuid().ToString('N')
$baseTag = "clipforge-week5-font-base:$runToken"
$fontTag = "clipforge-week5-font-check:$runToken"
$evidenceFolder = Join-Path $deliveryFolder "font-image-runs/$runToken"
New-Item -ItemType Directory -Path $evidenceFolder | Out-Null
$status = 'started'
$stage = 'freeze_local_base'
$derivedIdentity = $null
$capability = $null
$failure = $null
$sameServices = $false
try {
    & docker image tag $baseIdentity $baseTag
    if ($LASTEXITCODE -ne 0) { throw 'Cannot freeze the existing base image under a unique local tag.' }
    $stage = 'install_runtime_layer'
    Write-Output 'Building an independent image: installs FFmpeg/FFprobe and Noto CJK; already installed packages are reused.'
    $buildArguments = @('build', '--builder', $dockerContext, '--pull=false', '--load', '--progress=plain',
        '--build-arg', "BASE_IMAGE=$baseTag", '--tag', $fontTag,
        '--label', "clipforge.week5.fontbuild=$runToken",
        '--file', (Join-Path $deliveryFolder 'font-image/Dockerfile'), (Join-Path $deliveryFolder 'font-image'))
    & docker @buildArguments
    if ($LASTEXITCODE -ne 0) { throw 'Font image build failed. No automatic retry, image push or production service change.' }
    $derivedIdentity = & docker image inspect --format '{{.Id}}' $fontTag
    if ($LASTEXITCODE -ne 0 -or -not $derivedIdentity) { throw 'New font image identity could not be verified.' }
    $stage = 'image_capabilities'
    $runtimeCheck = Join-Path $deliveryFolder 'font-image/check_runtime.py'
    $capabilityArguments = @('run', '--rm', '--pull=never', '--network=none', '--read-only', '--user=10001:10001',
        '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--env', 'PYTHONPATH=/app/backend', '--env', 'CLIPFORGE_DATA_DIR=/data',
        '--tmpfs', '/tmp:rw,nosuid,noexec,size=32m,mode=1777', '--tmpfs', '/data:rw,nosuid,noexec,size=32m,mode=1777',
        '--mount', "type=bind,source=$($review.package),target=/app/backend/clipforge,readonly",
        '--mount', "type=bind,source=$($review.schemas),target=/app/schemas,readonly",
        '--mount', "type=bind,source=$runtimeCheck,target=/check_runtime.py,readonly",
        '--entrypoint', 'python', $derivedIdentity, '/check_runtime.py')
    $capabilityOutput = @(& docker @capabilityArguments)
    $capabilityExit = $LASTEXITCODE
    $capability = ($capabilityOutput -join "`n") | ConvertFrom-Json
    $capability | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceFolder 'image-capabilities.json') -Encoding UTF8
    if ($capabilityExit -ne 0 -or $capability.status -ne 'passed_image_capabilities_not_export_acceptance') {
        Write-Output ($capability | ConvertTo-Json -Depth 8 -Compress)
        throw 'Runtime capability check failed. All detected issues were saved together; export acceptance was not started.'
    }
    Write-Output 'RUNTIME READY: font glyphs, API imports, frontend, FFmpeg filters/codecs and FFprobe verified as UID 10001.'
    $stage = 'isolated_acceptance'
    & (Join-Path $deliveryFolder 'Test-IsolatedWeek5.ps1') -Execute -ApiImage $derivedIdentity
    $stage = 'completed'
    $status = 'passed'
} catch {
    $status = 'failed'
    $failure = $_.Exception.Message
} finally {
    $afterServices = @(& docker ps --format '{{.ID}}|{{.Names}}|{{.Image}}' | Sort-Object)
    $sameServices = $LASTEXITCODE -eq 0 -and (($baselineServices -join "`n") -eq ($afterServices -join "`n"))
    if ($status -eq 'passed' -and -not $sameServices) {
        $status = 'failed'
        $stage = 'service_inventory'
        $failure = 'Running-service inventory changed during the run; cannot confirm isolated acceptance.'
    }
    $summary = [ordered]@{
        status = $status; stage = $stage; error = $failure
        original_image_id = $baseIdentity; original_image_tag = $ApiImage; local_base_tag = $baseTag
        derived_image_id = $derivedIdentity; derived_image_tag = $fontTag; builder = $dockerContext
        existing_running_services_identical = $sameServices; production_service_actions = 0
        model_calls = 0; build_network = 'Debian package download'; acceptance_network = 'none'
        input_manifest_sha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceFolder 'font-repair-run.json') -Encoding UTF8
}
if ($status -ne 'passed') { throw "Font repair stopped at ${stage}: $failure. Keep report at $evidenceFolder and send the final output to Codex." }
if (-not $sameServices) { throw "Running-service inventory changed during the run; review $evidenceFolder before claiming acceptance." }
Write-Output "PASS: font repair and isolated acceptance completed. Additional report: $evidenceFolder"
