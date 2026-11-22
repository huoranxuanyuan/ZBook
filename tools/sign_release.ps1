# SPDX-License-Identifier: AGPL-3.0-only
# Requires PowerShell 7 and Windows CNG for signing. Verification uses only a public key.
[CmdletBinding(DefaultParameterSetName='Verify')]
param(
    [Parameter(ParameterSetName='Init', Mandatory)][switch] $Initialize,
    [Parameter(ParameterSetName='Sign', Mandatory)][switch] $Sign,
    [Parameter(ParameterSetName='Sign', Mandatory)]
    [Parameter(ParameterSetName='Verify', Mandatory)][string] $ReleaseDirectory
)
$ErrorActionPreference = 'Stop'
$publicFile = Join-Path $PSScriptRoot 'release-public.pem'
$algorithm = [System.Security.Cryptography.HashAlgorithmName]::SHA256
$format = [System.Security.Cryptography.DSASignatureFormat]::Rfc3279DerSequence
if ($Initialize -or $Sign) {
    $keyName = 'ZBook-local-release-signing-v1'
    if (-not [System.Security.Cryptography.CngKey]::Exists($keyName)) {
        if (-not $Initialize -or (Test-Path -LiteralPath $publicFile)) { throw 'Signing key unavailable. Select the original Windows account or document a new key.' }
        $parameters = [System.Security.Cryptography.CngKeyCreationParameters]::new()
        $parameters.KeyUsage = [System.Security.Cryptography.CngKeyUsages]::Signing
        $parameters.ExportPolicy = [System.Security.Cryptography.CngExportPolicies]::None
        $key = [System.Security.Cryptography.CngKey]::Create([System.Security.Cryptography.CngAlgorithm]::ECDsaP256, $keyName, $parameters)
    } else { $key = [System.Security.Cryptography.CngKey]::Open($keyName) }
    $signer = [System.Security.Cryptography.ECDsaCng]::new($key)
    $public = $signer.ExportSubjectPublicKeyInfoPem()
    if ($Initialize) {
        if (Test-Path -LiteralPath $publicFile) { throw 'A public key already exists.' }
        [System.IO.File]::WriteAllText($publicFile, $public + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        return
    }
    if (([System.IO.File]::ReadAllText($publicFile)).Trim() -ne $public.Trim()) { throw 'Signing key differs from the reviewed public key.' }
}
$directory = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
$checksum = Join-Path $directory 'SHA256SUMS.txt'
$signature = Join-Path $directory 'SHA256SUMS.txt.sig'
$data = [System.IO.File]::ReadAllBytes($checksum)
if ($Sign) {
    if (Test-Path -LiteralPath $signature) { throw 'A detached signature already exists.' }
    $bytes = $signer.SignData($data, $algorithm, $format)
    [System.IO.File]::WriteAllBytes($signature, $bytes)
}
$verifier = [System.Security.Cryptography.ECDsa]::Create()
$verifier.ImportFromPem([System.IO.File]::ReadAllText($publicFile))
if (-not $verifier.VerifyData($data, [System.IO.File]::ReadAllBytes($signature), $algorithm, $format)) { throw 'Signature verification failed.' }
Write-Output 'Release SHA-256 list signature verified (ECDSA P-256).'
