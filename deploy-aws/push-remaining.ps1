# Push only chunks not yet correctly on the box (size-verified), then reassemble.
$ErrorActionPreference = "Continue"
$KEY="parliament-key.pem"; $IP="3.237.29.243"; $U="ec2-user"; $R="/opt/parliament/chunks"
$opts = @('-i',$KEY,'-o','StrictHostKeyChecking=no','-o','ConnectTimeout=20','-o','ServerAliveInterval=15')

$chunks = Get-ChildItem chunks | Sort-Object Name
foreach ($c in $chunks) {
  $local = $c.Length
  # skip if already correct on box
  $remote = ssh @opts "${U}@${IP}" "stat -c %s ${R}/$($c.Name) 2>/dev/null" 2>$null
  if ("$remote".Trim() -eq "$local") { Write-Host "  skip $($c.Name) (already ok)"; continue }
  for ($try=1; $try -le 8; $try++) {
    scp @opts $c.FullName "${U}@${IP}:${R}/$($c.Name)" 2>$null
    $remote = ssh @opts "${U}@${IP}" "stat -c %s ${R}/$($c.Name) 2>/dev/null" 2>$null
    if ("$remote".Trim() -eq "$local") { Write-Host "  OK   $($c.Name)"; break }
    if ($try -eq 8) { Write-Host "  FAILED $($c.Name)"; exit 1 }
  }
}
Write-Host "==> All chunks verified. Reassembling."
ssh @opts "${U}@${IP}" "cd /opt/parliament && cat chunks/gw.* > gateway.tar && cat chunks/pp.* > postprocess.tar && echo gateway=\$(stat -c %s gateway.tar) postprocess=\$(stat -c %s postprocess.tar)"
