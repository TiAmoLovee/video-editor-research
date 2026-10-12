param([switch]$Execute)
# Reuse the exact locally built font image recorded by the user's previous run.
# No fallback pull, original-image retagging, or repeated font-only build.
& (Join-Path $PSScriptRoot 'Fix-Font-And-Test.ps1') -Execute:$Execute -ApiImage 'sha256:663b453f3106c4e5997cb3af3c137f844dc5d16daf596513572d34cdd2ce6b56'
