# Push the rebuilt gateway image chunks with per-chunk verify+retry, reassemble,
# load, and recreate the gateway container from the fresh image.
$ErrorActionPreference = "Continue"
$env:AWS_PROFILE = "prod"; $env:AWS_REGION = "us-east-1"
$KEY="parliament-key.pem"; $IP="3.237.29.243"; $U="ec2-user"; $R="/opt/parliament/chunks2"
$opts = @('-i',$KEY,'-o','StrictHostKeyChecking=no','-o','ConnectTimeout=20','-o','ServerAliveInterval=15')

# ensure current IP has SSH
$myip=(Invoke-RestMethod https://checkip.amazonaws.com).Trim()
aws ec2 authorize-security-group-ingress --group-id sg-0db339dfe4a6ae543 --region us-east-1 --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$myip/32,Description=ssh-gw2}]" 2>$null | Out-Null

ssh @opts "${U}@${IP}" "mkdir -p $R" 2>$null

$chunks = Get-ChildItem chunks2 | Sort-Object Name
foreach ($c in $chunks) {
  $local = $c.Length
  $remote = ssh @opts "${U}@${IP}" "stat -c %s $R/$($c.Name) 2>/dev/null" 2>$null
  if ("$remote".Trim() -eq "$local") { Write-Host "  skip $($c.Name)"; continue }
  for ($try=1; $try -le 8; $try++) {
    scp @opts $c.FullName "${U}@${IP}:$R/$($c.Name)" 2>$null
    $remote = ssh @opts "${U}@${IP}" "stat -c %s $R/$($c.Name) 2>/dev/null" 2>$null
    if ("$remote".Trim() -eq "$local") { Write-Host "  OK $($c.Name)"; break }
    if ($try -eq 8) { Write-Host "  FAILED $($c.Name)"; exit 1 }
  }
}
Write-Host "==> reassemble + load + recreate gateway"
ssh @opts "${U}@${IP}" @'
set -e
cd /opt/parliament
cat chunks2/gw.* > gateway2.tar
echo "reassembled: $(stat -c %s gateway2.tar)"
docker load -i gateway2.tar
docker compose -f docker-compose.prod.yml -f docker-compose.ship.yml up -d gateway
rm -rf chunks2 gateway2.tar
docker compose -f docker-compose.prod.yml -f docker-compose.ship.yml ps --format "{{.Service}} {{.Status}}"
'@
