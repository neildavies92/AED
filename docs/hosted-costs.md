# AED-056 hosting decision and monthly cost

Choose one Ubuntu VPS with local block storage, Docker Compose, two systemd timers
and an off-host Healthchecks.io check. The lowest suitable UK offer found was
OVHcloud VPS-1 from **£3.31 before VAT / £3.97 including VAT per month**, for
2 vCores, 4 GB RAM, 40 GB SSD, IPv4, UK traffic and standard daily backup. The
linked annual configurator shows £39.72 before VAT upfront. Confirm the actual UK
checkout, renewal, backup coverage and at-rest protections before ordering.
Target **£5/month including VAT** for the base deployment and monitoring.
Any required storage add-on or higher renewal price needs a new comparison.
[UK offer](https://www.ovhcloud.com/en-gb/vps/vps-uk/) ·
[Annual configurator](https://www.ovhcloud.com/en-gb/vps/configurator/?brick=VPS%2BModel%2B1&planCode=vps-2027-model1&pricing=upfront12&processor=+&storage=40__SSD__NVMe&vcore=2__vCore) ·
[Included IPs](https://www.ovhcloud.com/en/vps/vps-ip/)

Model checked 9 November 2026. AWS rates use US East (N. Virginia) to compare a
low-cost configuration. London scheduling works independently of the AWS region.
USD conversions use a planning rate of £0.77 per $1 and exclude VAT. A 30-day month
includes 30 or 60 runs, up to five minutes of work per run, one additional minute
to pull a Fargate image, 5 GB of active state/assets, 5 GB of retained encrypted
backup data, and under 0.1/0.2 GB of logs. Media and backup transfer depend on
asset sizes and restore frequency.

| Architecture | Fixed or retained resources | One post/day | Two posts/day |
| --- | --- | ---: | ---: |
| OVHcloud VPS-1 | £3.97 advertised monthly equivalent including VAT, with disk, IPv4 and standard backup | **£3.97** (~$5.16) | **£3.97** (~$5.16) |
| Lambda + EventBridge Scheduler + EFS, IPv4 | ~$36.50/month NAT and IPv4 at 730h, plus ~$1.50 EFS and shared storage/logging below | **~$38.4 / £29.6** | **~$38.6 / £29.7** |
| Fargate ARM + EventBridge Scheduler + EFS | No always-on compute. ~$1.50 EFS plus shared storage/logging below | **~$2.0 / £1.54** | **~$2.2 / £1.69** |

- **Lambda.** 1 GB × 300 s × 30/60 gives 9,000/18,000 GB-s, about $0.15/$0.30
  before shared monthly allowances (1M requests and 400,000 GB-s). EFS requires
  VPC connectivity. NAT at $0.045/h plus IPv4 at $0.005/h and $0.045/GB processing
  makes the IPv4 design expensive. Dual-stack egress could remove the NAT floor
  if all required endpoints support IPv6. That and the Lambda image entrypoint,
  process lifecycle, staging deadlines and crash-safe persistence would need
  verification. S3 can store backups, but copying SQLite from and back to S3
  around each run would lose checkpoints on a crash between a media POST and upload.
  [Lambda](https://aws.amazon.com/lambda/pricing/) ·
  [VPC networking](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc-internet.html) ·
  [NAT and IPv4](https://aws.amazon.com/vpc/pricing/)
- **Fargate.** 0.5 vCPU and 1 GB for 360 s on demand costs about $0.06/$0.12.
  Task IPv4 adds $0.015/$0.030. Use public subnets with outbound access and no
  inbound rules, no NAT. An ECS task can provision a new EBS volume from a snapshot,
  but can't attach the same existing volume on each run. EFS would need evidence
  that SQLite transactions, filesystem locks, checkpoint durability and failover
  behave correctly across successive task hosts. The estimate assumes 5 GB of
  Regional Standard at $0.30/GB-month and a small allowance for throughput,
  metadata and cross-AZ traffic. It excludes the engineering work needed to make
  that storage design safe. [Fargate](https://aws.amazon.com/fargate/pricing/) ·
  [ECS EBS constraints](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ebs-volumes.html) ·
  [EFS](https://aws.amazon.com/efs/pricing/) ·
  [SQLite filesystem caveats](https://www.sqlite.org/useovernet.html)
- **Shared AWS estimates.** One GB of private ECR image storage is ~$0.10 per
  month. Five GB of S3 Standard backups are ~$0.12 per month plus requests.
  CloudWatch ingestion is ~$0.05/$0.10 before allowances, plus retained logs and
  any paid alarms. Scheduler's 14M monthly invocation allowance covers this load.
  Keep credential files private on encrypted storage and use scoped task roles
  rather than a paid secret per file. A dedicated secret would add $0.40/month
  plus API requests. Model outbound traffic within AWS's shared 100 GB allowance.
  Restore egress, cross-AZ transfers, throughput and extra backup versions can
  add charges. Free allowances depend on account usage and eligibility.
  [ECR](https://aws.amazon.com/ecr/pricing/) · [S3](https://aws.amazon.com/s3/pricing/) ·
  [Logs and alarms](https://aws.amazon.com/cloudwatch/pricing/) ·
  [Scheduler](https://aws.amazon.com/eventbridge/pricing/) ·
  [Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/)
- **VPS operations.** Transfer the pinned image once over SSH and keep SQLite,
  canonical assets and refreshable credentials on local disk. Keep existing
  Quick Tunnel staging within the VPS traffic allowance. It has no uptime SLA.
  Cap Docker and journal logs. The external Hobbyist check is £0 within 20 checks
  and 100 entries per check. Use encrypted off-host copies in existing storage
  or budget B2 within its first 10 GB free and restore egress allowance. Standard
  VPS backup is useful for recovery but does not establish the latest Instagram
  outcome. [VPS backup](https://www.ovhcloud.com/en-gb/vps/options/) ·
  [Quick Tunnel limits](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) ·
  [Healthchecks](https://healthchecks.io/pricing/) ·
  [B2](https://help.backblaze.com/hc/en-us/articles/217667478-Understanding-B2-Pricing-Structure)

Hetzner CX23 is €5.49 plus €0.50 IPv4 before tax, about £5.15 before VAT using
£0.86/€, before optional backups. It is a fallback quote. Fargate has lower
estimated infrastructure charges, but the VPS avoids moving the critical SQLite
and filesystem locks across task hosts. The trade is a few pounds per month for a
known persistence model and host maintenance. No option eliminates operator work
when provider consent expires or an Instagram POST has an uncertain outcome.
[Hetzner rates](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/) ·
[IPv4 add-on](https://docs.hetzner.com/cloud/servers/overview/)
