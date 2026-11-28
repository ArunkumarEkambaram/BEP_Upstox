# BEP Upstox on a cloud server (AWS Lightsail, Mumbai)

This guide sets up the app once on a small cloud server. The server runs the app all day, every day. You open it from your PC or phone through **Tailscale**, a private network: the app is never open to the internet, and it asks for your password.

**Do the setup outside market hours.** Step 6 (registering the IP in Upstox) logs your Upstox token out.

## 1. Create the server
1. Go to the AWS Lightsail console → **Create instance**.
2. Choose:
   - Region **Mumbai (ap-south-1)**;
   - Platform **Linux/Unix**, then the **Operating System (OS) only** tab (not "Apps + OS") → **Ubuntu 24.04 LTS** (don't leave a ready-made app such as Plesk selected);
   - network type **Dual-stack** (not IPv6-only: Upstox needs an IPv4 address);
   - the **1 GB RAM** plan: **$7/month** (1 GB, 2 vCPU, 40 GB SSD, 1 TB transfer). The $5 plan now has only 512 MB, too little for the app;
   - automatic snapshots off (they cost extra).
3. Name it `bep-upstox` and create it.
4. **Networking → Create static IP**, then attach it to `bep-upstox`. This is the IP you register in Upstox. It's free while it's attached.
5. Leave the Lightsail firewall as it is (SSH 22). Don't open port 8084.

**AWS account (one time):** sign up on the **Paid plan** (a Free plan account closes by itself after 6 months, deleting the server and its IP; the new-account credits are given on both plans). Turn on **MFA** for the root user (account menu → Security credentials → Assign MFA device → Authenticator app) and create a **$10 monthly cost budget** (Budgets → Create budget → Monthly cost budget) so AWS emails you if anything costs more than expected. Billing is pay as you go, one invoice a month (credits first; a stopped server is still billed, only deleting it stops the charge; an unattached static IP is billed). In India the payment may be a UPI AutoPay mandate: keep it active, and if a debit fails, pay under Billing and Cost Management → Payments → Pay now.

## 2. Connect with SSH
Use the **Connect using SSH** button in Lightsail, or from your PC with the downloaded key:

```bash
ssh -i LightsailDefaultKey-ap-south-1.pem ubuntu@<static-ip>
```

In the browser terminal, paste with right-click or Ctrl+Shift+V. To copy text out, select it and use the clipboard icon at the bottom right.

Then add **1 GB of swap** (spare memory on the disk, so a memory peak during installs doesn't kill the app):

```bash
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

```bash
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

`free -h` should now show `Swap: 1.0Gi`.

## 3. Give the server read access to the repo (deploy key)
On the server:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
```

```bash
cat ~/.ssh/id_ed25519.pub
```

On GitHub, open the BEP_Upstox repo → **Settings → Deploy keys → Add deploy key**. Paste the key, leave **Allow write access** off, and save.

Test it (answer `yes` the first time; GitHub's ED25519 fingerprint is `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`):

```bash
ssh -T git@github.com
```

It should answer "Hi ArunkumarEkambaram/BEP_Upstox! You've successfully authenticated".

## 4. Clone and set up
```bash
git clone git@github.com:ArunkumarEkambaram/BEP_Upstox.git
```

```bash
cd BEP_Upstox && bash deploy/setup_server.sh
```

The script:
- installs Python 3.14 and the app's packages;
- adds the `bep-upstox` service (it starts at boot and restarts within 5 s if it stops);
- sets the IST clock;
- turns on a firewall that allows only SSH and Tailscale;
- installs **Tailscale**.

Install the Tailscale app on your PC and phone first and log in (the free Personal plan is enough; use a login with 2-step verification). When the script shows a Tailscale login link, open it and log in with the **same account**. If it then says **"Serve is not enabled on your tailnet"** with a second link, open it and click **Enable** (HTTPS); the script continues by itself. If the script stopped instead, run this again:

```bash
sudo tailscale serve --bg 8084
```

Then, in the Tailscale admin console → **Machines** → the server's row → **⋯** → **Disable key expiry** (otherwise the server drops off your network after 180 days). Only for the server.

**Renaming the server in Tailscale** (e.g. to `bep-upstox`) changes its address, but `tailscale serve` stays on the old name until you run:

```bash
sudo tailscale serve reset && sudo tailscale serve --bg 8084 && sudo tailscale serve status
```

## 5. Set the app password
```bash
.venv/bin/python auth.py set-password
```

```bash
sudo systemctl restart bep-upstox
```

The password needs at least 8 characters. Each browser stays logged in for 30 days; **Log out** is the icon next to the light/dark button. After 5 wrong tries, login is blocked for 5 minutes.

## 6. Register the static IP in Upstox (after market hours)
1. Go to the Upstox developer site (account.upstox.com/developer/apps) → **My Apps** → your app → **Static IPs**.
2. **Look before you change anything.** While BEP-AlgoDelta is still in use, the **Primary IP is AlgoDelta's**: leave it, and enter the Lightsail static IP as the **Secondary IP**. (Empty Primary: put it there.) Save.
3. Saving logs your token out: generate a new one, and enter it on Setup the next morning as usual.

A third-party guide says the IP can be changed only about once a week, so copy it carefully. Whether Upstox accepts orders from the Secondary IP while the Primary is also in use is confirmed by the first 1-lot Live test (refused = error UDAPI1154). When BEP Upstox replaces AlgoDelta: make the Lightsail IP the Primary and remove AlgoDelta's.

**Token:** one Upstox token per day works for both BEP apps (the PC app and the server): generate it once and paste the same text on Setup in each. AlgoDelta keeps its own broker login. If AlgoDelta ever shows the broker disconnected after you generate the token, log it in again.

## 7. Open the app on your PC and phone
1. Install the Tailscale app on your PC and phone and log in with the same account.
2. Open the address that `sudo tailscale serve status` shows, e.g. `https://bep-upstox.<your-tailnet>.ts.net`. Use **https://**: `http://` is refused (ERR_CONNECTION_REFUSED). The first open of a new name can take up to 30 s (certificate).

The app on your PC (BEP-AlgoDelta, port 8083) keeps working as before.

## Daily use
The app is already running. Before 09:08, open it and:
1. enter today's Upstox token on **Setup**;
2. upload the BEP CSV;
3. choose the order mode (Dry run / Sandbox / Live).

## Updating after a merged PR
```bash
cd ~/BEP_Upstox && bash deploy/update.sh
```

It pulls `master`, installs the packages, restarts the app and shows the last log lines. During market hours (Mon–Fri 09:00–15:35) it refuses unless you add `--force`; open positions resume from the orders file after the restart.

## Useful commands
| What | Command |
|---|---|
| Live log | `journalctl -u bep-upstox -f` |
| Status | `systemctl status bep-upstox` |
| Restart | `sudo systemctl restart bep-upstox` |
| Stop / start | `sudo systemctl stop bep-upstox` / `sudo systemctl start bep-upstox` |
| Turn the login off | `.venv/bin/python auth.py remove-password` then restart |
| Server time | `timedatectl` (should be Asia/Kolkata) |

The orders, settings and token files stay in `~/BEP_Upstox`, the same as on your PC. Download the orders from **Order Details** as usual.
