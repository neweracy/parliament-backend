# Transfer chunked tarballs with per-chunk verification + retry (resilient to
# flaky connections). Reassembles on the box and loads the images.
$ErrorActionPreference = "Continue"
$KEY="parliament-key.pem"; $IP="3.237.29.243"; $U="ec2-user"; $R="/opt/parliament/chunks"
$opts = @('-i',$KEY,'-o','StrictHostKeyChecking=no','-o','ConnectTimeout=20','-o','ServerAliveInterval=15')

$chunks = Get-ChildItem chunks | Sort-Object Name
foreach ($c in $chunks) {
  $local = $c.Length
  for ($try=1; $try -le 6; $try++) {
    scp @opts $c.FullName "${U}@${IP}:${R}/$($c.Name)" 2>$null
    $remote = ssh @opts "${U}@${IP}" "stat -c %s ${R}/$($c.Name) 2>/dev/null" 2>$null
    if ("$remote".Trim() -eq "$local") { Write-Host "  OK   $($c.Name) ($local b)"; break }
    Write-Host "  retry $($c.Name) attempt $try (got '$remote' want $local)"
    if ($try -eq 6) { Write-Host "  FAILED $($c.Name)"; exit 1 }
  }
}
Write-Host "==> All chunks transferred. Reassembling on box."
ssh @opts "${U}@${IP}" "cd /opt/parliament && cat chunks/gw.* > gateway.tar && cat chunks/pp.* > postprocess.tar && ls -l gateway.tar postprocess.tar"
