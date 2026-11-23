# AED-056 hosting decision and monthly cost

Prices checked 23 November 2026. Select **OVHcloud VPS-1 in Erith, UK**, subject
to checkout availability and verified disk and provider-backup encryption.
Use Ubuntu 24.04 where offered, 2 vCores, 4 GB RAM and 40 GB NVMe.
The UK configurator displayed a 12-month offer of £39.72 before tax
(**£47.66 including 20% VAT upfront**, equivalent to **£3.97/month**).
Confirm the actual Ubuntu image, commitment, renewal price and backup coverage
on the quote. If any protection needs a paid add-on, price it before approval.
[UK offer](https://www.ovhcloud.com/en-gb/vps/vps-uk/) ·
[12-month Erith quote](https://www.ovhcloud.com/en-gb/vps/configurator/?brick=VPS%2BModel%2B1&planCode=vps-2027-model1&pricing=upfront12&processor=+&storage=40__SSD__NVMe&vcore=2__vCore) ·
[Included IPs](https://www.ovhcloud.com/en/vps/vps-ip/)

| Monthly component | OVHcloud VPS-1 Erith | Hetzner CX23 Germany (Falkenstein or Nuremberg) |
| --- | ---: | ---: |
| Compute and local disk, pre-tax | £3.31 equivalent | €5.49 cap |
| One public IPv4 | Included | €0.50 |
| Provider daily backup | Included | €1.10 (20% of compute, rounded) |
| Traffic | Unlimited, 500 Mbps | 20 TB included |
| Private R2 Standard media and encrypted off-host repository | £0 within shared allowance | £0 within shared allowance |
| Two Healthchecks.io checks and existing Discord channel | £0 on Hobbyist | £0 on Hobbyist |
| Total at 20% UK VAT | **£3.97/month equivalent** | **€8.51/month**, about **£7.32** at £0.86/€ |

Hetzner also gives 2 vCPU, 4 GB RAM and 40 GB storage. Its cost-optimized
instances have limited availability. A quote may change by region or account.
The estimate includes optional provider snapshots for comparable recovery coverage.
The £0.86/€ rate is a budgeting assumption and excludes card fees.
[Hetzner plans](https://www.hetzner.com/cloud/cost-optimized/) ·
[Regional tariff](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/) ·
[IPv4](https://docs.hetzner.com/general/infrastructure-and-availability/ipv4-pricing/) ·
[Backup tariff](https://docs.hetzner.com/cloud/billing/faq/)

One 200 KB media object daily is about 6 MB/month before cleanup. Budget up to
5 GB retained for deduplicated encrypted backups. R2 Standard shares 10 GB-month,
one million writes and ten million reads monthly across both private buckets.
Expect 30 media uploads and 30 backup/restore checks monthly. Monitor actual
retained data, requests and disk use during acceptance. R2 egress is free. The
host transfer and verification use the provider's included traffic. Keep active
disk, Docker image and temporary backup plus full restore within the 40 GB budget.
No volume, public application port, domain or additional VM is in this estimate.
[R2 rates](https://developers.cloudflare.com/r2/pricing/) ·
[Healthchecks plans](https://healthchecks.io/pricing/)

Provider backups help recover the VM. The separate encrypted restic repository
verifies application consistency and needs its own bucket and scoped key. Obtain
the provider's active disk and snapshot encryption terms in writing. If those
terms or unattended reboot are unacceptable, pause and obtain a new protected
storage quote before transferring production state.
